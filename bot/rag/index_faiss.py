from __future__ import annotations
import numpy as np, pathlib, json
from typing import Dict, Any
import faiss

def build_index(X: np.ndarray) -> faiss.Index:
    # X expected normalized; use inner-product (cosine when normalized)
    d = X.shape[1]
    idx = faiss.IndexFlatIP(d)
    idx.add(X)
    return idx

def save_index(idx: faiss.Index, path: pathlib.Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(idx, str(path))

def load_index(path: pathlib.Path) -> faiss.Index:
    return faiss.read_index(str(path))

def search(idx: faiss.Index, q_vecs: np.ndarray, k: int = 5):
    D, I = idx.search(q_vecs, k)
    return D, I

def save_meta(path: pathlib.Path, meta: Dict[str, Any]):
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
