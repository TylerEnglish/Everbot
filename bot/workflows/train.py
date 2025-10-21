from __future__ import annotations
from bot.core.state import Orchestrator

def run() -> int:
    orch = Orchestrator(run_type="train", topic="train")
    return orch.run_train()
