# Root Tests (immutable)

This folder contains baseline tests that **must never regress**.

## What runs today via `poetry run everabot eval`
- `security`: verifies writes to `ruleset/` and `test/` are blocked by the guard.
- `root`: runs `test/run_root_tests.py` (YAML validation, policy sanity).
- `sanity`: tiny authored Turing-style prompts (SKIP unless an LLM backend is configured).

## Standard Benchmarks (wired, but optional)
Place datasets to un-skip:
- **MMLU** → `test/bench_mmlu/` (hendrycks/test format)
- **GSM8K** → `test/bench_gsm8k/`
- **HellaSwag** → `test/bench_hellaswag/`
- **HumanEval** → `test/bench_humaneval/`

> Until you drop assets, these benches report **SKIP** so CI remains green.
