from typing import List, Tuple
from pathlib import Path
import math
import numpy as np
import pyarrow.parquet as pq
from rank_bm25 import BM25Okapi
from bot.rag.embeddings import get_embedder
from bot.rag.index_faiss import load_index

def _normalize(mat: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(mat, axis=1, keepdims=True)
    return mat / np.clip(norm, 1e-12, None)

def _mmr(query_vec: np.ndarray, cand_vecs: np.ndarray, ids: List[str], k: int, lam: float = 0.6):
    # Maximal Marginal Relevance on normalized vectors
    q = _normalize(query_vec.reshape(1, -1))
    X = _normalize(cand_vecs)
    chosen, remaining = [], list(range(len(ids)))
    while remaining and len(chosen) < k:
        if chosen:
            diversity = np.max(X[remaining] @ X[chosen].T, axis=1)
        else:
            diversity = np.zeros(len(remaining))
        relevance = (X[remaining] @ q.T).ravel()
        score = lam * relevance - (1 - lam) * diversity
        pick_rel_idx = remaining[int(np.argmax(score))]
        chosen.append(pick_rel_idx)
        remaining.remove(pick_rel_idx)
    return [(ids[i], float((_normalize(X[i:i+1]) @ q.T).ravel()[0])) for i in chosen]

def hybrid_topk(store_dir: Path, query: str, k: int = 10, mmr: bool = True) -> List[Tuple[str, float]]:
    # BM25 over chunk texts
    chunks_tab = pq.read_table(store_dir / "chunks.parquet")
    texts = [chunks_tab["text"][i].as_py() for i in range(chunks_tab.num_rows)]
    ids = [chunks_tab["id"][i].as_py() for i in range(chunks_tab.num_rows)]
    bm25 = BM25Okapi([t.split() for t in texts])  # BM25Okapi API docs: rank-bm25. :contentReference[oaicite:1]{index=1}
    bm_scores = dict(zip(ids, bm25.get_scores(query.split())))

    # vector search (FAISS)
    emb_tab = pq.read_table(store_dir / "embeddings.parquet")
    emb_ids = [x.as_py() for x in emb_tab["chunk_id"]]
    embs = np.stack([np.array(emb_tab["vector"][i].as_py(), dtype=np.float32) for i in range(emb_tab.num_rows)])
    _, embed = get_embedder()
    qv = embed([query]).astype(np.float32)
    idx = load_index(store_dir / "index.faiss")
    topn = min(k * 4, len(emb_ids))
    D, I = idx.search(qv, topn)
    vec_ids = [emb_ids[j] for j in I[0]]

    # Reciprocal Rank Fusion
    def rrf(rank): return 1.0 / (60 + rank)  # standard RRF constant
    fused = {}
    for r, cid in enumerate(vec_ids, start=1):
        fused[cid] = fused.get(cid, 0.0) + rrf(r)
    for r, (cid, _) in enumerate(sorted(bm_scores.items(), key=lambda x: x[1], reverse=True)[:topn], start=1):
        fused[cid] = fused.get(cid, 0.0) + rrf(r)

    cand_ids = [cid for cid, _ in sorted(fused.items(), key=lambda x: x[1], reverse=True)[:topn]]
    if mmr:
        # map candidates to vectors for MMR
        pos = {cid: i for i, cid in enumerate(emb_ids)}
        cand_vecs = embs[[pos[cid] for cid in cand_ids]]
        return _mmr(qv[0], cand_vecs, cand_ids, k=k)
    else:
        return [(cid, float(fused[cid])) for cid in cand_ids[:k]]