from __future__ import annotations

def _python_chunker(doc_id: str, text: str, chunk_tokens: int, overlap_tokens: int):
    # Use the reference Python implementation you already vendor
    from bot.rag.chunk_spec import chunk_text_equiv
    return chunk_text_equiv(doc_id, text, chunk_tokens, overlap_tokens)

def chunk_text(doc_id: str, text: str, chunk_tokens: int = 400, overlap_tokens: int = 60):
    # Try C++ accelerator (optional)
    try:
        import everabot_chunk_cpp as cpp
        return cpp.chunk_text_equiv(doc_id, text, chunk_tokens, overlap_tokens)
    except Exception:
        pass
    # Try Rust accelerator (optional)
    try:
        import everabot_chunk_rust as r
        return r.chunk_text_equiv(doc_id, text, chunk_tokens, overlap_tokens)
    except Exception:
        pass
    # Try Go accelerator (optional)
    try:
        from bot.accel.go.go_client import chunk_text_equiv as go_chunk
        return go_chunk(doc_id, text, chunk_tokens, overlap_tokens)
    except Exception:
        pass
    # Fallback: Python reference
    return _python_chunker(doc_id, text, chunk_tokens, overlap_tokens)