from __future__ import annotations
import asyncio, json, os, time
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

try:
    # local import; avoids coupling this module to CLI
    from bot.paths import get_project_root
except Exception:
    # fallback to repo root guess
    get_project_root = lambda: Path(__file__).resolve().parents[1]

app = FastAPI(title="Everabot API (placeholder)", version="0.1.0")

ROOT = Path(get_project_root())
SNAP = ROOT / "snapshots"
RUNS = SNAP / "runs"
METRICS = SNAP / "metrics"

# Serve a tiny demo page (optional)
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/health")
def health():
    return {"ok": True, "time": time.time()}

@app.get("/", response_class=HTMLResponse)
def index():
    if not STATIC_DIR.exists():
        return HTMLResponse("<h3>Everabot API running.</h3><p>Try <code>/runs</code> or <code>/metrics/last_promoted</code>.</p>", status_code=200)
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

@app.get("/runs")
def list_runs():
    if not RUNS.exists():
        return []
    out = []
    for p in sorted(RUNS.iterdir()):
        man = p / "manifest.json"
        if not man.exists():
            continue
        try:
            m = json.loads(man.read_text(encoding="utf-8"))
            out.append({
                "id": p.name,
                "path": str(p),
                "status": m.get("status"),
                "run_type": m.get("run_type"),
                "topic": m.get("topic"),
                "ts": m.get("ts") or m.get("created_at"),
            })
        except Exception:
            continue
    return out

@app.get("/runs/{run_id}")
def get_run_manifest(run_id: str):
    man = RUNS / run_id / "manifest.json"
    if not man.exists():
        raise HTTPException(404, f"manifest not found for run {run_id}")
    return JSONResponse(json.loads(man.read_text(encoding="utf-8")))

@app.get("/scores/{run_id}")
def get_scores(run_id: str):
    """Return a small, normalized score payload from the manifest (if present)."""
    man = RUNS / run_id / "manifest.json"
    if not man.exists():
        raise HTTPException(404, f"manifest not found for run {run_id}")
    m = json.loads(man.read_text(encoding="utf-8"))
    dec = m.get("decision", {}) or {}
    return {
        "run_id": run_id,
        "topic": m.get("topic"),
        "new_topic_score": dec.get("new_topic_score"),
        "practice_score": dec.get("practice_score"),
        "root_ok": dec.get("root_ok"),
        "bullets_count": dec.get("bullets_count"),
        "artifact": m.get("artifact") or m.get("root_eval_artifact"),
    }

@app.get("/metrics/last_promoted")
def last_promoted():
    f = METRICS / "last_promoted.json"
    if not f.exists():
        raise HTTPException(404, "no last_promoted.json yet")
    return JSONResponse(json.loads(f.read_text(encoding="utf-8")))

def _pick_log_file(run_dir: Path) -> Optional[Path]:
    # Try common log/trace file names written by workflows/tracing.
    for name in ("events.jsonl", "trace.jsonl", "logs.jsonl"):
        f = run_dir / name
        if f.exists():
            return f
    return None

def _sse_json(obj) -> str:
    """Encode a single SSE event carrying compact JSON on one line."""
    return f"data: {json.dumps(obj, ensure_ascii=False, separators=(',', ':'))}\n\n"

async def _sse_file_tail(path: Path) -> AsyncIterator[str]:
    """SSE stream of a JSONL file; yields each new line as one JSON event."""
    last_pos = 0
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)  # start tailing at end
        last_pos = f.tell()
    while True:
        await asyncio.sleep(0.5)
        with path.open("rb") as f:
            f.seek(last_pos)
            chunk = f.read()
            if not chunk:
                continue
            last_pos = f.tell()
        for line in chunk.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line.decode("utf-8", errors="replace"))
            except Exception:
                payload = {"raw": line.decode("utf-8", errors="replace")}
            yield _sse_json(payload)   # <- one clean SSE event per JSON

async def _sse_manifest_watch(man: Path) -> AsyncIterator[str]:
    """SSE stream: push manifest whenever it changes (compact JSON per event)."""
    def _read_manifest_obj():
        try:
            return json.loads(man.read_text(encoding="utf-8"))
        except Exception as e:
            return {"error": "manifest-parse", "detail": str(e)}

    last_mtime = man.stat().st_mtime
    # Send initial state as one JSON event (single line)
    yield _sse_json(_read_manifest_obj())
    while True:
        await asyncio.sleep(1.0)
        try:
            mt = man.stat().st_mtime
        except FileNotFoundError:
            break
        if mt != last_mtime:
            last_mtime = mt
            yield _sse_json(_read_manifest_obj())

@app.get("/stream/logs/{run_id}")
async def stream_logs(run_id: str):
    """Server-Sent Events stream of run logs.
       Prefers events.jsonl/trace.jsonl/logs.jsonl; falls back to manifest change stream."""
    run_dir = RUNS / run_id
    if not run_dir.exists():
        raise HTTPException(404, f"run {run_id} not found")
    logf = _pick_log_file(run_dir)
    if logf is not None:
        gen = _sse_file_tail(logf)
    else:
        man = run_dir / "manifest.json"
        if not man.exists():
            raise HTTPException(404, f"manifest for {run_id} not found")
        gen = _sse_manifest_watch(man)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        # (CORS headers if you later serve the page from another origin)
    }
    return StreamingResponse(gen, media_type="text/event-stream", headers=headers)
