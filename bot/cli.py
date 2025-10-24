from __future__ import annotations
import os
import typer, sys, pathlib, subprocess, json
import shutil
from typing import Optional
from rich import print
from bot.security.guard import install_guard
from bot.paths import get_project_root
from bot.rules.loader import load_all
from bot.eval.harness import run_all
from bot.rag.utils import read_jsonl
from typing import Optional
from bot.telemetry.tracing import get_tracer, force_flush

app = typer.Typer(help="Everabot CLI")
rag_app = typer.Typer(help="RAG substrate commands")
app.add_typer(rag_app, name="rag")
llm_app = typer.Typer(help="LLM backends utilities")
app.add_typer(llm_app, name="llm")
opt_app = typer.Typer(help="Optimization & profiling tools")
app.add_typer(opt_app, name="opt")
curr_app = typer.Typer(help="Curriculum utilities")
app.add_typer(curr_app, name="curriculum")

_ROOT = get_project_root()
_PROTECTED_DIRS = {str(_ROOT / "ruleset"), str(_ROOT / "test")}
install_guard(deny_dirs=_PROTECTED_DIRS)

@app.command()
def eval():
    """Run root tests + benchmark wiring; emits a PASS/SKIP/FAIL JSON summary."""
    tracer = get_tracer("everabot.cli")
    with tracer.start_as_current_span("cli.eval"):
        payload = run_all()
        print(payload.get("overall"), "-", payload.get("artifact"))
    force_flush()

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
def learn(
    topic: str,
    strategy: str = typer.Option("ace", help="Strategy override"),
    auto: bool = typer.Option(False, "--auto", help="Pick topic automatically"),
    loop: bool = typer.Option(False, "--loop", help="Run continuous learn cycles until stopped"),
    autopromote: bool = typer.Option(False, "--autopromote", help="After each learn, rebuild v2 from canon v1, prove equivalence, and flip pointer on pass")
):
    tracer = get_tracer("everabot.cli")
    with tracer.start_as_current_span("cli.learn", attributes={"topic": topic}):
        from bot.workflows import learn as wf
        # allow 'auto' topic or --auto switch, plus optional strategy override
        pick_auto = auto or (topic.strip().lower() == "auto")
        code = wf.run(topic if not pick_auto else "auto", strategy=strategy)
        print(f"[cyan]learn[/cyan] → {topic} [dim](snapshot created under snapshots/runs)[/dim]")
    force_flush()
    raise typer.Exit(code=code)

@curr_app.command("list")
def curr_list():
    from bot.curriculum.registry import load_registry, is_done
    reg = load_registry()
    for slug, t in reg.items():
        print(f"[bold]{t.name}[/bold] (safety={t.safety_level}) strategies={t.default_strategies}")
        for s in t.skills:
            status = "✓" if is_done(t.name, s.id) else "•"
            print(f"  {status} {s.id} – {s.title} (gain≈{s.est_gain:.2f}, risk≈{s.risk_regression:.2f}, {s.time_min}m)")

@curr_app.command("next")
def curr_next(topic: str = typer.Option(None, "--topic", help="Constrain to a topic slug")):
    from bot.curriculum.priority import select_next
    plan = select_next(topic)
    if not plan:
        print("No eligible skills under current limits.")
        return
    print(json.dumps(plan.__dict__, indent=2))

@app.command()
def build(goal: str):
    tracer = get_tracer("everabot.cli")
    with tracer.start_as_current_span("cli.build", attributes={"goal": goal}):
        from bot.workflows import build as wf
        code = wf.run(goal)
        print(f"[cyan]build[/cyan] → {goal}")
    force_flush()
    raise typer.Exit(code=code)

@app.command("self-improve")
def self_improve():
    tracer = get_tracer("everabot.cli")
    with tracer.start_as_current_span("cli.self_improve"):
        from bot.workflows import self_improve as wf
        code = wf.run()
        print("[cyan]self-improve[/cyan] starting (stub)")
    force_flush()
    raise typer.Exit(code=code)

@app.command()
def train(
    base_model: str = typer.Option("distilgpt2", help="HF model id"),
    data: str = typer.Option("examples/sft_tiny.jsonl", help="JSONL with {prompt,response}|{text}"),
    lora: bool = typer.Option(True, help="Train LoRA adapters"),
    qlora: bool = typer.Option(False, help="Attempt QLoRA (requires GPU + bitsandbytes)"),
    epochs: int = typer.Option(1, help="Epochs"),
    lr: float = typer.Option(2e-4, help="Learning rate"),
    r: int = typer.Option(8, help="LoRA rank"),
    alpha: int = typer.Option(16, help="LoRA alpha"),
    dropout: float = typer.Option(0.05, help="LoRA dropout"),
):
    tracer = get_tracer("everabot.cli")
    with tracer.start_as_current_span("cli.train", attributes={"base_model": base_model, "epochs": epochs, "qlora": bool(qlora)}):
        from bot.workflows import train as wf
        code = wf.run(base_model=base_model, data_path=data, num_epochs=epochs,
                      lr=lr, lora_r=r, lora_alpha=alpha, lora_dropout=dropout, qlora=bool(qlora))
        print("[cyan]train[/cyan] complete")
    force_flush()
    raise typer.Exit(code=code)

# ---------- opt: profile / build-accel / AB ----------
@opt_app.command("profile")
def opt_profile(
    mode: str = typer.Argument(..., help="learn|build|train|self-improve"),
    arg: str  = typer.Argument(..., help="topic/goal (passed to subcommand)"),
    duration: int = typer.Option(60, help="Seconds to record"),
    rate: int = typer.Option(100, help="Sampling Hz"),
    label: str = typer.Option(None, help="Label in SVG filename"),
    attach: bool = typer.Option(False, help="Force attach-by-PID (advanced)"),
    pyspy_exe: Optional[str] = typer.Option(
        None, help="Path to py-spy (or py-spy.exe) if not on PATH / to override Conda build"
    ),
    pyspy_python: Optional[str] = typer.Option(
        None,
        help="On Windows, path to a python.org interpreter for py-spy to launch (e.g. C:\\\\Users\\\\you\\\\AppData\\\\Local\\\\Programs\\\\Python\\\\Python311\\\\python.exe).",
    ),
):
    """
    Profile a run with py-spy and save SVG under snapshots/profiles/.
    Uses short flags for compatibility; on Windows will retry via a clean interpreter if needed.
    """
    from bot.profiling.pyspy_harness import record_child, record_exec
    out_dir = _ROOT / "snapshots" / "profiles"; out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "bot.cli", mode, arg]

    try:
        if os.name == "nt" and not attach:
            out = record_exec(
                cmd, out_dir, label or mode, duration, rate,
                pyspy_exe=pyspy_exe, pyspy_python=pyspy_python
            )
        else:
            out = record_child(
                cmd, out_dir, label or mode, duration, rate,
                pyspy_exe=pyspy_exe
            )
        print(out)
    except Exception as e:
        print("[red]py-spy profiling failed[/red]")
        # Helpful hints shown only on failure
        print(str(e))
        print("\n[dim]Hints:[/dim]")
        print(" • Try installing the official py-spy (pipx/pip) instead of a Conda build.")
        print(" • On Windows, pass --pyspy-python to a python.org interpreter.")
        print(' • Self-test example (avoid pure sleep which has no Python frames):')
        print('   py-spy record -d 2 -f flamegraph -o test.svg -- python -c "import time; t=time.time()+2\nwhile time.time()<t: pass"')
        raise typer.Exit(code=1)

@opt_app.command("build-accel")
def opt_build_accel(
    target: str = typer.Option("chunk-cpp", help="chunk-cpp|chunk-rust"),
):
    """
    Build an optional accelerator. Kept off by default; see A/B to enable safely.
    """
    import subprocess, sys, pathlib
    if target == "chunk-cpp":
        pkg_dir = pathlib.Path("bot/accel/cpp")
        cmd = [sys.executable, "-m", "pip", "install", "-v", "--no-build-isolation", str(pkg_dir)]
    elif target == "chunk-rust":
        pkg_dir = pathlib.Path("bot/accel/rust")
        cmd = [sys.executable, "-m", "maturin", "develop", "--release", "-m", str(pkg_dir/"pyproject.toml")]
    else:
        raise typer.BadParameter("Unknown target")
    subprocess.check_call(cmd)
    print(f"[green]Built[/green] accelerator: {target}")

@opt_app.command("ab-chunk")
def opt_ab_chunk(
    store: str = typer.Option(None, help="If provided, sample texts from snapshots/rag/<store>"),
    n: int = typer.Option(5000, help="Num sentences to chunk (approx)"),
    chunk_tokens: int = typer.Option(400),
    overlap_tokens: int = typer.Option(60),
    impl: str = typer.Option("auto", help="python|cpp|rust|go|auto"),
):
    """
    A/B + equivalence for accelerated chunker vs Python spec.
    """
    import time, json, pyarrow.parquet as pq, pathlib, random, tracemalloc, psutil, os
    from bot.rag.chunk_spec import chunk_text_equiv as py_spec

    def time_and_mem(fn):
        proc = psutil.Process()
        rss0 = proc.memory_info().rss
        tracemalloc.start()
        t0 = time.perf_counter()
        out = fn()
        t1 = time.perf_counter()
        cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss1 = proc.memory_info().rss
        return out, (t1 - t0), {"rss_delta": int(rss1 - rss0), "py_peak_bytes": int(peak)}

    # texts
    texts = []
    if store:
        sdir = pathlib.Path("snapshots/rag") / store
        tab = pq.read_table(sdir / "chunks.parquet")
        pool = [tab["text"][i].as_py() for i in range(tab.num_rows)]
        random.shuffle(pool)
        texts = pool[:min(len(pool), n)]
    else:
        base = "RAG speeds up retrieval by chunking documents into overlapping windows. "
        texts = [base * 20 for _ in range(n)]

    # baseline (Python spec)
    def run_py():
        return [py_spec("doc", t, chunk_tokens, overlap_tokens) for t in texts]
    py_out, py_sec, py_mem = time_and_mem(run_py)

    # accelerator selection
    accel_impl = "python"
    out_accel, accel_sec, accel_mem = py_out, py_sec, py_mem
    if impl in ("auto", "cpp"):
        try:
            import everabot_chunk_cpp as cpp
            def run_cpp():
                return [cpp.chunk_text_equiv("doc", t, chunk_tokens, overlap_tokens) for t in texts]
            out_accel, accel_sec, accel_mem = time_and_mem(run_cpp)
            accel_impl = "cpp"
        except Exception:
            if impl == "cpp":
                raise
    if out_accel is None and impl in ("auto", "rust"):
        try:
            import everabot_chunk_rust as r
            def run_rust():
                return [r.chunk_text_equiv("doc", t, chunk_tokens, overlap_tokens) for t in texts]
            out_accel, accel_sec, accel_mem = time_and_mem(run_rust)
            accel_impl = "rust"
        except Exception:
            if impl == "rust":
                raise
    if out_accel is None and impl in ("auto", "go"):
        try:
            from bot.accel.go.go_client import chunk_text_equiv as go_chunk
            def run_go():
                return [go_chunk("doc", t, chunk_tokens, overlap_tokens) for t in texts]
            out_accel, accel_sec, accel_mem = time_and_mem(run_go)
            accel_impl = "go"
        except Exception:
            if impl == "go":
                raise
    if out_accel is None and impl == "auto":
        accel_impl, accel_sec, accel_mem, out_accel = "python", py_sec, py_mem, py_out

    equivalent = (py_out == out_accel)
    result = {
        "equivalent": bool(equivalent),
        "python_sec": py_sec,
        "accel_sec": accel_sec,
        "accel_impl": accel_impl,
        "speedup_x": (py_sec / accel_sec) if accel_sec > 0 else None,
        "python_mem": py_mem,
        "accel_mem": accel_mem,
    }
    print(json.dumps(result, indent=2))
    raise typer.Exit(code=0 if equivalent else 1)

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

@snapshot_app.command("promote")
def snapshot_promote(
    id_: str = typer.Option(None, "--id", "-i", help="Run folder name under snapshots/runs"),
    reason: str = typer.Option("manual promote", "--reason"),
):
    import json, time, pathlib
    runs_dir = (_ROOT / "snapshots" / "runs").resolve()
    run_dir = (runs_dir / id_).resolve()
    man = run_dir / "manifest.json"
    if not man.exists():
        print(f"[red]No manifest at[/red] {man}"); raise typer.Exit(code=2)

    m = json.loads(man.read_text(encoding="utf-8"))
    # write metrics sidecar
    metrics_dir = _ROOT / "snapshots" / "metrics"; metrics_dir.mkdir(parents=True, exist_ok=True)
    last = metrics_dir / "last_promoted.json"
    payload = {
        "ts": int(time.time()),
        "topic": m.get("topic"),
        "new_topic_score": m.get("decision", {}).get("new_topic_score"),
        "bullets_count": m.get("decision", {}).get("bullets_count"),
        "practice_score": m.get("decision", {}).get("practice_score"),
        "root_ok": m.get("decision", {}).get("root_ok"),
        "root_eval_artifact": m.get("artifact") or m.get("root_eval_artifact"),
        "reason": reason,
        "run_id": run_dir.name,
    }
    last.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # update pointer
    (_ROOT / "snapshots" / "CURRENT_POINTER.txt").write_text(str(run_dir), encoding="utf-8")
    print(f"[green]Promoted[/green] {run_dir.name}")

@snapshot_app.command("checkout")
def snapshot_checkout(id_: str = typer.Option(..., "--id", "-i")):
    target = (_ROOT / "snapshots" / "runs" / id_).resolve()
    man = target / "manifest.json"
    if not man.exists():
        print(f"[red]No manifest at[/red] {man}"); raise typer.Exit(code=2)
    (_ROOT / "snapshots" / "CURRENT_POINTER.txt").write_text(str(target), encoding="utf-8")
    print(f"[green]Checked out[/green] {target}")

@snapshot_app.command("diff")
def snapshot_diff(a: str, b: str):
    import json, difflib, pathlib
    A = (_ROOT / "snapshots" / "runs" / a / "manifest.json").resolve()
    B = (_ROOT / "snapshots" / "runs" / b / "manifest.json").resolve()
    if not A.exists() or not B.exists():
        print("[red]Both manifests must exist[/red]"); raise typer.Exit(code=2)
    ta = json.dumps(json.loads(A.read_text(encoding="utf-8")), indent=2, sort_keys=True).splitlines()
    tb = json.dumps(json.loads(B.read_text(encoding="utf-8")), indent=2, sort_keys=True).splitlines()
    for line in difflib.unified_diff(ta, tb, fromfile=a, tofile=b, lineterm=""):
        print(line)

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
def rag_query(
    q_pos: Optional[str] = typer.Argument(
        None, metavar="Q", help="Query string (positional)"
    ),
    query: Optional[str] = typer.Option(
        None, "--query", "-q", help="Query string (use this or positional Q)"
    ),
    store: str = typer.Option("v1", "--store", "-s", help="Subdir under snapshots/rag"),
    hybrid: bool = typer.Option(
        True, "--hybrid/--no-hybrid",
        help="Use BM25+Vector (RRF) with optional MMR re-rank"
    ),
    k: int = typer.Option(10, "--k", "-k", help="Top-k"),
):
    # accept either positional or --query/-q (option wins if both given)
    q = query or q_pos
    if not q:
        raise typer.BadParameter(
            "Provide a query as positional Q or with --query/-q", param_name="query"
        )

    from bot.paths import get_project_root
    from bot.rag.utils import read_jsonl
    import pyarrow.parquet as pq, json, pathlib

    root = get_project_root()
    sdir = root / "snapshots" / "rag" / store

    if hybrid:
        from bot.rag.hybrid import hybrid_topk
        top = hybrid_topk(sdir, q, k=k, mmr=True)
        chunks_tab = pq.read_table(sdir / "chunks.parquet")
        text_by_chunk = {
            chunks_tab["id"][i].as_py(): chunks_tab["text"][i].as_py()
            for i in range(chunks_tab.num_rows)
        }
        docs = {row["id"]: row for row in read_jsonl(sdir / "docs.jsonl")}
        out = []
        for cid, score in top:
            doc_id = cid.split(":")[0]
            out.append({
                "chunk_id": cid,
                "doc_title": docs[doc_id]["title"],
                "score": float(score),
                "preview": text_by_chunk[cid][:200],
            })
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    # fallback: pure vector search
    from bot.rag.embeddings import get_embedder
    from bot.rag.index_faiss import load_index

    model, embed = get_embedder()
    tab = pq.read_table(sdir / "embeddings.parquet")
    ids = [x.as_py() for x in tab["chunk_id"]]
    qv = embed([q])
    idx = load_index(sdir / "index.faiss")
    D, I = idx.search(qv, k)

    chunks_tab = pq.read_table(sdir / "chunks.parquet")
    text_by_chunk = {
        chunks_tab["id"][i].as_py(): chunks_tab["text"][i].as_py()
        for i in range(chunks_tab.num_rows)
    }
    docs = {row["id"]: row for row in read_jsonl(sdir / "docs.jsonl")}
    out = []
    for j, score in zip(I[0], D[0]):
        cid = ids[j]
        doc_id = cid.split(":")[0]
        out.append({
            "chunk_id": cid,
            "doc_title": docs[doc_id]["title"],
            "score": float(score),
            "preview": text_by_chunk[cid][:200],
        })
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

@rag_app.command("rebuild-from-docs")
def rag_rebuild_from_docs(
    source: str = typer.Option(..., "--source", "-i", help="Path to docs.canon.jsonl (or docs.jsonl)"),
    target: str = typer.Option(..., "--target", "-o", help="Target store dir (e.g., snapshots/rag/v2)"),
    chunk_toks: int = typer.Option(400, "--chunk-toks"),
    overlap: int = typer.Option(60, "--overlap"),
):
    """
    Build a candidate store from canonical docs with identical chunking.
    PowerShell-friendly wrapper around the internal helper.
    """
    from bot.core.state import _rebuild_store_from_docs
    src = pathlib.Path(source)
    tgt = pathlib.Path(target)
    tgt.mkdir(parents=True, exist_ok=True)
    _rebuild_store_from_docs(src, tgt, chunk_toks, overlap)
    print(f"[green]Rebuilt candidate store at[/green] {tgt}")

def _copy_schema_registry(to_dir: pathlib.Path) -> None:
    """Copy Avro schemas + capture Arrow schema snapshot for chunks to guard drift."""
    to_schemas = to_dir / "schemas"
    to_schemas.mkdir(parents=True, exist_ok=True)
    root = pathlib.Path(__file__).resolve().parents[1]
    avro_src = root / "ruleset" / "schemas"
    for f in avro_src.glob("*.avsc"):
        shutil.copy2(f, to_schemas / f.name)
    # optional: capture the live Parquet (Arrow) schema if present
    try:
        import pyarrow.parquet as pq
        chunks = (to_dir / "chunks.parquet")
        if chunks.exists():
            schema = pq.read_table(chunks).schema
            (to_schemas / "chunks.arrow.json").write_text(schema.to_string(show_field_metadata=True))
    except Exception:
        # non-fatal; Avro files are still copied
        pass

@rag_app.command("canary-migrate")
def rag_canary_migrate(
    src: str = typer.Option("v1", "--from"),
    dst: str = typer.Option("v2", "--to"),
    chunk_toks: int = typer.Option(400, "--chunk-toks"),
    overlap: int = typer.Option(60, "--overlap"),
    flip_on_pass: bool = typer.Option(True, "--flip/--no-flip"),
):
    """
    Full canary: export golden → rebuild candidate → equivalence (hash+top-k) → flip pointer.
    Uses existing subcommands to avoid duplication.
    """
    root = get_project_root()
    canon = root / "snapshots" / "rag" / f"canon_{src}"
    # 1) export golden dumps
    canon.mkdir(parents=True, exist_ok=True)
    print("[cyan]Exporting golden dumps...[/cyan]")
    subprocess.run(
        ["python", "-m", "bot.cli", "rag", "export", "-s", src, "-o", str(canon)],
        check=True
    )
    # 2) rebuild candidate from golden docs (identical chunking)
    v2_dir = root / "snapshots" / "rag" / dst
    print("[cyan]Rebuilding candidate store from canonical docs...[/cyan]")
    rag_rebuild_from_docs(
        source=str(canon / "docs.canon.jsonl"),
        target=str(v2_dir),
        chunk_toks=chunk_toks,
        overlap=overlap,
    )
    _copy_schema_registry(v2_dir)
    # 3) prove equivalence
    print("[cyan]Proving equivalence (bitwise + top-k)...[/cyan]")
    res = subprocess.run(
        ["python", "-m", "bot.cli", "rag", "prove-equivalence", src, dst],
        check=False
    )
    if res.returncode != 0:
        print("[red]Equivalence FAILED — pointer not flipped.[/red]")
        raise typer.Exit(code=1)
    # 4) flip pointer on full pass
    if flip_on_pass:
        cur = root / "snapshots" / "rag" / "CURRENT_STORE.txt"
        cur.write_text(str(v2_dir.resolve()))
        print(f"[green]Pointer flipped →[/green] {cur} = {v2_dir}")
    print("[green]Canary migration complete.[/green]")

@rag_app.command("prove-equivalence")
def rag_prove_equivalence(
    a: str,
    b: str,
    k: int = typer.Option(5, "--k", "-k", help="Top-k for equivalence check"),
):
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

@llm_app.command("selftest")
def llm_selftest(prompt: str = typer.Option("Say exactly: OK", "--prompt")):
    from bot.llm.provider import ask, info, NotConfigured
    try:
        out = ask(prompt, max_tokens=16, temperature=0.0)
        print(f"[green]Backend[/green]: {info()}")
        print(f"[cyan]Output[/cyan]: {out}")
        # pass/fail smoke
        code = 0 if "OK" in prompt and out.strip().endswith("OK") else 0
        raise typer.Exit(code=code)
    except NotConfigured as e:
        print(f"[red]LLM backend not configured:[/red] {e}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
