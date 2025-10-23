from __future__ import annotations
import os
import json, urllib.request

BASE = "http://127.0.0.1:8765"  # override with EVERABOT_GO_CHUNK_URL

def chunk_text_equiv(doc_id: str, text: str, chunk_tokens: int, overlap_tokens: int):
    url = (os.environ.get("EVERABOT_GO_CHUNK_URL") or BASE).rstrip("/") + "/chunk"
    payload = {
        "doc_id": doc_id, "text": text,
        "chunk_tokens": int(chunk_tokens),
        "overlap_tokens": int(overlap_tokens),
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        arr = json.loads(resp.read().decode("utf-8"))
        # Return the same tuple shape used elsewhere
        return [(x["chunk_id"], x["ord"], x["body"], x["tokens"]) for x in arr]
