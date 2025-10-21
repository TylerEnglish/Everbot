from __future__ import annotations
import json, pathlib, subprocess, sys, datetime
from typing import Dict, Any, List
from bot.paths import get_project_root

ROOT = get_project_root()

def _pass(msg: str) -> Dict[str, Any]:
    return {"status": "PASS", "detail": msg}

def _fail(msg: str) -> Dict[str, Any]:
    return {"status": "FAIL", "detail": msg}

def _skip(msg: str) -> Dict[str, Any]:
    return {"status": "SKIP", "detail": msg}

def test_guard_write_blocked() -> Dict[str, Any]:
    """Writing to ruleset/ or test/ should raise PermissionError (guard)."""
    errors: List[str] = []
    for rel in ("ruleset/__eval_probe.txt", "test/__eval_probe.txt"):
        p = ROOT / rel
        try:
            p.write_text("should fail")
            errors.append(f"Write unexpectedly succeeded: {p}")
        except Exception as e:
            # OK: blocked
            pass
    return _pass("Writes blocked") if not errors else _fail("; ".join(errors))

def test_root_stub_script() -> Dict[str, Any]:
    """Run your legacy root tests script to keep compatibility."""
    script = ROOT / "test" / "run_root_tests.py"
    if not script.exists():
        return _fail("Missing test/run_root_tests.py")
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    if r.returncode == 0:
        return _pass(r.stdout.strip())
    return _fail(f"returncode={r.returncode}, stdout={r.stdout}, stderr={r.stderr}")

def test_mmlu_wired() -> Dict[str, Any]:
    """Check whether MMLU assets are locally available; skip if not."""
    # canonical repo: https://github.com/hendrycks/test
    repo = ROOT / "test" / "bench_mmlu"  # placeholder path you can populate later
    if not repo.exists():
        return _skip("MMLU assets not found (expected under test/bench_mmlu).")
    return _skip("Harness present; scoring not implemented yet.")

def test_gsm8k_wired() -> Dict[str, Any]:
    """Check whether GSM8K assets are locally available; skip if not."""
    data = ROOT / "test" / "bench_gsm8k"
    if not data.exists():
        return _skip("GSM8K dataset not found (expected under test/bench_gsm8k).")
    return _skip("Harness present; scoring not implemented yet.")

def test_hellaswag_wired() -> Dict[str, Any]:
    data = ROOT / "test" / "bench_hellaswag"
    if not data.exists():
        return _skip("HellaSwag dataset not found (expected under test/bench_hellaswag).")
    return _skip("Harness present; scoring not implemented yet.")

def test_humaneval_wired() -> Dict[str, Any]:
    data = ROOT / "test" / "bench_humaneval"
    if not data.exists():
        return _skip("HumanEval dataset not found (expected under test/bench_humaneval).")
    return _skip("Harness present; scoring not implemented yet.")

def run_all() -> Dict[str, Any]:
    results = {
        "security": {
            "guard_write_blocked": test_guard_write_blocked(),
        },
        "root": {
            "legacy_stub": test_root_stub_script(),
        },
        "benchmarks": {
            "mmlu": test_mmlu_wired(),
            "gsm8k": test_gsm8k_wired(),
            "hellaswag": test_hellaswag_wired(),
            "humaneval": test_humaneval_wired(),
        },
    }
    # Decide overall pass: all FAIL -> fail; SKIP doesn't fail the run.
    def ok(entry): return entry["status"] in ("PASS", "SKIP")
    overall = (
        ok(results["security"]["guard_write_blocked"])
        and ok(results["root"]["legacy_stub"])
        and all(ok(v) for v in results["benchmarks"].values())
    )
    payload = {
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
        "overall": "PASS" if overall else "FAIL",
        "results": results,
    }
    # Write an eval artifact for provenance
    out_dir = ROOT / "snapshots" / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"root_eval_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    payload["artifact"] = str(out)
    return payload
