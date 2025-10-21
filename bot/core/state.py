from __future__ import annotations
import json, pathlib, re, time, enum, dataclasses
from typing import Dict, Any
from bot.telemetry.tracing import get_tracer
from bot.paths import get_project_root
from bot.rules.loader import load_all, topic_allowed, load_last_promoted, pareto_gate
from bot.governance.budget import BudgetEnforcer
from bot.core.task_graph import validate_path
from bot.core.phases import Phase
from bot.eval.harness import run_all


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
                    # Seed from rules (internet allowlisted in ruleset/tools.yaml)
                    urls = [
                        "https://arxiv.org/pdf/2510.04618",
                        "https://arxiv.org/pdf/2504.15228",
                        "https://arxiv.org/pdf/2005.11401",
                        "https://datatracker.ietf.org/doc/html/rfc3284",
                    ]
                    from bot.rag.pipeline import ingest
                    rag_dir = self.info.snapshot_dir / "rag_store"
                    meta = ingest(urls, rag_dir)
                    self._log_phase_result(Phase.RESEARCH, {
                        "topic": topic, "rag_store": str(rag_dir),
                        "counts": meta.get("counts"), "emb_model": meta.get("emb_model")
                    })

                with self.tracer.start_as_current_span("phase.distill"):
                    self._log_phase_result(Phase.DISTILL, {"bullets": ["(stub) atomic tactics"]})

                with self.tracer.start_as_current_span("phase.practice"):
                    self._log_phase_result(Phase.PRACTICE, {"attempts": 3, "score_stub": 0.0})

                with self.tracer.start_as_current_span("phase.eval"):
                    res = run_all()
                    root_ok = (res["overall"] == "PASS")
                    self._log_phase_result(Phase.EVAL, {"root_eval_artifact": res["artifact"], "root_ok": root_ok})

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
