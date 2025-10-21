
----
Context:

Please review that PDF very carefully, make sure you review all of it before you go onto my next portions.


Sources:
```
* [ACE](https://arxiv.org/pdf/2510.04618)
* [Self-Improving Coding Agent](https://arxiv.org/pdf/2504.15228)
* [RAG](https://arxiv.org/pdf/2506.00054)
* [Delta Encoding](https://nhsjs.com/2025/investigating-delta-encoding-with-error-control-for-scientific-data-reduction/)

```

----
tasks:

```


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

* [ ] Populate `ruleset/` with a minimal, explicit policy the agent can *read* but never change (allowed tools, safety, network scope, max CPU/RAM, promotion gate, etc.).
* [ ] In `test/`, add **baseline evaluations** the agent must never regress on:

* **Conversation/Turing-style** sanity prompts (your authored cases).
* **Standard LLM benchmarks** wired for local use:

  * **MMLU** for broad knowledge. ([arXiv][4])
  * **GSM8K** for math reasoning. ([arXiv][5])
  * **HellaSwag** for commonsense (note the known validity caveats). ([arXiv][6])
  * **HumanEval** for code functional correctness. ([arXiv][7])

* [ ] Expose a **single command** via Poetry scripts: `poetry run everabot eval` that runs these and emits a pass/fail summary. (No Makefile.)

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

---------
ruleset files

limits.yaml:
```yaml
tokens_max: 8192
wall_time_sec_max: 900
ram_mb_max: 4096
disk_mb_max: 4096
net:
  allow_http: true
  allow_https: true
  allowed_hosts: []

```

promotion.yaml:
```yaml
pareto_gate:
  require_no_regressions: true
  new_topic_min_score: 0.80
rollback_on_fail: true

```

tools.yaml:
```yaml
internet:
  allowed: true
  fetchers: ["http", "https"]
file_ops:
  write_whitelist: ["snapshots"]
  write_blacklist: ["ruleset", "test"]

```

topics.yaml:
```yaml

allowed_topics:
  - problem_solving
  - web_dev
  - game_dev
  - math
  - security_basics
```

------
test file

run_root_tests.py:
```python
from __future__ import annotations
import sys
print("Running root tests (stubs)…")
# TODO: wire MMLU/GSM8K/HellaSwag/HumanEval harnesses here
print("PASS (stub)")
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
from bot.paths import get_project_root
from bot.rules.loader import load_all
from bot.eval.harness import run_all

app = typer.Typer(help="Everabot CLI")

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
import json, pathlib, re, time, enum, dataclasses
from typing import Dict, Any
from bot.telemetry.tracing import get_tracer
from bot.paths import get_project_root
from bot.rules.loader import load_all, topic_allowed, load_last_promoted, pareto_gate
from bot.governance.budget import BudgetEnforcer
from bot.core.task_graph import validate_path
from bot.core.phases import Phase


_ROOT = get_project_root()
_SNAP = _ROOT / "snapshots"
def _sanitize(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", name.strip()).strip("-").lower()

class Phase(enum.Enum):
    IDLE = "IDLE"
    RESEARCH = "RESEARCH"
    DISTILL = "DISTILL"
    PRACTICE = "PRACTICE"
    EVAL = "EVAL"
    DECIDE = "DECIDE"

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
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", topic.strip()).strip("-").lower()
        snap_dir = _SNAP / "runs" / f"{time.strftime('%Y%m%d_%H%M%S', time.localtime(ts))}_{run_type}_{safe}"
        snap_dir.mkdir(parents=True, exist_ok=True)
        self.info = RunInfo(run_type, topic, ts, snap_dir)
        self.rules = load_all()
        (snap_dir / "manifest.json").write_text(json.dumps({
            "run_type": run_type, "topic": topic, "ts": ts,
            "phases": [], "status": "running", "rules": {"limits": self.rules["limits"]}
        }, indent=2), encoding="utf-8")

    def _log_phase_result(self, phase, payload: Dict[str, Any]) -> None:
        p = self.info.snapshot_dir / f"{phase.value.lower()}.json"
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        mpath = self.info.snapshot_dir / "manifest.json"
        m = json.loads(mpath.read_text(encoding="utf-8"))
        m["phases"].append({"phase": phase.value, "ts": int(time.time())})
        mpath.write_text(json.dumps(m, indent=2), encoding="utf-8")

    def run_learn(self) -> int:

        topic = self.info.topic_or_goal

        # Check topic allowlist (fast-fail)
        if not topic_allowed(topic, self.rules["topics"]):
            decision = {"promote": False, "reason": f"Topic '{topic}' not allowed by ruleset/topics.yaml."}
            self._log_phase_result(Phase.DECIDE, decision)
            man = self.info.snapshot_dir / "manifest.json"
            m = json.loads(man.read_text(encoding="utf-8"))
            m["status"] = "complete"
            m["decision"] = decision  # surface decision in manifest
            man.write_text(json.dumps(m, indent=2), encoding="utf-8")
            return 0

        with self.tracer.start_as_current_span("learn.run", attributes={"topic": topic}):
            # Enforce budgets for the whole run (soft-fail; influences DECIDE)
            with BudgetEnforcer(self.rules["limits"], self.info.snapshot_dir) as budget:
                with self.tracer.start_as_current_span("phase.research"):
                    self._log_phase_result(Phase.RESEARCH, {"topic": topic, "notes": "RAG: TBD"})

                with self.tracer.start_as_current_span("phase.distill"):
                    self._log_phase_result(Phase.DISTILL, {"bullets": ["(stub) atomic tactics"]})

                with self.tracer.start_as_current_span("phase.practice"):
                    self._log_phase_result(Phase.PRACTICE, {"attempts": 3, "score_stub": 0.0})

                with self.tracer.start_as_current_span("phase.eval"):
                    root_ok = True  # stub root tests succeeded
                    self._log_phase_result(Phase.EVAL, {"root_tests": "PASS (stub)", "root_ok": root_ok})

            # persist budget summary
            (self.info.snapshot_dir / "limits.json").write_text(
                json.dumps(budget.summary(), indent=2), encoding="utf-8"
            )

            # DECIDE (promotion gate + budget)
            with self.tracer.start_as_current_span("phase.decide"):
                last = load_last_promoted()
                new_topic_score = 0.0  # stub until real metrics
                promote, reason = pareto_gate(new_topic_score, True, self.rules["promotion"], last)
                if budget.exceeded():
                    promote = False
                    reason = "Rejected: run exceeded configured resource limits."

                decision = {"promote": promote, "reason": reason, "new_topic_score": new_topic_score}
                self._log_phase_result(Phase.DECIDE, decision)

            man = self.info.snapshot_dir / "manifest.json"
            m = json.loads(man.read_text(encoding="utf-8"))
            order = [e["phase"] for e in m.get("phases", [])]
            if not validate_path(order):
                # Don’t crash; mark decision reason for debugging
                self._log_phase_result(Phase.DECIDE, {
                    "promote": False,
                    "reason": f"Invalid task graph order: {order}"
                })
                m = json.loads(man.read_text(encoding="utf-8"))
                m["status"] = "complete"
                man.write_text(json.dumps(m, indent=2), encoding="utf-8")
                return 1

            # finalize manifest
            man = self.info.snapshot_dir / "manifest.json"
            m = json.loads(man.read_text(encoding="utf-8"))
            m["status"] = "complete"
            m["decision"] = decision  # <— show decision at a glance
            man.write_text(json.dumps(m, indent=2), encoding="utf-8")

        return 0
    def run_build(self) -> int:
        with self.tracer.start_as_current_span("build.run", attributes={"goal": self.info.topic_or_goal}):
            # Keep phases consistent; BUILD might skip PRACTICE later, but we log now.
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
        with self.tracer.start_as_current_span("self_improve.run"):
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
        with self.tracer.start_as_current_span("train.run"):
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

eval/harness.py
```python
from __future__ import annotations
import json, pathlib, subprocess, sys, datetime
from typing import Dict, Any, List
from bot.paths import get_project_root

ROOT = get_project_root()

def _pass(msg: str) -> Dict[str, Any]:
    return {"status": "PASS", "detail": msg}

def _fail(msg: str) -> Dict[str, Any]:
    return {"status": "FAIL", "detail": msg}

def _skip(msg: str) -> Dict[str, Any]:
    return {"status": "SKIP", "detail": msg}

def test_guard_write_blocked() -> Dict[str, Any]:
    """Writing to ruleset/ or test/ should raise PermissionError (guard)."""
    errors: List[str] = []
    for rel in ("ruleset/__eval_probe.txt", "test/__eval_probe.txt"):
        p = ROOT / rel
        try:
            p.write_text("should fail")
            errors.append(f"Write unexpectedly succeeded: {p}")
        except Exception as e:
            # OK: blocked
            pass
    return _pass("Writes blocked") if not errors else _fail("; ".join(errors))

def test_root_stub_script() -> Dict[str, Any]:
    """Run your legacy root tests script to keep compatibility."""
    script = ROOT / "test" / "run_root_tests.py"
    if not script.exists():
        return _fail("Missing test/run_root_tests.py")
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    if r.returncode == 0:
        return _pass(r.stdout.strip())
    return _fail(f"returncode={r.returncode}, stdout={r.stdout}, stderr={r.stderr}")

def test_mmlu_wired() -> Dict[str, Any]:
    """Check whether MMLU assets are locally available; skip if not."""
    # canonical repo: https://github.com/hendrycks/test
    repo = ROOT / "test" / "bench_mmlu"  # placeholder path you can populate later
    if not repo.exists():
        return _skip("MMLU assets not found (expected under test/bench_mmlu).")
    return _skip("Harness present; scoring not implemented yet.")

def test_gsm8k_wired() -> Dict[str, Any]:
    """Check whether GSM8K assets are locally available; skip if not."""
    data = ROOT / "test" / "bench_gsm8k"
    if not data.exists():
        return _skip("GSM8K dataset not found (expected under test/bench_gsm8k).")
    return _skip("Harness present; scoring not implemented yet.")

def test_hellaswag_wired() -> Dict[str, Any]:
    data = ROOT / "test" / "bench_hellaswag"
    if not data.exists():
        return _skip("HellaSwag dataset not found (expected under test/bench_hellaswag).")
    return _skip("Harness present; scoring not implemented yet.")

def test_humaneval_wired() -> Dict[str, Any]:
    data = ROOT / "test" / "bench_humaneval"
    if not data.exists():
        return _skip("HumanEval dataset not found (expected under test/bench_humaneval).")
    return _skip("Harness present; scoring not implemented yet.")

def run_all() -> Dict[str, Any]:
    results = {
        "security": {
            "guard_write_blocked": test_guard_write_blocked(),
        },
        "root": {
            "legacy_stub": test_root_stub_script(),
        },
        "benchmarks": {
            "mmlu": test_mmlu_wired(),
            "gsm8k": test_gsm8k_wired(),
            "hellaswag": test_hellaswag_wired(),
            "humaneval": test_humaneval_wired(),
        },
    }
    # Decide overall pass: all FAIL -> fail; SKIP doesn't fail the run.
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
    # Write an eval artifact for provenance
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
    return {
        "limits":    _read_yaml("limits.yaml"),
        "promotion": _read_yaml("promotion.yaml"),
        "tools":     _read_yaml("tools.yaml"),
        "topics":    _read_yaml("topics.yaml"),
    }

def topic_allowed(topic: str, topics_cfg: Dict[str, Any]) -> bool:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.strip().lower())
    allowed = [t.strip().lower() for t in topics_cfg.get("allowed_topics", [])]
    # allow if any allowed token appears as a whole word in slug
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
    require_no_reg = bool(promo_cfg.get("pareto_gate", {}).get("require_no_regressions", True))
    min_score = float(promo_cfg.get("pareto_gate", {}).get("new_topic_min_score", 0.0))
    if require_no_reg and not root_ok:
        return (False, "Rejected: root tests regressed.")
    if new_topic_score < min_score:
        return (False, f"Rejected: new_topic_score {new_topic_score:.2f} < threshold {min_score:.2f}.")
    return (True, "Promoted.")

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
typer = {extras = ["all"], version = "^0.12.3"}
rich = "^13.7.1"
pyarrow = "^17.0.0"
opentelemetry-api = "^1.27.0"
opentelemetry-sdk = "^1.27.0"
pyyaml = "^6.0.2"
psutil = "^5.9.8"      # new: budget monitoring (RAM)

[tool.poetry.group.dev.dependencies]
pytest = "^8.3.3"

[tool.poetry.scripts]
everabot = "bot.cli:app"

[build-system]
requires = ["poetry-core>=1.9.0"]
build-backend = "poetry.core.masonry.api"


```


