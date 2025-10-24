from __future__ import annotations
import json, subprocess, time, pathlib
from bot.telemetry.tracing import get_tracer
from bot.eval.harness import run_all
from bot.selfopt.ab import ab_chunk
from bot.accel.gate import set_chunk_preference
from bot.paths import get_project_root

def _git_safe_commit(msg: str, paths: list[str]) -> None:
    try:
        subprocess.check_call(["git", "add", *paths])
        subprocess.check_call(["git", "commit", "-m", msg])
    except Exception:
        pass  # not a git repo or nothing changed

def _write_note(p: pathlib.Path, payload: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

def run() -> int:
    tracer = get_tracer("everabot.self_improve")
    with tracer.start_as_current_span("self_improve.entry"):
        # Prefer real corpus if available
        sdir = get_project_root() / "snapshots" / "rag" / "v1"
        store = "v1" if (sdir / "chunks.parquet").exists() else None

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
            adopted = True
        note["adopted"] = adopted

        root = run_all()
        note["root_eval"] = root

        out = pathlib.Path("snapshots") / "selfopt" / f"chunk_{int(time.time())}.json"
        _write_note(out, note)

        status = "adopted" if adopted else "kept python"
        print(f"[green]self-improve[/green] chunker: impl={res.accel_impl} eq={res.equivalent} speedup≈{(res.speedup_x or 0):.2f}x adopted={adopted}")
        print(f"[dim]note:[/dim] {out}")

        ok = bool(root.get("overall") in ("PASS", "OK"))
        if ok and adopted:
            pref = pathlib.Path("snapshots") / "accel_prefs.json"
            _git_safe_commit(
                f"selfopt: adopt chunk engine {res.accel_impl} (≈{(res.speedup_x or 0):.2f}x, equivalence OK)",
                [str(pref), str(out)]
            )
        return 0 if ok else 1
