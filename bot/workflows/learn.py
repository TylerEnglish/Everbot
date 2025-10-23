from __future__ import annotations
from bot.core.state import Orchestrator
from bot.telemetry.mlflow_helper import (
    start_run_if_available, log_params_safe, log_from_manifest_if_found
)

def run(topic: str, strategy: str | None = None) -> int:
    with start_run_if_available("learn", topic):
        log_params_safe({"run_type": "learn", "topic": topic})
        orch = Orchestrator(run_type="learn", topic=topic)
        code = orch.run_learn(strategy=strategy)
        log_from_manifest_if_found("learn", topic)
        return code
