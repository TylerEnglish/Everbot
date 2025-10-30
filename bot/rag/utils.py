from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, time
from pathlib import Path
from typing import Iterable, Dict, Any, Optional, List, Tuple

# ---------------------------
# Legacy helpers (kept so other modules don't break)
# ---------------------------
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def sha256_str(s: str) -> str:
    return sha256_bytes(s.encode("utf-8", "replace"))

def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

def read_jsonl(path: Path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            yield json.loads(line)

def canonicalize_json_rows(rows: Iterable[Dict[str, Any]]) -> bytes:
    # Stable JSONL with sorted keys
    buf = []
    for r in rows:
        buf.append(json.dumps(r, ensure_ascii=False, sort_keys=True))
    buf.sort()
    return ("\n".join(buf) + "\n").encode("utf-8")
