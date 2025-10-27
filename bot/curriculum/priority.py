from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple, Iterable
import math, json, pathlib, time
from bot.paths import get_project_root
from bot.rules.loader import load_all, load_last_promoted
from bot.curriculum.registry import list_candidates, topic_default_strategies


@dataclass
class Plan:
    topic: str
    skill_id: str
    skill_title: str
    strategy: str
    reason: str

    @property
    def slug(self) -> str:
        return _slug(self.topic)


# ---------------- helpers ----------------

def _slug(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "-")


def _weights_and_caps(promo: dict) -> Tuple[Dict[str, float], Dict[str, float]]:
    u = (promo.get("utility") or {})
    w = (u.get("weights") or {})
    weights = {
        "score": float(w.get("score", 0.5)),
        "cost":  float(w.get("cost", 0.25)),
        "time":  float(w.get("time", 0.25)),
    }
    caps = {
        # NOTE: time_cap_sec governs the *utility* horizon (penalty curve) not a hard cap
        "time": float(u.get("time_cap_sec", 300)),
        # cost isn't $ here; we proxy with resource pressures
        "cost": float(u.get("cost_cap_usd", 10)),
    }
    return weights, caps


def _limits_caps(limits: dict) -> dict:
    ph = (limits.get("phases") or {}).get("RESEARCH", {}) or {}
    return {
        "wall_sec": float(limits.get("wall_time_sec_max", 900)),
        "phase_research_sec": float(ph.get("wall_time_sec_max", 240)),
        "ram_mb": float(limits.get("ram_mb_max", 4096)),
        "cpu_pct": float((limits.get("cpu") or {}).get("percent_max", 85)),
        # soft rotation: consecutive repeat avoidance
        "cooldown_runs": int((limits.get("curriculum") or {}).get("cooldown_runs", 1)),
    }


def _fits_budget(skill, caps: dict) -> bool:
    return (
        skill.time_min * 60.0 <= max(60.0, caps["phase_research_sec"]) and
        skill.ram_mb_est <= caps["ram_mb"] and
        skill.cpu_pct_est <= caps["cpu_pct"]
    )


def _latest_run_skill_id() -> Optional[str]:
    root = get_project_root()
    runs_dir = root / "snapshots" / "runs"
    if not runs_dir.exists():
        return None
    candidates = sorted([p for p in runs_dir.iterdir() if (p / "manifest.json").exists()])
    if not candidates:
        return None
    try:
        m = json.loads((candidates[-1] / "manifest.json").read_text(encoding="utf-8"))
        return m.get("decision", {}).get("skill_id") or m.get("skill_id")
    except Exception:
        return None


def _recent_topic_slugs(n: int = 5) -> List[str]:
    """
    Reads snapshots/metrics/learn_history.jsonl (if present) and returns the last n topic slugs.
    Each line is {"ts": <int>, "topic": "<slug or name>"}.
    """
    root = get_project_root()
    hist = root / "snapshots" / "metrics" / "learn_history.jsonl"
    if not hist.exists():
        return []
    try:
        lines = [x for x in hist.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
        recent = []
        for row in lines[-n:]:
            try:
                j = json.loads(row)
                t = j.get("topic")
                if t:
                    recent.append(_slug(t))
            except Exception:
                continue
        return recent
    except Exception:
        return []


def _utility(skill, weights: dict, caps: dict, last: dict | None) -> float:
    # Normalize time/cost pressure into [0,1]
    t_pen = min(1.0, (skill.time_min * 60.0) / max(1.0, caps["time"]))  # utility horizon
    # Cost proxy: average of RAM and CPU pressures (normalized)
    ram_p = min(1.0, skill.ram_mb_est / max(1.0, caps["ram_mb"]))
    cpu_p = min(1.0, skill.cpu_pct_est / max(1.0, caps["cpu_pct"]))
    cost_pen = 0.5 * (ram_p + cpu_p)
    # Risk: subtract modest penalty; if last promotion was weak, damp more
    kpi_last = float((last or {}).get("new_topic_score", 0.8))
    risk_pen = skill.risk_regression * (1.0 + (0.2 if kpi_last < 0.85 else 0.0))
    # Utility (larger is better)
    return (weights["score"] * skill.est_gain
            - weights["time"] * t_pen
            - weights["cost"] * cost_pen
            - 0.15 * risk_pen)


# ---------------- selection ----------------

def select_next(
    preferred_topic: Optional[str] = None,
    exclude: Optional[Iterable[str]] = None
) -> Optional[Plan]:
    """
    Pick the next skill to practice:
      • Honors resource/time budgets.
      • Supports rotation by excluding recently-practiced topics (cooldown).
      • Accepts an explicit 'exclude' list of topic slugs from the caller.
      • If preferred_topic is provided, we constrain to it (and relax exclude if that leaves nothing).

    Returns a Plan or None if nothing is eligible.
    """
    cfg = load_all()
    weights, u_caps = _weights_and_caps(cfg.get("promotion", {}))
    limits_caps = _limits_caps(cfg.get("limits", {}))
    last_promoted = load_last_promoted()
    last_skill_attempted = _latest_run_skill_id()
    cooldown_runs = max(0, int(limits_caps.get("cooldown_runs", 1)))

    # Build an exclude set from caller + recent history (cooldown)
    exclude_slugs: set[str] = set(_slug(x) for x in (exclude or []))
    if cooldown_runs > 0:
        # Pull last N topics from history and add them to the exclusion set
        for s in _recent_topic_slugs(cooldown_runs):
            exclude_slugs.add(_slug(s))

    # Candidate pool within budgets
    caps = {**u_caps, **limits_caps}
    skills = [s for s in list_candidates() if _fits_budget(s, caps)]

    # Optional topic constraint
    if preferred_topic:
        want = _slug(preferred_topic)
        constrained = [s for s in skills if _slug(s.topic) == want]
        # If everything is excluded by rotation, relax exclude for the requested topic
        if not constrained:
            constrained = [s for s in list_candidates() if _slug(s.topic) == want and _fits_budget(s, caps)]
        skills = constrained

    if not skills:
        return None

    # Apply topic-level exclude (rotation) first
    rotated = [s for s in skills if _slug(s.topic) not in exclude_slugs]
    if not rotated and not preferred_topic:
        # If rotation filters out everything, relax the rotation and try again
        rotated = skills[:]

    # Avoid immediate repeat of the very last attempted *skill id* (finer than topic)
    if cooldown_runs >= 1 and last_skill_attempted:
        rotated2 = [s for s in rotated if getattr(s, "id", None) != last_skill_attempted]
        if rotated2:
            rotated = rotated2

    if not rotated:
        return None

    # Maximize utility; tie-break by higher est_gain, then lower time, then lexicographic topic
    def _score_tuple(s):
        return (
            _utility(s, weights, caps, last_promoted),
            s.est_gain,
            -float(s.time_min),
            _slug(s.topic),
        )

    best = max(rotated, key=_score_tuple)

    # Strategy default for the chosen topic
    try:
        strategies = topic_default_strategies(best.topic)
        strategy = strategies[0] if strategies else "ace"
    except Exception:
        strategy = "ace"

    # Reason string mirrors previous CLI messaging, adds a rotation hint
    rotation_note = ""
    if exclude_slugs:
        rotation_note = f"; rotation applied (excluded={sorted(list(exclude_slugs))[:4]}{'…' if len(exclude_slugs) > 4 else ''})"

    return Plan(
        topic=best.topic,
        skill_id=best.id,
        skill_title=best.title,
        strategy=strategy,
        reason=f"max utility under time/CPU/RAM caps (est_gain={best.est_gain:.2f}){rotation_note}",
    )
