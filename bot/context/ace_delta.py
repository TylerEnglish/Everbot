from __future__ import annotations
import json, pathlib
from typing import List, Dict, Any

def _read_bullets(path: pathlib.Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not path.exists(): return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            try: out.append(json.loads(line))
            except Exception: continue
    return out

def compute_delta(prev_file: pathlib.Path, new_bullets: List[Dict[str, Any]]) -> Dict[str, Any]:
    prev = _read_bullets(prev_file) if prev_file else []
    prev_set = {b.get("text","").strip() for b in prev}
    new_set  = {b.get("text","").strip() for b in new_bullets}
    added    = sorted(x for x in (new_set - prev_set) if x)
    removed  = sorted(x for x in (prev_set - new_set) if x)
    unchanged= sorted(new_set & prev_set)
    return {"added": added, "removed": removed, "unchanged": unchanged,
            "prev_file": str(prev_file) if prev_file else None}
