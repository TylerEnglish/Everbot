from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple
import os, sys, pathlib, subprocess, time, json, re

from bot.paths import get_project_root
from bot.llm.provider import ask, info, NotConfigured  # LLM interface


@dataclass
class AutoCodeResult:
    title: str
    patch_path: str               # path to saved .diff (or raw output if no diff)
    patch_applied: bool
    touched_paths: List[str]
    artifacts_dir: str            # folder with prompt/raw/diff for this attempt
    note: str                     # human-readable status


# ---------------- internals ----------------

_ALLOWED_DIFF_PREFIXES = ("diff --git", "index ", "--- ", "+++ ", "@@ ")

def _llm_ok() -> bool:
    try:
        _ = info()  # raises NotConfigured if missing
        return True
    except NotConfigured:
        return False

def _scan_hotspots(root: pathlib.Path, max_files: int = 3) -> List[pathlib.Path]:
    """
    Ultra-light heuristic: prefer longer files with TODO markers; skip noisy/guarded dirs.
    """
    bot_dir = root / "bot"
    files: list[tuple[int, int, pathlib.Path]] = []
    for p in bot_dir.rglob("*.py"):
        parts = set(p.parts)
        if "test" in parts or "ruleset" in parts or "snapshots" in parts or "accel" in parts:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        length = text.count("\n")
        todos = text.count("TODO") + text.count("FIXME")
        score = 3 * todos + length
        files.append((score, length, p))
    files.sort(reverse=True)
    return [p for _, __, p in files[:max_files]]

def _build_prompt(file_paths: List[pathlib.Path]) -> str:
    header = (
        "You are Everabot's internal code refactorer.\n"
        "Goal: small, safe improvements (docstrings, type hints, clearer names, micro-optimizations, "
        "better error handling) that maintain behavior. You MAY add new helper modules if justified.\n"
        "Constraints:\n"
        " - Only modify files under 'bot/'.\n"
        " - Do NOT touch 'ruleset/' or 'test/'.\n"
        " - Output ONE unified diff across all changes. Use paths relative to repo root (e.g., 'bot/xxx.py').\n"
        " - No commentary, ONLY the diff.\n"
        " - Keep changes focused; prefer 20–150 lines of diff total.\n\n"
    )
    context = []
    for p in file_paths:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            context.append(f"--- FILE: {p.as_posix()} ---\n{text}\n")
        except Exception:
            pass
    return header + "\n".join(context)

def _strip_code_fences(s: str) -> str:
    # remove ```...``` fences if present
    return re.sub(r"```(?:diff|patch)?\\n|```", "", s, flags=re.IGNORECASE)

def _sanitize_to_diff(raw: str) -> str:
    """
    Keep only diff-looking lines and valid hunk bodies; fix a/b prefixes if missing.
    """
    raw = _strip_code_fences(raw).replace("\r\n", "\n")
    lines = raw.splitlines()
    out: list[str] = []
    in_hunk = False

    def _ensure_ab(line: str) -> str:
        # normalize ---/+++ headers to have a/ b/ prefixes when paths look relative
        if line.startswith("--- "):
            path = line[4:].strip()
            if not (path.startswith("a/") or path.startswith("/dev/null")):
                return "--- a/" + path
        if line.startswith("+++ "):
            path = line[4:].strip()
            if not (path.startswith("b/") or path.startswith("/dev/null")):
                return "+++ b/" + path
        return line

    for ln in lines:
        if ln.startswith(_ALLOWED_DIFF_PREFIXES):
            if ln.startswith("--- ") or ln.startswith("+++ "):
                ln = _ensure_ab(ln)
            out.append(ln)
            in_hunk = ln.startswith("@@ ") or in_hunk
            continue

        if in_hunk:
            # hunk body lines must be one of ' ', '+', '-'
            if ln.startswith((" ", "+", "-")):
                out.append(ln)
                continue
            else:
                in_hunk = False

    # If we have +++/--- but no diff --git headers, synthesize minimal headers per file.
    # This helps git apply on simple single-file patches the LLM produces.
    text = "\n".join(out).strip()
    if text and "diff --git " not in text:
        # try to detect current file
        cur: List[str] = []
        fixed: List[str] = []
        for ln in text.splitlines():
            cur.append(ln)
            if ln.startswith("+++ "):
                plus_path = ln[4:].strip()
                minus_path = None
                # find a matching --- line before
                for back in reversed(cur[:-1]):
                    if back.startswith("--- "):
                        minus_path = back[4:].strip()
                        break
                if plus_path and minus_path:
                    fixed.insert(0, f"diff --git {minus_path} {plus_path}")
        if fixed:
            text = "\n".join(fixed) + "\n" + text
    return (text + "\n") if text else ""

def _write_artifacts(prompt: str, raw: str, diff: str) -> Tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    base = get_project_root() / "snapshots" / "selfopt" / "autocode" / time.strftime("%Y%m%d_%H%M%S")
    base.mkdir(parents=True, exist_ok=True)
    (base / "prompt.txt").write_text(prompt, encoding="utf-8")
    (base / "raw.txt").write_text(raw, encoding="utf-8")
    diff_path = base / "patch.diff"
    if diff:
        diff_path.write_text(diff, encoding="utf-8")
    return base, diff_path, base / "raw.txt"

def _git_apply(patch_path: pathlib.Path) -> bool:
    attempts = [
        ["git", "apply", "--index", "--whitespace=fix", str(patch_path)],
        ["git", "apply", "--index", "--ignore-whitespace", str(patch_path)],
        ["git", "apply", "--reject", str(patch_path)],
    ]
    for cmd in attempts:
        try:
            subprocess.check_call(cmd)
            return True
        except Exception:
            continue
    return False

def _touch_list_from_diff(diff_text: str) -> List[str]:
    touched = []
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line.split(" ", 1)[1].strip()
            if path.startswith("b/"):
                path = path[2:]
            if path and not path.startswith("/dev/null"):
                touched.append(path)
    return touched

# ---------------- public API ----------------

def propose_and_apply_refactor(max_files: int = 3, retries: int = 2) -> Optional[AutoCodeResult]:
    """
    1) Pick hotspot files under bot/
    2) Ask the LLM for a single unified diff (retry with a 'convert to diff' repair prompt if needed)
    3) Save prompt/raw/diff under snapshots/selfopt/autocode/<ts>/
    4) Try to git-apply with fallbacks (CRLF, whitespace, reject)
    5) Return result so the workflow can gate adoption behind tests/evals
    """
    if not _llm_ok():
        return None

    root = get_project_root()
    hotspots = _scan_hotspots(root, max_files=max_files)
    if not hotspots:
        return None

    prompt = _build_prompt(hotspots)
    raw = ask(prompt, max_tokens=2500, temperature=0.2)

    diff = _sanitize_to_diff(raw)
    attempt = 0

    # repair pass if not a valid diff-looking output
    def looks_like_diff(d: str) -> bool:
        return ("+++ " in d and "--- " in d and "@@ " in d)

    while not looks_like_diff(diff) and attempt < retries:
        attempt += 1
        repair_prompt = (
            "Convert the following output into a VALID unified diff.\n"
            "Rules:\n"
            " - Only output the diff, no commentary.\n"
            " - Ensure each file has 'diff --git a/… b/…' and '--- a/…' + '+++ b/…' headers.\n"
            " - Include correct '@@' hunks.\n\n"
            "OUTPUT TO CONVERT:\n"
        ) + raw
        raw2 = ask(repair_prompt, max_tokens=2000, temperature=0.0)
        diff = _sanitize_to_diff(raw2)
        if diff.strip():
            raw = raw2  # keep latest raw

    # write artifacts (always)
    art_dir, diff_path, raw_path = _write_artifacts(prompt, raw, diff)
    touched = _touch_list_from_diff(diff) if diff else []

    if not looks_like_diff(diff):
        return AutoCodeResult(
            title="autocode-refactor",
            patch_path=str(raw_path),       # no diff; point at raw so you can inspect
            patch_applied=False,
            touched_paths=[],
            artifacts_dir=str(art_dir),
            note="LLM did not produce a valid unified diff",
        )

    applied = _git_apply(diff_path)
    note = "patch applied via git apply" if applied else "patch did NOT apply (see .rej files if any)"

    return AutoCodeResult(
        title="autocode-refactor",
        patch_path=str(diff_path),
        patch_applied=bool(applied),
        touched_paths=touched,
        artifacts_dir=str(art_dir),
        note=note,
    )
