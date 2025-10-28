from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple
import os, sys, pathlib, subprocess, time, json, re, difflib

from bot.paths import get_project_root
from bot.llm.provider import ask, info, NotConfigured  # LLM interface

# Optional kill-switch to disable synthesized fallback patches entirely.
_NO_SYNTH = os.getenv("EVERABOT_AUTOCODE_NO_SYNTH", "").strip() == "1"

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

# explicitly avoid files that fight with idempotent markers / CLI churn
_EXCLUDE_FILES = {"bot/cli.py"}

# Marker used in our synthesized patch comment so hotspot scan will spread edits.
_AUTOCODE_MARKER = "Autocode: non-functional"

# Heuristics: treat added lines as "non-functional" if they’re obviously just comments or docstring fences.
_DOC_OR_COMMENT_START = re.compile(r'^\+\s*(#|[rubfRUBF]?("""|\'\'\'))')
_DOC_OR_COMMENT_END   = re.compile(r'^\+\s*("""|\'\'\')\s*$')


def _llm_ok() -> bool:
    try:
        _ = info()  # raises NotConfigured if missing
        return True
    except NotConfigured:
        return False


def _scan_hotspots(root: pathlib.Path, max_files: int = 5) -> List[pathlib.Path]:
    """
    Prefer longer files and those with TODO/FIXME; scan common languages; skip noisy/guarded dirs.
    Penalize files we've already touched (marker present) to spread edits.
    """
    repo_dir = root / "bot"
    files: list[tuple[int, int, pathlib.Path]] = []
    for pat in _CODE_GLOBS:
        for p in repo_dir.rglob(pat):
            parts = {"/".join(p.parts[i:i+2]) for i in range(len(p.parts)-1)} | set(p.parts)
            if any(x in _EXCLUDE_DIRS for x in parts):
                continue
            rel_posix = p.relative_to(root).as_posix()
            if rel_posix in _EXCLUDE_FILES:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            length = text.count("\n")
            todos = text.upper().count("TODO") + text.upper().count("FIXME")
            touched_penalty = 200000 if "Autocode:" in text else 0
            score = (3 * todos + length) - touched_penalty
            files.append((score, length, p))
    files.sort(reverse=True)
    return [p for _, __, p in files[:max_files]]


def _build_prompt(file_paths: List[pathlib.Path]) -> str:
    header = (
        "You are Everabot's internal code refactorer.\n"
        "Goals: safe, behavior-preserving improvements: stronger type hints, clearer names, input validation, "
        "improved error messages, small helpers, and micro-optimizations. "
        "Avoid comment-only or docstring-only patches.\n"
        "Constraints:\n"
        " - Only modify files under 'bot/'.\n"
        " - Hard-ban generated/build/vendor paths (target/, build/, dist/, node_modules/, __pycache__/ ...).\n"
        " - Do NOT touch 'ruleset/' or 'test/'.\n"
        " - Output ONE unified diff across ALL changes. Paths relative to repo root and prefixed in headers as a/ b/ (e.g., 'bot/…').\n"
        " - No commentary; ONLY the diff.\n"
        " - Keep changes focused; ~20-150 lines of diff total.\n"
        " - Avoid editing 'bot/cli.py'; prefer utility modules or helpers.\n"
        " - Make at least one functional-neutral code improvement (e.g., type hints on public functions, "
        "   clearer exceptions, factoring a helper). Do NOT submit patches that are only comments or docstrings.\n"
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


def _norm_sep(path: str) -> str:
    """
    Normalize Git header paths to forward slashes; strip leading ./ .
    """
    if not path:
        return path
    return path.replace("\\", "/").lstrip("./")


def _normalize_diff_git_headers(text: str) -> str:
    """
    Ensure 'diff --git' headers use 'a/<path> b/<path>' and align with ---/+++.
    """
    out_lines: list[str] = []
    last_minus: Optional[str] = None
    last_plus: Optional[str] = None
    for ln in text.splitlines():
        if ln.startswith("--- "):
            path = _norm_sep(ln[4:].strip())
            if not (path.startswith("a/") or path.startswith("/dev/null")):
                path = "a/" + path
            ln = f"--- {path}"
            last_minus = path[2:] if path.startswith("a/") else None
        elif ln.startswith("+++ "):
            path = _norm_sep(ln[4:].strip())
            if not (path.startswith("b/") or path.startswith("/dev/null")):
                path = "b/" + path
            ln = f"+++ {path}"
            last_plus = path[2:] if path.startswith("b/") else None
        elif ln.startswith("diff --git "):
            m = re.match(r"^diff --git\s+(.+?)\s+(.+)$", ln)
            if m:
                a, b = _norm_sep(m.group(1).strip()), _norm_sep(m.group(2).strip())
                if not a.startswith("a/"):
                    a = f"a/{a}"
                if not b.startswith("b/"):
                    b = f"b/{b}"
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
    # normalize slashes and strip potential a/ or b/ prefixes
    p = _norm_sep(p)
    if p.startswith("a/") or p.startswith("b/"):
        p = p[2:]
    parts = p.split("/")
    pairs = set("/".join(parts[i:i+2]) for i in range(len(parts)-1))
    if p in _EXCLUDE_FILES:
        return True
    return (not p.startswith("bot/")) or any(x in _EXCLUDE_DIRS for x in (set(parts) | pairs))


def _block_has_only_comment_or_doc_additions(hunk_lines: List[str]) -> bool:
    """
    True if every added line looks like a comment or a docstring fence/body.
    Heuristic: treat empty/whitespace lines as non-functional too.
    """
    added = [ln for ln in hunk_lines if ln.startswith("+")]
    if not added:
        return False
    # Fast-path: if any '+' line looks like code (def/class/import/raise/return/with/try/except/=),
    # treat as functional-neutral code (allowed).
    codeish = re.compile(r'^\+\s*(def |class |from |import |raise |return |with |try:|except |if |for |while |@|[A-Za-z_]\w*\s*=)')
    for ln in added:
        if codeish.match(ln):
            return False
    # Otherwise, if all are comments/doc fences/whitespace, mark as doc/comment-only.
    for ln in added:
        body = ln[1:]
        if body.strip() == "":
            continue
        if _DOC_OR_COMMENT_START.match(ln) or _DOC_OR_COMMENT_END.match(ln):
            continue
        # Interior docstring text (no quotes) is hard to detect; allow if it contains obvious code tokens.
        if any(tok in body for tok in ("()", "{}", "[]", " = ", " -> ", ": ", "lambda ", " yield", " except", " try:", " return ")):
            return False
        # Default: treat as doc/comment-ish (plain text) and continue
    return True


def _rebuild_block(blk: list[str]) -> Optional[str]:
    """
    Rebuild a single file diff block into a canonical minimal diff:
      diff --git a/X b/X
      --- a/X
      +++ b/X
      @@ hunk
      (hunk body)
    Drops blocks that are exclusively docstring/comment additions.
    """
    head = next((x for x in blk if x.startswith("diff --git ")), None)
    if not head:
        return None

    m = re.match(r"^diff --git\s+(.+?)\s+(.+)$", head.strip())
    if not m:
        return None

    a_hdr, b_hdr = m.group(1).strip(), m.group(2).strip()

    def _norm_body(p: str) -> str:
        p = p.replace("\\", "/").lstrip("./")
        if p.startswith("a/") or p.startswith("b/"):
            p = p[2:]
        return p

    a_body = _norm_body(a_hdr)
    b_body = _norm_body(b_hdr)

    if _is_excluded_path(a_body) or _is_excluded_path(b_body):
        return None

    # Collect all hunks
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

    if not have_hunk or not lines:
        return None

    # Reject doc/comment-only additions (prevents the docstring spam)
    if _block_has_only_comment_or_doc_additions(lines):
        return None

    a_canon = a_body
    b_canon = b_body
    rebuilt = [
        f"diff --git a/{a_canon} b/{b_canon}",
        f"--- a/{a_canon}",
        f"+++ b/{b_canon}",
    ]
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
    Also drops blocks that are doc/comment-only.
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
        if line.startswith("--- "):
            path = _norm_sep(line[4:].strip())
            if not (path.startswith("a/") or path.startswith("/dev/null")):
                return "--- a/" + path
        if line.startswith("+++ "):
            path = _norm_sep(line[4:].strip())
            if not (path.startswith("b/") or path.startswith("/dev/null")):
                return "+++ b/" + path
        return line

    for ln in lines:
        if ln.startswith(_ALLOWED_DIFF_PREFIXES):
            if ln.startswith(("--- ", "+++ ")):
                ln = _ensure_ab(ln)
            out.append(ln)
            if ln.startswith("@@ "):
                in_hunk = True
            continue

        if in_hunk:
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
    raw_lines = list(difflib.unified_diff(
        original,
        new_lines,
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
        lineterm="",
        n=3,
    ))
    body = "".join((ln if ln.endswith("\n") else (ln + "\n")) for ln in raw_lines)
    return f"diff --git a/{rel_path} b/{rel_path}\n{body}"


def _synthesize_python_type_checking_noop(rel_path: str, abs_path: pathlib.Path) -> str:
    """
    Synthesize a tiny, behavior-neutral code change that is NOT a docstring/comment:
      * Insert a TYPE_CHECKING no-op block (no runtime effect) and tag with our marker comment.
    """
    try:
        data = abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    data = data.replace("\r\n", "\n").replace("\r", "\n")
    if "TYPE_CHECKING" in data and "Autocode:" in data:
        return ""  # already has our no-op
    original: List[str] = data.splitlines()
    if not original:
        original = [""]

    # Find insertion point: after shebang/coding and right after top import block if present.
    i = 0
    if original and original[0].startswith("#!"):
        i = 1
    if i < len(original) and re.match(r"#\s*-\*-\s*coding:\s*.*-\*-\s*$", original[i]):
        i += 1
    if i < len(original) and re.match(r"#\s*coding[:=]\s*.*$", original[i]):
        i += 1

    # Move past leading module docstring if present (we won't add another one).
    def _is_triple_quote_line(s: str) -> bool:
        s2 = s.lstrip()
        return s2.startswith('"""') or s2.startswith("'''")

    j = i
    if j < len(original) and _is_triple_quote_line(original[j]):
        quote = '"""' if original[j].lstrip().startswith('"""') else "'''"
        j += 1
        while j < len(original):
            if quote in original[j]:
                j += 1
                break

    # Move past top import block (import/from), so we inject below imports.
    k = j
    while k < len(original) and re.match(r"\s*(from\s+\S+\s+import|import\s+\S+)", original[k]):
        k += 1

    insert_idx = k
    lines_to_insert = []
    # Ensure the TYPE_CHECKING import exists only once
    has_tc_import = any(re.match(r"\s*from\s+typing\s+import\s+TYPE_CHECKING\b", ln) for ln in original)
    if not has_tc_import:
        lines_to_insert.append("from typing import TYPE_CHECKING")
    lines_to_insert.append("if TYPE_CHECKING:")
    lines_to_insert.append("    pass  # Autocode: non-functional type-only block")
    # Build new file
    new_lines = original[:insert_idx] + lines_to_insert + original[insert_idx:]
    return _make_unified_diff(rel_path, original, new_lines)


# ---------------- public API ----------------

def propose_and_apply_refactor(max_files: int = 5, retries: int = 2) -> Optional[AutoCodeResult]:
    """
    1) Pick hotspot files under bot/
    2) Ask the LLM for a single unified diff (retry with a repair prompt if needed)
    3) If still no valid diff, synthesize a minimal safe patch (TYPE_CHECKING no-op)
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
            " - Avoid comment-only or docstring-only changes.\n"
            "Minimal example:\n"
            "diff --git a/bot/autocoder.py b/bot/autocoder.py\n"
            "--- a/bot/autocoder.py\n"
            "+++ b/bot/autocoder.py\n"
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

    # Optional: disable synth fallback if user wants only LLM-derived diffs
    if not looks_like_diff(diff) and _NO_SYNTH:
        art_dir, diff_path, _ = _write_artifacts(prompt, raw, "")
        note = "LLM diff invalid; synth disabled via EVERABOT_AUTOCODE_NO_SYNTH=1"
        return AutoCodeResult(
            title="autocode-refactor", patch_path=str(diff_path), patch_applied=False,
            touched_paths=[], artifacts_dir=str(art_dir), note=note
        )

    # Fallback: synthesize a minimal safe patch so the pipeline always modifies something
    synth_used = False
    if not looks_like_diff(diff):
        target = _pick_first_python(hotspots)
        if target is not None:
            rel = str(target.relative_to(root).as_posix())
            if rel.startswith("bot/") and not _is_excluded_path(rel):
                synth = _synthesize_python_type_checking_noop(rel, target)
                if synth.strip():
                    diff = synth
                    raw = synth
                    synth_used = True

    art_dir, diff_path, _ = _write_artifacts(prompt, raw, diff)
    touched = _touch_list_from_diff(diff) if looks_like_diff(diff) else []

    applied = False
    if looks_like_diff(diff):
        applied = _git_apply(diff_path)

    # If nothing applied, try a synthesized TYPE_CHECKING no-op on the first eligible Python file
    if not applied:
        root = get_project_root()
        for cand in hotspots:
            if cand.suffix != ".py":
                continue
            rel = str(cand.relative_to(root).as_posix())
            if _is_excluded_path(rel):
                continue
            synth = _synthesize_python_type_checking_noop(rel, cand)
            if not synth.strip():
                continue
            _, diff_path, _ = _write_artifacts(prompt, synth, synth)
            applied = _git_apply(diff_path)
            if applied:
                touched = _touch_list_from_diff(synth)
                raw = synth
                synth_used = True
                break

    note_bits = []
    if synth_used:
        note_bits.append("used synth fallback (TYPE_CHECKING no-op) due to invalid or non-applying LLM diff")
    note_bits.append("patch applied via git apply" if applied else "patch did NOT apply (see .rej files if any)")
    note = "; ".join(note_bits)

    # Generate PR.md alongside artifacts
    try:
        pr_text = _generate_pr_md(raw if raw else "")
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
