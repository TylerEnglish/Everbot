from __future__ import annotations
import json, pathlib, time
from typing import List, Dict, Any
from bot.paths import get_project_root

_ROOT = get_project_root()
_MEM = _ROOT / "snapshots" / "memory"
_MEM.mkdir(parents=True, exist_ok=True)
_FILE = _MEM / "reflections.jsonl"

def record(topic: str, helped: str, harmed: str, snapshot_dir: pathlib.Path) -> None:
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "topic": topic,
        "helped": helped,
        "harmed": harmed,
        "snapshot": str(snapshot_dir),
    }
    with open(_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")

def recent(topic: str, limit: int = 5) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not _FILE.exists(): return out
    for line in reversed(_FILE.read_text(encoding="utf-8").splitlines()):
        try:
            item = json.loads(line)
            if item.get("topic", "").lower() == topic.lower():
                out.append(item)
                if len(out) >= limit: break
        except Exception:
            continue
    return list(reversed(out))
