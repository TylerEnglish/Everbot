FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# system basics
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential ca-certificates git && \
    rm -rf /var/lib/apt/lists/*

# non-root runtime user
RUN useradd -ms /bin/bash botuser
WORKDIR /workspace

# poetry (install lightweight)
RUN pip install --no-cache-dir poetry==1.8.3

# copy project files
COPY pyproject.toml README.md ./
COPY bot/ ./bot/
COPY ruleset/ ./ruleset/
COPY test/ ./test/
COPY app/ ./app/
COPY snapshots/ ./snapshots/

# install python deps (system site, no venv)
RUN poetry config virtualenvs.create false && poetry install --no-interaction --no-ansi

# harden: make ruleset/ and test/ read-only inside the image too
RUN chmod -R a-w /workspace/ruleset /workspace/test

# ensure the app user owns ONLY /workspace (writable area)
RUN chown -R botuser:botuser /workspace

# wrapper block
RUN printf '%s\n' '#!/bin/sh' 'set -e' \
  '[ "${EVERABOT_STARTUP_CHECK:-0}" = "1" ] && everabot selfcheck || true' \
  'exec "$@"' > /usr/local/bin/entrypoint && chmod +x /usr/local/bin/entrypoint

USER botuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -m bot.cli --help || exit 1

ENTRYPOINT ["entrypoint","everabot"]
CMD ["eval"]


