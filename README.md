# Everabot (Milestone A — Boot & Protect)

This seed gives you:
- Non-root Docker runtime and read-only mounts for `ruleset/` & `test/`.
- CLI with `eval`, `selfcheck`, `learn`, `self_improve`, `snapshot` stubs.
- FS guard that denies writes to protected dirs at runtime.

## Quickstart (host)

```bash
poetry install
poetry run everabot selfcheck   # should report blocked writes
poetry run everabot eval        # runs root test stubs
```

## Docker

```bash
make docker-build
make docker-run
```

`docker-run` binds:

* `ruleset/:ro` and `test/:ro`  (read-only)
* `snapshots/:rw`              (writable)

## Notes

* Use Parquet for columnar caches (docs/embeddings/metrics). ([Apache Parquet][2])
* Add OpenTelemetry spans when wiring the learning loop (Python SDK). ([OpenTelemetry][3])
* Profile hotspots with `py-spy` when you turn on `self_improve`. ([GitHub][4])
* For CPU-friendly local inference later, add a `llama.cpp` backend (GGUF). ([GitHub][5])


