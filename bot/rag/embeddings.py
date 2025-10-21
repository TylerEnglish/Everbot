from __future__ import annotations
import os
import numpy as np
from typing import List, Callable, Tuple

def _l2_normalize(X: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(X, axis=1, keepdims=True) + 1e-12
    return X / n

def _hashing_embedder(dim: int = 384) -> Callable[[List[str]], np.ndarray]:
    import mmh3
    dtype = np.float32
    def _one(texts: List[str]) -> np.ndarray:
        out = np.zeros((len(texts), dim), dtype=dtype)
        for i, t in enumerate(texts):
            if not isinstance(t, str):
                t = str(t)
            vals = []
            for j in range(dim):
                h = mmh3.hash128(t + f"|{j}", signed=False)
                vals.append((h % 1000003) * 1.0)
            out[i] = np.array(vals, dtype=dtype)
        return _l2_normalize(out)
    return _one

def get_embedder(model_hint: str | None = None) -> Tuple[str, Callable[[List[str]], np.ndarray]]:
    """
    Returns (model_id, embed_fn).
    Tries:
      1) FastEmbed (ONNX Runtime)
      2) sentence-transformers (PyTorch)
      3) hashing fallback (always works)
    You can force with EVERABOT_EMBED_BACKEND in {"fastembed","st","hash"}.
    """
    forced = (os.getenv("EVERABOT_EMBED_BACKEND") or "").strip().lower()

    # 1) FastEmbed
    if forced in ("", "fastembed"):
        try:
            from fastembed import TextEmbedding
            model_id = model_hint or "BAAI/bge-small-en-v1.5"
            te = TextEmbedding(model_id)
            def _fastembed(texts: List[str]) -> np.ndarray:
                embs = list(te.query_embed(texts))
                X = np.vstack(embs).astype(np.float32, copy=False)
                return _l2_normalize(X)
            return f"fastembed/{model_id}", _fastembed
        except Exception:
            pass

    # 2) sentence-transformers
    if forced in ("", "st"):
        try:
            from sentence_transformers import SentenceTransformer
            model_id = model_hint or "sentence-transformers/all-MiniLM-L6-v2"
            st = SentenceTransformer(model_id)
            def _st(texts: List[str]) -> np.ndarray:
                X = st.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
                return np.asarray(X, dtype=np.float32)
            return model_id, _st
        except Exception:
            pass

    # 3) hashing fallback
    dim = 384
    hf = _hashing_embedder(dim=dim)
    return f"hashing/{dim}", hf
