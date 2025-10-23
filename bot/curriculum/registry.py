from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
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
    topics = cfg.get("topics", {}) or {}
    out: Dict[str, TopicCfg] = {}
    for tname, tcfg in topics.items():
        skills_raw = tcfg.get("skills", []) or []
        skills = []
        for s in skills_raw:
            skills.append(Skill(
                topic=tname, id=s["id"], title=s["title"],
                est_gain=float(s.get("est_gain", 0.0)),
                risk_regression=float(s.get("risk_regression", 0.0)),
                time_min=int(s.get("time_min", 1)),
                cpu_pct_est=int(s.get("cpu_pct_est", 10)),
                ram_mb_est=int(s.get("ram_mb_est", 64)),
            ))
        out[_slug(tname)] = TopicCfg(
            name=tname,
            safety_level=str(tcfg.get("safety_level", "low")),
            default_strategies=list(tcfg.get("default_strategies", ["vanilla"])),
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
