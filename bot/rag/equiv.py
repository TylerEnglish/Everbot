from __future__ import annotations
from pathlib import Path
from typing import Iterable, Tuple, Dict, Any, List
import hashlib

from bot.rag.utils import read_jsonl, canonicalize_json_rows

def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()

def _docs_canon_hash(store_dir: Path) -> str:
    """Canonical SHA-256 of docs.jsonl under a store."""
    docs = list(read_jsonl(store_dir / "docs.jsonl"))
    canon = canonicalize_json_rows(docs)
    return _sha256_bytes(canon)

def _chunks_canon_hash(store_dir: Path) -> str:
    """
    Canonical SHA-256 of chunks, derived from chunks.parquet contents.
    We read Parquet → rows (stable column order) → canonicalize → sha256.
    """
    import pyarrow.parquet as pq
    import pyarrow as pa

    pq_path = store_dir / "chunks.parquet"
    if not pq_path.exists():
        # If chunks missing, treat as empty (hash of empty canon set)
        empty = canonicalize_json_rows([])
        return _sha256_bytes(empty)

    tab = pq.read_table(pq_path)
    rows: List[Dict[str, Any]] = []
    cols = list(tab.column_names)  # keep existing order for parity
    for i in range(tab.num_rows):
        rows.append({c: tab[c][i].as_py() for c in cols})
    canon = canonicalize_json_rows(rows)
    return _sha256_bytes(canon)

def bit_for_bit_equivalent(
    a_dir: Path,
    b_dir: Path,
    require_chunk_bits: bool = False,
) -> Tuple[bool, Dict[str, Dict[str, str]]]:
    """
    Returns (ok, detail) where ok is True iff:
      - docs canonical hashes match for A and B, and
      - if require_chunk_bits=True, chunks canonical hashes also match.

    detail = {
      "a": {"docs": "<sha>", "chunks": "<sha_optional>"},
      "b": {"docs": "<sha>", "chunks": "<sha_optional>"}
    }
    """
    a_docs = _docs_canon_hash(a_dir)
    b_docs = _docs_canon_hash(b_dir)

    detail: Dict[str, Dict[str, str]] = {
        "a": {"docs": a_docs},
        "b": {"docs": b_docs},
    }

    ok_docs = (a_docs == b_docs)

    ok_chunks = True
    if require_chunk_bits:
        a_chunks = _chunks_canon_hash(a_dir)
        b_chunks = _chunks_canon_hash(b_dir)
        detail["a"]["chunks"] = a_chunks
        detail["b"]["chunks"] = b_chunks
        ok_chunks = (a_chunks == b_chunks)

    return (ok_docs and ok_chunks), detail

def topk_equivalent(
    a_dir: Path,
    b_dir: Path,
    queries: Iterable[str],
    k: int = 5,
):
    """
    Compare top-k chunk IDs across two stores for a fixed query set.
    Implementation mirrors the vector path in the CLI's `rag_query` fallback:
      - load embeddings.parquet (chunk_id column)
      - load FAISS index
      - embed queries
      - collect top-k IDs
    Returns (ok, detail) with detail containing 'queries', 'topk_a', 'topk_b'.
    """
    import pyarrow.parquet as pq
    from bot.rag.embeddings import get_embedder
    from bot.rag.index_faiss import load_index

    # A
    tab_a = pq.read_table(a_dir / "embeddings.parquet")
    ids_a = [x.as_py() for x in tab_a["chunk_id"]]
    idx_a = load_index(a_dir / "index.faiss")

    # B
    tab_b = pq.read_table(b_dir / "embeddings.parquet")
    ids_b = [x.as_py() for x in tab_b["chunk_id"]]
    idx_b = load_index(b_dir / "index.faiss")

    model, embed = get_embedder()
    q_list = list(queries)
    q_vecs = embed(q_list)

    topk_a: List[List[str]] = []
    topk_b: List[List[str]] = []

    for qv in q_vecs:
        # Ensure 2D shape for faiss
        import numpy as np
        qv2 = np.expand_dims(qv, axis=0)
        Da, Ia = idx_a.search(qv2, k)
        Db, Ib = idx_b.search(qv2, k)
        topk_a.append([ids_a[j] for j in Ia[0]])
        topk_b.append([ids_b[j] for j in Ib[0]])

    ok = (topk_a == topk_b)
    detail = {
        "queries": q_list,
        "topk_a": topk_a,
        "topk_b": topk_b,
    }
    return ok, detail