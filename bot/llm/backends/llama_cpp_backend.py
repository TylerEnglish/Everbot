from __future__ import annotations
import os
from typing import Optional

from bot.llm.provider import NotConfigured  # reuse exception class

_LLAMA = None
_MODEL_PATH = None

def _int(env: str, default: int) -> int:
    try:
        return int(os.getenv(env, default))
    except Exception:
        return default

def _ensure_llama() -> None:
    global _LLAMA, _MODEL_PATH
    if _LLAMA is not None:
        return
    try:
        from llama_cpp import Llama  # Python binding for llama.cpp
    except Exception as e:
        raise NotConfigured(f"llama-cpp-python missing: {e}")
    model_path = os.getenv("EVERABOT_LLAMA_PATH")  # must point to .gguf
    if not model_path or not os.path.exists(model_path):
        # fall back to models.yaml default (provider will set) or tell the user
        raise NotConfigured("Set EVERABOT_LLAMA_PATH to a local .gguf model file.")
    n_ctx = _int("EVERABOT_LLAMA_N_CTX", 2048)
    n_threads = _int("EVERABOT_LLAMA_N_THREADS", max(1, os.cpu_count() or 2))
    n_batch = _int("EVERABOT_LLAMA_N_BATCH", 128)
    try:
        _LLAMA = Llama(
            model_path=model_path, n_ctx=n_ctx, n_threads=n_threads,
            n_batch=n_batch, logits_all=False, verbose=False
        )
        _MODEL_PATH = model_path
    except Exception as e:
        raise NotConfigured(f"Failed to load GGUF at {model_path}: {e}")

def ask(prompt: str, max_tokens: int = 128, temperature: float = 0.2) -> str:
    _ensure_llama()
    # Completion API — documented in the project’s usage examples
    # (create_completion returns {choices:[{text: ...}]})
    out = _LLAMA.create_completion(
        prompt=prompt, max_tokens=max_tokens,
        temperature=float(temperature), top_p=0.95
    )
    return (out.get("choices") or [{}])[0].get("text", "").strip()

def info() -> dict:
    _ensure_llama()
    return {"backend": "llama_cpp", "model": _MODEL_PATH}
