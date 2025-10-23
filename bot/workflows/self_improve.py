from __future__ import annotations
from bot.core.state import Orchestrator
from bot.telemetry.mlflow_helper import (
    start_run_if_available, log_params_safe, log_from_manifest_if_found
)

def run() -> int:
    topic = "self-improve"
    with start_run_if_available("self-improve", topic):
        log_params_safe({"run_type": "self-improve", "topic": topic})
        orch = Orchestrator(run_type="self-improve", topic=topic)
        code = orch.run_self_improve()
        log_from_manifest_if_found("self-improve", topic)
        return code
