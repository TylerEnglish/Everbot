from __future__ import annotations
from typing import List
import math

def estimate_tokens(s: str) -> int:
    # Simple: ~1 token ≈ 4 chars (rough); deterministic, fast
    return max(1, math.ceil(len(s) / 4))

def chunk_text(doc_id: str, text: str, chunk_tokens: int = 400, overlap_tokens: int = 60):
    from bot.accel.gate import selected_accel
    accel = selected_accel()
    if accel == "cpp":
        try:
            import everabot_chunk_cpp as m
            return m.chunk_text_equiv(doc_id, text, chunk_tokens, overlap_tokens)
        except Exception:
            pass
    elif accel == "rust":
        try:
            import everabot_chunk_rust as m
            return m.chunk_text_equiv(doc_id, text, chunk_tokens, overlap_tokens)
        except Exception:
            pass
    elif accel == "go":
        try:
            from bot.accel.go_client import chunk_text_equiv as go_chunk
            return go_chunk(doc_id, text, chunk_tokens, overlap_tokens)
        except Exception:
            pass
    # Fallback to the existing Python implementation you already had:
    return _your_existing_python_chunker(doc_id, text, chunk_tokens, overlap_tokens)


try:
    from .chunk_spec import chunk_text_equiv as chunk_text_equiv
except Exception:
    pass