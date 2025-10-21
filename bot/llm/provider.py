from __future__ import annotations
import os, shutil, subprocess

class NotConfigured(Exception): pass

def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def ask(prompt: str, max_tokens: int = 128, temperature: float = 0.2) -> str:
    """
    Pluggable local inference.
    - Preferred: OLLAMA CLI (EVERABOT_LLM=ollama, EVERABOT_OLLAMA_MODEL).
    - Fallback: Python client 'ollama' if CLI missing but daemon is running.
    """
    backend = os.getenv("EVERABOT_LLM", "").strip().lower()
    model = os.getenv("EVERABOT_OLLAMA_MODEL", "llama3")

    if backend == "ollama":
        if _has("ollama"):
            # CLI path — force UTF-8 and be robust to odd bytes on Windows.
            env = os.environ.copy()
            cmd = ["ollama", "run", model, f"{prompt}\n"]
            out = subprocess.run(
                cmd, capture_output=True, text=True, env=env,
                encoding="utf-8", errors="replace"  # <— key fix for Windows cp1252 locales
            )
            if out.returncode != 0:
                raise RuntimeError(out.stderr or "ollama failed")
            return out.stdout.strip()

        # Fallback: Python client talking to the local daemon
        try:
            from ollama import Client
            host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
            client = Client(host=host)
            resp = client.generate(
                model=model,
                prompt=prompt,
                options={"temperature": float(temperature), "num_predict": int(max_tokens)},
            )
            return (resp.get("response") or "").strip()
        except Exception as e:
            raise NotConfigured(f"Ollama backend not available: {e}")

    raise NotConfigured("No local LLM backend configured")
