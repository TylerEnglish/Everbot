from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from .types import Skill, TopicCfg  # whatever you already import today
from bot.rules.loader import load_all
import json, pathlib, re
from bot.rules.loader import load_all
from bot.paths import get_project_root

_ROOT = get_project_root()
_PROGRESS = _ROOT / "snapshots" / "curriculum" / "progress.json"

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.strip().lower()).strip("-")

@dataclass
class Skill:
    topic: str
    id: str
    title: str
    est_gain: float
    risk_regression: float
    time_min: int
    cpu_pct_est: int
    ram_mb_est: int

@dataclass
class TopicCfg:
    name: str
    safety_level: str
    default_strategies: List[str]
    skills: List[Skill]

def _load_progress() -> dict:
    if not _PROGRESS.exists(): return {"done": []}
    try: return json.loads(_PROGRESS.read_text(encoding="utf-8"))
    except Exception: return {"done": []}

def _save_progress(p: dict) -> None:
    _PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    _PROGRESS.write_text(json.dumps(p, indent=2), encoding="utf-8")

def mark_done(topic: str, skill_id: str) -> None:
    p = _load_progress()
    token = f"{_slug(topic)}/{_slug(skill_id)}"
    if token not in p["done"]:
        p["done"].append(token)
    _save_progress(p)

def is_done(topic: str, skill_id: str) -> bool:
    p = _load_progress()
    token = f"{_slug(topic)}/{_slug(skill_id)}"
    return token in p.get("done", [])

def load_registry() -> Dict[str, TopicCfg]:
    cfg = load_all().get("topics", {})
    raw = cfg.get("topics") or {}

    # Normalize: support both dict (legacy) and list (new)
    if isinstance(raw, list):
        topics_iter = [(t.get("id") or t.get("name"), t) for t in raw]
    elif isinstance(raw, dict):
        topics_iter = raw.items()
    else:
        topics_iter = []

    out: Dict[str, TopicCfg] = {}
    for tname, tcfg in topics_iter:
        if not tname or not isinstance(tcfg, dict):
            continue
        skills_raw = (tcfg.get("skills") or [])
        skills: List[Skill] = []
        for s in skills_raw:
            # Be permissive with missing fields
            skills.append(Skill(
                id=s["id"],
                title=s.get("title", s["id"]),
                est_gain=float(s.get("est_gain", 0.0)),
                risk_regression=float(s.get("risk_regression", 0.0)),
                time_min=int(s.get("time_min", 0)),
                cpu_pct_est=int(s.get("cpu_pct_est", 0)),
                ram_mb_est=int(s.get("ram_mb_est", 0)),
                notes=s.get("notes", None),
            ))
        out[tname] = TopicCfg(
            id=tcfg.get("id", tname),
            name=tcfg.get("name", tname),
            safety_level=tcfg.get("safety_level", "low"),
            default_strategies=list(tcfg.get("default_strategies", [])),
            notes=tcfg.get("notes", None),
            skills=skills,
        )
    return out

def list_candidates() -> List[Skill]:
    reg = load_registry()
    out: List[Skill] = []
    for t in reg.values():
        for s in t.skills:
            if not is_done(t.name, s.id):
                out.append(s)
    return out

def topic_default_strategies(topic_slug: str) -> List[str]:
    reg = load_registry()
    t = reg.get(_slug(topic_slug))
    return list(t.default_strategies) if t else ["vanilla"]
