from __future__ import annotations
import re, yaml, pathlib
from typing import Dict, Any, List
from bot.paths import get_project_root
from bot.llm.provider import ask, NotConfigured

ROOT = get_project_root()

def run_conversation_sanity() -> Dict[str, Any]:
    cases_p = ROOT / "test" / "sanity" / "cases.yaml"
    if not cases_p.exists():
        return {"status": "SKIP", "detail": "No sanity cases at test/sanity/cases.yaml"}
    try:
        cases = yaml.safe_load(cases_p.read_text(encoding="utf-8"))
        passed, total, details = 0, 0, []
        for c in cases:
            total += 1
            prompt = c["prompt"]
            pat = re.compile(c["must_match"])
            try:
                resp = ask(prompt)
                ok = bool(pat.search(resp))
                passed += int(ok)
                details.append({"id": c["id"], "ok": ok, "resp": resp[:200]})
            except NotConfigured:
                return {"status": "SKIP", "detail": "No LLM backend configured (set EVERABOT_LLM=ollama)"}
        return {
            "status": "PASS" if passed == total else "FAIL",
            "detail": f"{passed}/{total} cases matched",
            "cases": details
        }
    except Exception as e:
        return {"status": "FAIL", "detail": f"sanity runner error: {type(e).__name__}: {e}"}
