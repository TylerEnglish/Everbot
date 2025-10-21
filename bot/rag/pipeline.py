from __future__ import annotations
import pathlib, json
from typing import List, Tuple
from bot.rag.fetchers import fetch
from bot.rag.chunk import chunk_text
from bot.rag.embeddings import get_embedder
from bot.rag.schemas import Document, Chunk
from bot.rag.utils import sha256_str
from bot.rag.store import save_store
from bot.rag.index_faiss import build_index, save_index

def ingest(urls: List[str], out_dir: pathlib.Path) -> dict:
    docs: List[Document] = []
    chunks: List[Chunk] = []
    for u in urls:
        title, text = fetch(u)
        did = sha256_str(u)
        doc = Document(id=did, source=u, title=title, text=text, sha256=sha256_str(text))
        docs.append(doc)

        for (cid, ord_, body, ntok) in chunk_text(did, text, chunk_tokens=400, overlap_tokens=60):
            chunks.append(Chunk(id=cid, doc_id=did, ord=ord_, text=body, n_tokens_est=ntok))

    model_name, embed = get_embedder()
    emb = embed([c.text for c in chunks])  # np.ndarray [N, D]

    meta = save_store(out_dir, docs, chunks, emb, model_name)
    idx = build_index(emb)
    save_index(idx, out_dir / "index.faiss")

    (out_dir / "CURRENT.json").write_text(json.dumps({
        "ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        "count_docs": len(docs), "count_chunks": len(chunks),
        "emb_model": model_name
    }, indent=2), encoding="utf-8")
    return meta
