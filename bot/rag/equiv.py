from __future__ import annotations
import pathlib, json, numpy as np, pyarrow.parquet as pq
from typing import List, Tuple, Dict, Any
from bot.rag.utils import read_jsonl, canonicalize_json_rows, sha256_bytes
from bot.rag.embeddings import get_embedder
from bot.rag.index_faiss import load_index, search
import faiss

def _canonical_hash_docs(root: pathlib.Path) -> str:
    rows = list(read_jsonl(root / "docs.jsonl"))
    return sha256_bytes(canonicalize_json_rows(rows))

def _canonical_hash_chunks(root: pathlib.Path) -> str:
    import pyarrow.parquet as pq
    tab = pq.read_table(root / "chunks.parquet")
    rows = [dict(zip(tab.column_names, [col[i].as_py() for col in tab.columns]))
            for i in range(tab.num_rows)]
    return sha256_bytes(canonicalize_json_rows(rows))

def bit_for_bit_equivalent(a: pathlib.Path, b: pathlib.Path) -> Tuple[bool, Dict[str, Any]]:
    ha = {"docs": _canonical_hash_docs(a), "chunks": _canonical_hash_chunks(a)}
    hb = {"docs": _canonical_hash_docs(b), "chunks": _canonical_hash_chunks(b)}
    ok = (ha == hb)
    return ok, {"a": ha, "b": hb}

def topk_equivalent(a: pathlib.Path, b: pathlib.Path,
                    queries: List[str], k: int = 5) -> Tuple[bool, Dict[str, Any]]:
    # Load embeddings order to map FAISS ids -> chunk_ids
    ta, tb = pq.read_table(a / "embeddings.parquet"), pq.read_table(b / "embeddings.parquet")
    ids_a = [x.as_py() for x in ta["chunk_id"]]
    ids_b = [x.as_py() for x in tb["chunk_id"]]
    # Load FAISS indices
    ia, ib = faiss.read_index(str(a / "index.faiss")), faiss.read_index(str(b / "index.faiss"))

    model_name, embed = get_embedder()
    qv = embed(queries)

    Da, Ia = ia.search(qv, k); Db, Ib = ib.search(qv, k)
    tops_a = [[ids_a[j] for j in row] for row in Ia]
    tops_b = [[ids_b[j] for j in row] for row in Ib]
    ok = (tops_a == tops_b)
    detail = {"queries": queries, "topk_a": tops_a, "topk_b": tops_b}
    return ok, detail
