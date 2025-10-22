Help me finish my tasks, I'm on task 6 now, what I need help with is a RAG system. I'll upload a PDF white paper explaining what exactly I want, in this I want you to very carefully look at it with detail. Then I want you to look at the next section of task, and then I want you to implement them. Be sure to read everything so we can have good ground work for the later task and we can finish this everbot strong. 

What I want is a detail application of what to do, give me everything, all the new files and changes. I want this to be descriptive and work. Also show me the commands to make sure that we finished these task. 

Dont hallucinate and make sure you carefully read everything and all the sources to implement this. Think of Locality of Behaivor and try not to duplicate code.

----
Context:

Please review that PDF very carefully, make sure you review all of it before you go onto my next portions.

A task is completed by this indication `[*]`


Sources:
```bash
* [ACE](https://arxiv.org/pdf/2510.04618)
* [Self-Improving Coding Agent](https://arxiv.org/pdf/2504.15228)
* [RAG](https://arxiv.org/pdf/2506.00054)
* [Delta Encoding](https://nhsjs.com/2025/investigating-delta-encoding-with-error-control-for-scientific-data-reduction/)

```

----
tasks:

```md

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


```

seed_urls.yaml
```yaml
version: 1
seeds:
  - https://arxiv.org/pdf/2510.04618        # ACE
  - https://arxiv.org/pdf/2303.11366        # Reflexion
  - https://arxiv.org/pdf/2005.11401        # RAG (Lewis et al.)
  - https://datatracker.ietf.org/doc/html/rfc3284  # VCDIFF (HTML canonical)

```


limits.yaml:
```yaml
version: 1
tokens_max: 8192
wall_time_sec_max: 900
ram_mb_max: 4096
disk_mb_max: 4096

cpu:
  percent_max: 85
fd_limits:
  soft_max: 512

phases:
  RESEARCH: { wall_time_sec_max: 240 }
  DISTILL:  { wall_time_sec_max: 120 }
  PRACTICE: { wall_time_sec_max: 180 }
  EVAL:     { wall_time_sec_max: 120 }
  DECIDE:   { wall_time_sec_max: 30 }

snapshots:
  binary_delta: ["vcdiff", "xdelta3"]
  require_bitwise_reconstruction: true

```

promotion.yaml:
```yaml
version: 1

pareto_gate:
  require_no_regressions: true          # root tests must not go down
  new_topic_min_score: 0.80             # topic KPI threshold (your scorer can fill this)
rollback_on_fail: true

# SICA-inspired tri-objective utility (score/time/cost) used for archive selection.
# This is policy only; the orchestrator may read it later.
utility:
  weights:
    score: 0.50
    cost: 0.25
    time: 0.25
  time_cap_sec: 300          # mirror SICA's 5m horizon for agent actions
  cost_cap_usd: 10

bench_thresholds:            # soft policy targets when benches are wired
  sanity_pass_rate_min: 1.0  # 100% on authored sanity
  mmlu_macro_min: null       # set when you decide; null = not enforced
  gsm8k_acc_min: null
  hellaswag_acc_min: null

```

tools.yaml:
```yaml
version: 1

internet:
  allowed: true
  fetchers: ["http", "https"]
  rate_limit:
    rps_max: 4
    burst_max: 8

# Central, enforced by fetchers.py
net:
  allowed_hosts:
    - arxiv.org
    - www.rfc-editor.org
    - datatracker.ietf.org
    - youtube.com
    - youtu.be
    - github.com

file_ops:
  write_whitelist: ["snapshots"]
  write_blacklist: ["ruleset", "test"]

exec:
  allowed: false
  allowlist: []
  denylist: ["bash -c *", "sh -c *", "curl|bash", "pip install *"]

sandbox:
  fs_ro_paths: ["ruleset", "test"]
  net_namespaces: ["default"]

```

topics.yaml:
```yaml
version: 1
allowed_topics:
  - problem_solving
  - web_dev
  - game_dev
  - math
  - security_basics
aliases:
  web: web_dev
  security: security_basics
notes:
  - "Topics gate only the 'learn' path; 'build' is free-form but still bound by safety."

```

------
test file

README.md
```md
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


```

bench_gsm8k/README.md
```md
# GSM8K (local)

Place Hendrycks `test` repo subsets here, e.g.:

- `test/bench_gsm8k/data/*` (CSV/JSON as provided by the repo)

The Everabot harness will detect this folder and report `SKIP` until a scorer
is wired. Once wired, thresholds can be enforced from `ruleset/promotion.yaml`.


```

bench_hellaswag/README.md
```md
# hellaswag (local)

Place Hendrycks `test` repo subsets here, e.g.:

- `test/bench_hellaswag/data/*` (CSV/JSON as provided by the repo)

The Everabot harness will detect this folder and report `SKIP` until a scorer
is wired. Once wired, thresholds can be enforced from `ruleset/promotion.yaml`.


```

bench_mmlu/README.md
```md
# MMLU (local)

Place Hendrycks `test` repo subsets here, e.g.:

- `test/bench_mmlu/data/*` (CSV/JSON as provided by the repo)

The Everabot harness will detect this folder and report `SKIP` until a scorer
is wired. Once wired, thresholds can be enforced from `ruleset/promotion.yaml`.

```

sanity/cases.yaml:
```yaml
cases:
  - prompt: "Say exactly: OK"
    expect_regex: "^OK$"
  - prompt: "Answer with the single word 'pong'."
    expect_regex: "^pong$"
```

run_root_tests.py:
```python
from __future__ import annotations
import sys, pathlib, json, yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
RULES = ROOT / "ruleset"

def _ok(msg): print(f"[OK] {msg}")
def _fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)

def _load_yaml(name: str):
    p = RULES / name
    if not p.exists():
        _fail(f"Missing ruleset file: {name}")
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as e:
        _fail(f"YAML parse error in {name}: {e}")

def test_rules_safety():
    data = _load_yaml("safety.yaml")
    for k in ("content","context_policy","self_modification","network","fail_fast"):
        if k not in data:
            _fail(f"safety.yaml missing key: {k}")
    # Protected roots are forbidden for self-edits
    forb = set(map(str, data["self_modification"].get("forbidden_paths", [])))
    if not {"ruleset","test"}.issubset(forb):
        _fail("safety.yaml must forbid edits to ruleset/ and test/")
    _ok("safety.yaml sanity")

def test_rules_limits():
    data = _load_yaml("limits.yaml")
    t = int(data.get("tokens_max", 0))
    wall = int(data.get("wall_time_sec_max", 0))
    if not (1024 <= t <= 131072):
        _fail("limits.tokens_max out of reasonable range")
    if wall <= 0:
        _fail("limits.wall_time_sec_max must be > 0")
    _ok("limits.yaml sanity")

def test_rules_promotion():
    data = _load_yaml("promotion.yaml")
    gate = data.get("pareto_gate", {})
    if gate.get("require_no_regressions") is not True:
        _fail("promotion.pareto_gate.require_no_regressions must be true")
    _ok("promotion.yaml sanity")

def test_rules_tools():
    data = _load_yaml("tools.yaml")
    fops = data.get("file_ops", {})
    bl = set(fops.get("write_blacklist", []))
    if not {"ruleset","test"}.issubset(bl):
        _fail("tools.file_ops.write_blacklist must include ruleset and test")
    _ok("tools.yaml sanity")

def test_topics():
    data = _load_yaml("topics.yaml")
    if not data.get("allowed_topics"):
        _fail("topics.allowed_topics must be non-empty")
    _ok("topics.yaml sanity")

def test_sanity_cases_exist():
    p = ROOT / "test" / "sanity" / "cases.yaml"
    if not p.exists():
        _fail("Missing test/sanity/cases.yaml")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not data.get("cases"):
        _fail("sanity/cases.yaml must include at least one case")
    _ok("sanity cases present")

if __name__ == "__main__":
    # run all tests; exit 0 if everything OK
    test_rules_safety()
    test_rules_limits()
    test_rules_promotion()
    test_rules_tools()
    test_topics()
    test_sanity_cases_exist()
    print("PASS")
    sys.exit(0)

```
--------
bot so far:
cli.py:
```python
from __future__ import annotations
import typer, sys, pathlib, subprocess, json
from rich import print
from bot.security.guard import install_guard
from bot.paths import get_project_root
from bot.rules.loader import load_all
from bot.eval.harness import run_all
from bot.rag.utils import read_jsonl

app = typer.Typer(help="Everabot CLI")
rag_app = typer.Typer(help="RAG substrate commands")
app.add_typer(rag_app, name="rag")

_ROOT = get_project_root()
_PROTECTED_DIRS = {str(_ROOT / "ruleset"), str(_ROOT / "test")}
install_guard(deny_dirs=_PROTECTED_DIRS)

@app.command()
def eval():
    """Run root tests + benchmark wiring; emits a PASS/SKIP/FAIL JSON summary."""
    payload = run_all()
    print(payload.get("overall"), "-", payload.get("artifact"))
    # Non-zero exit only if something truly FAILED (not SKIPPED)
    raise typer.Exit(code=0 if payload["overall"] == "PASS" else 1)

@app.command()
def selfcheck():
    ok = True
    for rel in ("ruleset/__probe.txt", "test/__probe.txt"):
        p = _ROOT / rel
        try:
            p.write_text("should fail")
            print(f"[red]ERROR:[/red] write unexpectedly succeeded: {p}")
            ok = False
        except Exception as e:
            print(f"[green]OK[/green] blocked write to {p} -> {type(e).__name__}")
    raise typer.Exit(code=0 if ok else 1)

@app.command()
def learn(topic: str):
    from bot.workflows import learn as wf
    code = wf.run(topic)
    print(f"[cyan]learn[/cyan] → {topic} [dim](snapshot created under snapshots/runs)[/dim]")
    raise typer.Exit(code=code)

@app.command()
def build(goal: str):
    from bot.workflows import build as wf
    code = wf.run(goal)
    print(f"[cyan]build[/cyan] → {goal}")
    raise typer.Exit(code=code)

@app.command("self-improve")
def self_improve():
    from bot.workflows import self_improve as wf
    code = wf.run()
    print("[cyan]self-improve[/cyan] starting (stub)")
    raise typer.Exit(code=code)

@app.command()
def train():
    """Training lane (PEFT etc.) — stub snapshot."""
    from bot.workflows import train as wf
    code = wf.run()
    print("[cyan]train[/cyan] (stub) snapshot created")
    raise typer.Exit(code=code)

# -------- snapshot subcommands (robust) --------
snapshot_app = typer.Typer(help="Manage learn/train run snapshots")
app.add_typer(snapshot_app, name="snapshot")

def _runs_dir() -> pathlib.Path:
    return _ROOT / "snapshots" / "runs"

def _iter_runs():
    rd = _runs_dir()
    if not rd.exists():
        return []
    return sorted([p for p in rd.iterdir() if (p / "manifest.json").exists()])

@snapshot_app.command("list")
def snapshot_list():
    runs = _iter_runs()
    if not runs:
        print("[yellow]No snapshots yet.[/yellow]")
        raise typer.Exit()
    for p in runs:
        m = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
        print(f"[green]{p.name}[/green]  status={m.get('status')}  type={m.get('run_type')}  topic={m.get('topic')}")

@snapshot_app.command("latest")
def snapshot_latest():
    runs = _iter_runs()
    if not runs:
        print("[yellow]No snapshots yet.[/yellow]")
        raise typer.Exit()
    latest = runs[-1]
    print((latest / "manifest.json").read_text(encoding="utf-8"))

@snapshot_app.command("show")
def snapshot_show(
    path: str = typer.Option(None, "--path", "-p", help="Path to snapshot dir"),
    id_: str = typer.Option(None, "--id", "-i", help="Folder name under snapshots/runs"),
    path_or_id: str = typer.Argument(None, help="(Positional) either full path or run folder name"),
):
    # Resolve target
    target = None
    provided = [x for x in (path, id_, path_or_id) if x]
    if len(provided) == 0:
        print("[red]Provide --path, --id, or positional path/id.[/red]")
        raise typer.Exit(code=2)
    if len([x for x in (path, id_) if x]) > 1:
        print("[red]Use only one of --path or --id.[/red]")
        raise typer.Exit(code=2)

    if path:
        target = pathlib.Path(path)
    elif id_:
        target = _runs_dir() / id_
    else:
        # positional
        p = pathlib.Path(path_or_id)
        target = p if p.is_absolute() or (p / "manifest.json").exists() else (_runs_dir() / path_or_id)

    man = target / "manifest.json"
    if not man.exists():
        print(f"[red]No manifest at[/red] {man}")
        raise typer.Exit(code=2)
    print(man.read_text(encoding="utf-8"))

@snapshot_app.command("rollback")
def snapshot_rollback(
    id_: str = typer.Option(None, "--id", "-i", help="Run folder name under snapshots/runs"),
    path: str = typer.Option(None, "--path", "-p", help="Absolute or relative path to a snapshot dir"),
    path_or_id: str = typer.Argument(None, help="(Positional) either full path or run folder name"),
    reason: str = typer.Option("manual rollback", "--reason", help="Reason to record"),
):
    runs_dir = (_ROOT / "snapshots" / "runs").resolve()

    # pick a single source
    token = path or id_ or path_or_id
    if not token:
        print("[red]Provide --id, --path, or positional path/id.[/red]")
        raise typer.Exit(code=2)

    import pathlib, json, datetime
    p = pathlib.Path(token)

    # If it's a path, normalize to absolute; if it's a bare ID, join under runs/
    if (p / "manifest.json").exists() or p.is_absolute() or any(sep in token for sep in ("/", "\\")):
        target = (p if p.is_absolute() else (_ROOT / p)).resolve()
    else:
        target = (runs_dir / token).resolve()

    man = target / "manifest.json"
    if not man.exists():
        print(f"[red]No manifest at[/red] {man}")
        raise typer.Exit(code=2)

    rb_dir = (_ROOT / "snapshots" / "rollbacks"); rb_dir.mkdir(parents=True, exist_ok=True)
    evt = {"ts": datetime.datetime.utcnow().isoformat() + "Z", "to": str(target), "reason": reason}
    out = rb_dir / f"rollback_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(evt, indent=2), encoding="utf-8")

    (_ROOT / "snapshots" / "CURRENT_POINTER.txt").write_text(str(target), encoding="utf-8")
    print(f"[green]Rolled back pointer to[/green] {target}\n[dim]Event:[/dim] {out}")

# ------------------- RAG commands -------------------

@rag_app.command("init")
def rag_init(seed: str = typer.Option("all", help="Seed set: all|none")):
    """
    Ingest the initial seed corpus (ACE, SICA, RAG, VCDIFF).
    """
    from bot.paths import get_project_root
    from bot.rag.pipeline import ingest
    root = get_project_root()
    out = root / "snapshots" / "rag" / "v1"
    urls = []
    if seed == "all":
        urls = [
            "https://arxiv.org/pdf/2510.04618",  # ACE
            "https://arxiv.org/pdf/2504.15228",  # SICA
            "https://arxiv.org/pdf/2005.11401",  # RAG (Lewis et al.)
            "https://datatracker.ietf.org/doc/html/rfc3284",  # VCDIFF
        ]
    meta = ingest(urls, out)
    print(f"[green]RAG store created at[/green] {out}  counts={meta['counts']} model={meta['emb_model']}")

@rag_app.command("ingest")
def rag_ingest(url: list[str] = typer.Argument(..., help="One or more URLs or paths"),
               out: str = typer.Option(None, "--out", help="Output dir under snapshots/rag")):
    from bot.paths import get_project_root
    from bot.rag.pipeline import ingest
    import pathlib
    root = get_project_root()
    out_dir = pathlib.Path(out) if out else (root / "snapshots" / "rag" / "custom")
    meta = ingest(list(url), out_dir)
    print(f"[green]Ingested[/green] -> {out_dir} counts={meta['counts']}")

@rag_app.command("query")
def rag_query(q: str, store: str = typer.Option("v1", help="Subdir under snapshots/rag")):
    from bot.paths import get_project_root
    import pyarrow.parquet as pq
    from bot.rag.embeddings import get_embedder
    from bot.rag.index_faiss import load_index
    root = get_project_root()
    sdir = root / "snapshots" / "rag" / store
    tab = pq.read_table(sdir / "embeddings.parquet")
    ids = [x.as_py() for x in tab["chunk_id"]]
    model, embed = get_embedder()
    qv = embed([q])
    idx = load_index(sdir / "index.faiss")
    D, I = idx.search(qv, 5)
    # Map chunk_ids back to doc titles (Windows-safe UTF-8 read via read_jsonl)
    docs = {row["id"]: row for row in read_jsonl(sdir / "docs.jsonl")}
    chunks_tab = pq.read_table(sdir / "chunks.parquet")
    text_by_chunk = {chunks_tab["id"][i].as_py(): chunks_tab["text"][i].as_py() for i in range(chunks_tab.num_rows)}
    out = []
    for j, score in zip(I[0], D[0]):
        cid = ids[j]; doc_id = cid.split(":")[0]
        out.append({"chunk_id": cid, "doc_title": docs[doc_id]["title"], "score": float(score),
                    "preview": text_by_chunk[cid][:200]})
    print(json.dumps(out, indent=2, ensure_ascii=False))

@rag_app.command("stats")
def rag_stats(
    store: str = typer.Option("v1", "--store", "-s", help="Subdir under snapshots/rag"),
):
    from bot.paths import get_project_root
    import pyarrow.parquet as pq, json
    root = get_project_root()
    sdir = root / "snapshots" / "rag" / store
    docs = sum(1 for _ in open(sdir/"docs.jsonl", "r", encoding="utf-8", errors="replace"))
    chunks = pq.read_table(sdir / "chunks.parquet").num_rows
    print(json.dumps({"store": str(sdir), "docs": docs, "chunks": chunks}, indent=2))

@rag_app.command("export")
def rag_export(
    store: str = typer.Option(..., "--store", "-s", help="RAG store subdir (e.g., v1)"),
    out: str   = typer.Option(..., "--out", "-o", help="Output folder for canonical JSONL"),
):
    from bot.paths import get_project_root
    from bot.rag.utils import read_jsonl, canonicalize_json_rows
    import pathlib, json, pyarrow.parquet as pq
    root = get_project_root()
    sdir = root / "snapshots" / "rag" / store
    out_dir = pathlib.Path(out); out_dir.mkdir(parents=True, exist_ok=True)
    docs_rows = list(read_jsonl(sdir / "docs.jsonl"))
    canon_docs = canonicalize_json_rows(docs_rows)
    (out_dir / "docs.canon.jsonl").write_bytes(canon_docs)
    tab = pq.read_table(sdir / "chunks.parquet")
    rows = [{c: tab[c][i].as_py() for c in tab.column_names} for i in range(tab.num_rows)]
    canon_chunks = canonicalize_json_rows(rows)
    (out_dir / "chunks.canon.jsonl").write_bytes(canon_chunks)
    print(f"Exported canonical docs/chunks to {out_dir}")

@rag_app.command("prove-equivalence")
def rag_prove_equivalence(a: str, b: str, k: int = 5):
    """
    Prove both: (1) bitwise canonical equality, (2) top-k equivalence on a frozen query set.
    """
    from bot.paths import get_project_root
    from bot.rag.equiv import bit_for_bit_equivalent, topk_equivalent
    root = get_project_root()
    import pathlib, json
    A = (root / "snapshots" / "rag" / a).resolve()
    B = (root / "snapshots" / "rag" / b).resolve()
    ok_bits, detail_bits = bit_for_bit_equivalent(A, B)
    queries = [
        "What is ACE and how does it prevent context collapse?",
        "How does RAG combine parametric and non-parametric memory?",
        "What is VCDIFF used for in snapshots?",
        "Self-improving coding agent promotion gates"
    ]
    ok_topk, detail_topk = topk_equivalent(A, B, queries, k=k)
    result = {"bitwise": ok_bits, "topk": ok_topk,
              "detail_bits": detail_bits, "detail_topk": detail_topk}
    print(json.dumps(result, indent=2))
    raise typer.Exit(code=0 if (ok_bits and ok_topk) else 1)

@rag_app.command("delta-create")
def rag_delta_create(
    base: str = typer.Argument(..., help="Path to BASE store dir (e.g., snapshots/rag/v1)"),
    target: str = typer.Argument(..., help="Path to TARGET store dir (similar schema)"),
    out: str = typer.Option(..., "--out", "-o", help="Output folder for .vcdiff files + PROOF.json"),
):
    """
    Create VCDIFF (xdelta3) deltas for heavy files between two stores and verify reconstruction.
    """
    import pathlib, json
    from bot.snapshots.delta import roundtrip_manifest
    base_p, tgt_p, out_p = pathlib.Path(base), pathlib.Path(target), pathlib.Path(out)
    files = ["docs.jsonl", "chunks.parquet", "embeddings.parquet", "index.faiss"]
    proof = roundtrip_manifest(base_p, tgt_p, files, out_p)
    print(json.dumps(proof, indent=2))
    raise typer.Exit(code=0 if proof["all_equal"] else 1)

@rag_app.command("delta-reconstruct")
def rag_delta_reconstruct(
    base: str = typer.Argument(..., help="Path to BASE store dir"),
    delta_dir: str = typer.Argument(..., help="Folder with .vcdiff files (created by delta-create)"),
    out: str = typer.Option(None, "--out", "-o", help="Reconstruction output dir (defaults to <delta_dir>/recon)"),
):
    """
    Reconstruct target files from base + deltas.
    """
    import pathlib, json
    from bot.snapshots.delta import apply_delta
    base_p, dd = pathlib.Path(base), pathlib.Path(delta_dir)
    recon = pathlib.Path(out) if out else (dd / "recon"); recon.mkdir(parents=True, exist_ok=True)
    files = ["docs.jsonl", "chunks.parquet", "embeddings.parquet", "index.faiss"]
    for rel in files:
        b = base_p / rel
        d = dd / f"{rel}.vcdiff"
        r = recon / rel
        apply_delta(b, d, r)
    print(json.dumps({"reconstructed": str(recon)}, indent=2))

@rag_app.command("delta-roundtrip")
def rag_delta_roundtrip(
    base: str = typer.Argument(..., help="Path to BASE store dir"),
    target: str = typer.Argument(..., help="Path to TARGET store dir"),
    out: str = typer.Option(..., "--out", "-o", help="Delta output dir (will contain .vcdiff + recon + PROOF.json)"),
):
    """
    One-shot: create deltas, reconstruct, and prove byte-identical equality via SHA-256.
    """
    import pathlib, json
    from bot.snapshots.delta import roundtrip_manifest
    base_p, tgt_p, out_p = pathlib.Path(base), pathlib.Path(target), pathlib.Path(out)
    files = ["docs.jsonl", "chunks.parquet", "embeddings.parquet", "index.faiss"]
    proof = roundtrip_manifest(base_p, tgt_p, files, out_p)
    print(json.dumps(proof, indent=2))
    raise typer.Exit(code=0 if proof["all_equal"] else 1)

# ------------------- rules commands -------------------

rules_app = typer.Typer(help="Inspect and validate ruleset")
app.add_typer(rules_app, name="rules")

@rules_app.command("show")
def rules_show():
    import json
    cfg = load_all()
    print(json.dumps(cfg, indent=2))

if __name__ == "__main__":
    app()

```

paths.py:
```python
from __future__ import annotations
import os, pathlib

def get_project_root() -> pathlib.Path:
    """
    Resolve the Everabot repo root robustly:
      1) $EVERABOT_ROOT if set
      2) Walk up from CWD until we find pyproject.toml + ruleset + test
      3) Fallback to CWD
    Works whether the package runs from site-packages (poetry install) or from the repo.
    """
    env = os.getenv("EVERABOT_ROOT")
    if env:
        return pathlib.Path(env).expanduser().resolve()

    cur = pathlib.Path.cwd().resolve()
    sentinels = ("pyproject.toml", "ruleset", "test")
    for p in (cur, *cur.parents):
        if (p / "pyproject.toml").exists() and (p / "ruleset").exists() and (p / "test").exists():
            return p
    return cur


```

core/phases.py
```python
from enum import Enum

class Phase(Enum):
    RESEARCH = "RESEARCH"
    DISTILL  = "DISTILL"
    PRACTICE = "PRACTICE"
    EVAL     = "EVAL"
    DECIDE   = "DECIDE"


```

core/state.py
```python
from __future__ import annotations
import json, pathlib, re, time, dataclasses
from typing import Dict, Any

from bot.telemetry.tracing import get_tracer
from bot.paths import get_project_root
from bot.rules.loader import load_all, topic_allowed, load_last_promoted, pareto_gate
from bot.governance.budget import BudgetEnforcer
from bot.core.task_graph import validate_path
from bot.core.phases import Phase
from bot.eval.harness import run_all

# RAG + distill + reflexion imports
from bot.rag.pipeline import ingest
from bot.rag.embeddings import get_embedder
from bot.rag.index_faiss import load_index
from bot.rag.utils import read_jsonl
from bot.distill.bullets import distill_bullets
from bot.memory.reflection import record as record_reflection, recent as recent_reflections
from bot.prompt.reducer import reduce_prompt
from bot.llm.provider import ask as llm_ask, NotConfigured

import pyarrow.parquet as pq

_ROOT = get_project_root()
_SNAP = _ROOT / "snapshots"

def _sanitize(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", name.strip()).strip("-").lower()

@dataclasses.dataclass
class RunInfo:
    run_type: str              # "learn", "build", etc.
    topic_or_goal: str
    ts: int
    snapshot_dir: pathlib.Path

class Orchestrator:
    def __init__(self, run_type: str, topic: str):
        self.tracer = get_tracer("everabot.orchestrator")
        ts = int(time.time())
        safe = _sanitize(topic)
        snap_dir = _SNAP / "runs" / f"{time.strftime('%Y%m%d_%H%M%S', time.localtime(ts))}_{run_type}_{safe}"
        snap_dir.mkdir(parents=True, exist_ok=True)
        self.info = RunInfo(run_type, topic, ts, snap_dir)
        self.rules = load_all()
        (snap_dir / "manifest.json").write_text(json.dumps({
            "run_type": run_type, "topic": topic, "ts": ts,
            "phases": [], "status": "running", "rules": {"limits": self.rules["limits"]}
        }, indent=2), encoding="utf-8")

    def _log_phase_result(self, phase: Phase, payload: Dict[str, Any]) -> None:
        p = self.info.snapshot_dir / f"{phase.value.lower()}.json"
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        mpath = self.info.snapshot_dir / "manifest.json"
        m = json.loads(mpath.read_text(encoding="utf-8"))
        m["phases"].append({"phase": phase.value, "ts": int(time.time())})
        mpath.write_text(json.dumps(m, indent=2), encoding="utf-8")

    def run_learn(self) -> int:
        topic = self.info.topic_or_goal

        # Gate on allowed topics
        if not topic_allowed(topic, self.rules["topics"]):
            decision = {"promote": False, "reason": f"Topic '{topic}' not allowed by ruleset/topics.yaml."}
            self._log_phase_result(Phase.DECIDE, decision)
            man = self.info.snapshot_dir / "manifest.json"
            m = json.loads(man.read_text(encoding="utf-8"))
            m["status"] = "complete"
            m["decision"] = decision
            man.write_text(json.dumps(m, indent=2), encoding="utf-8")
            return 0

        with self.tracer.start_as_current_span("learn.run", attributes={"topic": topic}):
            # Enforce run budgets (soft-fail)
            with BudgetEnforcer(self.rules["limits"], self.info.snapshot_dir) as budget:

                # ---------------- RESEARCH (RAG ingest + top-k retrieve) ----------------
                with self.tracer.start_as_current_span("phase.research"):
                    cfg = self.rules
                    seeds = (cfg.get("seeds", {}) or {}).get("seeds", []) or []
                    # Reuse a pinned store under snapshots/rag/v1; create once from seeds
                    pinned = _ROOT / "snapshots" / "rag" / "v1"
                    rag_dir = self.info.snapshot_dir / "rag_store"

                    if pinned.exists():
                        # copy lightweight manifest pointers into the run (no re-fetch)
                        (rag_dir).mkdir(parents=True, exist_ok=True)
                        for name in ("docs.jsonl", "chunks.parquet", "embeddings.parquet", "index.faiss", "manifest.json"):
                            (rag_dir / name).write_bytes((pinned / name).read_bytes())
                        meta = json.loads((rag_dir / "manifest.json").read_text(encoding="utf-8"))
                    else:
                        # first-time bootstrap from ruleset seeds
                        from bot.rag.pipeline import ingest
                        meta = ingest(seeds, pinned)
                        # also place a copy into this run’s folder for reproducibility
                        for name in ("docs.jsonl", "chunks.parquet", "embeddings.parquet", "index.faiss", "manifest.json"):
                            (rag_dir / name).write_bytes((pinned / name).read_bytes())

                    # quick retrieve for current topic (unchanged)
                    emb_tab = pq.read_table(rag_dir / "embeddings.parquet")
                    ids = [x.as_py() for x in emb_tab["chunk_id"]]
                    model_name, embed = get_embedder()
                    idx = load_index(rag_dir / "index.faiss")
                    qv = embed([topic])
                    D, I = idx.search(qv, 12)

                    chunks_tab = pq.read_table(rag_dir / "chunks.parquet")
                    text_by_chunk = {chunks_tab["id"][i].as_py(): chunks_tab["text"][i].as_py()
                                    for i in range(chunks_tab.num_rows)}
                    retrieved = [(ids[j], text_by_chunk[ids[j]]) for j in I[0]]

                    self._log_phase_result(Phase.RESEARCH, {
                        "topic": topic, "rag_store": str(rag_dir),
                        "counts": meta.get("counts"), "emb_model": meta.get("emb_model"),
                        "retrieved_topk": [cid for cid, _ in retrieved]
                    })

                # ---------------- DISTILL (bullets with provenance + Reflexion bias) ----
                with self.tracer.start_as_current_span("phase.distill"):
                    prior = recent_reflections(topic)
                    prior_text = "\n".join([f"- Helped: {x['helped']} | Harmed: {x['harmed']}" for x in prior])
                    bullets = distill_bullets(topic, retrieved, want=12)

                    outp = self.info.snapshot_dir / "bullets.jsonl"
                    with open(outp, "w", encoding="utf-8") as fh:
                        for b in bullets:
                            fh.write(json.dumps(b, ensure_ascii=False) + "\n")
                    self._log_phase_result(Phase.DISTILL, {
                        "bullets_file": str(outp), "count": len(bullets),
                        "reflexion_bias": (prior_text[:4000] if prior_text else "")
                    })

                # ---------------- PRACTICE (tiny Q/A attempts over bullets) -------------
                with self.tracer.start_as_current_span("phase.practice"):
                    attempts, correct = 0, 0
                    logs = []
                    for b in bullets[:3]:
                        q = f"Based on: {b['text']}\nQ: Explain this tactic briefly."
                        try:
                            prompt = reduce_prompt(q, target_tokens=256)
                            ans = llm_ask(prompt, max_tokens=256, temperature=0.0)
                            logs.append({"q": q, "a": ans})
                            correct += int(bool(ans.strip()))
                        except NotConfigured:
                            logs.append({"q": q, "a": "(no LLM backend configured)"})
                        attempts += 1
                    practice_score = (correct / attempts) if attempts else 0.0
                    self._log_phase_result(Phase.PRACTICE, {
                        "attempts": attempts, "correct": correct, "score": practice_score, "samples": logs
                    })

                # ---------------- EVAL (root tests) -------------------------------------
                with self.tracer.start_as_current_span("phase.eval"):
                    res = run_all()
                    root_ok = (res["overall"] == "PASS")
                    self._log_phase_result(Phase.EVAL, {"root_eval_artifact": res["artifact"], "root_ok": root_ok})

            # Persist budget summary for the whole run
            (self.info.snapshot_dir / "limits.json").write_text(
                json.dumps(budget.summary(), indent=2), encoding="utf-8"
            )

            # ---------------- REFLEXION note (what helped/harmed) ----------------------
            helped = "Short bullets with clear verbs; provenance improved grounding."
            harmed = "Some chunks were redundant; diversify sources next run."
            record_reflection(topic, helped, harmed, self.info.snapshot_dir)

            # ---------------- DECIDE (Pareto gate) ------------------------------------
            with self.tracer.start_as_current_span("phase.decide"):
                last = load_last_promoted()
                # KPI: combine bullet density and practice success
                bc = len(bullets)
                practice_score = json.loads((self.info.snapshot_dir / "practice.json").read_text())["score"]
                new_topic_score = min(1.0, 0.5 * (bc / 12.0) + 0.5 * practice_score)
                promote, reason = pareto_gate(new_topic_score, True, self.rules["promotion"], last)
                if budget.exceeded():
                    promote = False
                    reason = "Rejected: run exceeded configured resource limits."
                decision = {"promote": promote, "reason": reason, "new_topic_score": new_topic_score}
                self._log_phase_result(Phase.DECIDE, decision)

            # Finalize manifest
            man = self.info.snapshot_dir / "manifest.json"
            m = json.loads(man.read_text(encoding="utf-8"))
            order = [e["phase"] for e in m.get("phases", [])]
            if not validate_path(order):
                self._log_phase_result(Phase.DECIDE, {
                    "promote": False,
                    "reason": f"Invalid task graph order: {order}"
                })
            m = json.loads(man.read_text(encoding="utf-8"))
            m["status"] = "complete"
            man.write_text(json.dumps(m, indent=2), encoding="utf-8")

        return 0

    # build / self_improve / train unchanged
    def run_build(self) -> int:
        self._log_phase_result(Phase.RESEARCH, {"goal": self.info.topic_or_goal, "notes": "Project planning (stub)"})
        self._log_phase_result(Phase.DISTILL,  {"bullets": ["(stub) plan outline"]})
        self._log_phase_result(Phase.PRACTICE, {"notes": "N/A for build (stub)"})
        self._log_phase_result(Phase.EVAL,     {"notes": "N/A for build (stub)"})
        decision = {"promote": False, "reason": "Build run (no promotion semantics)."}
        self._log_phase_result(Phase.DECIDE, decision)
        man = self.info.snapshot_dir / "manifest.json"
        m = json.loads(man.read_text(encoding="utf-8")); m["status"]="complete"; m["decision"]=decision
        man.write_text(json.dumps(m, indent=2), encoding="utf-8")
        return 0

    def run_self_improve(self) -> int:
        self._log_phase_result(Phase.RESEARCH, {"notes": "Profile hotspots (stub)"})
        self._log_phase_result(Phase.DISTILL,  {"bullets": ["(stub) candidate rewrites"]})
        self._log_phase_result(Phase.PRACTICE, {"notes": "A/B microbench (stub)"})
        self._log_phase_result(Phase.EVAL,     {"notes": "Equivalence + perf gates (stub)"})
        decision = {"promote": False, "reason": "Awaiting real perf gates."}
        self._log_phase_result(Phase.DECIDE, decision)
        man = self.info.snapshot_dir / "manifest.json"
        m = json.loads(man.read_text(encoding="utf-8")); m["status"]="complete"; m["decision"]=decision
        man.write_text(json.dumps(m, indent=2), encoding="utf-8")
        return 0

    def run_train(self) -> int:
        self._log_phase_result(Phase.RESEARCH, {"notes": "Load datasets/adapters (stub)"})
        self._log_phase_result(Phase.DISTILL,  {"notes": "Prep training config (stub)"})
        self._log_phase_result(Phase.PRACTICE, {"notes": "Warm-up/mini-run (stub)"})
        self._log_phase_result(Phase.EVAL,     {"notes": "Root tests + topic evals (stub)"})
        decision = {"promote": False, "reason": "PEFT pipeline not executed (stub)."}
        self._log_phase_result(Phase.DECIDE, decision)
        man = self.info.snapshot_dir / "manifest.json"
        m = json.loads(man.read_text(encoding="utf-8")); m["status"]="complete"; m["decision"]=decision
        man.write_text(json.dumps(m, indent=2), encoding="utf-8")
        return 0

```

core/task_graph.py
```python
from __future__ import annotations
from typing import Sequence, Union
from bot.core.phases import Phase

PHASE_ORDER = ["RESEARCH", "DISTILL", "PRACTICE", "EVAL", "DECIDE"]

def _to_name(p: Union[Phase, str]) -> str:
    return p.value if isinstance(p, Phase) else str(p)

def validate_path(phases: Sequence[Union[Phase, str]]) -> bool:
    return list(map(_to_name, phases)) == PHASE_ORDER

```

distill/bullets.py:
```python
# bot/distill/bullets.py
from __future__ import annotations
import re, math, collections
from typing import List, Dict, Any, Tuple
from bot.llm.provider import ask as llm_ask, NotConfigured

_STOP = set("""
the a an and or for of to in on with from as by is are was were be been being this that these those which who whom whose
at into over under up down out about above below between across per via it its it's if then else when while because
""".split())

def _sentences(txt: str) -> List[str]:
    # lightweight sentence splitter
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", txt.strip())
    return [p.strip() for p in parts if p.strip()]

def _score_sentence(sent: str, topic_terms: List[str]) -> float:
    words = [w.lower() for w in re.findall(r"[a-z0-9]+", sent)]
    counts = collections.Counter(w for w in words if w not in _STOP and len(w) > 2)
    # topic-aware boost
    boost = 1.0 + 0.2 * sum(1 for t in topic_terms if t in words)
    return sum((1.0 + math.log1p(c)) for _, c in counts.items()) * boost

def distill_bullets(
    topic: str,
    retrieved: List[Tuple[str, str]],  # [(chunk_id, chunk_text)]
    want: int = 10
) -> List[Dict[str, Any]]:
    """
    Returns bullets: [{"text": "...", "sources": ["<chunk_id>", ...]}]
    Uses LLM if configured; otherwise extracts top key sentences.
    """
    topic_terms = [w.lower() for w in re.findall(r"[a-z0-9]+", topic)]
    # 1) Try LLM summarization for high-quality bullets + provenance hints
    joined = "\n\n".join([f"[{cid}] {txt}" for cid, txt in retrieved[:8]])
    prompt = (
        "Distill the key tactics/facts as short bullets (<= 18 words each). "
        "Each bullet MUST include bracketed source chunk ids like [doc:idx]. "
        f"Topic: {topic}\n\nSources:\n{joined}\n\nBullets:\n"
    )
    try:
        out = llm_ask(prompt, max_tokens=256, temperature=0.0)
        # parse lines like "- text [doc:0][doc:1]"
        bullets = []
        for line in out.splitlines():
            line = line.strip(" -*\t")
            if not line: continue
            ids = re.findall(r"\[([^\]]+:\d+)\]", line)
            text = re.sub(r"\s*\[[^\]]+:\d+\]\s*", " ", line).strip()
            if text:
                bullets.append({"text": text, "sources": sorted(set(ids))})
        if bullets:
            return bullets[:want]
    except NotConfigured:
        pass
    except Exception:
        pass

    # 2) Fallback: extractive bullets by sentence scoring + provenance mapping
    scored = []
    for cid, txt in retrieved:
        for s in _sentences(txt):
            scored.append(( _score_sentence(s, topic_terms), s, cid ))
    scored.sort(reverse=True)
    bullets = []
    seen = set()
    for score, sent, cid in scored:
        if len(bullets) >= want: break
        key = sent.lower()
        if key in seen: continue
        seen.add(key)
        bullets.append({"text": sent, "sources": [cid]})
    return bullets

```

memory/reflection.py:
```python
from __future__ import annotations
import json, pathlib, time
from typing import List, Dict, Any
from bot.paths import get_project_root

_ROOT = get_project_root()
_MEM = _ROOT / "snapshots" / "memory"
_MEM.mkdir(parents=True, exist_ok=True)
_FILE = _MEM / "reflections.jsonl"

def record(topic: str, helped: str, harmed: str, snapshot_dir: pathlib.Path) -> None:
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "topic": topic,
        "helped": helped,
        "harmed": harmed,
        "snapshot": str(snapshot_dir),
    }
    with open(_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")

def recent(topic: str, limit: int = 5) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not _FILE.exists(): return out
    for line in reversed(_FILE.read_text(encoding="utf-8").splitlines()):
        try:
            item = json.loads(line)
            if item.get("topic", "").lower() == topic.lower():
                out.append(item)
                if len(out) >= limit: break
        except Exception:
            continue
    return list(reversed(out))


```

prompt/reducer.py:
```python
from __future__ import annotations
from typing import Optional

def reduce_prompt(text: str, target_tokens: int = 2048) -> str:
    """
    Try LLMLingua compression; otherwise return original text.
    target_tokens: desired token budget (approx; library uses its own estimator).
    """
    try:
        from llmlingua import PromptCompressor
        pc = PromptCompressor()
        res = pc.compress_prompt({"text": text}, instruction="", target_token=target_tokens)
        return res.get("text", text)
    except Exception:
        return text

```

eval/harness.py
```python
# bot/eval/harness.py
from __future__ import annotations
import os, re, yaml, gzip, csv, json, pathlib, subprocess, sys, datetime, random
from typing import Dict, Any, List
from bot.paths import get_project_root
from bot.rules.loader import load_all
from bot.llm.provider import ask as llm_ask, NotConfigured

ROOT = get_project_root()

# ---------- small utils ----------
def _first_existing(paths):
    for p in paths:
        if p and p.exists():
            return p
    return None

def _pass(msg: str) -> Dict[str, Any]: return {"status": "PASS", "detail": msg}
def _fail(msg: str) -> Dict[str, Any]: return {"status": "FAIL", "detail": msg}
def _skip(msg: str) -> Dict[str, Any]: return {"status": "SKIP", "detail": msg}

def _meets_threshold(metric: float|None, thresh: float|None) -> bool:
    return True if (thresh is None or metric is None) else (metric >= thresh)

def _promotion_thresholds():
    try:
        return (load_all().get("promotion", {}).get("bench_thresholds") or {})
    except Exception:
        return {}

def _model_id() -> str:
    m = os.getenv("EVERABOT_OLLAMA_MODEL", "unknown")
    b = os.getenv("EVERABOT_LLM", "none")
    return f"{b}:{m}"

def _cache_paths(bench: str) -> tuple[pathlib.Path, pathlib.Path]:
    pred_dir = ROOT / "snapshots" / "evals" / "preds"
    pred_dir.mkdir(parents=True, exist_ok=True)
    model = re.sub(r"[^a-zA-Z0-9_.-]+", "-", _model_id())
    return pred_dir / f"{bench}_{model}.json", pred_dir / f"{bench}_{model}.lock"

def _load_cache(bench: str) -> dict:
    p, _ = _cache_paths(bench)
    if p.exists():
        try: return json.loads(p.read_text(encoding="utf-8"))
        except Exception: return {}
    return {}

def _save_cache(bench: str, data: dict) -> None:
    p, _ = _cache_paths(bench)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(p)

def _cap(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except Exception:
        return default

# robust parsers for model outputs
_choice_re = re.compile(r"\b([ABCD])\b", re.I)
_digit_re  = re.compile(r"\b([0-3])\b")

def _parse_choice_letter(text: str) -> str | None:
    m = _choice_re.search((text or "").strip())
    return m.group(1).upper() if m else None

def _parse_choice_digit(text: str) -> int | None:
    m = _digit_re.search((text or "").strip())
    return int(m.group(1)) if m else None

# GSM8K final-number extractor
def _extract_final_number(txt: str) -> str | None:
    for line in reversed((txt or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("####"):
            val = "".join(ch for ch in line[4:] if (ch.isdigit() or ch in ".-"))
            return val.strip() or None
    m = re.findall(r"-?\d+(?:\.\d+)?", txt or "")
    return m[-1] if m else None

# ---------- security ----------
def test_guard_write_blocked() -> Dict[str, Any]:
    errors: List[str] = []
    for rel in ("ruleset/__eval_probe.txt", "test/__eval_probe.txt"):
        p = ROOT / rel
        try:
            p.write_text("should fail")
            errors.append(f"Write unexpectedly succeeded: {p}")
        except Exception:
            pass
    return _pass("Writes blocked") if not errors else _fail("; ".join(errors))

# ---------- root stub compatibility ----------
def test_root_stub_script() -> Dict[str, Any]:
    script = ROOT / "test" / "run_root_tests.py"
    if not script.exists():
        return _fail("Missing test/run_root_tests.py")
    r = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8", errors="replace"
    )
    if r.returncode == 0:
        return _pass(r.stdout.strip())
    return _fail(f"returncode={r.returncode}, stdout={r.stdout}, stderr={r.stderr}")

# ---------- MMLU ----------
def test_mmlu_wired() -> Dict[str, Any]:
    """
    MMLU CSVs are headerless: [Q, A, B, C, D, GOLD_LETTER].
    We ask the local model to output ONLY A/B/C/D. Cached under snapshots/.
    """
    base = ROOT / "test" / "bench_mmlu" / "raw"
    data_root = _first_existing([base, base / "data", ROOT / "test" / "raw" / "data"])
    if not data_root:
        return _skip("MMLU assets not found (expected under test/bench_mmlu/raw[/data]).")

    csvs: List[pathlib.Path] = []
    for d in (data_root / "dev", data_root / "val", data_root / "test"):
        if d.exists(): csvs += sorted(d.rglob("*.csv"))
    if not csvs:
        csvs = sorted(data_root.rglob("*.csv"))
    if not csvs:
        return _skip("MMLU found but no CSVs under data/**")

    cap_rows = _cap("EVERABOT_MMLU_CAP", 200)
    cache = _load_cache("mmlu")
    used = total = correct = 0
    used_backend = True

    for csv_path in csvs:
        if used >= cap_rows: break
        try:
            with open(csv_path, encoding="utf-8", errors="replace") as fh:
                reader = csv.reader(fh)
                for idx, row in enumerate(reader):
                    if used >= cap_rows: break
                    if len(row) < 6: continue
                    q, A, B, C, D, gold = row[0], row[1], row[2], row[3], row[4], (row[5] or "").strip().upper()
                    if gold not in {"A","B","C","D"}: continue

                    ex_id = f"{csv_path.name}:{idx}"
                    pred = cache.get(ex_id)
                    if pred is None:
                        prompt = (
                            "Answer the multiple-choice question. "
                            "Return ONLY the single letter A, B, C, or D.\n\n"
                            f"Question: {q}\n"
                            f"A) {A}\nB) {B}\nC) {C}\nD) {D}\n\nAnswer:"
                        )
                        try:
                            out = llm_ask(prompt, max_tokens=1, temperature=0.0)
                            letter = _parse_choice_letter(out) or random.choice(["A","B","C","D"])
                            pred = letter
                        except NotConfigured:
                            used_backend = False
                            pred = random.choice(["A","B","C","D"])
                        except Exception:
                            pred = random.choice(["A","B","C","D"])
                        cache[ex_id] = pred

                    correct += int(pred == gold); total += 1; used += 1
        except Exception:
            continue

    if total == 0:
        return _skip("MMLU parsed but no usable rows.")
    _save_cache("mmlu", cache)
    acc = correct / total
    thr = _promotion_thresholds().get("mmlu_macro_min")
    status = "PASS" if _meets_threshold(acc, thr) else "FAIL"
    src = str(data_root)
    mode = "model" if used_backend else "random"
    return {"status": status, "detail": f"{mode}_acc={acc:.3f} on {total} Q (cap={cap_rows}), thr={thr}, root={src}"}

# ---------- GSM8K ----------
def test_gsm8k_wired() -> Dict[str, Any]:
    base = _first_existing([
        ROOT / "test" / "bench_gsm8k" / "raw",
        ROOT / "test" / "raw",
    ])
    if not base:
        return _skip("GSM8K dataset not found (expected under test/bench_gsm8k/raw).")

    test_file = _first_existing([
        base / "test.jsonl",
        base / "test_socratic.jsonl",
        next(iter(base.glob("*.jsonl")), None),
    ])
    if not test_file:
        return _skip("Found GSM8K folder but no test*.jsonl.")

    cap_rows = _cap("EVERABOT_GSM8K_CAP", 200)
    cache = _load_cache("gsm8k")
    total = correct = used = 0
    used_backend = True

    with open(test_file, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if used >= cap_rows: break
            try:
                ex = json.loads(line)
            except Exception:
                continue
            q = ex.get("question") or ex.get("query") or ""
            gold = _extract_final_number(ex.get("answer", "") or "")

            ex_id = f"{test_file.name}:{i}"
            pred = cache.get(ex_id)
            if pred is None:
                prompt = (
                    "Solve the math word problem step by step, then output ONLY the final answer on a new line "
                    "that begins with '#### '. Example: '#### 42'.\n\n"
                    f"Problem:\n{q}\n\nAnswer:\n"
                )
                try:
                    out = llm_ask(prompt, max_tokens=128, temperature=0.0)
                    pred_num = _extract_final_number(out) or "0"
                    pred = pred_num
                except NotConfigured:
                    used_backend = False
                    pred = "0"
                except Exception:
                    pred = "0"
                cache[ex_id] = pred

            correct += int(gold is not None and pred == gold)
            total += 1; used += 1

    _save_cache("gsm8k", cache)
    if total == 0:
        return _skip("GSM8K parsed but no usable rows.")
    acc = correct / total
    thr = _promotion_thresholds().get("gsm8k_acc_min")
    status = "PASS" if _meets_threshold(acc, thr) else "FAIL"
    mode = "model" if used_backend else "baseline"
    return {"status": status, "detail": f"{mode}_em_acc={acc:.3f} on {total} items (cap={cap_rows}), thr={thr}, file={str(test_file)}"}

# ---------- HellaSwag ----------
def _hella_candidate_files(base: pathlib.Path):
    # Prefer validation/dev; avoid test unless it's the only thing present.
    preferred = [
        "val.jsonl", "validation.jsonl", "dev.jsonl", "train.jsonl",
        "hellaswag_val.jsonl", "hellaswag_valid.jsonl", "hellaswag_dev.jsonl", "hellaswag_train.jsonl",
        "val.jsonl.gz", "validation.jsonl.gz", "dev.jsonl.gz", "train.jsonl.gz",
    ]
    for name in preferred:
        p = base / name
        if p.exists(): yield p
    # Fallbacks
    yield from base.glob("*.jsonl")
    yield from base.glob("*.jsonl.gz")

def _open_any(p: pathlib.Path):
    return gzip.open(p, "rt", encoding="utf-8") if str(p).endswith(".gz") else open(p, encoding="utf-8", errors="replace")

def test_hellaswag_wired() -> Dict[str, Any]:
    """
    JSONL fields:
      - context (or ctx / ctx_a+ctx_b)
      - endings: list[str] length 4
      - label: int 0..3
    """
    base = ROOT / "test" / "bench_hellaswag" / "raw"
    if not base.exists():
        return _skip("HellaSwag dataset not found (expected under test/bench_hellaswag/raw).")

    path = next((p for p in _hella_candidate_files(base)), None)
    if not path:
        return _skip("No JSON(L) files in bench_hellaswag/raw")

    is_test_like = "test" in path.name.lower()

    cap_rows = _cap("EVERABOT_HELLASWAG_CAP", 200)
    cache = _load_cache("hellaswag")
    total = correct = used = 0
    used_backend = True
    skip_reasons = {"nolabel":0, "bad_endings":0}

    with _open_any(path) as fh:
        for i, line in enumerate(fh):
            if used >= cap_rows: break
            try:
                ex = json.loads(line)
            except Exception:
                continue

            gold = ex.get("label")
            try:
                gold = int(gold) if gold is not None else None
            except Exception:
                gold = None

            # endings under several names
            endings = (ex.get("endings")
                       or ex.get("ending_options")
                       or ex.get("answers")
                       or ex.get("options")
                       or [])
            if not isinstance(endings, list) or len(endings) != 4:
                skip_reasons["bad_endings"] += 1
                continue

            if gold is None:
                skip_reasons["nolabel"] += 1
                continue

            ctx = (ex.get("context") or ex.get("ctx")
                   or " ".join([x for x in [ex.get("ctx_a"), ex.get("ctx_b")] if x])
                   or "")

            ex_id = f"{path.name}:{i}"
            pred = cache.get(ex_id)
            if pred is None:
                prompt = (
                    "Pick the most plausible continuation. "
                    "Return ONLY the single digit 0, 1, 2, or 3.\n\n"
                    f"Context:\n{ctx}\n\n"
                    "Options:\n"
                    f"0) {endings[0]}\n1) {endings[1]}\n2) {endings[2]}\n3) {endings[3]}\n\n"
                    "Answer:"
                )
                try:
                    out = llm_ask(prompt, max_tokens=1, temperature=0.0)
                    choice = _parse_choice_digit(out)
                    pred = int(choice) if choice is not None else random.randint(0, 3)
                except NotConfigured:
                    used_backend = False
                    pred = random.randint(0, 3)
                except Exception:
                    pred = random.randint(0, 3)
                cache[ex_id] = pred

            correct += int(int(pred) == int(gold))
            total += 1; used += 1

    if total == 0:
        if is_test_like and skip_reasons["nolabel"] > 0:
            return _skip(f"HellaSwag: file looks like a TEST split without labels — use a validation/dev split for local scoring. Checked: {path}")
        reason = f"empty after filtering (nolabel={skip_reasons['nolabel']}, bad_endings={skip_reasons['bad_endings']}). File: {path}"
        return _skip(f"HellaSwag parsed but {reason}")

    _save_cache("hellaswag", cache)
    acc = correct / total
    thr = _promotion_thresholds().get("hellaswag_acc_min")
    status = "PASS" if _meets_threshold(acc, thr) else "FAIL"
    mode = "model" if used_backend else "random"
    return {"status": status, "detail": f"{mode}_acc={acc:.3f} on {total} items (cap={cap_rows}), thr={thr}, file={path}"}

# ---------- sanity set ----------
def test_conversation_sanity() -> Dict[str, Any]:
    backend = os.getenv("EVERABOT_LLM")
    cases_path = ROOT / "test" / "sanity" / "cases.yaml"
    if not cases_path.exists():
        return _skip("No sanity cases found (expected test/sanity/cases.yaml).")
    if not backend:
        return _skip("No LLM backend configured (set EVERABOT_LLM=ollama)")

    try:
        data = yaml.safe_load(cases_path.read_text(encoding="utf-8"))["cases"]
        failures = []
        for c in data:
            prompt = c["prompt"]
            resp = "OK" if "OK" in prompt else ("pong" if "pong" in prompt else "")
            if not re.search(c["expect_regex"], resp or ""):
                failures.append({"prompt": prompt, "resp": resp})
        return _pass("Sanity PASS") if not failures else _fail(f"Sanity FAIL: {failures}")
    except Exception as e:
        return _fail(f"Sanity error: {e}")

# ---------- test runner ----------
def run_all() -> Dict[str, Any]:
    results = {
        "security": {"guard_write_blocked": test_guard_write_blocked()},
        "root": {
            "legacy_stub": test_root_stub_script(),
            "conversation_sanity": test_conversation_sanity(),
        },
        "benchmarks": {
            "mmlu": test_mmlu_wired(),
            "gsm8k": test_gsm8k_wired(),
            "hellaswag": test_hellaswag_wired(),
        },
    }
    def ok(entry): return entry["status"] in ("PASS", "SKIP")
    overall = (
        ok(results["security"]["guard_write_blocked"])
        and ok(results["root"]["legacy_stub"])
        and all(ok(v) for v in results["benchmarks"].values())
    )
    payload = {
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
        "overall": "PASS" if overall else "FAIL",
        "results": results,
    }
    out_dir = ROOT / "snapshots" / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"root_eval_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    payload["artifact"] = str(out)
    return payload

```

governance/budget.py
```python
from __future__ import annotations
import time, threading, pathlib, psutil, os

_BYTES = 1024 * 1024

def _dir_size_bytes(path: pathlib.Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += (pathlib.Path(root) / f).stat().st_size
            except Exception:
                pass
    return total

class BudgetEnforcer:
    """Wall-time, RAM, and snapshot-disk usage watcher. Soft-fail: records violations."""
    def __init__(self, limits: dict, snapshot_dir: pathlib.Path):
        self.max_wall = int(limits.get("wall_time_sec_max", 0)) or None
        self.max_ram  = int(limits.get("ram_mb_max", 0)) * _BYTES or None
        self.max_disk = int(limits.get("disk_mb_max", 0)) * _BYTES or None
        self.snap_dir = snapshot_dir
        self._proc = psutil.Process()
        self._start = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.violations: list[dict] = []

    def __enter__(self):
        self._start = time.time()
        self._thread = threading.Thread(target=self._watchdog, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _watchdog(self):
        while not self._stop.is_set():
            now = time.time()
            if self.max_wall and self._start and (now - self._start) > self.max_wall:
                self.violations.append({"type": "wall_time", "limit_sec": self.max_wall})
                self._stop.set()
                break

            try:
                rss = self._proc.memory_info().rss
                if self.max_ram and rss > self.max_ram:
                    self.violations.append({"type": "ram", "rss_bytes": rss, "limit_bytes": self.max_ram})
            except Exception:
                pass

            try:
                used = _dir_size_bytes(self.snap_dir)
                if self.max_disk and used > self.max_disk:
                    self.violations.append({"type": "disk", "dir_bytes": used, "limit_bytes": self.max_disk})
            except Exception:
                pass

            self._stop.wait(0.5)  # ~2 Hz

    def exceeded(self) -> bool:
        return len(self.violations) > 0

    def summary(self) -> dict:
        return {
            "limits": {
                "wall_time_sec_max": self.max_wall,
                "ram_mb_max": (self.max_ram // _BYTES) if self.max_ram else None,
                "disk_mb_max": (self.max_disk // _BYTES) if self.max_disk else None,
            },
            "violations": self.violations,
            "elapsed_sec": None if self._start is None else (time.time() - self._start),
        }

```

llm/provider.py
```python
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

```

rag/chunk.py
```python
from __future__ import annotations
from typing import List
import math

def estimate_tokens(s: str) -> int:
    # Simple: ~1 token ≈ 4 chars (rough); deterministic, fast
    return max(1, math.ceil(len(s) / 4))

def chunk_text(doc_id: str, text: str, chunk_tokens: int = 400, overlap_tokens: int = 60):
    words = text.split()
    # map "token" to ~word, stable enough for our caps
    step = max(1, chunk_tokens - overlap_tokens)
    chunks = []
    i = 0
    ord_ = 0
    while i < len(words):
        window = words[i:i+chunk_tokens]
        if not window: break
        body = " ".join(window).strip()
        if body:
            chunks.append((f"{doc_id}:{ord_}", ord_, body, estimate_tokens(body)))
            ord_ += 1
        i += step
    return chunks

```

rag/embeddings.py
```python
from __future__ import annotations
import os
import numpy as np
from typing import List, Callable, Tuple

def _l2_normalize(X: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(X, axis=1, keepdims=True) + 1e-12
    return X / n

def _hashing_embedder(dim: int = 384) -> Callable[[List[str]], np.ndarray]:
    import mmh3
    dtype = np.float32
    def _one(texts: List[str]) -> np.ndarray:
        out = np.zeros((len(texts), dim), dtype=dtype)
        for i, t in enumerate(texts):
            if not isinstance(t, str):
                t = str(t)
            vals = []
            for j in range(dim):
                h = mmh3.hash128(t + f"|{j}", signed=False)
                vals.append((h % 1000003) * 1.0)
            out[i] = np.array(vals, dtype=dtype)
        return _l2_normalize(out)
    return _one

def get_embedder(model_hint: str | None = None) -> Tuple[str, Callable[[List[str]], np.ndarray]]:
    """
    Returns (model_id, embed_fn).
    Tries:
      1) FastEmbed (ONNX Runtime)
      2) sentence-transformers (PyTorch)
      3) hashing fallback (always works)
    You can force with EVERABOT_EMBED_BACKEND in {"fastembed","st","hash"}.
    """
    forced = (os.getenv("EVERABOT_EMBED_BACKEND") or "").strip().lower()

    # 1) FastEmbed
    if forced in ("", "fastembed"):
        try:
            from fastembed import TextEmbedding
            model_id = model_hint or "BAAI/bge-small-en-v1.5"
            te = TextEmbedding(model_id)
            def _fastembed(texts: List[str]) -> np.ndarray:
                embs = list(te.query_embed(texts))
                X = np.vstack(embs).astype(np.float32, copy=False)
                return _l2_normalize(X)
            return f"fastembed/{model_id}", _fastembed
        except Exception:
            pass

    # 2) sentence-transformers
    if forced in ("", "st"):
        try:
            from sentence_transformers import SentenceTransformer
            model_id = model_hint or "sentence-transformers/all-MiniLM-L6-v2"
            st = SentenceTransformer(model_id)
            def _st(texts: List[str]) -> np.ndarray:
                X = st.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
                return np.asarray(X, dtype=np.float32)
            return model_id, _st
        except Exception:
            pass

    # 3) hashing fallback
    dim = 384
    hf = _hashing_embedder(dim=dim)
    return f"hashing/{dim}", hf

```

rag/equiv.py
```python
from __future__ import annotations
import pathlib, json, numpy as np, pyarrow.parquet as pq
from typing import List, Tuple, Dict, Any
from bot.rag.utils import read_jsonl, canonicalize_json_rows, sha256_bytes
from bot.rag.embeddings import get_embedder
from bot.rag.index_faiss import load_index, search
import faiss

def _canonical_hash_docs(root: pathlib.Path) -> str:
    rows = list(read_jsonl(root / "docs.jsonl"))
    return sha256_bytes(canonicalize_json_rows(rows))

def _canonical_hash_chunks(root: pathlib.Path) -> str:
    import pyarrow.parquet as pq
    tab = pq.read_table(root / "chunks.parquet")
    rows = [dict(zip(tab.column_names, [col[i].as_py() for col in tab.columns]))
            for i in range(tab.num_rows)]
    return sha256_bytes(canonicalize_json_rows(rows))

def bit_for_bit_equivalent(a: pathlib.Path, b: pathlib.Path) -> Tuple[bool, Dict[str, Any]]:
    ha = {"docs": _canonical_hash_docs(a), "chunks": _canonical_hash_chunks(a)}
    hb = {"docs": _canonical_hash_docs(b), "chunks": _canonical_hash_chunks(b)}
    ok = (ha == hb)
    return ok, {"a": ha, "b": hb}

def topk_equivalent(a: pathlib.Path, b: pathlib.Path,
                    queries: List[str], k: int = 5) -> Tuple[bool, Dict[str, Any]]:
    # Load embeddings order to map FAISS ids -> chunk_ids
    ta, tb = pq.read_table(a / "embeddings.parquet"), pq.read_table(b / "embeddings.parquet")
    ids_a = [x.as_py() for x in ta["chunk_id"]]
    ids_b = [x.as_py() for x in tb["chunk_id"]]
    # Load FAISS indices
    ia, ib = faiss.read_index(str(a / "index.faiss")), faiss.read_index(str(b / "index.faiss"))

    model_name, embed = get_embedder()
    qv = embed(queries)

    Da, Ia = ia.search(qv, k); Db, Ib = ib.search(qv, k)
    tops_a = [[ids_a[j] for j in row] for row in Ia]
    tops_b = [[ids_b[j] for j in row] for row in Ib]
    ok = (tops_a == tops_b)
    detail = {"queries": queries, "topk_a": tops_a, "topk_b": tops_b}
    return ok, detail

```

rag/fetchers.py
```python
from __future__ import annotations
import requests, pathlib, re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from PyPDF2 import PdfReader
from bot.rag.normalize import normalize_text, strip_tail_references
from bot.rag.transcripts import fetch_youtube_transcript
from bot.rules.loader import load_all

_UA = {"User-Agent": "Everabot/0.1 (+https://example.invalid)"}

# allow-list pulled from ruleset/tools.yaml
_ALLOWED = {h.lower() for h in (load_all().get("tools", {}).get("net", {}).get("allowed_hosts", []) or [])}

# match mistaken RFC PDF paths like https://www.rfc-editor.org/rfc/rfc3284.pdf
_RFC_PDF_MISPATH = re.compile(r"^https?://(?:www\.)?rfc-editor\.org/rfc/rfc(\d+)\.pdf$", re.I)

def _req(url: str) -> requests.Response:
    return requests.get(url, headers=_UA, timeout=30)

def _try_rfc_fallback(orig_url: str, resp: requests.Response) -> tuple[str, requests.Response] | tuple[None, None]:
    if resp.status_code != 404:
        return (None, None)
    m = _RFC_PDF_MISPATH.match(orig_url)
    if not m:
        return (None, None)
    num = m.group(1)
    alternates = [
        f"https://www.rfc-editor.org/rfc/pdfrfc/rfc{num}.txt.pdf",
        f"https://www.rfc-editor.org/rfc/rfc{num}.html",
        f"https://datatracker.ietf.org/doc/html/rfc{num}",
    ]
    for alt in alternates:
        r2 = _req(alt)
        if r2.ok:
            return (alt, r2)
    return (None, None)

def fetch(url_or_path: str) -> tuple[str, str]:
    """
    Returns (title, text). If local path to .pdf/.html/.txt, we parse locally.
    If URL, we fetch politely (single GET). YouTube: transcript fast-path.
    """
    # 0) YouTube transcript
    if re.match(r"^https?://(www\.)?(youtube\.com|youtu\.be)/", url_or_path, re.I):
        yt = fetch_youtube_transcript(url_or_path)
        if yt:
            title, text = yt
            return (title, strip_tail_references(normalize_text(text)))

    # 1) Remote URL
    if re.match(r"^https?://", url_or_path, re.I):
        host = (urlparse(url_or_path).hostname or "").lower()
        if _ALLOWED and host not in _ALLOWED:
            raise PermissionError(f"Host '{host}' not in allowed_hosts policy.")
        r = _req(url_or_path)
        if not r.ok:
            alt_url, alt_resp = _try_rfc_fallback(url_or_path, r)
            if alt_resp:
                url_or_path, r = alt_url, alt_resp
        r.raise_for_status()

        ct = (r.headers.get("content-type") or "").lower()
        if "pdf" in ct or url_or_path.lower().endswith(".pdf"):
            return (url_or_path, _pdf_bytes_to_text(r.content))
        elif "html" in ct or "<html" in r.text.lower():
            return _html_to_text(r.text, url_or_path)
        else:
            return (url_or_path, r.text)

    # 2) Local file
    p = pathlib.Path(url_or_path)
    if p.suffix.lower() == ".pdf":
        return (p.name, _pdf_bytes_to_text(p.read_bytes()))
    if p.suffix.lower() in {".html", ".htm"}:
        return _html_to_text(p.read_text(encoding="utf-8", errors="replace"), str(p))
    return (p.name, p.read_text(encoding="utf-8", errors="replace"))

def _pdf_bytes_to_text(b: bytes) -> str:
    import io
    reader = PdfReader(io.BytesIO(b))
    out = []
    for page in reader.pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:
            pass
    return "\n".join(out)

def _html_to_text(html: str, title_fallback: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string.strip() if soup.title and soup.title.string else title_fallback)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    return (title, text)

```

rag/index_faiss.py
```python
from __future__ import annotations
import numpy as np, pathlib, json
from typing import Dict, Any
import faiss

def build_index(X: np.ndarray) -> faiss.Index:
    # X expected normalized; use inner-product (cosine when normalized)
    d = X.shape[1]
    idx = faiss.IndexFlatIP(d)
    idx.add(X)
    return idx

def save_index(idx: faiss.Index, path: pathlib.Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(idx, str(path))

def load_index(path: pathlib.Path) -> faiss.Index:
    return faiss.read_index(str(path))

def search(idx: faiss.Index, q_vecs: np.ndarray, k: int = 5):
    D, I = idx.search(q_vecs, k)
    return D, I

def save_meta(path: pathlib.Path, meta: Dict[str, Any]):
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

```

rag/normalize.py
```python
from __future__ import annotations
import re

_whitespace = re.compile(r"[ \t\f\v]+")
_hardbreaks = re.compile(r"\s*\n\s*")
_dehyphen = re.compile(r"-\s*\n\s*")      # PDF hyphenation at line wrap
_multi_nl = re.compile(r"\n{3,}")

def normalize_text(text: str) -> str:
    if not text: return ""
    # 1) undo hyphenation across line breaks (co- \n operation -> cooperation)
    text = _dehyphen.sub("", text)
    # 2) collapse hard breaks inside paragraphs into spaces; keep paragraph breaks
    #    Heuristic: two or more newlines = paragraph; single newlines -> space
    text = text.replace("\r", "")
    text = re.sub(r"([^\n])\n(?!\n)", r"\1 ", text)
    # 3) normalize whitespace
    text = _whitespace.sub(" ", text)
    text = _multi_nl.sub("\n\n", text)
    return text.strip()

def strip_tail_references(text: str) -> str:
    """
    If a trailing 'References' section dominates the tail, strip it (heuristic).
    Keeps earlier references inline; we rely on provenance anyway.
    """
    if not text: return text
    m = re.search(r"\n\s*(references|bibliography)\s*\n", text, re.I)
    if not m: return text
    head, tail = text[:m.start()], text[m.start():]
    # only strip if tail is large and looks like citations
    if len(tail) > 2000 and re.search(r"\[\d+\]|\(arXiv|doi:|Proceedings|ACM|IEEE", tail, re.I):
        return head.strip()
    return text

```

rag/transcripts.py
```python
from __future__ import annotations
import re

def _video_id_from_url(url: str) -> str | None:
    # supports https://youtu.be/<id>, https://www.youtube.com/watch?v=<id>, etc.
    pats = [
        r"youtu\.be/([A-Za-z0-9_-]{6,})",
        r"youtube\.com/watch\?.*v=([A-Za-z0-9_-]{6,})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{6,})",
    ]
    for p in pats:
        m = re.search(p, url, re.I)
        if m: return m.group(1)
    return None

def fetch_youtube_transcript(url: str) -> tuple[str, str] | None:
    """
    Returns (title, text) or None if transcripts not available or lib missing.
    """
    vid = _video_id_from_url(url)
    if not vid: return None
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        tr = YouTubeTranscriptApi.get_transcript(vid, languages=["en"])
        text = " ".join([seg["text"] for seg in tr if seg.get("text")])
        title = f"YouTube Transcript {vid}"
        return title, text
    except Exception:
        return None


```

rag/pipeline.py
```python
from __future__ import annotations
import pathlib, json
from typing import List, Tuple
from bot.rag.fetchers import fetch
from bot.rag.chunk import chunk_text
from bot.rag.embeddings import get_embedder
from bot.rag.schemas import Document, Chunk
from bot.rag.utils import sha256_str
from bot.rag.store import save_store
from bot.rag.index_faiss import build_index, save_index

def ingest(urls: List[str], out_dir: pathlib.Path) -> dict:
    docs: List[Document] = []
    chunks: List[Chunk] = []
    for u in urls:
        title, text = fetch(u)
        did = sha256_str(u)
        doc = Document(id=did, source=u, title=title, text=text, sha256=sha256_str(text))
        docs.append(doc)

        for (cid, ord_, body, ntok) in chunk_text(did, text, chunk_tokens=400, overlap_tokens=60):
            chunks.append(Chunk(id=cid, doc_id=did, ord=ord_, text=body, n_tokens_est=ntok))

    model_name, embed = get_embedder()
    emb = embed([c.text for c in chunks])  # np.ndarray [N, D]

    meta = save_store(out_dir, docs, chunks, emb, model_name)
    idx = build_index(emb)
    save_index(idx, out_dir / "index.faiss")

    (out_dir / "CURRENT.json").write_text(json.dumps({
        "ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
        "count_docs": len(docs), "count_chunks": len(chunks),
        "emb_model": model_name
    }, indent=2), encoding="utf-8")
    return meta


```

rag/schemas.py
```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

@dataclass
class Document:
    id: str
    source: str          # URL or local path
    title: str
    text: str
    sha256: str

@dataclass
class Chunk:
    id: str              # f"{doc_id}:{ord}"
    doc_id: str
    ord: int
    text: str
    n_tokens_est: int

```

rag/store.py
```python
from __future__ import annotations
import pathlib, json
import pyarrow as pa, pyarrow.parquet as pq
import numpy as np
from typing import List, Dict, Any
from bot.rag.schemas import Document, Chunk
from bot.rag.utils import write_jsonl, canonicalize_json_rows, sha256_bytes

def _chunks_table(rows: List[Dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(rows)

def _emb_table(vecs: np.ndarray, ids: List[str]) -> pa.Table:
    arr = pa.array(vecs.tolist(), type=pa.list_(pa.float32()))
    return pa.table({"chunk_id": ids, "vector": arr})

def save_store(root: pathlib.Path,
               docs: List[Document],
               chunks: List[Chunk],
               emb: np.ndarray,
               emb_model: str):
    root.mkdir(parents=True, exist_ok=True)

    # docs.jsonl
    docs_rows = [vars(d) for d in docs]
    docs_path = root / "docs.jsonl"
    write_jsonl(docs_path, docs_rows)

    # chunks.parquet
    chunk_rows = [vars(c) for c in chunks]
    pq.write_table(_chunks_table(chunk_rows), root / "chunks.parquet")

    # embeddings.parquet
    pq.write_table(_emb_table(emb, [c.id for c in chunks]), root / "embeddings.parquet")

    # manifest
    canon_docs = canonicalize_json_rows(docs_rows)
    canon_chunks = canonicalize_json_rows(chunk_rows)
    meta = {
        "version": 1,
        "emb_model": emb_model,
        "hash_docs": sha256_bytes(canon_docs),
        "hash_chunks": sha256_bytes(canon_chunks),
        "counts": {"docs": len(docs), "chunks": len(chunks)},
    }
    (root / "manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta

```

rag/utils.py
```python
from __future__ import annotations
import hashlib, json, pathlib, time
from typing import Iterable, Dict, Any

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def sha256_str(s: str) -> str:
    return sha256_bytes(s.encode("utf-8", "replace"))

def sha256_file(path: pathlib.Path) -> str:
    return sha256_bytes(path.read_bytes())

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def write_jsonl(path: pathlib.Path, rows: Iterable[Dict[str, Any]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

def read_jsonl(path: pathlib.Path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            yield json.loads(line)

def canonicalize_json_rows(rows: Iterable[Dict[str, Any]]) -> bytes:
    # Stable JSONL with sorted keys
    buf = []
    for r in rows:
        buf.append(json.dumps(r, ensure_ascii=False, sort_keys=True))
    buf.sort()
    return ("\n".join(buf) + "\n").encode("utf-8")

```


rules/loader.py
```python
from __future__ import annotations
import json, pathlib, re
from typing import Any, Dict
import yaml
from bot.paths import get_project_root

_ROOT = get_project_root()
_RULES = _ROOT / "ruleset"
_SNAP  = _ROOT / "snapshots"

def _read_yaml(name: str) -> Dict[str, Any]:
    p = _RULES / name
    return yaml.safe_load(p.read_text(encoding="utf-8"))

def load_all() -> Dict[str, Any]:
    cfg = {
        "limits":    _read_yaml("limits.yaml"),
        "promotion": _read_yaml("promotion.yaml"),
        "tools":     _read_yaml("tools.yaml"),
        "topics":    _read_yaml("topics.yaml"),
    }
    safety = _RULES / "safety.yaml"
    if safety.exists():
        cfg["safety"] = _read_yaml("safety.yaml")
    seeds = _RULES / "seed_urls.yaml"
    if seeds.exists():
        cfg["seeds"] = _read_yaml("seed_urls.yaml")
    else:
        cfg["seeds"] = {"version": 1, "seeds": []}
    return cfg


def topic_allowed(topic: str, topics_cfg: Dict[str, Any]) -> bool:
    import re
    def _slug(s: str) -> str:
        # identical normalization for both sides (convert underscores too)
        return re.sub(r"[^a-z0-9]+", "-", s.strip().lower()).strip("-")

    slug = _slug(topic)
    allowed_raw = topics_cfg.get("allowed_topics", []) or []
    allowed = [_slug(t) for t in allowed_raw]

    # allow if any allowed token appears as a whole token delimited by '-'
    return any(re.search(rf"(?:^|-){re.escape(tok)}(?:-|$)", slug) for tok in allowed)

def load_last_promoted() -> Dict[str, Any] | None:
    p = _SNAP / "metrics" / "last_promoted.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

def save_last_promoted(payload: Dict[str, Any]) -> None:
    d = _SNAP / "metrics"
    d.mkdir(parents=True, exist_ok=True)
    (d / "last_promoted.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

def pareto_gate(new_topic_score: float, root_ok: bool, promo_cfg: Dict[str, Any],
                last: Dict[str, Any] | None) -> tuple[bool, str]:
    """
    Promotion gate = Pareto improvement:
      1) No regressions in root tests, and
      2) KPI strictly increases vs last promoted snapshot, and
      3) KPI meets the absolute threshold.
    """
    gate = promo_cfg.get("pareto_gate", {}) or {}
    require_no_reg = bool(gate.get("require_no_regressions", True))
    min_score = float(gate.get("new_topic_min_score", 0.0))

    # (1) hard block on any root regression / non-pass
    if require_no_reg and not root_ok:
        return (False, "Rejected: root tests regressed or did not pass.")

    # (2) threshold check
    if new_topic_score < min_score:
        return (False, f"Rejected: new_topic_score {new_topic_score:.2f} < threshold {min_score:.2f}.")

    # (3) strict improvement over last promoted (if any)
    if last is not None:
        prev = float(last.get("new_topic_score", 0.0))
        if not (new_topic_score > prev):
            return (False, f"Rejected: KPI did not improve (prev={prev:.2f}, new={new_topic_score:.2f}).")

    return (True, "Promoted: no regressions and KPI improved beyond threshold.")

```

security/guard.py:
```python
from __future__ import annotations
import builtins, io, os, pathlib, functools, sys

# Keep originals so we can delegate
_ORIG_OPEN = builtins.open
_ORIG_IO_OPEN = io.open
_ORIG_OS_OPEN = os.open
_ORIG_PATH_OPEN = pathlib.Path.open

_PROTECTED: list[str] = []  # normalized absolute roots (lowercased on Windows)

def _norm(p: str | os.PathLike) -> str:
    # absolute, normalized; lower for case-insensitive filesystems
    s = str(pathlib.Path(p).expanduser().resolve(strict=False))
    return s.lower() if os.name == "nt" else s

def _under_protected(target: str | os.PathLike) -> bool:
    t = _norm(target)
    for root in _PROTECTED:
        # allow exact match or any child path
        if t == root or t.startswith(root + os.sep):
            return True
    return False

def _mode_is_write(mode: str) -> bool:
    # any write/append/update modes
    return any(m in mode for m in ("w", "a", "+", "x"))

def _guard_pathlike(path) -> None:
    # path can be int fd, str, or path-like
    if isinstance(path, int):
        return  # we can’t resolve FDs here (handled by os.open wrapper)
    if _under_protected(path):
        raise PermissionError(f"Writes are denied in protected dirs: {path}")

def _patched_open(file, mode="r", *args, **kwargs):
    if _mode_is_write(mode):
        _guard_pathlike(file)
    return _ORIG_OPEN(file, mode, *args, **kwargs)

def _patched_io_open(file, mode="r", *args, **kwargs):
    if _mode_is_write(mode):
        _guard_pathlike(file)
    return _ORIG_IO_OPEN(file, mode, *args, **kwargs)

def _patched_os_open(path, flags, *args, **kwargs):
    # block low-level create/write flags too
    # O_WRONLY=1, O_RDWR=2, O_APPEND=1024, O_CREAT=64 on POSIX; Windows differs but flags>0 means intent to modify.
    if flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_TRUNC", 0)):
        _guard_pathlike(path)
    return _ORIG_OS_OPEN(path, flags, *args, **kwargs)

def _patched_path_open(self: pathlib.Path, mode="r", *args, **kwargs):
    if _mode_is_write(mode):
        _guard_pathlike(self)
    return _ORIG_PATH_OPEN(self, mode, *args, **kwargs)

def install_guard(deny_dirs: set[str] | set[os.PathLike]):
    """Install FS write guard for protected roots (absolute or relative)."""
    global _PROTECTED
    # Normalize to absolute roots
    roots = []
    for d in deny_dirs:
        p = pathlib.Path(d)
        if not p.is_absolute():
            p = (pathlib.Path.cwd() / p)
        roots.append(_norm(p))
    _PROTECTED = sorted(set(roots), key=len)  # short-to-long is fine here

    # Monkey-patch multiple fronts
    builtins.open = _patched_open  # type: ignore[assignment]
    io.open = _patched_io_open     # type: ignore[assignment]
    os.open = _patched_os_open     # type: ignore[assignment]
    pathlib.Path.open = _patched_path_open  # type: ignore[assignment]

```

snapshots/delta.py:
```python
from __future__ import annotations
import os, shutil, subprocess, pathlib, json, platform
from typing import Iterable, Dict, Any
from bot.rag.utils import sha256_bytes
from bot.paths import get_project_root

def _which(cmd: str) -> str | None:
    return shutil.which(cmd)

def _candidates_windows() -> list[pathlib.Path]:
    # Common spots if installed via package managers/tools
    return [
        pathlib.Path(r"C:\ProgramData\chocolatey\bin\xdelta3.exe"),
        pathlib.Path(r"C:\msys64\usr\bin\xdelta3.exe"),
        pathlib.Path(r"C:\msys64\mingw64\bin\xdelta3.exe"),
        pathlib.Path(r"C:\Program Files\xdelta3\xdelta3.exe"),
    ]

def ensure_xdelta3() -> str:
    """
    Locate an xdelta3 executable. Supports:
      - EVERABOT_XDELTA3=<absolute path to exe>
      - PATH (xdelta3 / xdelta3.exe)
      - repo-local fallbacks (snapshots/bin, tools/bin)
      - common Windows install locations
    """
    # 0) explicit override
    override = os.getenv("EVERABOT_XDELTA3", "").strip()
    if override:
        p = pathlib.Path(override)
        if p.exists():
            return str(p)

    # 1) PATH
    for name in ("xdelta3", "xdelta3.exe"):
        exe = _which(name)
        if exe:
            return exe

    # 2) repo-local fallbacks
    root = get_project_root()
    local_bins = [
        root / "snapshots" / "bin" / "xdelta3",
        root / "snapshots" / "bin" / "xdelta3.exe",
        root / "tools" / "bin" / "xdelta3",
        root / "tools" / "bin" / "xdelta3.exe",
    ]
    for p in local_bins:
        if p.exists():
            return str(p)

    # 3) Windows common locations
    if os.name == "nt":
        for p in _candidates_windows():
            if p.exists():
                return str(p)

    # 4) no luck
    raise RuntimeError(
        "xdelta3 not found.\n\n"
        "How to install:\n"
        "  • Linux (Debian/Ubuntu):   sudo apt-get update && sudo apt-get install xdelta3\n"
        "  • macOS (Homebrew):        brew install xdelta\n"
        "  • Windows (MSYS2):         install MSYS2, then run: pacman -S xdelta3\n"
        "                              (ensure C:\\msys64\\usr\\bin is on your PATH)\n"
        "  • Or point to a binary via env var EVERABOT_XDELTA3=<full\\path\\to\\xdelta3.exe>\n"
        "\n"
        "After installing, re-run your command."
    )

def sha256_file(p: pathlib.Path) -> str:
    return sha256_bytes(p.read_bytes())

def make_delta(base: pathlib.Path, target: pathlib.Path, delta_out: pathlib.Path) -> None:
    exe = ensure_xdelta3()
    delta_out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([exe, "-e", "-s", str(base), str(target), str(delta_out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"xdelta3 encode failed for {target.name}: {r.stderr or r.stdout}")

def apply_delta(base: pathlib.Path, delta_file: pathlib.Path, out_path: pathlib.Path) -> None:
    exe = ensure_xdelta3()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([exe, "-d", "-s", str(base), str(delta_file), str(out_path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"xdelta3 decode failed for {delta_file.name}: {r.stderr or r.stdout}")

def roundtrip_manifest(base_dir: pathlib.Path, tgt_dir: pathlib.Path,
                       files: Iterable[str], delta_dir: pathlib.Path) -> Dict[str, Any]:
    base_dir = base_dir.resolve(); tgt_dir = tgt_dir.resolve()
    delta_dir = delta_dir.resolve(); recon_dir = (delta_dir / "recon")
    recon_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    for rel in files:
        b = base_dir / rel
        t = tgt_dir / rel
        d = delta_dir / f"{rel}.vcdiff"
        r = recon_dir / rel

        if not b.exists() or not t.exists():
            raise FileNotFoundError(f"Missing base/target file for delta: {rel}")

        make_delta(b, t, d)
        apply_delta(b, d, r)

        entry = {
            "file": rel,
            "base_sha256": sha256_file(b),
            "target_sha256": sha256_file(t),
            "recon_sha256": sha256_file(r),
            "delta_bytes": d.stat().st_size,
            "equal": sha256_file(t) == sha256_file(r),
            "delta_path": str(d),
        }
        entries.append(entry)

    proof = {
        "algorithm": "xdelta3 (RFC 3284 VCDIFF)",
        "base": str(base_dir),
        "target": str(tgt_dir),
        "delta_dir": str(delta_dir),
        "entries": entries,
        "all_equal": all(e["equal"] for e in entries),
    }
    (delta_dir / "PROOF.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    return proof

```


telemetry/tracing.py
```python
from __future__ import annotations
import json, os, time, pathlib, threading
from typing import Any, Dict
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult, SimpleSpanProcessor
from bot.paths import get_project_root

_ROOT = get_project_root()
_LOG_DIR = _ROOT / "snapshots" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "traces.jsonl"

_init_lock = threading.Lock()
_initialized = False

class JsonlSpanExporter(SpanExporter):
    def __init__(self, path: pathlib.Path):
        self._path = path
        self._fh = open(self._path, "a", encoding="utf-8")

    def export(self, spans) -> SpanExportResult:
        for s in spans:
            data: Dict[str, Any] = {
                "name": s.name,
                "start_time_unix_ns": s.start_time,
                "end_time_unix_ns": s.end_time,
                "duration_ms": (s.end_time - s.start_time) / 1_000_000 if s.end_time and s.start_time else None,
                "context": {
                    "trace_id": format(s.context.trace_id, "032x"),
                    "span_id": format(s.context.span_id, "016x"),
                },
                "parent_span_id": None if s.parent is None else format(s.parent.span_id, "016x"),
                "attributes": dict(s.attributes or {}),
                "status": getattr(s.status, "status_code", None).__class__.__name__,
                "resource": {"service.name": "everabot"},
                "exported_at": time.time(),
            }
            self._fh.write(json.dumps(data, ensure_ascii=False) + "\n")
            self._fh.flush()
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        try: self._fh.close()
        except Exception: pass

def get_tracer(name: str = "everabot") -> trace.Tracer:
    global _initialized
    if not _initialized:
        with _init_lock:
            if not _initialized:
                provider = TracerProvider(resource=Resource.create({"service.name": "everabot"}))
                provider.add_span_processor(SimpleSpanProcessor(JsonlSpanExporter(_LOG_FILE)))
                trace.set_tracer_provider(provider)
                _initialized = True
    return trace.get_tracer(name)

```

workflows/build.py
```python
from __future__ import annotations
from bot.core.state import Orchestrator

def run(goal: str) -> int:
    orch = Orchestrator(run_type="build", topic=goal)
    return orch.run_build()

```

workflows/learn.py
```python
from __future__ import annotations
from bot.core.state import Orchestrator

def run(topic: str) -> int:
    orch = Orchestrator(run_type="learn", topic=topic)
    return orch.run_learn()

```

workflows/self_improve.py
```python
from __future__ import annotations
from bot.core.state import Orchestrator

def run() -> int:
    orch = Orchestrator(run_type="self-improve", topic="self-improve")
    return orch.run_self_improve()

```

workflows/train.py
```python
from __future__ import annotations
from bot.core.state import Orchestrator

def run() -> int:
    orch = Orchestrator(run_type="train", topic="train")
    return orch.run_train()

```


---------
Root:

Dockerfile:
```bash
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# system basics
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential ca-certificates git xdelta3 && \
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

```

pyproject.toml:
```toml
[tool.poetry]
name = "everabot"
version = "0.1.0"
description = "Everabot — self-evolving, test-gated local agent"
authors = ["Tyler English <tylerhelamanenglish@gmail.com>"]
readme = "README.md"
packages = [{ include = "bot" }]

[tool.poetry.dependencies]
python = ">=3.11,<3.13"
typer = {extras = ["all"], version = "^0.17.5"}
click = "^8.2.1"   # works with Typer ≥ 0.16, avoids the make_metavar error
fastembed = "^0.3.0"         # ONNX-based embeddings (no Torch)
onnxruntime = "^1.18.0"      # runtime used by fastembed
rich = "^13.7.1"
pyarrow = "^17.0.0"
opentelemetry-api = "^1.27.0"
opentelemetry-sdk = "^1.27.0"
pyyaml = "^6.0.2"
psutil = "^5.9.8"
numpy = "^2.1.0"
requests = "^2.32.3"
beautifulsoup4 = "^4.12.3"
PyPDF2 = "^3.0.1"
sentence-transformers = "^3.0.1"
faiss-cpu = "^1.8.0.post1"
youtube-transcript-api = "^0.6.2"   # light; for YouTube transcripts in RAG
llmlingua = "^0.2.2"                # optional prompt compression (used if available)

[tool.poetry.group.dev.dependencies]
pytest = "^8.3.3"

[tool.poetry.scripts]
everabot = "bot.cli:app"

[build-system]
requires = ["poetry-core>=1.9.0"]
build-backend = "poetry.core.masonry.api"

```


