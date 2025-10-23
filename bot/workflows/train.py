from __future__ import annotations
from bot.train.peft import TrainConfig, train_adapter

def run(**kwargs) -> int:
    cfg = TrainConfig(**kwargs) if kwargs else TrainConfig()
    res = train_adapter(cfg)
    print(f"[train] adapter -> {res['out_dir']}")
    print(f"[train] decision -> {res['decision']}")
    return 0
