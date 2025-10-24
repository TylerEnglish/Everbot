from __future__ import annotations
import json, pathlib, re, time, dataclasses
from typing import Dict, Any, Iterable, List, Tuple

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from bot.telemetry.tracing import get_tracer
from bot.paths import get_project_root
from bot.rules.loader import (
    load_all, topic_allowed, load_last_promoted, save_last_promoted, pareto_gate
)
from bot.governance.budget import BudgetEnforcer
from bot.core.task_graph import validate_path
from bot.core.phases import Phase
from bot.context.ace_delta import compute_delta
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
from bot.curriculum.priority import select_next
from bot.curriculum.registry import mark_done
from bot.curriculum.strategies import get_params as get_strategy_params
from bot.rag.hybrid import hybrid_topk
from bot.rag.chunk_spec import chunk_text_equiv as _chunk_text  # spec chunker
from bot.governance.tokens import TokenBudget

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

# ------------------------- local RAG (re)builder -------------------------

def _iter_adapted_chunks(
    doc_id: str, text: str, chunk_tokens: int, overlap_tokens: int
) -> Iterable[Dict[str, Any]]:
    """
    Wrap the chunker output so it works whether it yields dicts or tuples:
      - dict: expects keys like id|doc_id|order|text
      - tuple/list: (chunk_id, order, text, [tok_count?])
    """
    for c in _chunk_text(str(doc_id), text, chunk_tokens, overlap_tokens):
        if isinstance(c, dict):
            cid = c.get("id") or f"{doc_id}:{int(c.get('order', 0))}"
            order = int(c.get("order", 0))
            ctext = c.get("text", "")
            doc_from_cid = c.get("doc_id") or (cid.split(":", 1)[0] if ":" in cid else doc_id)
            yield {"id": cid, "doc_id": doc_from_cid, "order": order, "text": ctext}
        elif isinstance(c, (tuple, list)) and len(c) >= 3:
            cid, order, ctext = c[0], int(c[1]), c[2]
            doc_from_cid = cid.split(":", 1)[0] if isinstance(cid, str) and ":" in cid else doc_id
            # ensure chunk_id is fully qualified
            if isinstance(cid, str) and ":" not in cid:
                cid = f"{doc_from_cid}:{order}"
            yield {"id": str(cid), "doc_id": str(doc_from_cid), "order": order, "text": str(ctext)}
        else:
            raise TypeError(f"Unsupported chunk record from chunker: {type(c)}")

def _write_chunks_parquet(chunks: List[Dict[str, Any]], out_path: pathlib.Path) -> None:
    table = pa.table({
        "id":     pa.array([c["id"] for c in chunks], pa.string()),
        "doc_id": pa.array([c["doc_id"] for c in chunks], pa.string()),
        "order":  pa.array([int(c["order"]) for c in chunks], pa.int32()),
        "text":   pa.array([c["text"] for c in chunks], pa.string()),
    })
    pq.write_table(table, out_path)

def _write_emb_parquet(chunk_ids: List[str], vecs: np.ndarray, out_path: pathlib.Path) -> None:
    # vecs shape: (N, D) float32
    n, d = vecs.shape
    # Represent embeddings as a fixed-size list of float32
    value_arr = pa.array(vecs.ravel().tolist(), type=pa.float32())
    list_type = pa.list_(pa.float32(), d)
    vectors = pa.FixedSizeListArray.from_arrays(values=value_arr, list_size=d)
    table = pa.table({
        "chunk_id": pa.array(chunk_ids, pa.string()),
        "vector":   vectors
    })
    pq.write_table(table, out_path)

def _build_faiss_index(vecs: np.ndarray, out_path: pathlib.Path) -> None:
    try:
        import faiss  # type: ignore
    except Exception:
        # FAISS optional: skip if not present
        return
    # cosine via IP on L2-normalized vectors
    faiss.normalize_L2(vecs)
    d = vecs.shape[1]
    index = faiss.IndexFlatIP(d)
    index.add(vecs)
    faiss.write_index(index, str(out_path))

def _rebuild_store_from_docs(
    docs_path: pathlib.Path,
    out_dir: pathlib.Path,
    chunk_tokens: int,
    overlap_tokens: int,
) -> Dict[str, Any]:
    """
    Re-chunk docs.jsonl with the requested window/overlap, then rebuild
    embeddings(+FAISS). Returns a small manifest dict.
    """
    docs = list(read_jsonl(docs_path))
    chunks: List[Dict[str, Any]] = []
    for d in docs:
        did = d.get("id") or d.get("doc_id") or d.get("title") or "doc"
        text = d.get("text") or d.get("content") or ""
        for c in _iter_adapted_chunks(str(did), text, chunk_tokens, overlap_tokens):
            chunks.append(c)

    # Write chunks
    chunks_path = out_dir / "chunks.parquet"
    _write_chunks_parquet(chunks, chunks_path)

    # Embeddings
    _, embed = get_embedder()
    texts = [c["text"] for c in chunks]
    vecs = embed(texts)
    if isinstance(vecs, tuple):
        vecs = vecs[0]
    vecs = np.asarray(vecs, dtype=np.float32)
    emb_path = out_dir / "embeddings.parquet"
    _write_emb_parquet([c["id"] for c in chunks], vecs, emb_path)

    # FAISS (optional)
    _build_faiss_index(vecs.copy(), out_dir / "index.faiss")

    meta = {
        "version": 1,
        "emb_model": "custom/rebuilt",  # embedder info is available via get_embedder(); keep generic
        "counts": {"docs": len(docs), "chunks": len(chunks)},
    }
    (out_dir / "manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta

# ------------------------------------------------------------------------

class Orchestrator:
    def __init__(self, run_type: str, topic: str, strategy: str | None = None):
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

    def run_learn(self, strategy: str | None = None) -> int:
        topic_input = self.info.topic_or_goal
        topic = topic_input  # may be replaced by curriculum
        # Defensive defaults for later DECIDE/logging
        res: Dict[str, Any] = {}
        root_ok: bool = False

        # Auto-plan (topic='auto') or plain topic; gate allowed topics when explicit
        plan = None
        if topic_input.strip().lower() == "auto":
            plan = select_next(None)
            if not plan:
                decision = {"promote": False, "reason": "No eligible skill under current limits."}
                self._log_phase_result(Phase.DECIDE, decision)
                man = self.info.snapshot_dir / "manifest.json"
                m = json.loads(man.read_text(encoding="utf-8"))
                m["status"] = "complete"; m["decision"] = decision
                man.write_text(json.dumps(m, indent=2), encoding="utf-8")
                return 0
            topic = plan.topic
            # adopt selected strategy unless user forced one
            chosen_strategy = strategy or plan.strategy
            (self.info.snapshot_dir / "curriculum.json").write_text(
                json.dumps(plan.__dict__, indent=2), encoding="utf-8"
            )
        else:
            chosen_strategy = strategy or "vanilla"
            # Gate only when user provides explicit high-level topic
            if not topic_allowed(topic, self.rules["topics"]):
                decision = {"promote": False, "reason": f"Topic '{topic}' not allowed by ruleset/topics.yaml."}
                self._log_phase_result(Phase.DECIDE, decision)
                man = self.info.snapshot_dir / "manifest.json"
                m = json.loads(man.read_text(encoding="utf-8"))
                m["status"] = "complete"; m["decision"] = decision
                man.write_text(json.dumps(m, indent=2), encoding="utf-8")
                return 0

        # Retrieval params for chosen strategy
        rparams = get_strategy_params(chosen_strategy or "vanilla")

        with self.tracer.start_as_current_span("learn.run", attributes={"topic": topic, "strategy": chosen_strategy}):
            # Enforce run budgets (soft-fail)
            with BudgetEnforcer(self.rules["limits"], self.info.snapshot_dir) as budget:

                # ---------------- RESEARCH (RAG ingest + top-k retrieve) ----------------
                with self.tracer.start_as_current_span("phase.research"):
                    cfg = self.rules
                    seeds = (cfg.get("seeds", {}) or {}).get("seeds", []) or []
                    pinned = _ROOT / "snapshots" / "rag" / "v1"
                    # Optional pointer switch for canary migration (Task 13)
                    ptr = _ROOT / "snapshots" / "rag" / "CURRENT_STORE.txt"
                    try:
                        if ptr.exists():
                            cand = pathlib.Path(ptr.read_text(encoding="utf-8").strip())
                            if cand.exists(): pinned = cand
                    except Exception: pass
                    rag_dir = self.info.snapshot_dir / "rag_store"

                    rag_dir.mkdir(parents=True, exist_ok=True)

                    if pinned.exists():
                        # copy base store into the run
                        for name in ("docs.jsonl", "chunks.parquet", "embeddings.parquet", "index.faiss", "manifest.json"):
                            (rag_dir / name).write_bytes((pinned / name).read_bytes())
                        meta = json.loads((rag_dir / "manifest.json").read_text(encoding="utf-8"))
                        # If strategy wants different chunking than pinned, rebuild from docs
                        pinned_chunk_toks, pinned_overlap_toks = 400, 60  # known from seed build
                        need_rechunk = (
                            getattr(rparams, "chunk_tokens", pinned_chunk_toks) != pinned_chunk_toks or
                            getattr(rparams, "overlap_tokens", pinned_overlap_toks) != pinned_overlap_toks
                        )
                        if need_rechunk:
                            meta = _rebuild_store_from_docs(
                                rag_dir / "docs.jsonl",
                                rag_dir,
                                int(getattr(rparams, "chunk_tokens", 400)),
                                int(getattr(rparams, "overlap_tokens", 60)),
                            )
                    else:
                        # first-time bootstrap from ruleset seeds
                        meta = ingest(seeds, pinned)
                        # also place a copy into this run’s folder
                        for name in ("docs.jsonl", "chunks.parquet", "embeddings.parquet", "index.faiss", "manifest.json"):
                            (rag_dir / name).write_bytes((pinned / name).read_bytes())

                    # Retrieval query text (topic or sub-skill title)
                    qtext = topic.split("/", 1)[-1] if "/" in topic else topic

                    # Load chunk text map
                    chunks_tab = pq.read_table(rag_dir / "chunks.parquet")
                    text_by_chunk = {
                        chunks_tab["id"][i].as_py(): chunks_tab["text"][i].as_py()
                        for i in range(chunks_tab.num_rows)
                    }

                    # Strategy-driven retrieval
                    if getattr(rparams, "use_hybrid", True):
                        top = hybrid_topk(rag_dir, qtext, k=int(rparams.k), mmr=bool(rparams.mmr))
                        retrieved = [(cid, text_by_chunk[cid]) for cid, _ in top]
                    else:
                        emb_tab = pq.read_table(rag_dir / "embeddings.parquet")
                        ids = [x.as_py() for x in emb_tab["chunk_id"]]
                        _, embed = get_embedder()
                        idx = load_index(rag_dir / "index.faiss")
                        qv = embed([qtext])
                        if isinstance(qv, tuple):
                            qv = qv[0]
                        qv = np.asarray(qv, dtype=np.float32)
                        # IndexFlatIP expects L2-normalized for cosine
                        try:
                            import faiss  # type: ignore
                            faiss.normalize_L2(qv)
                        except Exception:
                            pass
                        D, I = idx.search(qv, int(rparams.k))
                        retrieved = [(ids[j], text_by_chunk[ids[j]]) for j in I[0]]

                    self._log_phase_result(Phase.RESEARCH, {
                        "topic": topic,
                        "strategy": chosen_strategy,
                        "k": int(rparams.k),
                        "rag_store": str(rag_dir),
                        "counts": meta.get("counts"),
                        "emb_model": meta.get("emb_model"),
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

                    # ACE-style delta vs last promoted bullets
                    last = load_last_promoted()
                    prev_list = []
                    if last and last.get("bullets_file"):
                        try:
                            prev_list = list(read_jsonl(pathlib.Path(last["bullets_file"])))
                        except Exception:
                            prev_list = []
                    try:
                        delta = compute_delta(prev_list, bullets)
                    except TypeError:
                        delta = compute_delta(last.get("bullets_file") if last else None, str(outp))
                    (self.info.snapshot_dir / "bullets.delta.json").write_text(
                        json.dumps(delta, indent=2), encoding="utf-8"
                    )

                    self._log_phase_result(Phase.DISTILL, {
                        "bullets_file": str(outp), "count": len(bullets),
                        "reflexion_bias": (prior_text[:4000] if prior_text else "")
                    })

                # ---------------- PRACTICE (tiny Q/A attempts over bullets) -------------
                with self.tracer.start_as_current_span("phase.practice"):
                    attempts, correct = 0, 0
                    logs = []
                    # token budget from ruleset
                    tmax = int(self.rules.get("limits", {}).get("tokens_max", 0) or 0)
                    tbudget = TokenBudget(tmax)
                    for b in bullets[:3]:
                        q = f"Based on: {b['text']}\nQ: Explain this tactic briefly."
                        try:
                            prompt = reduce_prompt(q, target_tokens=256)
                            ok, max_ans = tbudget.will_fit(prompt, max_answer_tokens=256)
                            if not ok or max_ans <= 0:
                                logs.append({"q": q, "a": "(skipped: token budget exhausted)"})
                                continue
                            ans = llm_ask(prompt, max_tokens=max_ans, temperature=0.0)
                            # record consumption (prompt + answer) using rough count
                            try:
                                tbudget.consume(prompt, ans)
                            except Exception:
                                pass
                            logs.append({"q": q, "a": ans})
                            correct += int(bool(ans.strip()))
                        except NotConfigured:
                            logs.append({"q": q, "a": "(no LLM backend configured)"})
                        attempts += 1
                    practice_score = (correct / attempts) if attempts else 0.0
                    self._log_phase_result(Phase.PRACTICE, {
                        "attempts": attempts, "correct": correct, "score": practice_score,
                        "samples": logs, "token_budget": {"max": tmax, "used": tbudget.used}
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
                bc = int(json.loads((self.info.snapshot_dir / "distill.json").read_text())["count"])
                practice_score = float(json.loads((self.info.snapshot_dir / "practice.json").read_text())["score"])
                new_topic_score = min(1.0, 0.5 * (bc / 12.0) + 0.5 * practice_score)

                promote, reason = pareto_gate(new_topic_score, root_ok, self.rules["promotion"], last)

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
                    # helpful breadcrumb to the exact eval artifact we just produced
                    root_art = res.get("artifact") if res else None
                    save_last_promoted({
                        "ts": int(time.time()),
                        "topic": topic,
                        "new_topic_score": new_topic_score,
                        "bullets_count": bc,
                        "practice_score": practice_score,
                        "root_ok": root_ok,
                        "root_eval_artifact": root_art,
                        "bullets_file": str(self.info.snapshot_dir / "bullets.jsonl"),
                    })
                    # mark curriculum progress when sub-skill path was used
                    if "/" in topic:
                        tname, sid = topic.split("/", 1)
                        try:
                            mark_done(tname, sid)
                        except Exception:
                            pass

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
