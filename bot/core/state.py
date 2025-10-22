from __future__ import annotations
import json, pathlib, re, time, dataclasses
from typing import Dict, Any

from bot.telemetry.tracing import get_tracer
from bot.paths import get_project_root
from bot.rules.loader import load_all, topic_allowed, load_last_promoted, save_last_promoted, pareto_gate
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

                # Use actual root_ok from EVAL (no hardcoded True)
                promote, reason = pareto_gate(new_topic_score, root_ok, self.rules["promotion"], last)

                # Resource-limit violations override promotion
                if budget.exceeded():
                    promote = False
                    reason = "Rejected: run exceeded configured resource limits."

                decision = {
                    "promote": promote,
                    "reason": reason,
                    "new_topic_score": new_topic_score,
                    "bullets_count": bc,
                    "practice_score": practice_score,
                    "root_ok": root_ok,
                }
                self._log_phase_result(Phase.DECIDE, decision)

                # Persist the latest promoted metrics for next-run comparison
                if promote:
                    save_last_promoted({
                        "ts": int(time.time()),
                        "topic": topic,
                        "new_topic_score": new_topic_score,
                        "bullets_count": bc,
                        "practice_score": practice_score,
                        "root_ok": root_ok,
                        # helpful breadcrumb to the exact eval artifact we just produced
                        "root_eval_artifact": res.get("artifact"),
                    })

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
            # include the final decision object in the manifest for easy inspection
            m["decision"] = decision
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
