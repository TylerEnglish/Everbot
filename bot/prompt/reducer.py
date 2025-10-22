from __future__ import annotations
from typing import Optional

def reduce_prompt(text: str, target_tokens: int = 2048) -> str:
    """
    Try LLMLingua compression; otherwise return original text.
    target_tokens: desired token budget (approx; library uses its own estimator).
    """
    try:
        from llmlingua import PromptCompressor
        pc = PromptCompressor()
        res = pc.compress_prompt({"text": text}, instruction="", target_token=target_tokens)
        return res.get("text", text)
    except Exception:
        return text
    