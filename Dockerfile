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
COPY pyproject.toml README.md Makefile ./
COPY bot/ ./bot/
COPY ruleset/ ./ruleset/
COPY test/ ./test/
COPY app/ ./app/
COPY snapshots/ ./snapshots/

# install python deps
RUN poetry config virtualenvs.create false && poetry install --no-interaction --no-ansi

# extra safety: make ruleset/ and test/ read-only inside the image too
RUN chmod -R a-w /workspace/ruleset /workspace/test

# switch to non-root
USER botuser

# simple healthcheck: CLI should respond
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -m bot.cli --help || exit 1

ENTRYPOINT ["everabot"]
CMD ["eval"]
