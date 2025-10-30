from __future__ import annotations
import json, pathlib, re, os, math, time
from typing import Dict, Any, Tuple

_FLOAT = r"([-+]?\d+(?:\.\d+)?)"

def _m(m, i, d=0.0):
    try:
        return float(m.group(i))
    except Exception:
        return float(d)

def _rx(pat: str, s: str) -> float:
    m = re.search(pat, s or "")
    return float(m.group(1)) if m else 0.0

def _extract_bench(results: Dict[str, Any]) -> Dict[str, float]:
    out = {"mmlu_acc": 0.0, "gsm_em": 0.0, "hella_acc": 0.0}
    if "benchmarks" not in results: 
        return out
    b = results["benchmarks"]
    if "mmlu" in b and isinstance(b["mmlu"], dict):
        out["mmlu_acc"] = _rx(r"model_acc=" + _FLOAT, b["mmlu"].get("detail",""))
    if "gsm8k" in b and isinstance(b["gsm8k"], dict):
        out["gsm_em"] = _rx(r"model_em_acc=" + _FLOAT, b["gsm8k"].get("detail",""))
    if "hellaswag" in b and isinstance(b["hellaswag"], dict):
        out["hella_acc"] = _rx(r"model_acc=" + _FLOAT, b["hellaswag"].get("detail",""))
    return out

def _extract_perf(results: Dict[str, Any]) -> Dict[str, float]:
    out = {"tps_avg": 0.0, "p50_ms": 0.0, "rss_end_mb": 0.0, "efficiency": 0.0}
    p = results.get("performance", {})
    if "speed" in p and isinstance(p["speed"], dict):
        out["tps_avg"] = _rx(r"tps_avg=" + _FLOAT, p["speed"].get("detail",""))
        out["p50_ms"]  = _rx(r"p50=" + _FLOAT,     p["speed"].get("detail",""))
    if "memory" in p and isinstance(p["memory"], dict):
        out["rss_end_mb"] = _rx(r"rss_end=" + _FLOAT, p["memory"].get("detail",""))
    if "efficiency" in p and isinstance(p["efficiency"], dict):
        out["efficiency"] = _rx(r"efficiency=" + _FLOAT, p["efficiency"].get("detail",""))
    return out

def _composite(bench: Dict[str,float], perf: Dict[str,float]) -> float:
    # Bench dominates; perf nudges. All terms are ~[0..1] scale except perf units.
    bench_score = (bench["mmlu_acc"] + bench["gsm_em"] + bench["hella_acc"]) / 3.0
    # Normalize perf roughly: higher TPS/eff is better; lower p50/rss is better.
    def nz(x): return x if x > 1e-9 else 1e-9
    tps_term = math.tanh(perf["tps_avg"] / 40.0)          # ~20 TPS → 0.46, 40 → ~0.76
    eff_term = math.tanh(perf["efficiency"] / 100.0)
    p50_term = math.tanh(8_000.0 / (nz(perf["p50_ms"]) + 1.0))  # 7.5s → ~0.73
    rss_term = math.tanh(600.0 / (nz(perf["rss_end_mb"]) + 1.0))
    perf_score = (0.35*tps_term + 0.25*eff_term + 0.20*p50_term + 0.20*rss_term)
    return 0.85*bench_score + 0.15*perf_score

def enhance_eval_payload(payload: Dict[str,Any], write_back: bool=True) -> Dict[str,Any]:
    """Add scorecard (+composite) into eval payload; optionally persist to artifact."""
    results = payload.get("results") or {}
    bench = _extract_bench(results)
    perf  = _extract_perf(results)
    comp  = _composite(bench, perf)
    sc = {"bench": bench, "perf": perf, "composite": comp}
    payload["scorecard"] = sc
    # persist
    art = payload.get("artifact")
    if write_back and art:
        try:
            p = pathlib.Path(art)
            obj = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            obj["scorecard"] = sc
            p.write_text(json.dumps(obj, indent=2), encoding="utf-8")
        except Exception:
            pass
    return payload

def load_baseline_score() -> Tuple[Dict[str,float], Dict[str,float], float] | None:
    """
    Try to read the last promoted run's eval JSON and return (bench, perf, composite).
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    last = root / "snapshots" / "metrics" / "last_promoted.json"
    if not last.exists():
        return None
    try:
        meta = json.loads(last.read_text(encoding="utf-8"))
        art = meta.get("root_eval_artifact")
        if not art:
            return None
        obj = json.loads(pathlib.Path(art).read_text(encoding="utf-8"))
        sc = obj.get("scorecard")
        if not sc:
            # Allows backward-compat: compute on the fly if missing
            sc = enhance_eval_payload(obj, write_back=False).get("scorecard")
        if not sc:
            return None
        return sc["bench"], sc["perf"], float(sc["composite"])
    except Exception:
        return None

def should_adopt(current_sc: Dict[str,Any], baseline: Tuple[Dict[str,float],Dict[str,float],float] | None) -> Tuple[bool,str]:
    """
    Decide adoption by 'not worse' with small epsilon; prefer improvements.
    Guardrails:
      - No benchmark regresses > eps_bench
      - At least one signal improves > eps_imp  OR composite >= baseline - eps_equal (if mode==not_worse)
    """
    mode = os.getenv("EVERABOT_PROMOTE_MODE","improve").strip().lower()  # improve|not_worse
    eps_bench = float(os.getenv("EVERABOT_PROMOTE_BENCH_EPS","0.005"))   # 0.5 pp
    eps_imp   = float(os.getenv("EVERABOT_PROMOTE_IMP_EPS","0.010"))     # 1.0 pp
    eps_equal = float(os.getenv("EVERABOT_PROMOTE_EQUAL_EPS","0.0025"))  # 0.25 pp composite

    cb, cp = current_sc["bench"], current_sc["perf"]
    cc = float(current_sc["composite"])
    if not baseline:
        return False, "no-baseline"
    bb, bp, bc = baseline

    # No hard regression on bench
    for k in ("mmlu_acc","gsm_em","hella_acc"):
        if cb[k] < bb[k] - eps_bench:
            return False, f"bench-regress:{k}:{cb[k]:.4f}<{bb[k]-eps_bench:.4f}"

    # Any improvement?
    imp = (
        (cb["mmlu_acc"] > bb["mmlu_acc"] + eps_imp) or
        (cb["gsm_em"]   > bb["gsm_em"]   + eps_imp) or
        (cb["hella_acc"]> bb["hella_acc"]+ eps_imp) or
        (cp["tps_avg"]  > bp["tps_avg"]  * (1.0 + 0.03)) or        # +3% TPS
        (cp["p50_ms"]   < bp["p50_ms"]   * (1.0 - 0.03)) or        # -3% p50
        (cp["rss_end_mb"] < bp["rss_end_mb"] * (1.0 - 0.03)) or    # -3% RSS
        (cp["efficiency"] > bp["efficiency"] * (1.0 + 0.03))       # +3% eff
    )
    if imp:
        return True, "improved"
    if mode == "not_worse" and (cc >= (bc - eps_equal)):
        return True, "not-worse"
    return False, "no-improvement"
