from __future__ import annotations
from bot.core.state import Orchestrator

def run(goal: str) -> int:
    orch = Orchestrator(run_type="build", topic=goal)
    return orch.run_build()
