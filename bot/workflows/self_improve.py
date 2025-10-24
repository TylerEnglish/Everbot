from __future__ import annotations
import json
import subprocess
import time
import pathlib
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
        # No-op if not a git repo or nothing changed
        pass


def _write_note(p: pathlib.Path, payload: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run() -> int:
    tracer = get_tracer("everabot.self_improve")
    with tracer.start_as_current_span("self_improve.entry"):
        # 1) Try to find a free win on the chunker (use real corpus if available)
        store = None
        sdir = get_project_root() / "snapshots" / "rag" / "v1"
        if (sdir / "chunks.parquet").exists():
            store = "v1"
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

        # 2) If equivalent and meaningfully faster, adopt it
        adopted = False
        if res.equivalent and res.speedup_x >= 1.5 and res.accel_impl != "python":
            set_chunk_preference(res.accel_impl)
            adopted = True
        note["adopted"] = adopted

        # 3) Re-run root tests as the promotion gate
        root = run_all()
        note["root_eval"] = root

        # Persist the decision + A/B evidence into snapshots
        out = pathlib.Path("snapshots") / "selfopt" / f"chunk_{int(time.time())}.json"
        _write_note(out, note)
        # Make it visible in the CLI output
        print(json.dumps({"self_improve": note, "note_path": str(out)}, indent=2))

        # 4) If tests passed and we adopted, commit the pref file
        ok = bool(root.get("overall") == "PASS" or root.get("overall") == "OK")
        if ok and adopted:
            pref = pathlib.Path("snapshots") / "accel_prefs.json"
            _git_safe_commit(
                f"selfopt: adopt chunk engine {res.accel_impl} (≈{res.speedup_x:.2f}x, equivalence OK)",
                [str(pref), str(out)],
            )

        # Exit code follows promotion rule: only keep if no regressions
        return 0 if ok else 1
