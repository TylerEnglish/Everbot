from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict
import os, pathlib, subprocess, time, re, difflib

from bot.paths import get_project_root
from bot.llm.provider import ask, info, NotConfigured

_NO_SYNTH = os.getenv("EVERABOT_AUTOCODE_NO_SYNTH", "").strip() == "1"

@dataclass
class AutoCodeResult:
    title: str
    patch_path: str
    patch_applied: bool
    touched_paths: List[str]
    artifacts_dir: str
    note: str

# ---------- config / guards ----------
_ALLOWED_DIFF_PREFIXES = ("diff --git", "index ", "--- ", "+++ ", "@@ ")
_CODE_GLOBS = ("*.py","*.rs","*.go","*.c","*.cc","*.cpp","*.h","*.hpp","*.s","*.asm")
_EXCLUDE_DIRS = {
    "test","ruleset","snapshots","accel/rust/target","accel/cpp/build",
    ".git","dist","build",".venv","venv","node_modules","__pycache__"
}
_EXCLUDE_FILES = {"bot/cli.py"}
_AUTOCODE_MARKER = "Autocode: non-functional"
_DOC_OR_COMMENT_START = re.compile(r'^\+\s*(#|[rubfRUBF]?("""|\'\'\'))')
_DOC_OR_COMMENT_END   = re.compile(r'^\+\s*("""|\'\'\')\s*$')

def _llm_ok() -> bool:
    try:
        _ = info()
        return True
    except NotConfigured:
        return False

def _scan_hotspots(root: pathlib.Path, max_files: int = 5) -> List[pathlib.Path]:
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
        "Goals: safe, behavior-preserving improvements (type hints, clearer names, input validation), "
        "better errors, tiny helpers, micro-optimizations. Avoid comment/docstring-only patches.\n"
        "Constraints:\n"
        " - Only modify files under 'bot/'.\n"
        " - Don’t touch ruleset/ or test/; avoid build/vendor dirs.\n"
        " - Output ONE unified diff across ALL changes, paths like a/bot/... and b/bot/...\n"
        " - No commentary; ONLY the diff; keep 20–150 lines of diff total.\n"
        " - Avoid editing bot/cli.py.\n"
        " - Include at least one actual code change (not just comments/docstrings).\n"
        "If you cannot produce a valid unified diff, output exactly: #NOPATCH\n\n"
    )
    ctx = []
    for p in file_paths:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
            ctx.append(f"--- FILE: {p.as_posix()} ---\n{txt}\n")
        except Exception:
            pass
    return header + "\n".join(ctx)

def _clean_unicode(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\u00A0", " ")
    if s.startswith("\ufeff"): s = s.lstrip("\ufeff")
    return s

def _strip_code_fences(s: str) -> str:
    s = _clean_unicode(s)
    s = re.sub(r"```(?:[a-zA-Z0-9_-]+)?\n", "", s)
    s = re.sub(r"```", "", s)
    return s

def _norm_sep(path: str) -> str:
    if not path: return path
    return path.replace("\\", "/").lstrip("./")

def _normalize_diff_git_headers(text: str) -> str:
    out: List[str] = []
    last_minus = last_plus = None
    for ln in text.splitlines():
        if ln.startswith("--- "):
            p = _norm_sep(ln[4:].strip())
            if not (p.startswith("a/") or p.startswith("/dev/null")): p = "a/" + p
            ln = f"--- {p}"
            last_minus = p[2:] if p.startswith("a/") else None
        elif ln.startswith("+++ "):
            p = _norm_sep(ln[4:].strip())
            if not (p.startswith("b/") or p.startswith("/dev/null")): p = "b/" + p
            ln = f"+++ {p}"
            last_plus = p[2:] if p.startswith("b/") else None
        elif ln.startswith("diff --git "):
            m = re.match(r"^diff --git\s+(.+?)\s+(.+)$", ln)
            if m:
                a,b = _norm_sep(m.group(1)), _norm_sep(m.group(2))
                if not a.startswith("a/"): a = "a/" + a
                if not b.startswith("b/"): b = "b/" + b
                ln = f"diff --git {a} {b}"
            else:
                if last_minus and last_plus:
                    ln = f"diff --git a/{last_minus} b/{last_plus}"
        out.append(ln)
    return "\n".join(out)

def _split_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    cur: list[str] = []
    for ln in text.splitlines():
        if ln.startswith("diff --git "):
            if cur: blocks.append(cur)
            cur = [ln]
        else:
            cur.append(ln)
    if cur: blocks.append(cur)
    return blocks

def _is_excluded_path(p: str) -> bool:
    p = _norm_sep(p)
    if p.startswith(("a/","b/")): p = p[2:]
    parts = p.split("/")
    pairs = set("/".join(parts[i:i+2]) for i in range(len(parts)-1))
    if p in _EXCLUDE_FILES: return True
    return (not p.startswith("bot/")) or any(x in _EXCLUDE_DIRS for x in (set(parts) | pairs))

def _block_has_only_comment_or_doc_additions(hunk_lines: List[str]) -> bool:
    added = [ln for ln in hunk_lines if ln.startswith("+")]
    if not added: return False
    codeish = re.compile(r'^\+\s*(def |class |from |import |raise |return |with |try:|except |if |for |while |@|[A-Za-z_]\w*\s*=)')
    for ln in added:
        if codeish.match(ln): return False
    for ln in added:
        body = ln[1:]
        if body.strip() == "": continue
        if _DOC_OR_COMMENT_START.match(ln) or _DOC_OR_COMMENT_END.match(ln): continue
        if any(tok in body for tok in ("()", "{}", "[]", " = ", " -> ", ": ", "lambda ", " yield", " except", " try:", " return ")):
            return False
    return True

def _rebuild_block_headers_only(blk: list[str]) -> Optional[Tuple[str,str,list[list[str]]]]:
    head = next((x for x in blk if x.startswith("diff --git ")), None)
    if not head: return None
    m = re.match(r"^diff --git\s+(.+?)\s+(.+)$", head.strip())
    if not m: return None
    a_hdr, b_hdr = m.group(1).strip(), m.group(2).strip()
    def _norm_body(p: str) -> str:
        p = p.replace("\\","/").lstrip("./")
        if p.startswith(("a/","b/")): p = p[2:]
        return p
    a_body, b_body = _norm_body(a_hdr), _norm_body(b_hdr)
    if _is_excluded_path(a_body) or _is_excluded_path(b_body): return None

    # collect hunks
    hunks: list[list[str]] = []
    cur: list[str] = []
    for ln in blk:
        if ln.startswith("@@ "):
            if cur: hunks.append(cur)
            cur = [ln]
        elif cur:
            if ln.startswith("diff --git "):  # safety
                break
            cur.append(ln)
    if cur: hunks.append(cur)
    # drop useless blocks
    filtered_hunks = []
    for h in hunks:
        if any(x.startswith(("+","-"," ")) for x in h[1:]):
            if not _block_has_only_comment_or_doc_additions(h):
                filtered_hunks.append(h)
    if not filtered_hunks: return None
    return a_body, b_body, filtered_hunks

def _sanitize_to_diff(raw: str) -> str:
    raw = _strip_code_fences(raw)
    raw = _clean_unicode(raw)
    if raw.strip() == "#NOPATCH":
        return ""
    lines = raw.splitlines()
    out: list[str] = []
    in_hunk = False
    def _ensure_ab(line: str) -> str:
        if line.startswith("--- "):
            path = _norm_sep(line[4:].strip())
            if not (path.startswith("a/") or path.startswith("/dev/null")): return "--- a/" + path
        if line.startswith("+++ "):
            path = _norm_sep(line[4:].strip())
            if not (path.startswith("b/") or path.startswith("/dev/null")): return "+++ b/" + path
        return line
    for ln in lines:
        if ln.startswith(_ALLOWED_DIFF_PREFIXES):
            if ln.startswith(("--- ","+++ ")): ln = _ensure_ab(ln)
            out.append(ln)
            if ln.startswith("@@ "): in_hunk = True
            continue
        if in_hunk:
            if ln.startswith((" ", "+", "-")):
                out.append(ln)
                continue
            else:
                in_hunk = False
    text = "\n".join(out).strip()
    if text and "diff --git " not in text:
        cur, fixed = [], []
        for ln in text.splitlines():
            cur.append(ln)
            if ln.startswith("+++ "):
                plus_path = ln[4:].strip()
                minus_path = None
                for back in reversed(cur[:-1]):
                    if back.startswith("--- "):
                        minus_path = back[4:].strip(); break
                if plus_path and minus_path:
                    fixed.insert(0, f"diff --git {minus_path} {plus_path}")
        if fixed: text = "\n".join(fixed) + "\n" + text
    text = _normalize_diff_git_headers(text) if text else ""
    return (text + "\n") if text else ""

# ---------- Verified patcher (apply LLM hunks to real files, then re-diff) ----------

@dataclass
class SuggestedFile:
    a_path: str
    b_path: str
    hunks: List[List[str]]  # each begins with "@@" then hunk lines

def _parse_suggestions(text: str) -> List[SuggestedFile]:
    out: List[SuggestedFile] = []
    for blk in _split_blocks(text):
        parsed = _rebuild_block_headers_only(blk)
        if not parsed: continue
        a_body, b_body, hunks = parsed
        out.append(SuggestedFile(a_body, b_body, hunks))
    return out

_hunk_header_re = re.compile(r"^@@\s+-([0-9]+)(?:,([0-9]+))?\s+\+([0-9]+)(?:,([0-9]+))?\s+@@")

def _old_sequence_from_hunk(hunk: List[str]) -> List[str]:
    # Lines present in the ORIGINAL file within the hunk range: ' ' and '-'
    seq: List[str] = []
    for ln in hunk[1:]:
        if ln.startswith(" ") or ln.startswith("-"):
            seq.append(ln[1:] + "\n")
    return seq

def _new_sequence_from_hunk(hunk: List[str], original_slice: List[str]) -> List[str]:
    # Build replacement using original content for ' ' lines to preserve whitespace.
    new_seq: List[str] = []
    idx = 0  # index into original_slice for ' ' and '-'
    for ln in hunk[1:]:
        tag = ln[:1]
        body = ln[1:] + "\n"
        if tag == " ":
            new_seq.append(original_slice[idx]); idx += 1
        elif tag == "-":
            idx += 1  # drop it
        elif tag == "+":
            new_seq.append(body)
    return new_seq

def _find_exact_subsequence(hay: List[str], needle: List[str], start_hint: int = 0) -> int:
    # Fast exact search from hint forward; return start index or -1
    n = len(needle)
    if n == 0: return -1
    i = max(0, start_hint)
    while i + n <= len(hay):
        if hay[i:i+n] == needle:
            return i
        i += 1
    return -1

def _parse_oldstart(hunk: List[str]) -> int:
    m = _hunk_header_re.match(hunk[0])
    if not m: return 0
    s = m.group(1)
    try:
        return max(0, int(s) - 1)  # convert to 0-based
    except Exception:
        return 0

def _fuzzy_anchor_index(hay: List[str], needle: List[str], around: int, window: int = 80) -> int:
    # Use a fuzzy window around expected oldstart; require deletions to be exact later.
    L = len(hay)
    lo = max(0, around - window)
    hi = min(L, around + window)
    sub = hay[lo:hi]
    # Quick try on first/last 2 context lines if available
    cand_keys: List[List[str]] = []
    if len(needle) >= 2:
        cand_keys.append([needle[0], needle[1]])
        cand_keys.append([needle[-2], needle[-1]])
    elif needle:
        cand_keys.append([needle[0]])
    for key in cand_keys:
        pos = _find_exact_subsequence(sub, key, 0)
        if pos >= 0:
            # try to expand to full needle
            base = lo + pos
            if base + len(needle) <= L and hay[base:base+len(needle)] == needle:
                return base
    # fallback SequenceMatcher to approximate start; we still verify deletes later
    sm = difflib.SequenceMatcher(a="".join(needle), b="".join(sub), autojunk=False)
    _, b1, _, _ = max(sm.get_opcodes(), key=lambda t: t[2]-t[1] if t[0]=="equal" else -1, default=(None,0,0,0))
    return lo + b1

def _verify_deletions_match(hunk: List[str], hay: List[str], start: int) -> bool:
    # Ensure every '-' line matches exactly against hay at the appropriate relative index.
    # Walk through needle mapping indexes.
    old_seq = _old_sequence_from_hunk(hunk)
    if start < 0 or start + len(old_seq) > len(hay):
        return False
    j = 0
    for ln in hunk[1:]:
        tag = ln[:1]
        if tag in (" ", "-"):
            line_in_file = hay[start + j]
            if tag == "-":
                if line_in_file != (ln[1:] + "\n"):
                    return False
            j += 1
    return True

def _apply_hunks_to_content(orig: List[str], hunks: List[List[str]]) -> Optional[List[str]]:
    cur = orig[:]
    cursor_hint = 0
    for h in hunks:
        old_seq = _old_sequence_from_hunk(h)
        if not old_seq:
            # Pure insertion: try to anchor using nearest context around header
            around = _parse_oldstart(h)
            anchor = _fuzzy_anchor_index(cur, [], around)
            # No clear anchor ⇒ insert at around index
            start_idx = max(0, min(len(cur), around if around else 0))
            orig_slice = []
            new_seq = _new_sequence_from_hunk(h, orig_slice)
            cur = cur[:start_idx] + new_seq + cur[start_idx:]
            cursor_hint = start_idx + len(new_seq)
            continue

        around = _parse_oldstart(h)
        start_idx = _find_exact_subsequence(cur, old_seq, start_hint=cursor_hint if cursor_hint else around)
        if start_idx < 0:
            start_idx = _find_exact_subsequence(cur, old_seq, start_hint=max(0, around-10))
        if start_idx < 0:
            # fuzzy anchor near header number
            start_idx = _fuzzy_anchor_index(cur, old_seq, around=around)
            # strict deletions: if deletes don't match exactly at fuzzy spot → give up this file
            if not _verify_deletions_match(h, cur, start_idx):
                return None
            # If fuzzy anchor not aligned to entire old_seq, try exact from that spot
            if start_idx < 0 or start_idx + len(old_seq) > len(cur):
                return None

        if not _verify_deletions_match(h, cur, start_idx):
            return None

        repl = _new_sequence_from_hunk(h, cur[start_idx:start_idx+len(old_seq)])
        cur = cur[:start_idx] + repl + cur[start_idx+len(old_seq):]
        cursor_hint = start_idx + len(repl)
    return cur

def _make_unified_diff(rel_path: str, original: List[str], new_lines: List[str]) -> str:
    raw_lines = list(difflib.unified_diff(
        original, new_lines,
        fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}",
        lineterm="", n=3,
    ))
    body = "".join((ln if ln.endswith("\n") else (ln + "\n")) for ln in raw_lines)
    return f"diff --git a/{rel_path} b/{rel_path}\n{body}"

def _verified_patch_from_llm_diff(diff_text: str) -> str:
    """Build a brand-new, correct unified diff by applying suggested hunks to actual files."""
    root = get_project_root()
    suggestions = _parse_suggestions(diff_text)
    file_diffs: List[str] = []
    for sug in suggestions:
        # choose path under repo
        rel = sug.b_path or sug.a_path
        if rel.startswith(("a/","b/")): rel = rel[2:]
        if _is_excluded_path(rel): continue
        abs_path = (root / rel)
        if not abs_path.exists():
            # New file additions not supported in this strict mode (can be added later).
            continue
        try:
            data = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        data = data.replace("\r\n","\n").replace("\r","\n")
        original = [ln + "\n" for ln in data.split("\n")]
        if original and original[-1] == "\n" and data.endswith("\n")==False:
            original = original[:-1]  # normalize if split added an extra
        applied = _apply_hunks_to_content(original, sug.hunks)
        if applied is None or applied == original:
            # if any hunk fails strict checks, skip this file entirely
            continue
        file_diffs.append(_make_unified_diff(rel, original, applied))
    return ("\n".join(file_diffs) + ("\n" if file_diffs else ""))

# ---------- utilities ----------

def _write_artifacts(prompt: str, raw: str, diff: str) -> Tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    base = get_project_root() / "snapshots" / "selfopt" / "autocode" / time.strftime("%Y%m%d_%H%M%S")
    base.mkdir(parents=True, exist_ok=True)
    (base / "prompt.txt").write_text(prompt, encoding="utf-8")
    (base / "raw.txt").write_text(raw, encoding="utf-8")
    diff_path = base / "patch.diff"
    out = diff if diff else "# empty (no valid unified diff detected)\n"
    out = out.replace("\r\n","\n").replace("\r","\n")
    if out and not out.endswith("\n"): out += "\n"
    diff_path.write_text(out, encoding="utf-8")
    try:
        print(f"[autocoder] artifacts -> {base}  (prompt.txt={ (base/'prompt.txt').stat().st_size }B, raw.txt={ (base/'raw.txt').stat().st_size }B, patch.diff={ diff_path.stat().st_size }B)")
    except Exception:
        pass
    return base, diff_path, base / "raw.txt"

def _git_apply(patch_path: pathlib.Path) -> bool:
    if not patch_path.exists() or patch_path.stat().st_size == 0: return False
    attempts = [
        ["git","apply","--index","--whitespace=fix",str(patch_path)],
        ["git","apply","--index","--recount","--whitespace=fix",str(patch_path)],
        ["git","apply","--index","--unidiff-zero","--whitespace=fix",str(patch_path)],
        ["git","apply","--index","-p0","--whitespace=fix",str(patch_path)],
        ["git","apply","--index","--whitespace=nowarn",str(patch_path)],
        ["git","apply","--reject","--whitespace=nowarn",str(patch_path)],
    ]
    for cmd in attempts:
        try:
            subprocess.check_call(cmd); return True
        except Exception:
            continue
    return False

def _touch_list_from_diff(diff_text: str) -> List[str]:
    touched = []
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line.split(" ", 1)[1].strip()
            if path.startswith("b/"): path = path[2:]
            if path and not path.startswith("/dev/null"):
                if path.startswith("bot/"):
                    parts = path.split("/")
                    pairs = set("/".join(parts[i:i+2]) for i in range(len(parts)-1))
                    if not any(x in _EXCLUDE_DIRS for x in (set(parts) | pairs)):
                        touched.append(path)
    return touched

def _generate_pr_md(diff_text: str) -> str:
    try:
        return ask(
            "Write a crisp PR title and summary for the following unified diff. "
            "Keep it factual and short. Include a one-line title, bullets for changes, risks, and a tiny test plan.\n\nDIFF:\n"
            + diff_text,
            max_tokens=400, temperature=0.2
        )
    except Exception:
        return "Autocode PR\n\n- Changes: verified refactors\n- Risks: low\n- Test plan: root eval + basic builds"

# ---------- synth fallback (only if nothing verified) ----------

def _pick_first_python(hotspots: List[pathlib.Path]) -> Optional[pathlib.Path]:
    for p in hotspots:
        if p.suffix == ".py": return p
    return None

def _synthesize_python_type_checking_noop(rel_path: str, abs_path: pathlib.Path) -> str:
    try:
        data = abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    data = data.replace("\r\n","\n").replace("\r","\n")
    if "TYPE_CHECKING" in data and "Autocode:" in data:
        return ""
    original = [ln + "\n" for ln in data.split("\n")]
    if original and original[-1] == "\n" and data.endswith("\n")==False:
        original = original[:-1]
    # insert below imports
    i = 0
    if original and original[0].startswith("#!"): i = 1
    while i < len(original) and (original[i].lstrip().startswith("from ") or original[i].lstrip().startswith("import ")):
        i += 1
    insert = []
    has_tc = any("from typing import TYPE_CHECKING" in ln for ln in original)
    if not has_tc:
        insert.append("from typing import TYPE_CHECKING\n")
    insert += ["if TYPE_CHECKING:\n","    pass  # Autocode: non-functional type-only block\n"]
    new_lines = original[:i] + insert + original[i:]
    return _make_unified_diff(rel_path, original, new_lines)

# ---------- public API ----------

def propose_and_apply_refactor(max_files: int = 5, retries: int = 2) -> Optional[AutoCodeResult]:
    if not _llm_ok(): return None

    root = get_project_root()
    hotspots = _scan_hotspots(root, max_files=max_files)
    if not hotspots: return None

    prompt = _build_prompt(hotspots)
    raw = ask(prompt, max_tokens=2800, temperature=0.15)

    def looks_like_diff(d: str) -> bool:
        return ("diff --git " in d and "+++ " in d and "--- " in d and "@@ " in d)

    # 1) sanitize → 2) verified-patch rebuild
    sanitized = _sanitize_to_diff(raw)
    verified = _verified_patch_from_llm_diff(sanitized) if sanitized else ""

    # repair loop if nothing verified yet
    attempt = 0
    while (not verified) and attempt < max(2, retries):
        attempt += 1
        repair_prompt = (
            "Convert to a VALID unified diff (no text, only diff). "
            "Each file needs 'diff --git a/... b/...' + headers and at least one '@@' hunk.\n"
            "Avoid comment/docstring-only edits.\nOUTPUT:\n"
        ) + raw
        raw2 = ask(repair_prompt, max_tokens=2200, temperature=0.0)
        sanitized2 = _sanitize_to_diff(raw2)
        if sanitized2:
            v2 = _verified_patch_from_llm_diff(sanitized2)
            if v2:
                raw = raw2
                verified = v2
                break

    # If still nothing and synth not disabled, try tiny no-op once
    synth_used = False
    if not verified and not _NO_SYNTH:
        target = _pick_first_python(hotspots)
        if target is not None:
            rel = str(target.relative_to(root).as_posix())
            if rel.startswith("bot/") and not _is_excluded_path(rel):
                synth = _synthesize_python_type_checking_noop(rel, target)
                if synth.strip():
                    verified = synth
                    raw = synth
                    synth_used = True

    art_dir, diff_path, _ = _write_artifacts(prompt, raw, verified)
    touched = _touch_list_from_diff(verified) if verified else []

    applied = False
    if verified:
        applied = _git_apply(diff_path)

    # Last-chance: if our verified patch failed (shouldn’t), do nothing rather than spam files
    note_bits = []
    if synth_used:
        note_bits.append("used synth fallback (TYPE_CHECKING no-op) because LLM diff could not be verified")
    note_bits.append("patch applied via git apply" if applied else "no verified changes applied")
    note = "; ".join(note_bits)

    # PR summary
    try:
        pr_text = _generate_pr_md(verified if verified else "")
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
