from __future__ import annotations
from bot.core.state import Orchestrator
from bot.telemetry.mlflow_helper import (
    start_run_if_available, log_params_safe, log_from_manifest_if_found
)

def run(goal: str) -> int:
    with start_run_if_available("build", goal):
        log_params_safe({"run_type": "build", "topic": goal})
        orch = Orchestrator(run_type="build", topic=goal)
        code = orch.run_build()
        log_from_manifest_if_found("build", goal)
        return code
