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
