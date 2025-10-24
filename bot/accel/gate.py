from __future__ import annotations
import json, pathlib, os

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PREF = _ROOT / "snapshots" / "accel_prefs.json"

def _read_prefs() -> dict:
    if _PREF.exists():
        try:
            return json.loads(_PREF.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _write_prefs(d: dict) -> None:
    _PREF.parent.mkdir(parents=True, exist_ok=True)
    _PREF.write_text(json.dumps(d, indent=2), encoding="utf-8")

def get_chunk_preference() -> str | None:
    return _read_prefs().get("chunk_engine")

def set_chunk_preference(impl: str | None) -> None:
    d = _read_prefs()
    if impl:
        d["chunk_engine"] = impl
    else:
        d.pop("chunk_engine", None)
    _write_prefs(d)

def set_default_env_from_gate() -> None:
    # Only set if user hasn't explicitly pinned via env
    if os.environ.get("EVERABOT_CHUNK_ENGINE"):
        return
    pref = get_chunk_preference()
    if pref:
        os.environ["EVERABOT_CHUNK_ENGINE"] = pref
def canon_chunks(batch):
    def c1(rec):
        doc, start, text, toks = rec[0], rec[1], rec[2], rec[3]
        return (str(doc), int(start), str(text), int(toks))
    return [[c1(r) for r in group] for group in batch]
