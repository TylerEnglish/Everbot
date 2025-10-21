from __future__ import annotations
from bot.core.state import Orchestrator

def run(topic: str) -> int:
    orch = Orchestrator(run_type="learn", topic=topic)
    return orch.run_learn()
