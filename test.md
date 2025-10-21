huh?
let's redo that, so this is what I got in the json:
```
{
  "ts": "2025-10-21T18:00:16.297431Z",
  "overall": "PASS",
  "results": {
    "security": {
      "guard_write_blocked": {
        "status": "PASS",
        "detail": "Writes blocked"
      }
    },
    "root": {
      "legacy_stub": {
        "status": "PASS",
        "detail": "[OK] safety.yaml sanity\n[OK] limits.yaml sanity\n[OK] promotion.yaml sanity\n[OK] tools.yaml sanity\n[OK] topics.yaml sanity\n[OK] sanity cases present\nPASS"
      },
      "conversation_sanity": {
        "status": "PASS",
        "detail": "Sanity PASS"
      }
    },
    "benchmarks": {
      "mmlu": {
        "status": "SKIP",
        "detail": "MMLU CSVs parsed but no usable rows"
      },
      "gsm8k": {
        "status": "PASS",
        "detail": "em_acc=0.000 on 200 items, thr=None"
      },
      "hellaswag": {
        "status": "SKIP",
        "detail": "HellaSwag file parsed but empty."
      }
    }
  }
}
```

Seems it skips MMLU and hellaswag

MMLU Looks like this:

```
test/raw/data/
|-auxiliary_train/ (csvs)
|-dev/ (csvs)
|-test/ (csvs)
|-val/ (csvs)
|-possibly_contaminated_urls.txt
```

hellaswage looks like this:
```
test/raw/*.jsonl
```

So fix this harness file:
```
from __future__ import annotations
import os, re, yaml
import json, pathlib, subprocess, sys, datetime, random, csv
from typing import Dict, Any, List
from bot.paths import get_project_root
from bot.eval.sanity import run_conversation_sanity
from bot.rules.loader import load_all

ROOT = get_project_root()

def _pass(msg: str) -> Dict[str, Any]:
    return {"status": "PASS", "detail": msg}

def _fail(msg: str) -> Dict[str, Any]:
    return {"status": "FAIL", "detail": msg}

def _skip(msg: str) -> Dict[str, Any]:
    return {"status": "SKIP", "detail": msg}

def _meets_threshold(metric: float|None, thresh: float|None) -> bool:
    if thresh is None or metric is None:
        return True
    return metric >= thresh

def _promotion_thresholds():
    try:
        promo = load_all().get("promotion", {})
        return (promo.get("bench_thresholds") or {})
    except Exception:
        return {}

# --- helper for GSM8K ---
def _extract_final_number(txt: str) -> str|None:
    for line in reversed((txt or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("####"):
            return "".join(ch for ch in line[4:] if ch.isdigit() or ch in ".-").strip() or None
    return None

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
    base = ROOT / "test" / "bench_mmlu" / "raw"
    if not base.exists():
        return _skip("MMLU assets not found (expected under test/bench_mmlu/raw).")

    csvs = list(base.rglob("*.csv"))
    if not csvs:
        return _skip("MMLU found but no CSV files under raw/**")

    total = correct = 0
    for csv_path in sorted(csvs)[:30]:  # small cap to keep it quick
        try:
            with open(csv_path, encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                # support common headers: answer or label as letters A-D
                for row in reader:
                    gold = (row.get("answer") or row.get("Answer") or row.get("label") or "").strip().upper()
                    if gold not in {"A","B","C","D"}:
                        continue
                    guess = random.choice(["A","B","C","D"])
                    correct += int(guess == gold)
                    total += 1
        except Exception:
            continue
    if total == 0:
        return _skip("MMLU CSVs parsed but no usable rows")
    acc = correct / total
    thr = _promotion_thresholds().get("mmlu_macro_min")
    status = "PASS" if _meets_threshold(acc, thr) else "FAIL"
    return {"status": status, "detail": f"random_acc={acc:.3f} on {total} Q, thr={thr}"}

# --- GSM8K scorer (trivial EM baseline) ---
def test_gsm8k_wired() -> Dict[str, Any]:
    base = ROOT / "test" / "bench_gsm8k" / "raw"
    if not base.exists():
        return _skip("GSM8K dataset not found (expected under test/bench_gsm8k/raw).")
    test_file = next((p for p in [base / "test.jsonl", base / "test_socratic.jsonl"] if p.exists()), None)
    if not test_file:
        # fall back to any jsonl
        test_file = next(iter(base.glob("*.jsonl")), None)
        if not test_file:
            return _skip("Found GSM8K folder but no test*.jsonl.")
    total = correct = 0
    with open(test_file, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i >= 200:
                break
            ex = json.loads(line)
            gold = _extract_final_number(ex.get("answer", ""))
            pred = "0"
            correct += int(gold is not None and pred == gold)
            total += 1
    acc = correct / total if total else 0.0
    thr = _promotion_thresholds().get("gsm8k_acc_min")
    status = "PASS" if _meets_threshold(acc, thr) else "FAIL"
    return {"status": status, "detail": f"em_acc={acc:.3f} on {total} items, thr={thr}"}

def test_hellaswag_wired() -> Dict[str, Any]:
    base = ROOT / "test" / "bench_hellaswag" / "raw"
    if not base.exists():
        return _skip("HellaSwag dataset not found (expected under test/bench_hellaswag/raw).")
    # try common file names; otherwise any *.jsonl
    candidates = [base / "val.jsonl", base / "dev.jsonl", base / "validation.jsonl", base / "train.jsonl"]
    path = next((p for p in candidates if p.exists()), None) or next(iter(base.glob("*.jsonl")), None)
    if not path:
        return _skip("No JSONL files found in bench_hellaswag/raw")

    total = correct = 0
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i >= 200:
                break
            ex = json.loads(line)
            gold = ex.get("label")
            if gold is None:
                continue
            guess = random.randint(0, 3)
            correct += int(guess == gold)
            total += 1
    if total == 0:
        return _skip("HellaSwag file parsed but empty.")
    acc = correct / total
    thr = _promotion_thresholds().get("hellaswag_acc_min")
    status = "PASS" if _meets_threshold(acc, thr) else "FAIL"
    return {"status": status, "detail": f"random_acc={acc:.3f} on {total} items, thr={thr}"}

def test_conversation_sanity() -> Dict[str, Any]:
    """
    Runs a tiny, authored sanity set. SKIP until an LLM backend is configured.
    Backend selection: set EVERABOT_LLM=ollama (or another string you support later).
    """
    backend = os.getenv("EVERABOT_LLM")
    cases_path = ROOT / "test" / "sanity" / "cases.yaml"
    if not cases_path.exists():
        return _skip("No sanity cases found (expected test/sanity/cases.yaml).")
    if not backend:
        return _skip("No LLM backend configured (set EVERABOT_LLM=ollama)")

    # Minimal “loopback” to prove the harness runs without shipping an LLM integration yet:
    # For now we just echo back a deterministic answer for the first token-level prompts.
    # Later, replace this with your backend call (ollama/local transformers).
    try:
        data = yaml.safe_load(cases_path.read_text(encoding="utf-8"))["cases"]
        failures = []
        for c in data:
            prompt = c["prompt"]
            # Stubbed agent response: simplest viable template you’ll replace later.
            resp = "OK" if "OK" in prompt else ("pong" if "pong" in prompt else "")
            if not re.search(c["expect_regex"], resp or ""):
                failures.append({"prompt": prompt, "resp": resp})
        return _pass("Sanity PASS") if not failures else _fail(f"Sanity FAIL: {failures}")
    except Exception as e:
        return _fail(f"Sanity error: {e}")


def run_all() -> Dict[str, Any]:
    results = {
        "security": {
            "guard_write_blocked": test_guard_write_blocked(),
        },
        "root": {
            "legacy_stub": test_root_stub_script(),
            "conversation_sanity": test_conversation_sanity(), 
        },
        "benchmarks": {
            "mmlu": test_mmlu_wired(),
            "gsm8k": test_gsm8k_wired(),
            "hellaswag": test_hellaswag_wired(),
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

```