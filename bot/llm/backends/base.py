from __future__ import annotations
from typing import Protocol

class Backend(Protocol):
    def ask(self, prompt: str, max_tokens: int, temperature: float) -> str: ...
