from __future__ import annotations
import json, subprocess, time, pathlib, tempfile, os, sys
from bot.telemetry.tracing import get_tracer
from bot.eval.harness import run_all
from bot.selfopt.ab import ab_chunk
from bot.accel.gate import set_chunk_preference
from bot.paths import get_project_root

# NEW: safe auto-refactorer (writes and adopts patches gated by tests)
from bot.selfopt.autocoder import propose_and_apply_refactor, AutoCodeResult


def _git_safe_commit(msg: str, paths: list[str]) -> None:
    try:
        subprocess.check_call(["git", "add", *paths])
        subprocess.check_call(["git", "commit", "-m", msg])
    except Exception:
        # not a git repo or nothing changed
        pass


def _git_safe_revert() -> None:
    try:
        subprocess.check_call(["git", "reset", "--hard", "HEAD"])
        subprocess.check_call(["git", "clean", "-fd"])
    except Exception:
        pass


def _write_note(p: pathlib.Path, payload: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _adopted_engine_marker() -> pathlib.Path:
    return get_project_root() / "snapshots" / "selfopt" / "ADOPTED_ENGINE.txt"


def _record_adopted_engine(engine: str) -> None:
    m = _adopted_engine_marker()
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(engine.strip(), encoding="utf-8")


def run() -> int:
    """
    1) A/B the chunker (python vs {cpp|rust|go}) with equivalence checks.
       If equivalent and >=1.5x faster, adopt via gate and record marker.
    2) Run root eval to ensure we didn't regress.
    3) Auto-refactor pass (optional; only if LLM configured). Proposes a unified diff,
       applies behind the guard, runs tests; if PASS, commits; else reverts.
    """
    tracer = get_tracer("everabot.self_improve")
    with tracer.start_as_current_span("self_improve.entry"):
        # Prefer real corpus if available
        sdir = get_project_root() / "snapshots" / "rag" / "v1"
        store = "v1" if (sdir / "chunks.parquet").exists() else None

        # --- 1) A/B accelerated chunker
        res = ab_chunk(store=store, n=2000)  # modest load
        note = {
            "ts": int(time.time()),
            "task": "chunk_ab",
            "store": store,
            "equivalent": res.equivalent,
            "accel_impl": res.accel_impl,
            "speedup_x": res.speedup_x,
            "python_sec": res.python_sec,
            "accel_sec": res.accel_sec,
            "python_mem": res.python_mem,
            "accel_mem": res.accel_mem,
        }

        adopted = False
        if res.equivalent and (res.speedup_x or 0) >= 1.5 and res.accel_impl != "python":
            set_chunk_preference(res.accel_impl)
            _record_adopted_engine(res.accel_impl)
            adopted = True
        note["adopted"] = adopted

        # --- 2) Root eval (hard gate)
        root = run_all()
        note["root_eval"] = root

        out = pathlib.Path("snapshots") / "selfopt" / f"chunk_{int(time.time())}.json"
        _write_note(out, note)

        print(f"[green]self-improve[/green] chunker: impl={res.accel_impl} eq={res.equivalent} speedup≈{(res.speedup_x or 0):.2f}x adopted={adopted}")
        print(f"[dim]note:[/dim] {out}")

        ok_root = bool(root.get("overall") in ("PASS", "OK"))
        if ok_root and adopted:
            pref = pathlib.Path("snapshots") / "accel_prefs.json"
            _git_safe_commit(
                f"selfopt: adopt chunk engine {res.accel_impl} (≈{(res.speedup_x or 0):.2f}x, equivalence OK)",
                [str(pref), str(out), str(_adopted_engine_marker())]
            )

        # --- 3) Auto-refactor & code improvement (guarded + gated)
        auto_result: AutoCodeResult | None = None
        try:
            auto_result = propose_and_apply_refactor(max_files=3)
        except Exception as e:
            print(f"[yellow]autocoder skipped[/yellow]: {e}")

        if auto_result and auto_result.patch_applied:
            # re-run root eval after code change
            print("[cyan]autocoder[/cyan] → patch applied; running root eval...")
            root2 = run_all()
            ok2 = bool(root2.get("overall") in ("PASS", "OK"))
            print(f"[cyan]autocoder[/cyan] root eval: {root2.get('overall')}")

            if ok2:
                _git_safe_commit(
                    f"selfopt: autocode patch '{auto_result.title}' (tests PASS)",
                    [str(p) for p in auto_result.touched_paths] + [auto_result.patch_path]
                )
            else:
                print("[red]autocoder change failed tests; reverting[/red]")
                _git_safe_revert()
                # keep the patch artifact for inspection; commit only the patch file if desired

        return 0 if ok_root else 1
