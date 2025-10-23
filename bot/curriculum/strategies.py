from __future__ import annotations
from dataclasses import dataclass
from typing import Dict

@dataclass
class RetrievalParams:
    use_hybrid: bool = True
    mmr: bool = True
    k: int = 12
    chunk_tokens: int = 400
    overlap_tokens: int = 60

_STRATEGIES: Dict[str, RetrievalParams] = {
    "vanilla":     RetrievalParams(use_hybrid=True,  mmr=True,  k=10, chunk_tokens=400, overlap_tokens=60),
    "ace":         RetrievalParams(use_hybrid=True,  mmr=True,  k=12, chunk_tokens=400, overlap_tokens=60),
    # Long context: larger k and wider chunks to mimic long-context retrieval patterns (cf. LongRAG)
    "long_context":RetrievalParams(use_hybrid=True,  mmr=True,  k=20, chunk_tokens=800, overlap_tokens=80),
}

def get_params(name: str) -> RetrievalParams:
    return _STRATEGIES.get(name, _STRATEGIES["vanilla"])
