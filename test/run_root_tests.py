from __future__ import annotations
import sys, pathlib, json, yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
RULES = ROOT / "ruleset"

def _ok(msg): print(f"[OK] {msg}")
def _fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)

def _load_yaml(name: str):
    p = RULES / name
    if not p.exists():
        _fail(f"Missing ruleset file: {name}")
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as e:
        _fail(f"YAML parse error in {name}: {e}")

def test_rules_safety():
    data = _load_yaml("safety.yaml")
    for k in ("content","context_policy","self_modification","network","fail_fast"):
        if k not in data:
            _fail(f"safety.yaml missing key: {k}")
    # Protected roots are forbidden for self-edits
    forb = set(map(str, data["self_modification"].get("forbidden_paths", [])))
    if not {"ruleset","test"}.issubset(forb):
        _fail("safety.yaml must forbid edits to ruleset/ and test/")
    _ok("safety.yaml sanity")

def test_rules_limits():
    data = _load_yaml("limits.yaml")
    t = int(data.get("tokens_max", 0))
    wall = int(data.get("wall_time_sec_max", 0))
    if not (1024 <= t <= 131072):
        _fail("limits.tokens_max out of reasonable range")
    if wall <= 0:
        _fail("limits.wall_time_sec_max must be > 0")
    _ok("limits.yaml sanity")

def test_rules_promotion():
    data = _load_yaml("promotion.yaml")
    gate = data.get("pareto_gate", {})
    if gate.get("require_no_regressions") is not True:
        _fail("promotion.pareto_gate.require_no_regressions must be true")
    _ok("promotion.yaml sanity")

def test_rules_tools():
    data = _load_yaml("tools.yaml")
    fops = data.get("file_ops", {})
    bl = set(fops.get("write_blacklist", []))
    if not {"ruleset","test"}.issubset(bl):
        _fail("tools.file_ops.write_blacklist must include ruleset and test")
    _ok("tools.yaml sanity")

def test_topics():
    data = _load_yaml("topics.yaml")
    if not data.get("allowed_topics"):
        _fail("topics.allowed_topics must be non-empty")
    _ok("topics.yaml sanity")

def test_sanity_cases_exist():
    p = ROOT / "test" / "sanity" / "cases.yaml"
    if not p.exists():
        _fail("Missing test/sanity/cases.yaml")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not data.get("cases"):
        _fail("sanity/cases.yaml must include at least one case")
    _ok("sanity cases present")

if __name__ == "__main__":
    # run all tests; exit 0 if everything OK
    test_rules_safety()
    test_rules_limits()
    test_rules_promotion()
    test_rules_tools()
    test_topics()
    test_sanity_cases_exist()
    print("PASS")
    sys.exit(0)
