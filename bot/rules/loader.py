from __future__ import annotations
import json, pathlib, re
from typing import Any, Dict
import yaml
from bot.paths import get_project_root

_ROOT = get_project_root()
_RULES = _ROOT / "ruleset"
_SNAP  = _ROOT / "snapshots"

def _read_yaml(name: str) -> Dict[str, Any]:
    p = _RULES / name
    return yaml.safe_load(p.read_text(encoding="utf-8"))

def load_all() -> Dict[str, Any]:
    cfg = {
        "limits":    _read_yaml("limits.yaml"),
        "promotion": _read_yaml("promotion.yaml"),
        "tools":     _read_yaml("tools.yaml"),
        "topics":    _read_yaml("topics.yaml"),
    }
    safety = _RULES / "safety.yaml"
    if safety.exists():
        cfg["safety"] = _read_yaml("safety.yaml")
    seeds = _RULES / "seed_urls.yaml"
    if seeds.exists():
        cfg["seeds"] = _read_yaml("seed_urls.yaml")
    else:
        cfg["seeds"] = {"version": 1, "seeds": []}
    return cfg


def topic_allowed(topic: str, topics_cfg: Dict[str, Any]) -> bool:
    import re
    def _slug(s: str) -> str:
        # identical normalization for both sides (convert underscores too)
        return re.sub(r"[^a-z0-9]+", "-", s.strip().lower()).strip("-")

    slug = _slug(topic)
    allowed_raw = topics_cfg.get("allowed_topics", []) or []
    allowed = [_slug(t) for t in allowed_raw]

    # allow if any allowed token appears as a whole token delimited by '-'
    return any(re.search(rf"(?:^|-){re.escape(tok)}(?:-|$)", slug) for tok in allowed)

def load_last_promoted() -> Dict[str, Any] | None:
    p = _SNAP / "metrics" / "last_promoted.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

def save_last_promoted(payload: Dict[str, Any]) -> None:
    d = _SNAP / "metrics"
    d.mkdir(parents=True, exist_ok=True)
    (d / "last_promoted.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

def pareto_gate(new_topic_score: float, root_ok: bool, promo_cfg: Dict[str, Any],
                last: Dict[str, Any] | None) -> tuple[bool, str]:
    """
    Promotion gate = Pareto improvement:
      1) No regressions in root tests, and
      2) KPI strictly increases vs last promoted snapshot, and
      3) KPI meets the absolute threshold.
    """
    gate = promo_cfg.get("pareto_gate", {}) or {}
    require_no_reg = bool(gate.get("require_no_regressions", True))
    min_score = float(gate.get("new_topic_min_score", 0.0))

    # (1) hard block on any root regression / non-pass
    if require_no_reg and not root_ok:
        return (False, "Rejected: root tests regressed or did not pass.")

    # (2) threshold check
    if new_topic_score < min_score:
        return (False, f"Rejected: new_topic_score {new_topic_score:.2f} < threshold {min_score:.2f}.")

    # (3) strict improvement over last promoted (if any)
    if last is not None:
        prev = float(last.get("new_topic_score", 0.0))
        if not (new_topic_score > prev):
            return (False, f"Rejected: KPI did not improve (prev={prev:.2f}, new={new_topic_score:.2f}).")

    return (True, "Promoted: no regressions and KPI improved beyond threshold.")

