from __future__ import annotations
import json, pathlib
from typing import Any, Dict

def _load(path: pathlib.Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def _walk(a: Any, b: Any, prefix=""):
    diffs = []
    if type(a) != type(b):
        diffs.append((prefix, a, b)); return diffs
    if isinstance(a, dict):
        keys = set(a.keys()) | set(b.keys())
        for k in sorted(keys):
            pa = a.get(k, "<MISSING>"); pb = b.get(k, "<MISSING>")
            diffs += _walk(pa, pb, f"{prefix}.{k}" if prefix else k)
        return diffs
    if isinstance(a, list):
        n = max(len(a), len(b))
        for i in range(n):
            va = a[i] if i < len(a) else "<MISSING>"
            vb = b[i] if i < len(b) else "<MISSING>"
            diffs += _walk(va, vb, f"{prefix}[{i}]")
        return diffs
    if a != b:
        diffs.append((prefix, a, b))
    return diffs

def diff_manifests(a_manifest: pathlib.Path, b_manifest: pathlib.Path) -> Dict[str, Any]:
    A = _load(a_manifest); B = _load(b_manifest)
    changes = _walk(A, B)
    return {"left": str(a_manifest), "right": str(b_manifest), "changes": [
        {"path": p, "left": la, "right": rb} for (p, la, rb) in changes
    ]}
