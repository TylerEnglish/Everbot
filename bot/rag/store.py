from __future__ import annotations
import pathlib, json
import pyarrow as pa, pyarrow.parquet as pq
import numpy as np
from typing import List, Dict, Any
from bot.rag.schemas import Document, Chunk
from bot.rag.utils import write_jsonl, canonicalize_json_rows, sha256_bytes

def _chunks_table(rows: List[Dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(rows)

def _emb_table(vecs: np.ndarray, ids: List[str]) -> pa.Table:
    arr = pa.array(vecs.tolist(), type=pa.list_(pa.float32()))
    return pa.table({"chunk_id": ids, "vector": arr})

def save_store(root: pathlib.Path,
               docs: List[Document],
               chunks: List[Chunk],
               emb: np.ndarray,
               emb_model: str):
    root.mkdir(parents=True, exist_ok=True)

    # docs.jsonl
    docs_rows = [vars(d) for d in docs]
    docs_path = root / "docs.jsonl"
    write_jsonl(docs_path, docs_rows)

    # chunks.parquet
    chunk_rows = [vars(c) for c in chunks]
    pq.write_table(_chunks_table(chunk_rows), root / "chunks.parquet")

    # embeddings.parquet
    pq.write_table(_emb_table(emb, [c.id for c in chunks]), root / "embeddings.parquet")

    # manifest
    canon_docs = canonicalize_json_rows(docs_rows)
    canon_chunks = canonicalize_json_rows(chunk_rows)
    meta = {
        "version": 1,
        "emb_model": emb_model,
        "hash_docs": sha256_bytes(canon_docs),
        "hash_chunks": sha256_bytes(canon_chunks),
        "counts": {"docs": len(docs), "chunks": len(chunks)},
    }
    (root / "manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta
