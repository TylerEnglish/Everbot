from __future__ import annotations
import os
from typing import Optional

from bot.llm.provider import NotConfigured  # reuse your exception

_PIPELINE = None
_MODEL_ID = None

def _ensure_pipeline() -> None:
    global _PIPELINE, _MODEL_ID
    if _PIPELINE is not None:
        return
    try:
        from transformers import pipeline  # HF pipeline API
    except Exception as e:
        raise NotConfigured(f"transformers missing: {e}")
    # Model: prefer env, else reasonable tiny CPU default
    model_id = os.getenv("EVERABOT_TRANSFORMERS_MODEL", "distilgpt2")
    try:
        _PIPELINE = pipeline("text-generation", model=model_id, device_map="auto")
        _MODEL_ID = model_id
    except Exception as e:
        raise NotConfigured(f"Transformers backend failed to load {model_id}: {e}")

def ask(prompt: str, max_tokens: int = 128, temperature: float = 0.2) -> str:
    _ensure_pipeline()
    # HF pipelines return list[dict]; we keep it simple and mirror docs usage
    out = _PIPELINE(prompt, max_new_tokens=max_tokens, do_sample=(temperature > 0),
                    temperature=temperature)
    text = out[0].get("generated_text", "")
    # strip the prompt prefix if present
    return text[len(prompt):].strip() if text.startswith(prompt) else text.strip()

def info() -> dict:
    _ensure_pipeline()
    return {"backend": "transformers", "model": _MODEL_ID}
