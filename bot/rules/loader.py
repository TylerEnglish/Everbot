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
    return {
        "limits":    _read_yaml("limits.yaml"),
        "promotion": _read_yaml("promotion.yaml"),
        "tools":     _read_yaml("tools.yaml"),
        "topics":    _read_yaml("topics.yaml"),
    }

def topic_allowed(topic: str, topics_cfg: Dict[str, Any]) -> bool:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.strip().lower())
    allowed = [t.strip().lower() for t in topics_cfg.get("allowed_topics", [])]
    # allow if any allowed token appears as a whole word in slug
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
    require_no_reg = bool(promo_cfg.get("pareto_gate", {}).get("require_no_regressions", True))
    min_score = float(promo_cfg.get("pareto_gate", {}).get("new_topic_min_score", 0.0))
    if require_no_reg and not root_ok:
        return (False, "Rejected: root tests regressed.")
    if new_topic_score < min_score:
        return (False, f"Rejected: new_topic_score {new_topic_score:.2f} < threshold {min_score:.2f}.")
    return (True, "Promoted.")
