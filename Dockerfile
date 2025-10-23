# ---- base ----
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_CREATE=false \
    PATH="/home/botuser/.local/bin:/usr/local/bin:${PATH}"

# OS deps:
# - build-essential, cmake, pkg-config: build llama-cpp-python from source
# - libopenblas-dev: fast CPU via OpenBLAS
# - libgomp1: OpenMP runtime used by BLAS/others
# - plus your existing minimal tools
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates git xdelta3 \
      build-essential cmake pkg-config libopenblas-dev libgomp1 \
  && rm -rf /var/lib/apt/lists/*

# Non-root user & workdir
RUN useradd -ms /bin/bash botuser
WORKDIR /workspace

# Poetry
RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"

# ---------- layer 1: deps only (fast rebuilds) ----------
# Only files needed to resolve dependencies
COPY pyproject.toml poetry.lock* README.md ./

# Install all deps EXCEPT the 'llamacpp' group (we will build it from source with pip)
RUN poetry install --without llamacpp --no-root --no-interaction --no-ansi

# Build and install llama-cpp-python from source (glibc-native) with OpenBLAS
# Notes:
# - CMAKE_ARGS toggles BLAS and native CPU optimizations
# - pin to a recent known-good; adjust as you like
ENV CMAKE_ARGS="-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS -DLLAMA_NATIVE=ON"
RUN pip install --no-cache-dir --no-binary=:all: "llama-cpp-python==0.3.2"

# Sanity check key imports so we fail early if anything is off
RUN python - <<'PY'
import importlib, sys
for mod in ("transformers","llama_cpp"):
    try:
        importlib.import_module(mod)
        print(f"OK: imported {mod}")
    except Exception as e:
        print(f"FAILED: import {mod}: {e}", file=sys.stderr)
        raise
PY

# optional: bitsandbytes only if GPU image is used; skip on slim CPU
# RUN poetry add -E qlora bitsandbytes

# ---------- layer 2: app code ----------
COPY bot/ ./bot/
COPY ruleset/ ./ruleset/
COPY test/ ./test/

# Install just your package (no dependency resolution now)
RUN poetry install --only-root --no-interaction --no-ansi

# Harden & ownership
RUN chmod -R a-w /workspace/ruleset /workspace/test \
 && chown -R botuser:botuser /workspace

USER botuser

# Lightweight healthcheck – meaningful for long-running modes
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -m bot.cli --help >/dev/null 2>&1 || exit 1

# CLI entrypoint; subcommands become args
ENTRYPOINT ["python","-m","bot.cli"]
CMD ["eval"]
