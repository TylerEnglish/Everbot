from __future__ import annotations
import json, subprocess, time, pathlib, tempfile, os, sys, re
from typing import Dict, List, Tuple, Optional

from bot.telemetry.tracing import get_tracer
from bot.eval.harness import run_all
from bot.selfopt.ab import ab_chunk
from bot.accel.gate import set_chunk_preference
from bot.paths import get_project_root
from bot.selfopt.autocoder import propose_and_apply_refactor, AutoCodeResult
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    pass  # Autocode: non-functional type-only block
from bot.eval.postprocess import load_baseline_score, enhance_eval_payload, should_adopt

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

def _git_safe_branch(prefix: str) -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    name = f"{prefix}/{ts}"
    try:
        subprocess.check_call(["git", "checkout", "-b", name])
    except Exception:
        # ignore if not a git repo or branch exists
        pass
    return name

def _write_note(p: pathlib.Path, payload: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

def _adopted_engine_marker() -> pathlib.Path:
    return get_project_root() / "snapshots" / "selfopt" / "ADOPTED_ENGINE.txt"

def _record_adopted_engine(engine: str) -> None:
    m = _adopted_engine_marker()
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(engine.strip(), encoding="utf-8")

def _lang_compile_checks(touched: list[str]) -> list[dict]:
    """
    Run cheap, best-effort builds for accel targets when relevant files changed.
    Never fails the run; returns a list of results for logging.
    """
    root = get_project_root()
    exts = {pathlib.Path(p).suffix.lower() for p in touched}
    results: list[dict] = []
    def _run(cmd: list[str], cwd: pathlib.Path | None = None) -> tuple[int,str,str]:
        try:
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
            return p.returncode, p.stdout[-4000:], p.stderr[-4000:]
        except Exception as e:
            return 127, "", str(e)
    # C/C++ -> try building the cpp accelerator if present
    if any(x in exts for x in (".c",".cc",".cpp",".h",".hpp")) and (root/"bot/accel/cpp").exists():
        rc,out,err = _run([sys.executable,"-m","bot.cli","opt","build-accel","--target","chunk-cpp"], cwd=root)
        results.append({"target":"cpp","rc":rc,"out":out,"err":err})
    # Rust
    if ".rs" in exts and (root/"bot/accel/rust").exists():
        rc,out,err = _run([sys.executable,"-m","bot.cli","opt","build-accel","--target","chunk-rust"], cwd=root)
        results.append({"target":"rust","rc":rc,"out":out,"err":err})
    # Go (best effort; only if a go module is present)
    if ".go" in exts and (root/"bot/accel/go").exists():
        rc,out,err = _run(["go","build","./..."], cwd=root/"bot/accel/go")
        results.append({"target":"go","rc":rc,"out":out,"err":err})
    # ASM: no generic build step here
    return results


# -------------------- New: bad-patch guardrails --------------------

_DUP_EXCEPT_RE = re.compile(r'^\+\s*except\b[^:]*:', re.IGNORECASE)
# Common “same line added multiple times” junk smell (e.g., raise NotConfigured lines repeated)
_DUP_LINE_BUCKET: int = 3  # if the exact same added line appears ≥3 times across hunks → reject

def _diff_has_duplicate_except_or_spam(diff_text: str) -> bool:
    """
    Return True if the proposed unified diff shows obviously bad patterns:
      • Duplicated 'except …:' lines within a single file’s patch
      • The exact same '+…' line added 3+ times across the whole patch
    """
    if not diff_text.strip():
        return False
    per_file_buckets: Dict[str, Dict[str, int]] = {}
    global_added_count: Dict[str, int] = {}
    cur_file: Optional[str] = None

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            parts = raw.split()
            cur_file = parts[3][2:] if len(parts) >= 4 and parts[3].startswith("b/") else None
            continue
        if not cur_file:
            continue
        if raw.startswith("@@ "):
            continue
        if raw.startswith("+++ ") or raw.startswith("--- "):
            continue
        if raw.startswith("+"):
            body = raw[1:].rstrip()
            global_added_count[body] = global_added_count.get(body, 0) + 1
            # per-file duplicate excepts
            if _DUP_EXCEPT_RE.match(raw):
                bucket = per_file_buckets.setdefault(cur_file, {})
                key = body  # exact text of the except line
                bucket[key] = bucket.get(key, 0) + 1
                if bucket[key] >= 2:
                    return True  # duplicate identical except within same file
    # global spam line repeated many times
    if any(cnt >= _DUP_LINE_BUCKET for cnt in global_added_count.values()):
        return True
    return False

def _run_root_eval() -> dict:
    p = subprocess.run([sys.executable, "-m", "bot.cli", "eval"], capture_output=True, text=True, check=False)
    # The CLI prints a summary line; we load the JSON artifact that eval wrote.
    # Find the newest root_eval_*.json under snapshots/evals
    root = pathlib.Path(__file__).resolve().parents[2]
    evdir = root / "snapshots" / "evals"
    latest = max(evdir.glob("root_eval_*.json"), key=lambda p: p.stat().st_mtime)
    obj = json.loads(latest.read_text(encoding="utf-8"))
    return enhance_eval_payload(obj, write_back=True)

def _git_reset_hard(ref: str="HEAD"):
    subprocess.run(["git","reset","--hard", ref], check=False)

def run() -> int:
    """
    1) A/B the chunker (python vs {cpp|rust|go}) with equivalence checks.
       If equivalent and >=1.5x faster, adopt via gate and record marker.
    2) Run root eval to ensure we didn't regress.
    3) Auto-refactor pass with stricter adoption gate:
       - Reject obviously junk patches (duplicate 'except' lines / spam).
       - Only commit if baseline was failing and the patch makes tests PASS.
         (If baseline already PASS, keep artifacts but don't commit.)
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

        baseline_ok = bool(root.get("overall") in ("PASS", "OK"))

        if baseline_ok and adopted:
            pref = pathlib.Path("snapshots") / "accel_prefs.json"
            _git_safe_commit(
                f"selfopt: adopt chunk engine {res.accel_impl} (≈{(res.speedup_x or 0):.2f}x, equivalence OK)",
                [str(pref), str(out), str(_adopted_engine_marker())]
            )

        # --- 3) Auto-refactor & code improvement (guarded + gated), multiple attempts
        attempts = 3
        ok2 = True  # default: no patch applied → baseline stays valid
        for i in range(attempts):
            auto_result: AutoCodeResult | None = None
            try:
                # Keep the underlying autocoder intact; we enforce quality here.
                auto_result = propose_and_apply_refactor(max_files=3)
            except Exception as e:
                print(f"[yellow]autocoder skipped[/yellow]: {e}")
                break

            if not auto_result:
                print("[dim]autocoder produced no output.[/dim]")
                break
            else:
                print(f"[dim]autocoder artifacts:[/dim] {auto_result.artifacts_dir}  [dim]patch:[/dim] {auto_result.patch_path}")

            if not auto_result.patch_applied:
                print(f"[dim]autocoder attempt {i+1} produced no applicable patch ({auto_result.note}).[/dim]")
                continue

            # New: reject obviously junk patches (duplicate 'except', spam lines) before any eval
            try:
                diff_text = pathlib.Path(auto_result.patch_path).read_text(encoding="utf-8", errors="replace")
            except Exception:
                diff_text = ""
            if _diff_has_duplicate_except_or_spam(diff_text):
                print("[red]autocoder change flagged as junk (duplicate 'except' or spam). Reverting.[/red]")
                _git_safe_revert()
                continue

            # Patch applied → re-run root eval and gate adoption
            print("[cyan]autocoder[/cyan] → patch applied; running root eval...")
            root2 = run_all()
            ok2 = bool(root2.get("overall") in ("PASS", "OK"))
            print(f"[cyan]autocoder[/cyan] root eval: {root2.get('overall')}")

            # New: Only commit if the patch *improves a failing baseline*.
            # If baseline was already PASS, keep artifacts for review but don't commit “busywork” patches.
            if ok2 and not baseline_ok:
                _git_safe_commit(
                    f"selfopt: autocode patch '{auto_result.title}' (fixed failing baseline → PASS)",
                    [str(p) for p in auto_result.touched_paths] + [auto_result.patch_path]
                )
                break  # success; no further attempts this cycle
            else:
                print("[yellow]autocoder result did not improve baseline (or baseline already PASS); reverting.[/yellow]")
                _git_safe_revert()
                # keep artifacts for inspection; try another attempt

        return 0 if baseline_ok or ok2 else 1
