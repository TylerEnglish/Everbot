from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

@dataclass
class Store:
    root: Path  # folder with docs.jsonl, chunks.parquet, embeddings.parquet, index.faiss

    def exists(self) -> bool: return (self.root / "docs.jsonl").exists()
    def path(self) -> Path:   return self.root
