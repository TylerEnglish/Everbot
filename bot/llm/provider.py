from __future__ import annotations
import os, shutil, subprocess, json
from bot.rules.loader import load_all

class NotConfigured(Exception): pass

_backend_singleton = None
_backend_info = None

def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def _pick_from_rules() -> tuple[str, dict]:
    try:
        cfg = load_all().get("models", {}) or {}
        default = (cfg.get("default") or {})
        backend = str(default.get("backend") or "llama_cpp")
        return backend, default
    except Exception:
        return "llama_cpp", {}

def _init_backend():
    global _backend_singleton, _backend_info
    if _backend_singleton is not None:
        return

    # Order: env override -> ruleset default -> auto
    choice = (os.getenv("EVERABOT_LLM") or "").strip().lower()
    if not choice:
        choice, default_cfg = _pick_from_rules()
    else:
        _, default_cfg = _pick_from_rules()

    if choice == "llama_cpp":
        # seed env vars from ruleset default if not provided
        m = (default_cfg.get("llama_cpp") or {})
        os.environ.setdefault("EVERABOT_LLAMA_PATH", m.get("model_path", ""))
        if m.get("n_ctx"): os.environ.setdefault("EVERABOT_LLAMA_N_CTX", str(m["n_ctx"]))
        if m.get("n_threads"): os.environ.setdefault("EVERABOT_LLAMA_N_THREADS", str(m["n_threads"]))
        if m.get("n_batch"): os.environ.setdefault("EVERABOT_LLAMA_N_BATCH", str(m["n_batch"]))
        try:
            from bot.llm.backends import llama_cpp_backend as be
            _backend_singleton = be
            _backend_info = be.info()
            return
        except NotConfigured as e:
            raise
        except Exception as e:
            raise NotConfigured(f"llama_cpp backend init failed: {e}")

    if choice == "transformers":
        m = (default_cfg.get("transformers") or {})
        if m.get("model"):
            os.environ.setdefault("EVERABOT_TRANSFORMERS_MODEL", m["model"])
        try:
            from bot.llm.backends import transformers_backend as be
            _backend_singleton = be
            _backend_info = be.info()
            return
        except NotConfigured as e:
            raise
        except Exception as e:
            raise NotConfigured(f"transformers backend init failed: {e}")

    if choice == "ollama":
        # existing local runner (CLI preferred); keep as fallback option
        model = os.getenv("EVERABOT_OLLAMA_MODEL", "llama3")
        if _has("ollama"):
            _backend_singleton = ("ollama_cli", {"model": model})
            _backend_info = {"backend": "ollama", "model": model}
            return
        # Python client fallback
        try:
            from ollama import Client  # noqa: F401
            _backend_singleton = ("ollama_py", {"model": model})
            _backend_info = {"backend": "ollama", "model": model}
            return
        except Exception as e:
            raise NotConfigured(f"Ollama not available: {e}")

    # If we get here, try a best-effort auto choice
    os.environ.setdefault("EVERABOT_LLM", "llama_cpp")
    return _init_backend()

def ask(prompt: str, max_tokens: int = 128, temperature: float = 0.2) -> str:
    _init_backend()
    if hasattr(_backend_singleton, "ask"):
        # llama_cpp / transformers backends are modules with ask()
        return _backend_singleton.ask(prompt, max_tokens=max_tokens, temperature=temperature)

    # Ollama paths preserved (CLI first)
    kind, cfg = _backend_singleton
    model = cfg["model"]
    if kind == "ollama_cli":
        env = os.environ.copy()
        cmd = ["ollama", "run", model, f"{prompt}\n"]
        out = subprocess.run(
            cmd, capture_output=True, text=True, env=env,
            encoding="utf-8", errors="replace"
        )
        if out.returncode != 0:
            raise RuntimeError(out.stderr or "ollama failed")
        return out.stdout.strip()
    elif kind == "ollama_py":
        from ollama import Client
        host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
        client = Client(host=host)
        resp = client.generate(
            model=model, prompt=prompt,
            options={"temperature": float(temperature), "num_predict": int(max_tokens)},
        )
        return (resp.get("response") or "").strip()

    raise NotConfigured("No local LLM backend configured")

def info() -> dict:
    _init_backend()
    if hasattr(_backend_singleton, "info"):
        return _backend_singleton.info()
    return _backend_info or {"backend": "unknown", "model": "unknown"}
