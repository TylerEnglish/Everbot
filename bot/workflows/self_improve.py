from __future__ import annotations
from bot.core.state import Orchestrator

def run() -> int:
    orch = Orchestrator(run_type="self-improve", topic="self-improve")
    return orch.run_self_improve()
