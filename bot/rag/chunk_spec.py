from __future__ import annotations
from typing import List, Tuple

def estimate_tokens(s: str) -> int:
    n = len(s)
    return max(1, (n + 3) // 4)  # ~4 chars/token

def chunk_text_equiv(doc_id: str, text: str, chunk_tokens: int, overlap_tokens: int):
    words = text.split()
    step = max(1, chunk_tokens - overlap_tokens)
    out = []
    i = 0
    ord_ = 0
    while i < len(words):
        end = min(len(words), i + chunk_tokens)
        if i >= end:
            break
        body = " ".join(words[i:end])
        if body:
            out.append((f"{doc_id}:{ord_}", ord_, body, estimate_tokens(body)))
            ord_ += 1
        i += step
    return out
