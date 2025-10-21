from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

@dataclass
class Document:
    id: str
    source: str          # URL or local path
    title: str
    text: str
    sha256: str

@dataclass
class Chunk:
    id: str              # f"{doc_id}:{ord}"
    doc_id: str
    ord: int
    text: str
    n_tokens_est: int
