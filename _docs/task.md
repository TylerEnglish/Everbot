awesome — here’s a sequenced, no-nonsense build plan for your **Everabot** that fits your constraints (Python orchestrator, Docker-local, open weights, read-only rules/tests, can retrain its own weights, lightweight to start, and able to refactor storage/RAG with provable equivalence). I’ve grounded the plan on well-known techniques/papers and link them where it matters.

---

# Everabot — build plan & giant TODO list (in order)

### 0) Initialize the repo exactly as you want it

* [ ] Create the skeleton you specified:

  ```
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
  ```
* [ ] Initialize `poetry`, add core deps (Python 3.11+), and commit the empty scaffold.

---

### 1) Docker, security, and “can’t-edit rules/tests” enforcement

* [ ] **Run as non-root** in container; add `USER` in Dockerfile; verify writes fail in `ruleset/` and `test/`. ([Docker Documentation][1])
* [ ] **Mount `ruleset/` and `test/` read-only** (`:ro`) via bind/volume mounts in `docker run` / Compose. Add a startup self-check that attempts to write and expects `EPERM`. ([Docker Documentation][2])
* [ ] Implement a **Gatekeeper** in `bot/security/guard.py` that denies open/write to those paths (monkey-patch file ops; enforce at CLI & tool layer too).
* [ ] Add a container healthcheck; export only `/workspace` as writable.

---

### 2) The Python orchestrator & CLI (terminal-first)

* [ ] Create `bot/cli.py` exposing subcommands:

  * `learn <topic>` (RAG + practice + eval + snapshot)
  * `build <thing>` (goal-oriented project, read-only against mission repos unless told)
  * `self-improve` (optimize its internals only)
  * `train` (weight finetune/QLoRA/LoRA on local data)
  * `eval` (run root tests + any new eval suites)
  * `snapshot` / `rollback`
* [ ] Define a **Task graph** + **state machine**: `IDLE → RESEARCH → DISTILL → PRACTICE → EVAL → DECIDE(PROMOTE/REJECT)`.
* [ ] Add **OpenTelemetry** logging hooks (trace spans per stage) to power later XAI reporting; set up a file exporter first. ([OpenTelemetry][3])

---

### 3) Immutable rules & base tests (what *must* always pass)

* [ ] Populate `ruleset/` with a minimal, explicit policy the agent can *read* but never change (allowed tools, safety, network scope, max CPU/RAM, promotion gate, etc.).
* [ ] In `test/`, add **baseline evaluations** the agent must never regress on:

  * **Conversation/Turing-style** sanity prompts (your authored cases).
  * **Standard LLM benchmarks** wired for local use:

    * **MMLU** for broad knowledge. ([arXiv][4])
    * **GSM8K** for math reasoning. ([arXiv][5])
    * **HellaSwag** for commonsense (note the known validity caveats). ([arXiv][6])
    * **HumanEval** for code functional correctness. ([arXiv][7])
* [ ] Add a **single command** `make root-check` that runs these and emits a pass/fail summary.

---

### 4) Storage/RAG substrate (fast start, upgradable later with **provable equivalence**)

* [ ] Start with **Parquet** for columnar caches (sources, embeddings, judgments) via PyArrow; keep doc blobs as JSONL, metadata/embeddings as Parquet. ([Apache Parquet][8])
* [ ] Implement a tiny **vector index** (FAISS/ann-lite) with a **manifest** that maps query→top-k doc IDs and scores.
* [ ] Add **equivalence tests** for store refactors:

  * **Bit-for-bit check** for serialized doc/metadata parity (hash).
  * **RAG recall parity**: for a frozen query set, `old_topk == new_topk` (ID-level), otherwise block promotion.
* [ ] For snapshot size: store diffs of large artifacts via **VCDIFF**/xdelta; prove you can reconstruct byte-identical states. ([IETF Datatracker][9])

---

### 5) Research → Distill → Practice loop (context engineering + reflection)

* [ ] Implement **RAG** ingestion: respectful fetchers (docs, arXiv PDFs, transcripts), text normalizers, chunkers, embedder. Wire retrieval to `learn`. ([arXiv][10])
* [ ] Add **“Bullets” distillation**: compress sources into atomic tactics/facts; keep provenance links.
* [ ] Integrate **Reflexion-style self-feedback**: after each attempt, write a short “what helped/harmed” note that is retrieved next time. ([arXiv][11])
* [ ] Optional prompt-token reduction with **LLMLingua** when long. ([arXiv][12])
* [ ] Promotion rule: **only** if new topic KPIs ↑ **and** all root tests stay ≥ previous (Pareto gate).

---

### 6) Local inference (open weights) that runs inside Docker on modest machines

* [ ] Add two backends behind a single interface:

  * **Transformers** (PyTorch) for standard HF models. ([Hugging Face][13])
  * **llama.cpp** for quantized **GGUF** models (CPU-friendly); expose sampling and KV cache tuning. ([GitHub][14])
* [ ] Provide a config knob to select backend/model/precision at runtime; default to a small quantized model to keep CPU/RAM light.

---

### 7) **Training its own weights** (PEFT first, full finetune optional later)

* [ ] Implement **LoRA/QLoRA** training pipelines (PEFT) with HuggingFace `datasets` and `accelerate`/bitsandbytes. Start with small adapters to keep it light. ([arXiv][15])
* [ ] Add `train` CLI supporting:

  * **Domain adapters** (LoRA) on your local datasets.
  * **QLoRA** when you have a GPU; keep the adapters small and versioned in `snapshots/models/adapters/…`. ([arXiv][16])
* [ ] Plug **evaluation gates**: after training, run **root tests** + topic evals; only **promote** adapter if Pareto gate passes.
* [ ] Add a lane for **from-scratch or hybrid** (if the agent deems it better): wire a minimal pretraining scaffold (tokenizer, dataset streaming), but protect it behind strict resource ceilings.

---

### 8) “Self-optimization” for speed/RAM (hotspot rewrite path)

* [ ] Add profiling harnesses:

  * Python **py-spy** sampling during `learn`/`build` runs. Store flamegraphs in `snapshots/profiles/`. ([GitHub][17])
* [ ] If a hotspot is found, support drop-in rewrites behind stable interfaces:

  * **C/C++** via `pybind11`. ([pybind11.readthedocs.io][18])
  * **Rust** via `PyO3` + `maturin`. ([pyo3.rs][19])
  * **Go** microservice or cgo bridge if needed. ([Go Packages][20])
* [ ] Always A/B the rewritten module on perf+memory, plus **functional equivalence tests** (same I/O) before enabling.

---

### 9) Snapshots, rollback, and **evidence logging (XAI)**

* [ ] Every `learn`/`train` run creates a **snapshot**:

  * code diff, configs, dataset manifest, RAG store manifest, model refs/adapters, **proof logs** (sources → distilled bullets → attempts → scores → decision rationale).
* [ ] Use **VCDIFF** for large binary deltas to keep snapshot sizes small. ([IETF Datatracker][9])
* [ ] Implement `snapshot promote|list|checkout|diff` commands.
* [ ] Use **OpenTelemetry** context to correlate logs/metrics/artifacts; optionally surface runs in **MLflow Tracking** (local filesystem backend). ([OpenTelemetry][3])

---

### 10) The learning curriculum & auto-expansion

* [ ] Add a **topic registry** (`ruleset/topics.yaml`) the *agent reads* (not edits) to know allowed domains and safety levels.
* [ ] Implement **priority selection**: choose next sub-skill by expected gain vs. regression risk, respecting a budget (time/CPU/RAM).
* [ ] Integrate **context-engineering** patterns (ACE-style memory evolution; recent agentic long-context variants) as optional strategies the agent can pick. ([arXiv][21])

---

### 11) Evaluations by mode (plug-and-play harnesses)

* [ ] **Math**: GSM8K + unit tests for step-by-step solutions; verifier scoring. ([arXiv][5])
* [ ] **Knowledge**: MMLU (subset first), track macro-average. ([arXiv][4])
* [ ] **Commonsense**: HellaSwag; annotate the benchmark’s caveats in logs. ([arXiv][6])
* [ ] **Code**: HumanEval (pass@k). ([arXiv][7])
* [ ] **Your Turing-style tests**: fixture prompts with rubric scoring (fluency/consistency/safety).

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

* [ ] Makefiles/`poe` scripts for `format`, `lint`, `typecheck`, `test`, `eval`, `snapshot`.
* [ ] Devcontainer config so you can pop into a consistent shell.
* [ ] Pre-commit hooks to prevent accidental edits to `ruleset/` and `test/`.

---

### 16) Minimal milestones (sanity to advanced)

**Milestone A — Boot & Protect**

* [ ] Docker image builds; non-root; read-only mounts proven (write attempts fail). ([Docker Documentation][2])
* [ ] `everabot eval` runs root tests (stubs OK).

**Milestone B — Learn & Log**

* [ ] `everabot learn "grade-school math"` ingests public sources, distills bullets, practices on GSM8K subset, logs traces, snapshots a decision (promote/reject). ([arXiv][5])

**Milestone C — Self-Improve**

* [ ] `everabot self-improve` profiles itself with **py-spy**; if hotspot detected, proposes a `pybind11`/`PyO3` rewrite behind an interface; runs perf A/B + functional checks; promotes if win & no regressions. ([GitHub][17])

**Milestone D — Retrain**

* [ ] `everabot train --lora` runs a tiny LoRA/QLoRA adapter on your local data; gates via root tests; keeps adapters versioned. ([arXiv][15])

**Milestone E — RAG Refactor**

* [ ] Migrate the RAG store (e.g., new encoding); pass **100% equivalence** (hash+top-k) before switching; old and new both snapshot-reconstructable with VCDIFF. ([IETF Datatracker][9])

---

## Suggested file layout details (what goes where)

```
Everabot/
├─ ruleset/
│  ├─ limits.yaml           # budgets: tokens, wall-time, RAM, disk, net
│  ├─ promotion.yaml        # Pareto gate, eval thresholds, rollback policy
│  ├─ topics.yaml           # allowed domains & safety levels (read-only)
│  └─ tools.yaml            # whitelisted tools & network scopes
├─ test/
│  ├─ turing/               # your authored conversation tests
│  ├─ mmlu/                 # harness + cached subset
│  ├─ gsm8k/                # harness + cached subset
│  ├─ hellaswag/            # harness + cached subset
│  ├─ humaneval/            # harness
│  └─ run_root_tests.py
├─ bot/
│  ├─ cli.py
│  ├─ core/                 # state machine, snapshot manager, gatekeeper
│  ├─ learn/                # research, distill, practice, eval, decide
│  ├─ rag/                  # loaders, chunkers, embed, retrieve, store iface
│  ├─ storage/              # parquet store v1, migrations, equivalence tests
│  ├─ evals/                # wrappers for MMLU/GSM8K/HellaSwag/HumanEval
│  ├─ training/             # LoRA/QLoRA pipelines, dataset adapters
│  ├─ optimize/             # profilers, pybind11/PyO3 stubs, A/B harness
│  ├─ models/               # HF + llama.cpp backends (unified interface)
│  ├─ security/             # guard.py (FS denies), sandbox utils
│  └─ logging/              # OpenTelemetry emitters, XAI proof logs
├─ snapshots/
│  ├─ profiles/             # py-spy flamegraphs
│  ├─ data/                 # parquet + manifests (+ vcdiff deltas)
│  ├─ models/               # model refs, quantized weights, adapters
│  └─ runs/                 # each run: inputs, outputs, proofs, decisions
├─ app/                     # (empty to start; dashboard later)
├─ Dockerfile
├─ pyproject.toml
├─ poetry.lock
└─ README.md
```

---

## Notes on choices (why these pieces)

* **Context-first improvement** (Reflexion/ACE-style) gives you learning without mandatory weight updates, and coexists with later **LoRA/QLoRA** when you *do* want to update weights. ([arXiv][11])
* **Parquet + manifests** keep storage compact and schema-controlled; **VCDIFF** lets you snapshot frequently without ballooning disk. ([Apache Parquet][8])
* **py-spy + pybind11/PyO3/cgo** gives a clear, testable path to speed when needed. ([GitHub][17])
* **Transformers + llama.cpp** cover open-weights local inference across CPU/GPU and quantized setups. ([Hugging Face][13])
* **MLflow/OpenTelemetry** provide durable, auditable “proofs” of what was learned and why. ([MLflow][22])

---

If you want, I can turn this into a first commit: a working Dockerfile, `poetry` env, CLI skeleton, logging hooks, the root tests harness stubs, and the read-only mounts with a failing write-attempt test—so you can start checking boxes immediately.

[1]: https://docs.docker.com/build/building/best-practices/?utm_source=chatgpt.com "Building best practices"
[2]: https://docs.docker.com/engine/storage/volumes/?utm_source=chatgpt.com "Volumes"
[3]: https://opentelemetry.io/docs/languages/python/?utm_source=chatgpt.com "Python"
[4]: https://arxiv.org/abs/2009.03300?utm_source=chatgpt.com "Measuring Massive Multitask Language Understanding"
[5]: https://arxiv.org/abs/2110.14168?utm_source=chatgpt.com "Training Verifiers to Solve Math Word Problems"
[6]: https://arxiv.org/abs/1905.07830?utm_source=chatgpt.com "HellaSwag: Can a Machine Really Finish Your Sentence?"
[7]: https://arxiv.org/abs/2107.03374?utm_source=chatgpt.com "Evaluating Large Language Models Trained on Code"
[8]: https://parquet.apache.org/docs/overview/?utm_source=chatgpt.com "Overview | Parquet"
[9]: https://datatracker.ietf.org/doc/html/rfc3284?utm_source=chatgpt.com "RFC 3284 - The VCDIFF Generic Differencing and ..."
[10]: https://arxiv.org/abs/2005.11401?utm_source=chatgpt.com "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
[11]: https://arxiv.org/abs/2303.11366?utm_source=chatgpt.com "Reflexion: Language Agents with Verbal Reinforcement Learning"
[12]: https://arxiv.org/abs/2310.05736?utm_source=chatgpt.com "LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models"
[13]: https://huggingface.co/docs/transformers/en/quicktour?utm_source=chatgpt.com "Quickstart"
[14]: https://github.com/ggml-org/llama.cpp?utm_source=chatgpt.com "ggml-org/llama.cpp: LLM inference in C/C++"
[15]: https://arxiv.org/abs/2106.09685?utm_source=chatgpt.com "LoRA: Low-Rank Adaptation of Large Language Models"
[16]: https://arxiv.org/pdf/2305.14314?utm_source=chatgpt.com "QLORA: Efficient Finetuning of Quantized LLMs"
[17]: https://github.com/benfred/py-spy?utm_source=chatgpt.com "benfred/py-spy: Sampling profiler for Python programs"
[18]: https://pybind11.readthedocs.io/?utm_source=chatgpt.com "pybind11 documentation"
[19]: https://pyo3.rs/?utm_source=chatgpt.com "Introduction - PyO3 user guide"
[20]: https://pkg.go.dev/cmd/cgo?utm_source=chatgpt.com "cgo command - cmd/cgo"
[21]: https://arxiv.org/abs/2506.06698?utm_source=chatgpt.com "Contextual Experience Replay for Self-Improvement of Language ..."
[22]: https://mlflow.org/docs/1.29.0/tracking.html?utm_source=chatgpt.com "MLflow Tracking"
