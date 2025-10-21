from __future__ import annotations
from typing import List
import math

def estimate_tokens(s: str) -> int:
    # Simple: ~1 token ≈ 4 chars (rough); deterministic, fast
    return max(1, math.ceil(len(s) / 4))

def chunk_text(doc_id: str, text: str, chunk_tokens: int = 400, overlap_tokens: int = 60):
    words = text.split()
    # map "token" to ~word, stable enough for our caps
    step = max(1, chunk_tokens - overlap_tokens)
    chunks = []
    i = 0
    ord_ = 0
    while i < len(words):
        window = words[i:i+chunk_tokens]
        if not window: break
        body = " ".join(window).strip()
        if body:
            chunks.append((f"{doc_id}:{ord_}", ord_, body, estimate_tokens(body)))
            ord_ += 1
        i += step
    return chunks
