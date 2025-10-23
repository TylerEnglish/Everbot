from __future__ import annotations
from pathlib import Path
from bot.train.peft import TrainConfig, train_adapter
from bot.telemetry.mlflow_helper import (
    start_run_if_available, log_params_safe, log_metrics_safe,
    log_artifact_safe, log_artifacts_safe
)

def run(**kwargs) -> int:
    cfg = TrainConfig(**kwargs) if kwargs else TrainConfig()

    # Extract a few common params if present; fall back to getattr checks
    params = {}
    for k in ("base_model","data_path","num_epochs","lr","lora_r","lora_alpha","lora_dropout","qlora"):
        if hasattr(cfg, k):
            params[k] = getattr(cfg, k)

    run_name_topic = getattr(cfg, "base_model", "train")

    with start_run_if_available("train", str(run_name_topic)):
        log_params_safe({"run_type": "train", **params})

        res = train_adapter(cfg)
        print(f"[train] adapter -> {res.get('out_dir')}")
        print(f"[train] decision -> {res.get('decision')}")

        # optional metrics
        dec = res.get("decision") or {}
        if isinstance(dec, dict):
            log_metrics_safe({k: v for k, v in dec.items() if isinstance(v, (int, float))})

        # optional artifacts
        out_dir = res.get("out_dir")
        if isinstance(out_dir, str) and Path(out_dir).exists():
            # log whole adapter directory
            log_artifacts_safe(Path(out_dir))
            # and a single manifest/config file if one exists
            for fname in ("adapter_config.json","trainer_state.json","manifest.json"):
                p = Path(out_dir) / fname
                if p.exists():
                    log_artifact_safe(p)

    return 0
