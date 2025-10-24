from __future__ import annotations
import json, pathlib
from typing import List, Dict, Any, Union, Sequence

def _read_bullets(path: pathlib.Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not path.exists(): return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            try: out.append(json.loads(line))
            except Exception: continue
    return out

PrevType = Union[pathlib.Path, Sequence[Dict[str, Any]], None]
def compute_delta(prev_source: PrevType, new_bullets: List[Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(prev_source, (list, tuple)):
        prev = list(prev_source)
    elif isinstance(prev_source, pathlib.Path):
        prev = _read_bullets(prev_source)
    elif prev_source is None:
        prev = []
    else:
        # Be defensive but never crash the run
        prev = []
    prev_set = {b.get("text","").strip() for b in prev}
    new_set  = {b.get("text","").strip() for b in new_bullets}
    added    = sorted(x for x in (new_set - prev_set) if x)
    removed  = sorted(x for x in (prev_set - new_set) if x)
    unchanged = sorted(x for x in (new_set & prev_set) if x)
    return {"added": added, "removed": removed, "unchanged": unchanged}