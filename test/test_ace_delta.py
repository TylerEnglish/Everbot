import json
from pathlib import Path
from bot.context.ace_delta import compute_delta

def test_delta_with_list():
    prev = [{"text": "A"}, {"text": "B"}]
    new  = [{"text": "B"}, {"text": "C"}]
    d = compute_delta(prev, new)
    assert d["added"] == ["C"]
    assert d["removed"] == ["A"]
    assert d["unchanged"] == ["B"]

def test_delta_with_path(tmp_path: Path):
    p = tmp_path / "bullets.jsonl"
    rows = [{"text": "A"}, {"text": "B"}]
    p.write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
    new = [{"text": "B"}, {"text": "C"}]
    d = compute_delta(p, new)
    assert d["added"] == ["C"]
    assert d["removed"] == ["A"]
    assert d["unchanged"] == ["B"]

def test_delta_none_prev():
    new = [{"text": "X"}, {"text": "Y"}]
    d = compute_delta(None, new)
    assert d["added"] == ["X", "Y"]
    assert d["removed"] == []
    assert d["unchanged"] == []
