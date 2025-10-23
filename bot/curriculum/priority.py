from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, List
import math
from bot.rules.loader import load_all, load_last_promoted
from bot.curriculum.registry import list_candidates, topic_default_strategies

@dataclass
class Plan:
    topic: str
    skill_id: str
    skill_title: str
    strategy: str
    reason: str

def _weights_and_caps(promo: dict) -> tuple[Dict[str,float], Dict[str,float]]:
    u = (promo.get("utility") or {})
    w = (u.get("weights") or {})
    weights = {
        "score": float(w.get("score", 0.5)),
        "cost":  float(w.get("cost", 0.25)),
        "time":  float(w.get("time", 0.25)),
    }
    caps = {
        "time": float(u.get("time_cap_sec", 300)),
        "cost": float(u.get("cost_cap_usd", 10)),  # not used directly; we proxy w/ RAM/CPU
    }
    return weights, caps

def _limits_caps(limits: dict) -> dict:
    # Convert wall-time cap to seconds per “learn” run (we’ll use phase caps if present)
    ph = (limits.get("phases") or {}).get("RESEARCH", {}) or {}
    return {
        "wall_sec": float(limits.get("wall_time_sec_max", 900)),
        "phase_research_sec": float(ph.get("wall_time_sec_max", 240)),
        "ram_mb": float(limits.get("ram_mb_max", 4096)),
        "cpu_pct": float((limits.get("cpu") or {}).get("percent_max", 85)),
    }

def _fits_budget(skill, caps: dict) -> bool:
    return (
        skill.time_min * 60.0 <= max(60.0, caps["phase_research_sec"]) and
        skill.ram_mb_est <= caps["ram_mb"] and
        skill.cpu_pct_est <= caps["cpu_pct"]
    )

def _utility(skill, weights: dict, caps: dict, last: dict|None) -> float:
    # Normalize time/cost pressure into [0,1]
    t_pen = min(1.0, (skill.time_min * 60.0) / max(1.0, caps["time"]))  # use utility time horizon
    # Cost proxy: average of RAM and CPU pressures (normalized)
    ram_p = min(1.0, skill.ram_mb_est / max(1.0, caps["ram_mb"]))
    cpu_p = min(1.0, skill.cpu_pct_est / max(1.0, caps["cpu_pct"]))
    cost_pen = 0.5 * (ram_p + cpu_p)
    # Risk: subtract a modest penalty; if last promotion was weak, damp more
    kpi_last = float((last or {}).get("new_topic_score", 0.8))
    risk_pen = skill.risk_regression * (1.0 + (0.2 if kpi_last < 0.85 else 0.0))
    # Utility (larger is better)
    return (weights["score"] * skill.est_gain
            - weights["time"] * t_pen
            - weights["cost"] * cost_pen
            - 0.15 * risk_pen)

def select_next(preferred_topic: str|None = None) -> Plan|None:
    cfg = load_all()
    weights, u_caps = _weights_and_caps(cfg.get("promotion", {}))
    limits_caps = _limits_caps(cfg.get("limits", {}))
    last = load_last_promoted()
    skills = [s for s in list_candidates() if _fits_budget(s, {**u_caps, **limits_caps})]
    if preferred_topic:
        slug = preferred_topic.lower().replace(" ", "-")
        skills = [s for s in skills if s.topic.lower().replace(" ", "-") == slug]
    if not skills:
        return None
    best = max(skills, key=lambda s: _utility(s, weights, {**u_caps, **limits_caps}, last))
    strategy = topic_default_strategies(best.topic)[0]
    return Plan(topic=best.topic, skill_id=best.id, skill_title=best.title,
                strategy=strategy,
                reason=f"max utility under time/CPU/RAM caps (est_gain={best.est_gain:.2f})")
