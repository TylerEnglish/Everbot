from __future__ import annotations
import os, json, time, pathlib, dataclasses
from typing import Optional, Dict, Any

import torch
from datasets import load_dataset
from transformers import (AutoTokenizer, AutoModelForCausalLM, Trainer, TrainingArguments,
                          DataCollatorForLanguageModeling)
from peft import LoraConfig, get_peft_model

from bot.paths import get_project_root
from bot.eval.harness import run_all
from bot.rules.loader import load_all, load_last_promoted, pareto_gate, save_last_promoted
from bot.rag.utils import now_iso

@dataclasses.dataclass
class TrainConfig:
    base_model: str = "distilgpt2"          # CPU-friendly default
    data_path: str = "examples/sft_tiny.jsonl"
    output_dir: Optional[str] = None        # resolved under snapshots/models/adapters/
    num_epochs: int = 1
    lr: float = 2e-4
    train_batch_size: int = 4
    grad_accum_steps: int = 2
    lora_r: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    qlora: bool = False                     # enabled only when bitsandbytes + CUDA available

def _format_example(ex: Dict[str, Any]) -> str:
    p = ex.get("prompt"); r = ex.get("response")
    if p is not None and r is not None:
        return f"### Instruction:\n{p}\n\n### Response:\n{r}\n"
    t = ex.get("text")
    return f"{t}\n" if t else ""

def _load_jsonl_dataset(path: str):
    ds = load_dataset("json", data_files=path)["train"]
    return ds

def _maybe_4bit_kwargs(enable_qlora: bool) -> Dict[str, Any]:
    if not enable_qlora:
        return {}
    try:
        import bitsandbytes as bnb  # noqa: F401
    except Exception:
        return {}
    if not torch.cuda.is_available():
        return {}
    return dict(
        load_in_4bit=True,
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float16,
    )

def train_adapter(cfg: TrainConfig) -> Dict[str, Any]:
    root = get_project_root()
    out_root = root / "snapshots" / "models" / "adapters"
    out_root.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d_%H%M%S")
    out_dir = pathlib.Path(cfg.output_dir) if cfg.output_dir else (out_root / f"{run_id}_{('qlora' if cfg.qlora else 'lora')}_{cfg.base_model.replace('/','_')}")
    out_dir.mkdir(parents=True, exist_ok=True)

    tok = AutoTokenizer.from_pretrained(cfg.base_model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(cfg.base_model, **_maybe_4bit_kwargs(cfg.qlora))
    model.resize_token_embeddings(len(tok))

    peft_cfg = LoraConfig(
        r=cfg.lora_r, lora_alpha=cfg.lora_alpha, lora_dropout=cfg.lora_dropout,
        target_modules=["c_attn","q_proj","k_proj","v_proj","o_proj"],  # robust defaults; ignored where absent
        task_type="CAUSAL_LM"
    )
    model = get_peft_model(model, peft_cfg)

    ds = _load_jsonl_dataset(cfg.data_path)
    def _tok(batch):
        texts = [_format_example(ex) for ex in batch]
        toks = tok(texts, truncation=True, padding="max_length", max_length=512)
        toks["labels"] = toks["input_ids"].copy()
        return toks
    ds = ds.map(lambda ex: _tok([ex]), batched=True, remove_columns=list(ds.features))

    args = TrainingArguments(
        output_dir=str(out_dir / "hf_out"),
        per_device_train_batch_size=cfg.train_batch_size,
        gradient_accumulation_steps=cfg.grad_accum_steps,
        learning_rate=cfg.lr,
        num_train_epochs=cfg.num_epochs,
        bf16=torch.cuda.is_available(),
        fp16=(torch.cuda.is_available() and not torch.cuda.is_bf16_supported()),
        logging_steps=10,
        save_strategy="no",
        report_to=[],
    )
    collator = DataCollatorForLanguageModeling(tok, mlm=False)
    tr = Trainer(model=model, args=args, train_dataset=ds, data_collator=collator)
    tr.train()

    # save adapter (small)
    model.save_pretrained(str(out_dir))
    tok.save_pretrained(str(out_dir))

    # run root evals and apply Pareto gate
    eval_payload = run_all()
    root_ok = (eval_payload["overall"] == "PASS")
    new_topic_score = 0.80  # demo KPI; wire your topic scorer here

    rules = load_all()
    last = load_last_promoted()
    promote, reason = pareto_gate(new_topic_score, root_ok, rules["promotion"], last)
    decision = {"promote": promote, "reason": reason, "new_topic_score": new_topic_score,
                "root_ok": root_ok, "eval_artifact": eval_payload["artifact"]}

    if promote:
        save_last_promoted({"ts": now_iso(), "new_topic_score": new_topic_score,
                            "adapter_path": str(out_dir), "base_model": cfg.base_model})

    (out_dir / "TRAIN_DECISION.json").write_text(json.dumps(decision, indent=2), encoding="utf-8")
    return {"out_dir": str(out_dir), "decision": decision}
