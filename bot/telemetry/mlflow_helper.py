from __future__ import annotations
import json, pathlib, time
from contextlib import nullcontext
from typing import Any, Dict, Iterable
from bot.paths import get_project_root

def _mlflow_or_none():
    try:
        import mlflow  # type: ignore
        return mlflow
    except Exception:
        return None

def start_run_if_available(run_type: str, topic: str):
    """
    Configure local file-backed tracking (snapshots/mlruns) and start a run.
    No-ops cleanly if MLflow isn't installed.
    """
    mlflow = _mlflow_or_none()
    if not mlflow:
        return nullcontext()

    mlruns_dir = get_project_root() / "snapshots" / "mlruns"
    mlflow.set_tracking_uri(f"file:{mlruns_dir.as_posix()}")   # file-backed store
    mlflow.set_experiment("everabot-runs")
    return mlflow.start_run(run_name=f"{run_type}:{topic}")

def log_params_safe(params: Dict[str, Any]):
    mlflow = _mlflow_or_none()
    if not mlflow: return
    # coerce simple JSONable types
    clean = {k: (v if isinstance(v, (str, int, float, bool)) or v is None else str(v))
             for k, v in params.items()}
    try: mlflow.log_params(clean)
    except Exception: pass

def log_metrics_safe(metrics: Dict[str, float]):
    mlflow = _mlflow_or_none()
    if not mlflow: return
    # only numeric metrics
    numeric = {k: float(v) for k, v in metrics.items()
               if v is not None and isinstance(v, (int, float))}
    if not numeric: return
    try: mlflow.log_metrics(numeric)
    except Exception: pass

def log_artifact_safe(path: pathlib.Path):
    mlflow = _mlflow_or_none()
    if not mlflow: return
    if path.exists():
        try: mlflow.log_artifact(str(path))
        except Exception: pass

def log_artifacts_safe(path: pathlib.Path):
    mlflow = _mlflow_or_none()
    if not mlflow: return
    if path.exists():
        try: mlflow.log_artifacts(str(path))
        except Exception: pass

def _latest_matching_manifest(run_type: str, topic: str) -> pathlib.Path | None:
    runs_dir = get_project_root() / "snapshots" / "runs"
    if not runs_dir.exists(): return None
    best = None
    best_ts = -1
    for run_dir in runs_dir.iterdir():
        man = run_dir / "manifest.json"
        if not man.exists(): continue
        try:
            m = json.loads(man.read_text(encoding="utf-8"))
            if m.get("run_type") == run_type and m.get("topic") == topic:
                ts = int(m.get("ts") or run_dir.stat().st_mtime)
                if ts > best_ts:
                    best, best_ts = man, ts
        except Exception:
            continue
    return best

def log_from_manifest_if_found(run_type: str, topic: str):
    """
    Try to find the manifest for this run; log it as an artifact and
    emit a few decision metrics if present.
    """
    man = _latest_matching_manifest(run_type, topic)
    if not man: return
    log_artifact_safe(man)

    try:
        m = json.loads(man.read_text(encoding="utf-8"))
        dec = m.get("decision") or {}
        # common scores you showed in your manifests
        log_metrics_safe({
            "practice_score": dec.get("practice_score"),
            "new_topic_score": dec.get("new_topic_score"),
            "bullets_count": dec.get("bullets_count"),
            "root_ok": 1.0 if dec.get("root_ok") else 0.0,
        })
    except Exception:
        pass
