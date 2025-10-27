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
_CODE_GLOBS = ("*.py","*.rs","*.go","*.c","*.cc","*.cpp","*.h","*.hpp","*.s","*.asm")
# avoid generated/build/vendor paths entirely
_EXCLUDE_DIRS = {
    "test","ruleset","snapshots","accel/rust/target","accel/cpp/build",
    ".git","dist","build",".venv","venv","node_modules","__pycache__"
}

def _llm_ok() -> bool:
    try:
        _ = info()  # raises NotConfigured if missing
        return True
    except NotConfigured:
        return False

def _scan_hotspots(root: pathlib.Path, max_files: int = 5) -> List[pathlib.Path]:
    """
    Prefer longer files and those with TODO/FIXME; scan common languages; skip noisy/guarded dirs.
    """
    repo_dir = root / "bot"
    files: list[tuple[int, int, pathlib.Path]] = []
    for pat in _CODE_GLOBS:
        for p in repo_dir.rglob(pat):
            parts = {"/".join(p.parts[i:i+2]) for i in range(len(p.parts)-1)} | set(p.parts)
            if any(x in _EXCLUDE_DIRS for x in parts):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            length = text.count("\n")
            todos = text.upper().count("TODO") + text.upper().count("FIXME")
            score = 3 * todos + length
            files.append((score, length, p))
    files.sort(reverse=True)
    return [p for _, __, p in files[:max_files]]

def _build_prompt(file_paths: List[pathlib.Path]) -> str:
    header = (
        "You are Everabot's internal code refactorer.\n"
        "Goals: safe improvements (docstrings, type hints, clearer names, micro-opts, better errors) "
        "without changing behavior. Multi-language is OK (Python/C/C++/Rust/Go/ASM) if helpful.\n"
        "Constraints:\n"
        " - Only modify files under 'bot/'.\n"
        " - Hard-ban generated/build/vendor paths (target/, build/, dist/, node_modules/, __pycache__/ ...).\n"
        " - Do NOT touch 'ruleset/' or 'test/'.\n"
        " - Output ONE unified diff across ALL changes. Paths relative to repo root (e.g., 'bot/…').\n"
        " - No commentary; ONLY the diff.\n"
        " - Keep changes focused; ~20-150 lines of diff total.\n\n"
    )
    context = []
    for p in file_paths:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            context.append(f"--- FILE: {p.as_posix()} ---\n{text}\n")
        except Exception:
            pass
    return header + "\n".join(context)

def _clean_unicode(s: str) -> str:
    # Normalize line endings, strip BOM, and collapse NBSP to regular spaces.
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\u00A0", " ")
    if s.startswith("\ufeff"):
        s = s.lstrip("\ufeff")
    return s

def _strip_code_fences(s: str) -> str:
    """
    Remove ```...``` fences if present. The previous pattern used '\\n' (literal) instead of a newline.
    """
    s = _clean_unicode(s)
    # Remove an opening code fence with optional language and the closing fence
    s = re.sub(r"```(?:[a-zA-Z0-9_-]+)?\n", "", s)
    s = re.sub(r"```", "", s)
    return s

def _normalize_diff_git_headers(text: str) -> str:
    """
    Ensure 'diff --git' headers use 'a/<path> b/<path>' and align with ---/+++.
    """
    out_lines: list[str] = []
    last_minus: Optional[str] = None
    last_plus: Optional[str] = None
    for ln in text.splitlines():
        if ln.startswith("--- "):
            path = ln[4:].strip()
            if not (path.startswith("a/") or path.startswith("/dev/null")):
                path = "a/" + path.lstrip("./")
            ln = f"--- {path}"
            last_minus = path[2:] if path.startswith("a/") else None
        elif ln.startswith("+++ "):
            path = ln[4:].strip()
            if not (path.startswith("b/") or path.startswith("/dev/null")):
                path = "b/" + path.lstrip("./")
            ln = f"+++ {path}"
            last_plus = path[2:] if path.startswith("b/") else None
        elif ln.startswith("diff --git "):
            m = re.match(r"^diff --git\s+(.+?)\s+(.+)$", ln)
            if m:
                a, b = m.group(1).strip(), m.group(2).strip()
                if not a.startswith("a/"):
                    a = f"a/{a.lstrip('./')}"
                if not b.startswith("b/"):
                    b = f"b/{b.lstrip('./')}"
                ln = f"diff --git {a} {b}"
            else:
                if last_minus and last_plus:
                    ln = f"diff --git a/{last_minus} b/{last_plus}"
        out_lines.append(ln)
    return "\n".join(out_lines)

def _split_blocks(text: str) -> list[list[str]]:
    """Split a normalized diff into file blocks starting at 'diff --git'."""
    blocks: list[list[str]] = []
    cur: list[str] = []
    for ln in text.splitlines():
        if ln.startswith("diff --git "):
            if cur:
                blocks.append(cur)
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        blocks.append(cur)
    return blocks

def _is_excluded_path(p: str) -> bool:
    parts = p.split("/")
    pairs = set("/".join(parts[i:i+2]) for i in range(len(parts)-1))
    return (not p.startswith("bot/")) or any(x in _EXCLUDE_DIRS for x in (set(parts) | pairs))

def _rebuild_block(blk: list[str]) -> Optional[str]:
    """
    Rebuild a single file diff block into a canonical minimal diff:
    diff --git a/X b/X
    --- a/X
    +++ b/X
    @@ hunk
    (hunk body)
    """
    head = next((x for x in blk if x.startswith("diff --git ")), None)
    if not head:
        return None
    m = re.match(r"^diff --git\s+a/(.+?)\s+b/(.+?)$", head.strip())
    if not m:
        return None
    a_path, b_path = m.group(1), m.group(2)
    if _is_excluded_path(a_path) or _is_excluded_path(b_path):
        return None

    # Collect hunks and body
    lines: list[str] = []
    have_hunk = False
    in_hunk = False
    for ln in blk:
        if ln.startswith("@@ "):
            lines.append(ln)
            have_hunk = True
            in_hunk = True
            continue
        if in_hunk:
            if ln.startswith((" ", "+", "-")):
                lines.append(ln)
            else:
                in_hunk = False

    if not have_hunk:
        return None

    rebuilt = []
    rebuilt.append(f"diff --git a/{a_path} b/{b_path}")
    rebuilt.append(f"--- a/{a_path}")
    rebuilt.append(f"+++ b/{b_path}")
    rebuilt.extend(lines)
    return "\n".join(rebuilt)

def _canonicalize_blocks(text: str) -> str:
    """
    Keep only valid file blocks under bot/ and rebuild them into a
    canonical Git-style unified diff (ordering & headers fixed).
    """
    out: list[str] = []
    for blk in _split_blocks(text):
        rebuilt = _rebuild_block(blk)
        if rebuilt:
            out.append(rebuilt)
    return ("\n".join(out) + ("\n" if out else ""))

def _sanitize_to_diff(raw: str) -> str:
    """
    Keep only diff-looking lines and valid hunk bodies; fix a/b prefixes if missing;
    then canonicalize into minimal per-file diffs.
    """
    raw = _strip_code_fences(raw)
    raw = _clean_unicode(raw)

    lines = raw.splitlines()
    out: list[str] = []
    in_hunk = False

    def _ensure_ab(line: str) -> str:
        # normalize ---/+++ headers to have a/ b/ prefixes when paths look relative
        if line.startswith("--- "):
            path = line[4:].strip()
            if not (path.startswith("a/") or path.startswith("/dev/null")):
                return "--- a/" + path.lstrip("./")
        if line.startswith("+++ "):
            path = line[4:].strip()
            if not (path.startswith("b/") or path.startswith("/dev/null")):
                return "+++ b/" + path.lstrip("./")
        return line

    for ln in lines:
        if ln.startswith(_ALLOWED_DIFF_PREFIXES):
            if ln.startswith("--- ") or ln.startswith("+++ "):
                ln = _ensure_ab(ln)
            out.append(ln)
            if ln.startswith("@@ "):
                in_hunk = True
            continue

        if in_hunk:
            # hunk body lines must be one of ' ', '+', '-'
            if ln.startswith((" ", "+", "-")):
                out.append(ln)
                continue
            else:
                in_hunk = False

    text = "\n".join(out).strip()

    # If we have +++/--- but no diff --git headers, synthesize minimal headers per file.
    if text and "diff --git " not in text:
        cur: List[str] = []
        fixed: List[str] = []
        for ln in text.splitlines():
            cur.append(ln)
            if ln.startswith("+++ "):
                plus_path = ln[4:].strip()
                minus_path = None
                for back in reversed(cur[:-1]):
                    if back.startswith("--- "):
                        minus_path = back[4:].strip()
                        break
                if plus_path and minus_path:
                    fixed.insert(0, f"diff --git {minus_path} {plus_path}")
        if fixed:
            text = "\n".join(fixed) + "\n" + text

    # Normalize headers and canonicalize blocks under bot/
    text = _normalize_diff_git_headers(text) if text else ""
    text = _canonicalize_blocks(text) if text else ""
    return (text + "\n") if text else ""

def _write_artifacts(prompt: str, raw: str, diff: str) -> Tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    base = get_project_root() / "snapshots" / "selfopt" / "autocode" / time.strftime("%Y%m%d_%H%M%S")
    base.mkdir(parents=True, exist_ok=True)
    (base / "prompt.txt").write_text(prompt, encoding="utf-8")
    (base / "raw.txt").write_text(raw, encoding="utf-8")
    diff_path = base / "patch.diff"
    if diff:
        # Ensure trailing newline
        if not diff.endswith("\n"):
            diff = diff + "\n"
        (base / "patch.diff").write_text(diff, encoding="utf-8")
    return base, diff_path, base / "raw.txt"

def _git_apply(patch_path: pathlib.Path) -> bool:
    if not patch_path.exists() or patch_path.stat().st_size == 0:
        return False
    attempts = [
        ["git", "apply", "--index", "--whitespace=fix", str(patch_path)],
        ["git", "apply", "--index", "--recount", "--whitespace=fix", str(patch_path)],
        ["git", "apply", "--index", "--unidiff-zero", str(patch_path)],
        ["git", "apply", "--index", "-p0", str(patch_path)],
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
                if path.startswith("bot/"):
                    parts = path.split("/")
                    pairs = set("/".join(parts[i:i+2]) for i in range(len(parts)-1))
                    if not any(x in _EXCLUDE_DIRS for x in (set(parts) | pairs)):
                        touched.append(path)
    return touched

def _generate_pr_md(diff_text: str) -> str:
    prompt = (
        "Write a crisp PR title and summary for the following unified diff. "
        "Keep it factual and short. Include a one-line title, a short bullet list of changes, "
        "risks, and a minimal test plan. Prefer language-agnostic phrasing.\n\nDIFF:\n" + diff_text
    )
    try:
        return ask(prompt, max_tokens=400, temperature=0.2)
    except Exception:
        return "Autocode PR\n\n- Changes: refactors & cleanup\n- Risks: low\n- Test plan: root eval + basic builds"

# ---------------- public API ----------------

def propose_and_apply_refactor(max_files: int = 5, retries: int = 2) -> Optional[AutoCodeResult]:
    """
    1) Pick hotspot files under bot/
    2) Ask the LLM for a single unified diff (retry with a repair prompt if needed)
    3) Save prompt/raw/diff under snapshots/selfopt/autocode/<ts>/
    4) Try to git-apply with fallbacks
    5) Save PR.md with title/summary derived from diff
    6) Return result (caller gates adoption behind tests/evals)
     """
    if not _llm_ok():
        return None

    root = get_project_root()
    hotspots = _scan_hotspots(root, max_files=max_files)
    if not hotspots:
        return None

    prompt = _build_prompt(hotspots)
    raw = ask(prompt, max_tokens=2500, temperature=0.2)

    def looks_like_diff(d: str) -> bool:
        return ("diff --git " in d and "+++ " in d and "--- " in d and "@@ " in d)

    diff = _sanitize_to_diff(raw)
    attempt = 0
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
            raw = raw2

    art_dir, diff_path, raw_path = _write_artifacts(prompt, raw, diff)
    touched = _touch_list_from_diff(diff) if looks_like_diff(diff) else []

    if not looks_like_diff(diff):
        return AutoCodeResult(
            title="autocode-refactor",
            patch_path=str(raw_path),
            patch_applied=False,
            touched_paths=[],
            artifacts_dir=str(art_dir),
            note="LLM did not produce a valid unified diff",
        )

    # Try to apply
    applied = _git_apply(diff_path)
    note = "patch applied via git apply" if applied else "patch did NOT apply (see .rej files if any)"

    # Generate PR.md alongside artifacts
    try:
        pr_text = _generate_pr_md(diff)
        (pathlib.Path(art_dir) / "PR.md").write_text(pr_text, encoding="utf-8")
    except Exception:
        pass

    return AutoCodeResult(
        title="autocode-refactor",
        patch_path=str(diff_path),
        patch_applied=bool(applied),
        touched_paths=touched,
        artifacts_dir=str(art_dir),
        note=note,
    )
