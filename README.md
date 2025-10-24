# Everabot (Milestone A — Boot & Protect)

This seed gives you:
- Non-root Docker runtime and read-only mounts for `ruleset/` & `test/`.
- CLI with `eval`, `selfcheck`, `learn`, `self_improve`, `snapshot` stubs.
- FS guard that denies writes to protected dirs at runtime.

## Quickstart (host)

```bash
# from repo root
poetry install
poetry run everabot selfcheck
poetry run everabot eval
```

# Docker
```bash
docker build -t everabot:dev .
docker run --rm -it `
  -v "${PWD}\ruleset:/workspace/ruleset:ro" `
  -v "${PWD}\test:/workspace/test:ro" `
  -v "${PWD}\snapshots:/workspace/snapshots" `
  everabot:dev eval

```

`docker-run` binds:

* `ruleset/:ro` and `test/:ro`  (read-only)
* `snapshots/:rw`              (writable)

## Notes

* Use Parquet for columnar caches (docs/embeddings/metrics). ([Apache Parquet][2])
* Add OpenTelemetry spans when wiring the learning loop (Python SDK). ([OpenTelemetry][3])
* Profile hotspots with `py-spy` when you turn on `self_improve`. ([GitHub][4])
* For CPU-friendly local inference later, add a `llama.cpp` backend (GGUF). ([GitHub][5])


File structure
```bash
Everbot
|-docs/
|   |-accel.sh
|   |-code.md
|   |-docker.sh
|   |-Everabot_A Self-Evoling AI Agent Architecture (White Paper Outline).pdf
|   |-model.sh
|   |-setup.md
|   |-sources.md
|   |-task.md
|   |-xdelta3.sh
|-.devcontainer/
|   |-devcontainer.json
|-app/
|   |-static/
|   |   |-index.html
|   |-server.py
|-bin/
|   |-precommit_block_protected.py
|-examples/
|   |-sft_tiny.jsonl
|-ruleset/
|   |-schemas/
|   |   |-rag_chuncks.avsc
|   |   |-rag_docs.avsc
|   |-limits.yaml
|   |-models.yaml
|   |-promotion.yaml
|   |-safety.yaml
|   |-seed_urls.yaml
|   |-tools.yaml
|   |-topics.yaml
|-snapshots/ (database)
|-test/
|   |-bench_gsm8k/raw/ ...
|   |-bench_hellaswag/raw/ ...
|   |-bench_mmlu/raw/data/ ...
|   |-eval/
|   |   |-config.yaml
|   |   |-turing_fixtures.yaml
|   |-sanity/
|   |   |-cases.yaml
|   |-run_root_tests.py
|   |-test_ace_delta.py
|-bot/
|   |-accel/
|   |   |-cpp/
|   |   |   |-src/
|   |   |   |   |-chunk_ext.cpp
|   |   |   |-CMakeLists.txt
|   |   |   |-pyproject.toml
|   |   |   |-README.md
|   |   |-go/
|   |   |   |-bin/
|   |   |   |   |-everabot_chunk_go.exe
|   |   |   |-chunker/
|   |   |   |   |-chunk.go
|   |   |   |-cmd/chunker/
|   |   |   |   |-main.go
|   |   |   |-go_client.py
|   |   |   |-go.mod
|   |   |-rust/
|   |   |   |-src/
|   |   |   |   |-lib.rs
|   |   |   |-target/ ... (whats built)
|   |   |   |-Cargo.lock
|   |   |   |-Cargo.toml
|   |   |   |-pyproject.toml
|   |   |-gate.py
|   |-context/
|   |   |-ace_delta.py
|   |-core/
|   |   |-phases.py
|   |   |-state.py
|   |   |-task_graph.py
|   |-curriculum/
|   |   |-priority.py
|   |   |-registry.py
|   |   |-strategies.py
|   |-distill/
|   |   |-bullets.py
|   |-eval/
|   |   |-harness.py
|   |   |-sanity.py
|   |-governance/
|   |   |-budget.py
|   |   |-tokens.py
|   |-llm/
|   |   |-backends/
|   |   |   |-base.py
|   |   |   |-llama_cpp_backend.py
|   |   |   |-transformers_backend.py
|   |   |-provider.py
|   |-memory/
|   |   |-reflection.py
|   |-profiling/
|   |   |-pyspy_harness.py
|   |-prompt/
|   |   |-reducer.py
|   |-rag/
|   |   |-chunk_spec.py
|   |   |-chunk.py
|   |   |-embeddings.py
|   |   |-equiv.py
|   |   |-fetchers.py
|   |   |-hybrid.py
|   |   |-index_faiss.py
|   |   |-normalize.py
|   |   |-pipeline.py
|   |   |-schemas.py
|   |   |-store_api.py
|   |   |-store.py
|   |   |-transcripts.py
|   |   |-utils.py
|   |-rules/
|   |   |-loader.py
|   |-security/
|   |   |-__init__.py
|   |   |-guard.py
|   |-selfopt/
|   |   |-ab.py
|   |-snapshots/
|   |   |-delta.py
|   |   |-diff.py
|   |-telemetry/
|   |   |-mlflow_helper.py
|   |   |-tracing.py
|   |-train/
|   |   |-peft.py
|   |-workflows/
|   |   |-__init__.py
|   |   |-build.py
|   |   |-learn.py
|   |   |-self_improve.py
|   |   |-train.py
|   |-__init__.py
|   |-cli.py
|   |-paths.py
|-.pre-commit-config.yaml
|-Dockerfile
|-.gitignore
|-poetry.lock
|-README.md

```