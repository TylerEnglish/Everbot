from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple
import os, sys, pathlib, subprocess, time, json, re, difflib

from bot.paths import get_project_root
from bot.llm.provider import ask, info, NotConfigured  # LLM interface


@dataclass
class AutoCodeResult:
    title: str
    patch_path: str               # path to saved .diff (or synthesized diff)
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
        " - Output ONE unified diff across ALL changes. Paths relative to repo root and prefixed in headers as a/ b/ (e.g., 'bot/…').\n"
        " - No commentary; ONLY the diff.\n"
        " - Keep changes focused; ~20-150 lines of diff total.\n"
        "If you cannot produce a valid unified diff, output exactly: #NOPATCH\n\n"
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
    Remove ```...``` fences if present.
    """
    s = _clean_unicode(s)
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

    # Short-circuit explicit no-op marker
    if raw.strip() == "#NOPATCH":
        return ""

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
    # Always write a patch file so you can inspect what the sanitizer (or synthesizer) produced.
    diff_path = base / "patch.diff"
    out = diff if diff else "# empty (no valid unified diff detected)\n"
    # normalize to LF to avoid CRLF double-terminators confusing git apply on Windows
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    if out and not out.endswith("\n"):
        out += "\n"
    diff_path.write_text(out, encoding="utf-8")
    # Lightweight debug print
    try:
        print(f"[autocoder] artifacts -> {base}  (prompt.txt={ (base/'prompt.txt').stat().st_size }B, "
              f"raw.txt={ (base/'raw.txt').stat().st_size }B, patch.diff={ diff_path.stat().st_size }B)")
    except Exception:
        pass
    return base, diff_path, base / "raw.txt"

def _git_apply(patch_path: pathlib.Path) -> bool:
    if not patch_path.exists() or patch_path.stat().st_size == 0:
        return False
    attempts = [
        ["git", "apply", "--index", "--whitespace=fix", str(patch_path)],
        ["git", "apply", "--index", "--recount", "--whitespace=fix", str(patch_path)],
        ["git", "apply", "--index", "--unidiff-zero", "--whitespace=fix", str(patch_path)],
        ["git", "apply", "--index", "-p0", "--whitespace=fix", str(patch_path)],
        ["git", "apply", "--index", "--whitespace=nowarn", str(patch_path)],
        ["git", "apply", "--reject", "--whitespace=nowarn", str(patch_path)],
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

# ---------- synthesis fallback (ensures at least one safe change is made) ----------

def _pick_first_python(hotspots: List[pathlib.Path]) -> Optional[pathlib.Path]:
    for p in hotspots:
        if p.suffix == ".py":
            return p
    return None

def _make_unified_diff(rel_path: str, original: List[str], new_lines: List[str]) -> str:
    """
    Create a robust unified diff with each output line explicitly newline-terminated.
    This avoids malformed hunks on Windows (git apply 'corrupt patch' errors).
    """
    # difflib sometimes returns hunk lines without line terminators; add them.
    raw_lines = list(difflib.unified_diff(
        original,
        new_lines,
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
        lineterm="",   # emit without line ends; we’ll add '\n' to every line
        n=3,
    ))
    body = "".join((ln if ln.endswith("\n") else (ln + "\n")) for ln in raw_lines)
    return f"diff --git a/{rel_path} b/{rel_path}\n{body}"

def _synthesize_python_module_comment_or_docstring(rel_path: str, abs_path: pathlib.Path) -> str:
    """
    Create a minimal, safe unified diff for a Python module:
      - If there is no module docstring at file top, insert one.
      - Else insert a top-level comment after the docstring.
    Normalizes line endings to LF and ensures every hunk line ends with '\n'.
    """
    try:
        data = abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

    # Normalize to LF; split without keeping ends (we’ll manage line ends in the diff)
    data = data.replace("\r\n", "\n").replace("\r", "\n")
    original: List[str] = data.splitlines()
    if not original:
        original = [""]

    # Find insertion point: after shebang / coding lines
    i = 0
    if original and original[0].startswith("#!"):
        i = 1
    if i < len(original) and re.match(r"#\s*-\*-\s*coding:\s*.*-\*-\s*$", original[i]):
        i += 1
    if i < len(original) and re.match(r"#\s*coding[:=]\s*.*$", original[i]):
        i += 1

    # Detect existing docstring at top
    def _is_triple_quote_line(s: str) -> bool:
        s2 = s.lstrip()
        return s2.startswith('"""') or s2.startswith("'''")

    insert_line: str
    j = i
    if j < len(original) and _is_triple_quote_line(original[j]):
        # Skip past the closing triple-quote
        quote = '"""' if original[j].lstrip().startswith('"""') else "'''"
        j += 1
        while j < len(original):
            if quote in original[j]:
                j += 1
                break
            j += 1
        insert_line = "# Autocode: non-functional comment."
        ins_idx = j
    else:
        insert_line = '"""Autocode: module-level docstring (non-functional)."""'
        ins_idx = i

    new_lines = original[:ins_idx] + [insert_line] + original[ins_idx:]
    return _make_unified_diff(rel_path, original, new_lines)

# ---------------- public API ----------------

def propose_and_apply_refactor(max_files: int = 5, retries: int = 2) -> Optional[AutoCodeResult]:
    """
    1) Pick hotspot files under bot/
    2) Ask the LLM for a single unified diff (retry with a repair prompt if needed)
    3) If still no valid diff, synthesize a minimal safe patch for a Python hotspot
    4) Save prompt/raw/diff under snapshots/selfopt/autocode/<ts>/
    5) Try to git-apply with fallbacks
    6) Save PR.md with title/summary derived from diff
    7) Return result (caller gates adoption behind tests/evals)
     """
    if not _llm_ok():
        return None

    root = get_project_root()
    hotspots = _scan_hotspots(root, max_files=max_files)
    if not hotspots:
        return None

    prompt = _build_prompt(hotspots)
    raw = ask(prompt, max_tokens=2800, temperature=0.15)

    def looks_like_diff(d: str) -> bool:
        # Require headers and at least one hunk to avoid empty headers
        return ("diff --git " in d and "+++ " in d and "--- " in d and "@@ " in d)

    diff = _sanitize_to_diff(raw)

    # Stronger repair loop with explicit instructions + tiny template example
    attempt = 0
    while not looks_like_diff(diff) and attempt < max(2, retries):
        attempt += 1
        repair_prompt = (
            "Convert the following into a VALID unified diff. Rules:\n"
            " - Only output the diff, no commentary or code fences.\n"
            " - Every file must have 'diff --git a/… b/…' and matching '--- a/…' + '+++ b/…' headers.\n"
            " - Include at least one '@@' hunk per file with correct line numbers.\n"
            "Minimal example:\n"
            "diff --git a/bot/example.py b/bot/example.py\n"
            "--- a/bot/example.py\n"
            "+++ b/bot/example.py\n"
            "@@ -1,3 +1,5 @@\n"
            " import os\n"
            "+# Autocode: non-functional comment\n"
            " def f():\n"
            "     return 1\n\n"
            "OUTPUT TO CONVERT:\n"
        ) + raw
        raw2 = ask(repair_prompt, max_tokens=2200, temperature=0.0)
        diff = _sanitize_to_diff(raw2)
        if diff.strip():
            raw = raw2

    # Fallback: synthesize a minimal safe patch so the pipeline always modifies something
    synth_used = False
    if not looks_like_diff(diff):
        target = _pick_first_python(hotspots)
        if target is not None:
            rel = str(target.relative_to(root).as_posix())
            if rel.startswith("bot/") and not _is_excluded_path(rel):
                synth = _synthesize_python_module_comment_or_docstring(rel, target)
                if synth.strip():
                    diff = synth
                    raw = synth
                    synth_used = True

    art_dir, diff_path, raw_path = _write_artifacts(prompt, raw, diff)
    touched = _touch_list_from_diff(diff) if looks_like_diff(diff) else []

    if not looks_like_diff(diff):
        # No valid diff even after synthesis
        return AutoCodeResult(
            title="autocode-refactor",
            patch_path=str(diff_path),               # point to patch.diff for consistency
            patch_applied=False,
            touched_paths=[],
            artifacts_dir=str(art_dir),
            note="LLM did not produce a valid unified diff (no changes applied)",
        )

    # Try to apply
    applied = _git_apply(diff_path)
    note_bits = []
    if synth_used:
        note_bits.append("synthesized safe patch")
    note_bits.append("patch applied via git apply" if applied else "patch did NOT apply (see .rej files if any)")
    note = "; ".join(note_bits)

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
