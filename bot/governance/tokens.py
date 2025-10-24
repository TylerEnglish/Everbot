from __future__ import annotations
import threading

class TokenBudget:
    """Thread-safe token budget for a single run."""
    def __init__(self, max_tokens: int):
        self.max = int(max_tokens or 0)
        self.used = 0
        self._lock = threading.Lock()

    @staticmethod
    def _rough_count(text: str) -> int:
        # OpenAI guidance: ~4 characters ≈ 1 token (rough) → keep conservative. 
        # https://help.openai.com/en/articles/7934971 (search: “~4 characters per token”)
        return max(1, len(text) // 4)

    def will_fit(self, prompt: str, max_answer_tokens: int) -> tuple[bool, int]:
        if self.max <= 0:
            return (True, max_answer_tokens)
        with self._lock:
            prompt_tokens = self._rough_count(prompt)
            remaining = self.max - self.used - prompt_tokens
            if remaining <= 0:
                return (False, 0)
            return (True, max(0, min(remaining, int(max_answer_tokens))))

    def consume(self, prompt: str, answer_text: str = "") -> int:
        if self.max <= 0:
            return 0
        with self._lock:
            t = self._rough_count(prompt) + (self._rough_count(answer_text) if answer_text else 0)
            self.used += t
            return t
