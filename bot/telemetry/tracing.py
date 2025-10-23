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
# allow override (useful in CI or different workspace roots)
_LOG_FILE = pathlib.Path(os.getenv("EVERABOT_TRACE_FILE", str(_LOG_DIR / "traces.jsonl")))

_init_lock = threading.Lock()
_initialized = False
_processor: SimpleSpanProcessor | None = None

class JsonlSpanExporter(SpanExporter):
    def __init__(self, path: pathlib.Path):
        self._path = path
        self._fh = open(self._path, "a", encoding="utf-8")

    def export(self, spans) -> SpanExportResult:
        for s in spans:
            # status_code is an enum; serialize as its name for readability
            try:
                status_name = getattr(s.status, "status_code", None).name  # Enum -> "UNSET"/"OK"/"ERROR"
            except Exception:
                status_name = None
            # instrumentation scope (name/version) is helpful to group spans
            scope = getattr(s, "instrumentation_scope", None)
            scope_name = getattr(scope, "name", None)
            scope_ver  = getattr(scope, "version", None)

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
                "status": status_name,
                "resource": {"service.name": "everabot"},
                "scope": {"name": scope_name, "version": scope_ver},
                "exported_at": time.time(),
            }
            self._fh.write(json.dumps(data, ensure_ascii=False) + "\n")
            self._fh.flush()
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        try: self._fh.close()
        except Exception: pass

def get_tracer(name: str = "everabot") -> trace.Tracer:
    global _initialized, _processor
    if not _initialized:
        with _init_lock:
            if not _initialized:
                provider = TracerProvider(resource=Resource.create({"service.name": "everabot"}))
                _processor = SimpleSpanProcessor(JsonlSpanExporter(_LOG_FILE))
                provider.add_span_processor(_processor)
                trace.set_tracer_provider(provider)
                _initialized = True
    return trace.get_tracer(name)

def force_flush() -> None:
    """Best-effort flush of the simple processor so traces hit disk before exit."""
    try:
        if _processor:
            _processor.force_flush()
    except Exception:
        pass
