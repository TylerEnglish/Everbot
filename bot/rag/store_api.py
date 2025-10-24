from __future__ import annotations
import pathlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable, Dict, Type, Optional

ROOT = pathlib.Path(__file__).resolve().parents[2]  # repo root (bot/.. up 2)

@runtime_checkable
class Store(Protocol):
    root: pathlib.Path
    def docs_path(self) -> pathlib.Path: ...
    def chunks_path(self) -> pathlib.Path: ...
    def embeddings_path(self) -> pathlib.Path: ...
    def index_path(self) -> pathlib.Path: ...
    def exists(self) -> bool: ...

@dataclass
class ParquetV1:
    root: pathlib.Path
    def docs_path(self) -> pathlib.Path: return self.root / "docs.jsonl"
    def chunks_path(self) -> pathlib.Path: return self.root / "chunks.parquet"
    def embeddings_path(self) -> pathlib.Path: return self.root / "embeddings.parquet"
    def index_path(self) -> pathlib.Path: return self.root / "index.faiss"
    def exists(self) -> bool:
        return all(p.exists() for p in [
            self.docs_path(), self.chunks_path(), self.embeddings_path(), self.index_path()
        ])

@dataclass
class ParquetV2:
    """
    Candidate “v2” layout. Keep filenames stable for now to force
    equivalence; we can change them later without touching callers.
    """
    root: pathlib.Path
    def docs_path(self) -> pathlib.Path: return self.root / "docs.jsonl"
    def chunks_path(self) -> pathlib.Path: return self.root / "chunks.parquet"
    def embeddings_path(self) -> pathlib.Path: return self.root / "embeddings.parquet"
    def index_path(self) -> pathlib.Path: return self.root / "index.faiss"
    def exists(self) -> bool:
        return all(p.exists() for p in [
            self.docs_path(), self.chunks_path(), self.embeddings_path(), self.index_path()
        ])

# Simple registry so the CLI can resolve by name or path
REGISTRY: Dict[str, Type[Store]] = {
    "v1": ParquetV1,
    "v2": ParquetV2,
}

def open_store(name_or_path: str) -> Store:
    p = pathlib.Path(name_or_path)
    if p.exists():
        # Guess version by directory name; default to v1 semantics
        key = p.name if p.name in REGISTRY else "v1"
        return REGISTRY[key](p)
    if name_or_path in REGISTRY:
        base = ROOT / "snapshots" / "rag" / name_or_path
        return REGISTRY[name_or_path](base)
    raise ValueError(f"Unknown store: {name_or_path}")

