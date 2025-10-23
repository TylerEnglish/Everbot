
# Everabot — build plan & giant TODO list (in order)

### 0) Initialize the repo exactly as you want it

* [*] Create the skeleton you specified:


Everabot/
├─ ruleset/                 # Immutable runtime rules & governance
├─ test/                    # Immutable root tests (Turing-style + standard LLM evals)
├─ app/                     # Optional UI the agent can build later
├─ bot/                     # Agent core, tools, skills, trainers, evaluators
├─ snapshots/               # Versioned state (code, data, models, indices)
├─ Dockerfile
├─ pyproject.toml
├─ poetry.lock
└─ README.md


* [*] Initialize `poetry`, add core deps (Python 3.11+), and commit the empty scaffold.

---

### 1) Docker, security, and “can’t-edit rules/tests” enforcement

* [*] **Run as non-root** in container; add `USER` in Dockerfile; verify writes fail in `ruleset/` and `test/`. ([Docker Documentation][1])
* [*] **Mount `ruleset/` and `test/` read-only** (`:ro`) via bind/volume mounts in `docker run`. Add a startup self-check that attempts to write and expects `EPERM`. ([Docker Documentation][2])
* [*] Implement a **Gatekeeper** in `bot/security/guard.py` that denies open/write to those paths (monkey-patch file ops; enforce at CLI & tool layer too).
* [*] Add a container healthcheck; export only `/workspace` as writable.

---

### 2) The Python orchestrator & CLI (terminal-first)

* [*] Create `bot/cli.py` exposing subcommands:

* `learn <topic>` (RAG + practice + eval + snapshot)
* `build <thing>` (goal-oriented project, read-only against mission repos unless told)
* `self-improve` (optimize its internals only)
* `train` (weight finetune/QLoRA/LoRA on local data)
* `eval` (run root tests + any new eval suites)
* `snapshot` / `rollback`
* [*] Define a **Task graph** + **state machine**: `IDLE → RESEARCH → DISTILL → PRACTICE → EVAL → DECIDE(PROMOTE/REJECT)`.
* [*] Add **OpenTelemetry** logging hooks (trace spans per stage) to power later XAI reporting; set up a file exporter first. ([OpenTelemetry][3])

---

### 3) Immutable rules & base tests (what *must* always pass)

* [*] Populate `ruleset/` with a minimal, explicit policy the agent can *read* but never change (allowed tools, safety, network scope, max CPU/RAM, promotion gate, etc.).
* [*] In `test/`, add **baseline evaluations** the agent must never regress on:

* **Conversation/Turing-style** sanity prompts (your authored cases).
* **Standard LLM benchmarks** wired for local use:

  * **MMLU** for broad knowledge. ([arXiv][4])
  * **GSM8K** for math reasoning. ([arXiv][5])
  * **HellaSwag** for commonsense (note the known validity caveats). ([arXiv][6])

* [*] Expose a **single command** via Poetry scripts: `poetry run everabot eval` that runs these and emits a pass/fail summary. (No Makefile.)

---

### 4) Storage/RAG substrate (fast start, upgradable later with **provable equivalence**)

* [*] Start with **Parquet** for columnar caches (sources, embeddings, judgments) via PyArrow; keep doc blobs as JSONL, metadata/embeddings as Parquet. ([Apache Parquet][8])
* [*] Implement a tiny **vector index** (FAISS/ann-lite) with a **manifest** that maps query→top-k doc IDs and scores.
* [*] Add **equivalence tests** for store refactors:

* **Bit-for-bit check** for serialized doc/metadata parity (hash).
* **RAG recall parity**: for a frozen query set, `old_topk == new_topk` (ID-level), otherwise block promotion.
* [*] For snapshot size: store diffs of large artifacts via **VCDIFF**/xdelta; prove you can reconstruct byte-identical states. ([IETF Datatracker][9])

---

### 5) Research → Distill → Practice loop (context engineering + reflection)

* [*] Implement **RAG** ingestion: respectful fetchers (docs, arXiv PDFs, transcripts), text normalizers, chunkers, embedder. Wire retrieval to `learn`. ([arXiv][10])
* [*] Add **“Bullets” distillation**: compress sources into atomic tactics/facts; keep provenance links.
* [*] Integrate **Reflexion-style self-feedback**: after each attempt, write a short “what helped/harmed” note that is retrieved next time. ([arXiv][11])
* [*] Optional prompt-token reduction with **LLMLingua** when long. ([arXiv][12])
* [*] Promotion rule: **only** if new topic KPIs ↑ **and** all root tests stay ≥ previous (Pareto gate).

---

### 6) Local inference (open weights) that runs inside Docker on modest machines

* [*] Add two backends behind a single interface:

* **Transformers** (PyTorch) for standard HF models. ([Hugging Face][13])
* **llama.cpp** for quantized **GGUF** models (CPU-friendly); expose sampling and KV cache tuning. ([GitHub][14])

* [*] Provide a config knob to select backend/model/precision at runtime; default to a small quantized model to keep CPU/RAM light.

---

### 7) **Training its own weights** (PEFT first, full finetune optional later)

* [*] Implement **LoRA/QLoRA** training pipelines (PEFT) with HuggingFace `datasets` and `accelerate`/bitsandbytes. Start with small adapters to keep it light. ([arXiv][15])
* [*] Add `train` CLI supporting:

* **Domain adapters** (LoRA) on your local datasets.
* **QLoRA** when you have a GPU; keep the adapters small and versioned in `snapshots/models/adapters/…`. ([arXiv][16])

* [*] Plug **evaluation gates**: after training, run **root tests** + topic evals; only **promote** adapter if Pareto gate passes.
* [*] Add a lane for **from-scratch or hybrid** (if the agent deems it better): wire a minimal pretraining scaffold (tokenizer, dataset streaming), but protect it behind strict resource ceilings.

---

### 8) “Self-optimization” for speed/RAM (hotspot rewrite path)

* [*] Add profiling harnesses:

* Python **py-spy** sampling during `learn`/`build` runs. Store flamegraphs in `snapshots/profiles/`. ([GitHub][17])

* [*] If a hotspot is found, support drop-in rewrites behind stable interfaces:

* **C/C++** via `pybind11`. ([pybind11.readthedocs.io][18])
* **Rust** via `PyO3` + `maturin`. ([pyo3.rs][19])
* **Go** microservice or cgo bridge if needed. ([Go Packages][20])

* [*] Always A/B the rewritten module on perf+memory, plus **functional equivalence tests** (same I/O) before enabling.

---

### 9) Snapshots, rollback, and **evidence logging (XAI)**

* [*] Every `learn`/`train` run creates a **snapshot**:

* code diff, configs, dataset manifest, RAG store manifest, model refs/adapters, **proof logs** (sources → distilled bullets → attempts → scores → decision rationale).

* [*] Use **VCDIFF** for large binary deltas to keep snapshot sizes small. ([IETF Datatracker][9])
* [*] Implement `snapshot promote|list|checkout|diff` commands.
* [*] Use **OpenTelemetry** context to correlate logs/metrics/artifacts; optionally surface runs in **MLflow Tracking** (local filesystem backend). ([OpenTelemetry][3])

---

### 10) The learning curriculum & auto-expansion

* [*] Add a **topic registry** (`ruleset/topics.yaml`) the *agent reads* (not edits) to know allowed domains and safety levels.
* [*] Implement **priority selection**: choose next sub-skill by expected gain vs. regression risk, respecting a budget (time/CPU/RAM).
* [*] Integrate **context-engineering** patterns (ACE-style memory evolution; recent agentic long-context variants) as optional strategies the agent can pick. ([arXiv][21])

---

### 11) Evaluations by mode (plug-and-play harnesses)

* [*] **Math**: GSM8K + unit tests for step-by-step solutions; verifier scoring. ([arXiv][5])
* [*] **Knowledge**: MMLU (subset first), track macro-average. ([arXiv][4])
* [*] **Commonsense**: HellaSwag; annotate the benchmark’s caveats in logs. ([arXiv][6])
* [*] **Your Turing-style tests**: fixture prompts with rubric scoring (fluency/consistency/safety).

---

### 12) Governance: promotion gates & safety rails

* [ ] Encode the **Pareto gate**: `new_topic_score >= threshold` AND `all_root_scores >= last_promoted` else rollback.
* [ ] Rate-limit and sandbox “internet tools”; whitelist only fetchers and doc types you’re comfortable with.
* [ ] Enforce **max token**, **max wall-time**, **max disk** budgets per run (configurable in `ruleset/limits.yaml`).

---

### 13) RAG & storage **refactor** path with 100% match proof

* [ ] Provide an interface for candidate stores (e.g., Parquet→custom columnar, index v1→v2).
* [ ] Implement **canary migration**:

1. Export canonical **golden dumps** (ordered JSONL + Parquet schemas).
2. Import into new store.
3. Run **bitwise hash** and **top-k equivalence** suites.
4. Only switch the pointer (`current_store`) on full pass.

* [ ] Maintain **schema registry** (Avro/Arrow schema files) to avoid silent drift. ([Apache Parquet][8])

---

### 14) Optional: lightweight UI the agent can build later

* [ ] Keep `app/` empty except for a placeholder API that streams logs and scores; first milestone is terminal-only.
* [ ] When you ask for it, the agent scaffolds a local dashboard that reads **OpenTelemetry**/MLflow runs and renders **proofs** (sources, bullets, attempts, diffs). ([OpenTelemetry][3])

---

### 15) Developer ergonomics

* [ ] **Poetry scripts** (and optional `poethepoet` tasks) for `eval`, `selfcheck`, `snapshot`, etc. (No Makefile.)
* [ ] Devcontainer config so you can pop into a consistent shell.
* [ ] Pre-commit hooks to prevent accidental edits to `ruleset/` and `test/`.

---

### 16) Minimal milestones (sanity to advanced)

**Milestone A — Boot & Protect**

* [ ] Docker image builds; non-root; **read-only mounts** proven (write attempts fail). ([Docker Documentation][2])
* [ ] `poetry run everabot eval` runs root tests (stubs OK).

**Milestone B — Learn & Log**

* [ ] `everabot learn "grade-school math"` ingests public sources, distills bullets, practices on GSM8K subset, logs traces, snapshots a decision (promote/reject). ([arXiv][5])

**Milestone C — Self-Improve**

* [ ] `everabot self-improve` profiles itself with **py-spy**; if hotspot detected, proposes a `pybind11`/`PyO3` rewrite behind an interface; runs perf A/B + functional checks; promotes if win & no regressions. ([GitHub][17])

**Milestone D — Retrain**

* [ ] `everabot train --lora` runs a tiny LoRA/QLoRA adapter on your local data; gates via root tests; keeps adapters versioned. ([arXiv][15])

**Milestone E — RAG Refactor**

* [ ] Migrate the RAG store (e.g., new encoding); pass **100% equivalence** (hash+top-k) before switching; old and new both snapshot-reconstructable with VCDIFF. ([IETF Datatracker][9])
