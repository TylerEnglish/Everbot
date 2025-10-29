from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict
import os, pathlib, subprocess, time, re, difflib, json, textwrap

from bot.paths import get_project_root
from bot.llm.provider import ask, info, NotConfigured

# ==============================
# Env toggles & knobs
# ==============================
_NO_SYNTH = os.getenv("EVERABOT_AUTOCODE_NO_SYNTH", "").strip() == "1"
_VERBOSE = os.getenv("EVERABOT_AUTOCODE_VERBOSE", "").strip() == "1"

# When true, reject typing-only diffs and DO NOT fall back to synth
_STRICT_NOOP = os.getenv("EVERABOT_AUTOCODE_STRICT", "1").strip().lower() not in ("0", "false")
# Allow accepting doc/comment-only or whitespace-only diffs as last resort
_ALLOW_COMMENT_ONLY = os.getenv("EVERABOT_AUTOCODE_ALLOW_COMMENT_ONLY", "").strip() in ("1", "true", "yes")
_ALLOW_WHITESPACE_ONLY = os.getenv("EVERABOT_AUTOCODE_ALLOW_WHITESPACE", "").strip() in ("1", "true", "yes")

# Line budget for verified diff (added+removed); if exceeded, we repair/prompt again
_MAX_DIFF_LINES = int(os.getenv("EVERABOT_AUTOCODE_MAX_DIFF_LINES", "400"))
# Cooldown for files recently touched by autocoder (in hours)
_COOLDOWN_HOURS = float(os.getenv("EVERABOT_AUTOCODE_COOLDOWN_HOURS", "24"))
# Optional comma-separated list of path globs (under bot/) to bias hotspots
_TARGETS_ENV = os.getenv("EVERABOT_AUTOCODE_TARGETS", "").strip()
# Optional single file to nudge when everything else fails
_FORCE_FILE = os.getenv("EVERABOT_AUTOCODE_FORCE_FILE", "").strip()

# Repair attempts override
_REPAIR_ATTEMPTS = int(os.getenv("EVERABOT_AUTOCODE_REPAIR_ATTEMPTS", "3"))

@dataclass
class AutoCodeResult:
    title: str
    patch_path: str
    patch_applied: bool
    touched_paths: List[str]
    artifacts_dir: str
    note: str

# ---------- config / guards ----------
_ALLOWED_DIFF_PREFIXES = (
    "diff --git", "index ", "--- ", "+++ ", "@@ ",
    "new file mode ", "deleted file mode ", "old mode ", "new mode ",
    "rename from ", "rename to ", "copy from ", "copy to ",
    "similarity index ", "dissimilarity index "
)
_CODE_GLOBS = ("*.py","*.rs","*.go","*.c","*.cc","*.cpp","*.h","*.hpp","*.s","*.asm")
_EXCLUDE_DIRS = {
    "test","ruleset","snapshots","accel/rust/target","accel/cpp/build",
    ".git","dist","build",".venv","venv","node_modules","__pycache__"
}
_EXCLUDE_FILES = {"bot/cli.py"}

_AUTOCODE_MARKER = "Autocode: non-functional"
_DOC_OR_COMMENT_START = re.compile(r'^\+\s*(#|[rubfRUBF]?("""|\'\'\'))')
_DOC_OR_COMMENT_END   = re.compile(r'^\+\s*("""|\'\'\')\s*$')

# Heuristics for extracting "File: bot/x.py" around code blocks
_FILE_HINT_PATTERNS = [
    re.compile(r'(?im)^[>\-\*\s#]*file(?:name)?\s*:\s*(bot/[^\s:]+)\s*$', re.MULTILINE),
    re.compile(r'(?im)^[>\-\*\s#]*path\s*:\s*(bot/[^\s:]+)\s*$', re.MULTILINE),
    re.compile(r'(?im)^\s*(bot/[A-Za-z0-9_\-./]+)\s*:?$', re.MULTILINE),
    re.compile(r'(?im)^[>\-\*\s#]*(?:updated|edit|replace)\s+(bot/[A-Za-z0-9_\-./]+)\s*:?$', re.MULTILINE),
    re.compile(r'(?im)^#+\s*(bot/[A-Za-z0-9_\-./]+)\s*$'),                 # "### bot/x.py"
    re.compile(r'(?im)^<<<?FILE\s+(bot/[A-Za-z0-9_\-./]+)>>?>\s*$'),       # <<<FILE bot/x.py>>>
]

def _llm_ok() -> bool:
    try:
        _ = info()
        return True
    except NotConfigured:
        return False

# ---------- bookkeeping to avoid thrashing ----------
def _recent_autocode_touches(root: pathlib.Path) -> Dict[str, float]:
    base = root / "snapshots" / "selfopt" / "autocode"
    out: Dict[str, float] = {}
    if not base.exists():
        return out
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        ts = None
        try:
            ts = time.mktime(time.strptime(d.name, "%Y%m%d_%H%M%S"))
        except Exception:
            ts = None
        ap = d / "apply.json"
        if not ap.exists():
            continue
        try:
            obj = json.loads(ap.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        touched = obj.get("touched") or []
        when = ts or os.path.getmtime(ap)
        for p in touched:
            p = p.replace("\\", "/")
            if p.startswith("./"): p = p[2:]
            if not p.startswith("bot/"):
                continue
            prev = out.get(p, 0.0)
            if when > prev:
                out[p] = float(when)
    return out

def _targets_from_env(root: pathlib.Path) -> List[pathlib.Path]:
    if not _TARGETS_ENV:
        return []
    raw_globs = [g.strip() for g in _TARGETS_ENV.split(",") if g.strip()]
    picks: List[pathlib.Path] = []
    for pat in raw_globs:
        for p in root.glob(pat):
            if not p.exists() or p.is_dir():
                continue
            try:
                rel = p.relative_to(root).as_posix()
            except Exception:
                continue
            if rel in _EXCLUDE_FILES:
                continue
            parts = set(p.parts) | {"/".join(p.parts[i:i+2]) for i in range(len(p.parts)-1)}
            if not rel.startswith("bot/") or any(x in _EXCLUDE_DIRS for x in parts):
                continue
            picks.append(p)
    return picks

def _scan_hotspots(root: pathlib.Path, max_files: int = 5) -> List[pathlib.Path]:
    repo_dir = root / "bot"
    files: list[tuple[float, int, pathlib.Path]] = []

    cooldown = _recent_autocode_touches(root)
    now = time.time()

    targeted = set(_targets_from_env(root))

    def score_for(p: pathlib.Path) -> float:
        rel_posix = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return -1e12
        length = text.count("\n")
        todos = text.upper().count("TODO") + text.upper().count("FIXME")
        touched_penalty = 250000 if "Autocode:" in text else 0

        cd_pen = 0.0
        last = cooldown.get(rel_posix)
        if last:
            hours = (now - last) / 3600.0
            if hours < _COOLDOWN_HOURS:
                scale = max(0.0, (_COOLDOWN_HOURS - hours) / _COOLDOWN_HOURS)
                cd_pen = 200000.0 * scale

        base = (3 * todos + length) - touched_penalty - cd_pen
        if p in targeted:
            base += 50000.0
        return base

    for pat in _CODE_GLOBS:
        for p in repo_dir.rglob(pat):
            parts = {"/".join(p.parts[i:i+2]) for i in range(len(p.parts)-1)} | set(p.parts)
            if any(x in _EXCLUDE_DIRS for x in parts):
                continue
            rel_posix = p.relative_to(root).as_posix()
            if rel_posix in _EXCLUDE_FILES:
                continue
            files.append((score_for(p), 0, p))

    files.sort(reverse=True, key=lambda t: t[0])
    top = [p for _, __, p in files[:max_files]]
    for p in targeted:
        if p not in top:
            top.append(p)
    # Emergency nudge if everything fails
    if _FORCE_FILE:
        forced = root / _FORCE_FILE
        if forced.exists() and forced not in top:
            top[-1:] = [forced]
    return top[:max_files]

def _write_status(art_dir: pathlib.Path, status: Dict[str, object]) -> None:
    try:
        (art_dir / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    except Exception:
        pass

def _empty_reason(raw: str, sanitized: str, verified: str, no_op_reason: str) -> str:
    if not (raw or "").strip():
        return "llm_raw_empty"
    if (sanitized or "").strip() == "" and (verified or "").strip() == "":
        return "no_diff_and_no_salvage"
    if no_op_reason:
        return f"filtered_noop:{no_op_reason}"
    if (verified or "").strip() == "":
        return "verified_patch_empty_after_filters"
    return "unknown"

# ---------- prompt + sanitation ----------
def _build_prompt(file_paths: List[pathlib.Path]) -> str:
    header = (
        "You are Everabot's internal code refactorer.\n"
        "Goals: SAFE, behavior-preserving improvements: clearer names, small helpers, input validation,\n"
        "micro-optimizations, tighter error handling. Avoid docstring/comment-only patches.\n"
        "HARD CONSTRAINTS:\n"
        " - Only modify files under 'bot/'.\n"
        " - Don’t touch ruleset/ or test/; avoid build/vendor dirs.\n"
        " - Output ONE unified diff across ALL changes, with paths like a/bot/... and b/bot/...\n"
        " - No commentary; ONLY the diff; keep ~20–180 lines of diff total.\n"
        " - Avoid editing bot/cli.py.\n"
        " - Include at least ONE meaningful code change (arg checks, helper extraction, better errors,\n"
        "   micro-perf). Not just imports.\n"
        " - Do NOT produce a typing-only patch (e.g., only TYPE_CHECKING scaffolding). Such diffs are rejected.\n"
        " - Do NOT introduce new hard runtime deps.\n"
        "If you cannot produce a valid unified diff, then output FILE BLOCKS instead, exactly like this:\n"
        "<<<FILE bot/path/to/file.py>>>\n"
        "<entire updated file content>\n"
        "<<<END>>>\n"
        "Repeat that for each changed file. No other prose.\n\n"
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
    # Remove both ```lang and ~~~lang fences
    s = re.sub(r"```(?:[a-zA-Z0-9_.+\-]+)?\n", "", s)
    s = s.replace("```", "")
    s = re.sub(r"~~~(?:[a-zA-Z0-9_.+\-]+)?\n", "", s)
    s = s.replace("~~~", "")
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
    if p == "/dev/null":
        return False
    if p.startswith(("a/","b/")): p = p[2:]
    parts = p.split("/")
    pairs = set("/".join(parts[i:i+2]) for i in range(len(parts)-1))
    if p in _EXCLUDE_FILES: return True
    return (not p.startswith("bot/")) or any(x in _EXCLUDE_DIRS for x in (set(parts) | pairs))

def _block_has_only_comment_or_doc_additions(hunk_lines: List[str]) -> bool:
    added = [ln for ln in hunk_lines if ln.startswith("+")]
    if not added: return False
    codeish = re.compile(r'^\+\s*(def |class |from |import |raise |return |with |try:|except |if |for |while |@|[A-Za-z_]\w*\s*=)')
    has_code = False
    for ln in added:
        if codeish.match(ln):
            has_code = True
            break
    if has_code:
        return False
    for ln in added:
        body = ln[1:]
        if body.strip() == "": 
            continue
        if _DOC_OR_COMMENT_START.match(ln) or _DOC_OR_COMMENT_END.match(ln):
            continue
        if any(tok in body for tok in ("()", "{}", "[]", " = ", " -> ", ": ", "lambda ", " yield", " except", " try:", " return ")):
            return False
    return True

def _block_is_typechecking_noop(hunk_lines: List[str]) -> bool:
    plus = [ln[1:].rstrip("\n") for ln in hunk_lines[1:] if ln.startswith("+")]
    if not plus:
        return False
    s = "\n".join(x.strip() for x in plus if x.strip())
    has_tc = "from typing import TYPE_CHECKING" in s and "if TYPE_CHECKING:" in s
    only_noop = "Autocode: non-functional" in s
    suspicious = any(re.search(r"\bdef\b|\bclass\b|\breturn\b|[^_]raise\b|[^a-zA-Z]for\b|[^a-zA-Z]while\b", x) for x in plus)
    return has_tc and only_noop and not suspicious

def _rebuild_block_headers_only(blk: list[str]) -> Optional[Tuple[str,str,list[list[str]]]]:
    head = next((x for x in blk if x.startswith("diff --git ")), None)
    if not head: return None
    m = re.match(r"^diff --git\s+(.+?)\s+(.+)$", head.strip())
    if not m: return None
    a_hdr, b_hdr = m.group(1).strip(), m.group(2).strip()

    minus_line = next((x for x in blk if x.startswith("--- ")), "")
    plus_line  = next((x for x in blk if x.startswith("+++ ")), "")
    minus_path = _norm_sep(minus_line[4:].strip()) if minus_line else ""
    plus_path  = _norm_sep(plus_line[4:].strip()) if plus_line  else ""

    def _norm_body(p: str) -> str:
        p = p.replace("\\","/").lstrip("./")
        if p.startswith(("a/","b/")): p = p[2:]
        return p

    a_body, b_body = _norm_body(a_hdr), _norm_body(b_hdr)
    if minus_path == "/dev/null":
        a_body = "/dev/null"
    if plus_path == "/dev/null":
        b_body = "/dev/null"

    if _is_excluded_path(a_body) or _is_excluded_path(b_body): return None

    hunks: list[list[str]] = []
    cur: list[str] = []
    for ln in blk:
        if ln.startswith("@@ "):
            if cur: hunks.append(cur)
            cur = [ln]
        elif cur:
            if ln.startswith("diff --git "):
                break
            cur.append(ln)
    if cur: hunks.append(cur)

    filtered_hunks = []
    for h in hunks:
        if any(x.startswith(("+","-"," ")) for x in h[1:]):
            if _block_has_only_comment_or_doc_additions(h) and not _ALLOW_COMMENT_ONLY:
                continue
            if _block_is_typechecking_noop(h) and _STRICT_NOOP:
                continue
            filtered_hunks.append(h)

    # If a pure rename or new/delete, keep it even if hunks filtered out
    if not filtered_hunks and a_body == b_body:
        return a_body, b_body, []
    if not filtered_hunks and a_body != b_body:
        return a_body, b_body, []
    return (a_body, b_body, filtered_hunks) if filtered_hunks or a_body != b_body else None

def _sanitize_to_diff(raw: str) -> str:
    """
    Accepts true diffs, possibly missing some headers; repairs to a usable
    unified diff that git can parse.
    """
    raw = _clean_unicode(raw)
    if raw.strip() == "#NOPATCH":
        return ""
    # Fast path: if it already has diff headers, normalize and return
    if "diff --git " in raw and "--- " in raw and "+++ " in raw:
        txt = _normalize_diff_git_headers(raw)
        return (txt + ("\n" if txt and not txt.endswith("\n") else "")) if txt else ""

    # Extract ```diff or ```patch blocks and stitch them
    stitched = []
    for m in re.finditer(r"```(?:diff|patch)\n([\s\S]*?)```", raw, re.MULTILINE):
        stitched.append(m.group(1))
    for m in re.finditer(r"~~~(?:diff|patch)\n([\s\S]*?)~~~", raw, re.MULTILINE):
        stitched.append(m.group(1))

    if stitched:
        txt = _normalize_diff_git_headers("\n".join(stitched))
        return (txt + ("\n" if txt and not txt.endswith("\n") else "")) if txt else ""

    # Try to recover when only ---/+++ pairs exist (no 'diff --git' lines)
    if ("--- " in raw and "+++ " in raw and "@@ " in raw):
        # inject missing 'diff --git' lines before each ---/+++ pair
        lines = raw.splitlines()
        out: List[str] = []
        i = 0
        while i < len(lines):
            ln = lines[i]
            if ln.startswith("--- "):
                minus_path = _norm_sep(ln[4:].strip())
                plus_line = None
                j = i + 1
                while j < len(lines) and not lines[j].startswith("+++ "):
                    j += 1
                if j < len(lines):
                    plus_line = lines[j]
                if plus_line:
                    plus_path = _norm_sep(plus_line[4:].strip())
                    a = minus_path if minus_path.startswith("a/") else ("a/" + minus_path)
                    b = plus_path if plus_path.startswith("b/") else ("b/" + plus_path)
                    out.append(f"diff --git {a} {b}")
                else:
                    # unable to pair, just pass through
                    out.append(ln)
            out.append(ln)
            i += 1
        txt = _normalize_diff_git_headers("\n".join(out))
        return (txt + ("\n" if txt and not txt.endswith("\n") else "")) if txt else ""

    return ""

# ---------- Verified patcher (apply LLM hunks to real files, then re-diff) ----------
@dataclass
class SuggestedFile:
    a_path: str
    b_path: str
    hunks: List[List[str]]  # each begins with "@@" then hunk lines

_hunk_header_re = re.compile(r"^@@\s+-([0-9]+)(?:,([0-9]+))?\s+\+([0-9]+)(?:,([0-9]+))?\s+@@")

def _old_sequence_from_hunk(hunk: List[str]) -> List[str]:
    seq: List[str] = []
    for ln in hunk[1:]:
        if ln.startswith(" ") or ln.startswith("-"):
            seq.append(ln[1:] + "\n")
    return seq

def _new_sequence_from_hunk(hunk: List[str], original_slice: List[str]) -> List[str]:
    new_seq: List[str] = []
    idx = 0
    for ln in hunk[1:]:
        tag = ln[:1]
        body = ln[1:] + "\n"
        if tag == " ":
            if idx < len(original_slice):
                new_seq.append(original_slice[idx])
            else:
                new_seq.append(body)
            idx += 1
        elif tag == "-":
            idx += 1
        elif tag == "+":
            new_seq.append(body)
    return new_seq

def _find_exact_subsequence(hay: List[str], needle: List[str], start_hint: int = 0) -> int:
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
    try:
        return max(0, int(m.group(1)) - 1)
    except Exception:
        return 0

def _fuzzy_anchor_index(hay: List[str], needle: List[str], around: int, window: int = 80) -> int:
    L = len(hay)
    lo = max(0, around - window)
    hi = min(L, around + window)
    sub = hay[lo:hi]
    cand_keys: List[List[str]] = []
    if len(needle) >= 2:
        cand_keys.append([needle[0], needle[1]])
        cand_keys.append([needle[-2], needle[-1]])
    elif needle:
        cand_keys.append([needle[0]])
    for key in cand_keys:
        pos = _find_exact_subsequence(sub, key, 0)
        if pos >= 0:
            base = lo + pos
            if base + len(needle) <= L and hay[base:base+len(needle)] == needle:
                return base
    sm = difflib.SequenceMatcher(a="".join(needle), b="".join(sub), autojunk=False)
    best = max(
        (op for op in sm.get_opcodes() if op[0] == "equal"),
        key=lambda t: t[2] - t[1],
        default=("equal", 0, 0, 0, 0),
    )
    b1 = best[3] if len(best) >= 4 else 0
    return lo + b1

def _verify_deletions_match(hunk: List[str], hay: List[str], start: int) -> bool:
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

@dataclass
class ApplyReport:
    applied: List[int]
    skipped: List[int]
    result: List[str]

def _apply_hunks_to_content(orig: List[str], hunks: List[List[str]]) -> ApplyReport:
    cur = orig[:]
    cursor_hint = 0
    applied_idx: List[int] = []
    skipped_idx: List[int] = []
    for i, h in enumerate(hunks):
        old_seq = _old_sequence_from_hunk(h)
        if not old_seq:
            around = _parse_oldstart(h)
            start_idx = max(0, min(len(cur), around if around else 0))
            new_seq = _new_sequence_from_hunk(h, [])
            cur = cur[:start_idx] + new_seq + cur[start_idx:]
            cursor_hint = start_idx + len(new_seq)
            applied_idx.append(i)
            continue

        around = _parse_oldstart(h)
        start_idx = _find_exact_subsequence(cur, old_seq, start_hint=cursor_hint if cursor_hint else around)
        if start_idx < 0:
            start_idx = _find_exact_subsequence(cur, old_seq, start_hint=max(0, around-10))
        if start_idx < 0:
            start_idx = _fuzzy_anchor_index(cur, old_seq, around=around)
            if not _verify_deletions_match(h, cur, start_idx):
                skipped_idx.append(i)
                continue
            if start_idx < 0 or start_idx + len(old_seq) > len(cur):
                skipped_idx.append(i)
                continue

        if not _verify_deletions_match(h, cur, start_idx):
            skipped_idx.append(i)
            continue

        repl = _new_sequence_from_hunk(h, cur[start_idx:start_idx+len(old_seq)])
        cur = cur[:start_idx] + repl + cur[start_idx+len(old_seq):]
        cursor_hint = start_idx + len(repl)
        applied_idx.append(i)

    return ApplyReport(applied=applied_idx, skipped=skipped_idx, result=cur)

def _make_unified_diff(rel_path: str, original: List[str], new_lines: List[str]) -> str:
    raw_lines = list(diflib.unified_diff(
        original, new_lines,
        fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}",
        lineterm="", n=3,
    ))
    body = "".join((ln if ln.endswith("\n") else (ln + "\n")) for ln in raw_lines)
    return f"diff --git a/{rel_path} b/{rel_path}\n{body}"

def _make_new_file_diff(rel_path: str, new_lines: List[str]) -> str:
    n = len(new_lines)
    hdr = f"@@ -0,0 +1,{n} @@\n" if n > 0 else "@@ -0,0 +1,0 @@\n"
    plus = "".join("+" + (ln[:-1] if ln.endswith("\n") else ln) + "\n" for ln in new_lines)
    return (
        f"diff --git a/{rel_path} b/{rel_path}\n"
        f"--- /dev/null\n"
        f"+++ b/{rel_path}\n"
        f"{hdr}{plus}"
    )

def _make_deleted_file_diff(rel_path: str, original: List[str]) -> str:
    n = len(original)
    hdr = f"@@ -1,{n} +0,0 @@\n" if n > 0 else "@@ -1,0 +0,0 @@\n"
    minus = "".join("-" + (ln[:-1] if ln.endswith("\n") else ln) + "\n" for ln in original)
    return (
        f"diff --git a/{rel_path} b/{rel_path}\n"
        f"--- a/{rel_path}\n"
        f"+++ /dev/null\n"
        f"{hdr}{minus}"
    )

def _parse_suggestions(text: str) -> List[SuggestedFile]:
    out: List[SuggestedFile] = []
    for blk in _split_blocks(text):
        parsed = _rebuild_block_headers_only(blk)
        if not parsed: continue
        a_body, b_body, hunks = parsed
        out.append(SuggestedFile(a_body, b_body, hunks))
    return out

# ---------- Python import graph + rewrite for renames ----------
_py_import_from = re.compile(r'^\s*from\s+([a-zA-Z_][\w\.]*)\s+import\b')
_py_import_direct = re.compile(r'^\s*import\s+([a-zA-Z_][\w\.]*)(?:\s+as\b|\s*$|\s*,)')

def _py_module_from_path(rel_path: str) -> Optional[str]:
    if not rel_path.endswith(".py"): return None
    if rel_path == "/dev/null": return None
    return rel_path[:-3].replace("/", ".")

def _collect_repo_py_files(root: pathlib.Path) -> List[pathlib.Path]:
    lst: List[pathlib.Path] = []
    for p in (root / "bot").rglob("*.py"):
        parts = set(p.parts) | {"/".join(p.parts[i:i+2]) for i in range(len(p.parts)-1)}
        if any(x in _EXCLUDE_DIRS for x in parts): continue
        if p.relative_to(root).as_posix() in _EXCLUDE_FILES: continue
        lst.append(p)
    return lst

def _rewrite_imports_py(lines: List[str], old_mod: str, new_mod: str) -> Tuple[List[str], bool]:
    changed = False
    out: List[str] = []
    pat_from = re.compile(rf'^(\s*from\s+){re.escape(old_mod)}(\.|[\s]+import\b)')
    pat_imp1 = re.compile(rf'^(\s*import\s+){re.escape(old_mod)}(\s|$|,|\.|as\b)')
    pat_imp2 = re.compile(r'^(\s*import\s+)(.*)')
    for ln in lines:
        s = ln
        m = pat_from.match(s)
        if m:
            s = s.replace(m.group(0), f"{m.group(1)}{new_mod}{m.group(2)}", 1)
            changed = True
        else:
            m2 = pat_imp1.match(s)
            if m2:
                def _rep_import_list(body: str) -> str:
                    parts = [p.strip() for p in body.split(",")]
                    new_parts = []
                    for p in parts:
                        if p.startswith(old_mod + ".") or p == old_mod:
                            new_parts.append(new_mod + p[len(old_mod):])
                        else:
                            new_parts.append(p)
                    return ", ".join(new_parts)
                m_any = pat_imp2.match(s)
                if m_any:
                    prefix = m_any.group(1)
                    rest = m_any.group(2)
                    new_rest = _rep_import_list(rest)
                    if new_rest != rest:
                        s = prefix + new_rest
                        changed = True
        out.append(s)
    return out, changed

def _rewrite_imports_for_rename(root: pathlib.Path, old_rel: str, new_rel: str) -> List[Tuple[str, List[str], List[str]]]:
    results: List[Tuple[str, List[str], List[str]]] = []
    old_mod = _py_module_from_path(old_rel)
    new_mod = _py_module_from_path(new_rel)
    if not old_mod or not new_mod:
        return results
    for p in _collect_repo_py_files(root):
        rel = p.relative_to(root).as_posix()
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        orig = [ln + "\n" for ln in raw.split("\n")]
        if orig and orig[-1] == "\n" and not raw.endswith("\n"):
            orig = orig[:-1]
        new_lines, changed = _rewrite_imports_py(orig, old_mod, new_mod)
        if changed:
            results.append((rel, orig, new_lines))
    return results

# ---------- diff size accounting ----------
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

def _parse_added_removed(diff_text: str) -> dict:
    files: dict[str, dict] = {}
    current: str | None = None
    in_hunk = False
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            parts = raw.split()
            target = parts[3] if len(parts) >= 4 else None
            if target and target.startswith("b/"):
                current = target[2:]
                files.setdefault(current, {"added": 0, "removed": 0, "added_imports": [], "added_typing_tc": False, "non_ws_adds": 0})
            else:
                current = None
            in_hunk = False
            continue
        if raw.startswith("@@ "):
            in_hunk = True
            continue
        if not in_hunk or not current:
            continue
        if raw.startswith("+++ ") or raw.startswith("--- "):
            continue
        if raw.startswith("+"):
            body = raw[1:]
            files[current]["added"] += 1
            if body.strip():
                files[current]["non_ws_adds"] += 1
            s = body.strip()
            if s.startswith("from typing import TYPE_CHECKING") or s == "if TYPE_CHECKING:":
                files[current]["added_typing_tc"] = True
            if s.startswith("import ") or s.startswith("from "):
                files[current]["added_imports"].append(s)
        elif raw.startswith("-"):
            files[current]["removed"] += 1
    return files

def _diff_is_typing_noop(diff_text: str) -> bool:
    has_removals = False
    has_typing_scaffold = False
    substantive_additions = 0

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git ") or raw.startswith(("--- ", "+++ ", "@@ ")):
            continue
        if raw.startswith("-"):
            has_removals = True
        elif raw.startswith("+"):
            body = raw[1:].strip()
            if body in ("",):
                continue
            if body.startswith("from typing import TYPE_CHECKING") or body == "if TYPE_CHECKING:" or "Autocode: non-functional" in body:
                has_typing_scaffold = True
                continue
            substantive_additions += 1

    if has_removals:
        return False
    return has_typing_scaffold and substantive_additions == 0

def _diff_is_whitespace_only(diff_text: str) -> bool:
    meta = _parse_added_removed(diff_text)
    if not meta:
        return False
    # if every added line is whitespace-only and no removals, classify as whitespace-only
    total_added = sum(v["added"] for v in meta.values())
    total_removed = sum(v["removed"] for v in meta.values())
    total_non_ws_adds = sum(v["non_ws_adds"] for v in meta.values())
    return total_removed == 0 and total_added > 0 and total_non_ws_adds == 0

def _diff_line_count(diff_text: str) -> int:
    s = _parse_added_removed(diff_text)
    return sum(v["added"] + v["removed"] for v in s.values())

def _generate_pr_md(diff_text: str) -> str:
    summary = _parse_added_removed(diff_text)
    if not summary:
        return "Autocode PR\n\n- No verified diff to summarize."

    total_added = sum(v["added"] for v in summary.values())
    total_removed = sum(v["removed"] for v in summary.values())
    files = list(summary.keys())

    lines = []
    lines.append(f"**PR Title:** Autocode refactor: {len(files)} file(s), +{total_added} −{total_removed}")
    lines.append("")
    lines.append("**Summary:**")
    lines.append("This PR applies small, behavior-preserving refactors and safety/perf polish.")
    lines.append("")
    lines.append("**Changes:**")
    for f, meta in summary.items():
        bullets = []
        if meta["added_typing_tc"]:
            bullets.append("add TYPE_CHECKING guard")
        if meta["added_imports"] and not meta["added_typing_tc"]:
            for imp in meta["added_imports"]:
                bullets.append(f"add `{imp}`")
        if not bullets:
            bullets.append(f"+{meta['added']} −{meta['removed']} line changes")
        lines.append(f"- `{f}`: " + "; ".join(bullets))
    only_typing = all(v["added_typing_tc"] and v["added"] > 0 and v["removed"] == 0 for v in summary.values())
    if only_typing:
        lines.append("")
        lines.append("> Note: all changes look typing-only; verify this is not a no-op.")
    lines.append("")
    lines.append("**Risks:** Low (no functional logic changed).")
    lines.append("")
    lines.append("**Test Plan:**")
    lines.append("1) Run root eval (`python -m bot.cli eval`) → expect PASS.")
    lines.append('2) Sanity import (`python -c "import bot; print(\'OK\')"`).')
    return "\n".join(lines)

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
    i = 0
    if original and original[0].startswith("#!"): i = 1
    while i < len(original) and (original[i].lstrip().startswith("from ") or original[i].lstrip().startswith("import ")):
        i += 1
    insert = []
    has_tc = any("from typing import TYPE_CHECKING" in ln for ln in original)
    if not has_tc:
        insert.append("from typing import TYPE_CHECKING\n")
    insert += ["if TYPE_CHECKING:\n","    pass  # Autocode: non-functional type-only block\n"]
    return _make_unified_diff(rel_path, original, new_lines=original[:i] + insert + original[i:])

# ---------- code-block / file-block → diff salvage ----------
_CODE_FENCE_RE = re.compile(r"```(?:[a-zA-Z0-9_.+\-]*)\n([\s\S]*?)```", re.MULTILINE)
_TILDE_FENCE_RE = re.compile(r"~~~(?:[a-zA-Z0-9_.+\-]*)\n([\s\S]*?)~~~", re.MULTILINE)
_FILE_BLOCK_RE = re.compile(r"<<<?FILE\s+(bot/[A-Za-z0-9_\-./]+)>>?>\s*\n([\s\S]*?)\n<<<?END>>?>", re.MULTILINE)
_PLAIN_FILE_HEADER_RE = re.compile(r'(?im)^File\s*:\s*(bot/[A-Za-z0-9_\-./]+)\s*$')

def _guess_path_from_context(whole: str, start_idx: int, code_text: str) -> Optional[str]:
    lo = max(0, start_idx - 500)
    ctx = whole[lo:start_idx]
    for pat in _FILE_HINT_PATTERNS:
        m = pat.search(ctx)
        if m:
            candidate = _norm_sep(m.group(1))
            if candidate.startswith("bot/") and not _is_excluded_path(candidate):
                return candidate

    # also inspect first few lines of the code block for a "FILE:" hint
    top_lines = "\n".join(code_text.splitlines()[:5])
    m = re.search(r'(?im)^\s*(?:#\s*)?(?:file|path)\s*:\s*(bot/[^\s:]+)\s*$', top_lines)
    if m:
        candidate = _norm_sep(m.group(1))
        if candidate.startswith("bot/") and not _is_excluded_path(candidate):
            return candidate

    # last resort: bare "bot/..." line
    m = re.search(r'(?m)^\s*(bot/[A-Za-z0-9_\-./]+)\s*:?$', ctx)
    if m:
        candidate = _norm_sep(m.group(1))
        if candidate.startswith("bot/") and not _is_excluded_path(candidate):
            return candidate

    return None

def _collect_file_blocks_from_plaintext(raw: str) -> List[Tuple[str, str]]:
    """
    Handles:
      - Explicit <<<FILE bot/x.py>>> ... <<<END>>> blocks.
      - Lines like 'File: bot/x.py' followed by a code block (indented or fenced) or just raw until next 'File:'.
    """
    found: List[Tuple[str, str]] = []
    # 1) <<<FILE ...>>> blocks
    for m in _FILE_BLOCK_RE.finditer(raw):
        rel = _norm_sep(m.group(1))
        code = m.group(2)
        if rel.startswith("bot/") and not _is_excluded_path(rel):
            found.append((rel, code))

    # 2) "File: bot/x.py" headers
    plain = raw.splitlines()
    i = 0
    while i < len(plain):
        line = plain[i]
        m = _PLAIN_FILE_HEADER_RE.match(line)
        if m:
            rel = _norm_sep(m.group(1))
            i += 1
            # capture until next "File:" header or end
            body_lines: List[str] = []
            while i < len(plain) and not _PLAIN_FILE_HEADER_RE.match(plain[i]):
                body_lines.append(plain[i])
                i += 1
            code = "\n".join(body_lines)
            if rel.startswith("bot/") and not _is_excluded_path(rel) and code.strip():
                found.append((rel, code))
        else:
            i += 1
    return found

def _diff_from_code_blocks(raw: str, root: pathlib.Path) -> Tuple[str, List[str], List[str]]:
    """
    Extract code fences and file blocks, locate intended target paths,
    and build a single concatenated unified diff across all recognized files.
    Returns (diff_text, touched_paths, reasons).
    """
    reasons: List[str] = []
    diffs: List[str] = []
    touched: List[str] = []

    # 0) Explicit FILE blocks first
    for rel, code_text in _collect_file_blocks_from_plaintext(raw):
        abs_path = root / rel
        try:
            if abs_path.exists():
                original_text = abs_path.read_text(encoding="utf-8", errors="replace").replace("\r\n","\n").replace("\r","\n")
                original = [ln + "\n" for ln in original_text.split("\n")]
                if original and original[-1] == "\n" and not original_text.endswith("\n"):
                    original = original[:-1]
                new_lines = [(ln if ln.endswith("\n") else ln + "\n") for ln in code_text.replace("\r\n","\n").replace("\r","\n").split("\n")]
                if new_lines and new_lines[-1] == "\n" and not code_text.endswith("\n"):
                    new_lines = new_lines[:-1]
                diffs.append(_make_unified_diff(rel, original, new_lines))
            else:
                new_lines = [(ln if ln.endswith("\n") else ln + "\n") for ln in code_text.replace("\r\n","\n").replace("\r","\n").split("\n")]
                if new_lines and new_lines[-1] == "\n" and not code_text.endswith("\n"):
                    new_lines = new_lines[:-1]
                diffs.append(_make_new_file_diff(rel, new_lines))
            touched.append(rel)
        except Exception as e:
            reasons.append(f"file-block error for {rel}: {e}")

    # 1) Triple backticks
    for m in _CODE_FENCE_RE.finditer(raw):
        code_text = m.group(1)
        rel = _guess_path_from_context(raw, m.start(), code_text)
        if not rel:
            reasons.append("code fence without path hint")
            continue
        abs_path = root / rel
        try:
            if abs_path.exists():
                original_text = abs_path.read_text(encoding="utf-8", errors="replace").replace("\r\n","\n").replace("\r","\n")
                original = [ln + "\n" for ln in original_text.split("\n")]
                if original and original[-1] == "\n" and not original_text.endswith("\n"):
                    original = original[:-1]
                new_lines = [(ln if ln.endswith("\n") else ln + "\n") for ln in code_text.replace("\r\n","\n").replace("\r","\n").split("\n")]
                if new_lines and new_lines[-1] == "\n" and not code_text.endswith("\n"):
                    new_lines = new_lines[:-1]
                diffs.append(_make_unified_diff(rel, original, new_lines))
            else:
                new_lines = [(ln if ln.endswith("\n") else ln + "\n") for ln in code_text.replace("\r\n","\n").replace("\r","\n").split("\n")]
                if new_lines and new_lines[-1] == "\n" and not code_text.endswith("\n"):
                    new_lines = new_lines[:-1]
                diffs.append(_make_new_file_diff(rel, new_lines))
            touched.append(rel)
        except Exception as e:
            reasons.append(f"code-fence error for {rel}: {e}")

    # 2) Tilde fences
    for m in _TILDE_FENCE_RE.finditer(raw):
        code_text = m.group(1)
        rel = _guess_path_from_context(raw, m.start(), code_text)
        if not rel:
            reasons.append("tilde fence without path hint")
            continue
        abs_path = root / rel
        try:
            if abs_path.exists():
                original_text = abs_path.read_text(encoding="utf-8", errors="replace").replace("\r\n","\n").replace("\r","\n")
                original = [ln + "\n" for ln in original_text.split("\n")]
                if original and original[-1] == "\n" and not original_text.endswith("\n"):
                    original = original[:-1]
                new_lines = [(ln if ln.endswith("\n") else ln + "\n") for ln in code_text.replace("\r\n","\n").replace("\r","\n").split("\n")]
                if new_lines and new_lines[-1] == "\n" and not code_text.endswith("\n"):
                    new_lines = new_lines[:-1]
                diffs.append(_make_unified_diff(rel, original, new_lines))
            else:
                new_lines = [(ln if ln.endswith("\n") else ln + "\n") for ln in code_text.replace("\r\n","\n").replace("\r","\n").split("\n")]
                if new_lines and new_lines[-1] == "\n" and not code_text.endswith("\n"):
                    new_lines = new_lines[:-1]
                diffs.append(_make_new_file_diff(rel, new_lines))
            touched.append(rel)
        except Exception as e:
            reasons.append(f"tilde-fence error for {rel}: {e}")

    joined = ("\n".join(diffs) + ("\n" if diffs else ""))
    return joined, touched, reasons

# ---------- artifact helpers ----------
def _write_artifacts(prompt: str, raw: str, diff: str, salvage: str = "", salvage_reasons: Optional[List[str]]=None) -> Tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    base = get_project_root() / "snapshots" / "selfopt" / "autocode" / time.strftime("%Y%m%d_%H%M%S")
    base.mkdir(parents=True, exist_ok=True)
    (base / "prompt.txt").write_text(prompt, encoding="utf-8")
    (base / "raw.txt").write_text(raw, encoding="utf-8")
    diff_path = base / "patch.diff"
    out = diff if diff else "# empty (no valid unified diff detected)\n"
    out = out.replace("\r\n","\n").replace("\r","\n")
    if out and not out.endswith("\n"): out += "\n"
    diff_path.write_text(out, encoding="utf-8")
    if salvage:
        (base / "salvage.diff").write_text(salvage, encoding="utf-8")
    if salvage_reasons:
        (base / "salvage_reasons.txt").write_text("\n".join(salvage_reasons), encoding="utf-8")
    try:
        print(f"[autocoder] artifacts -> {base}  (prompt.txt={ (base/'prompt.txt').stat().st_size }B, raw.txt={ (base/'raw.txt').stat().st_size }B, patch.diff={ diff_path.stat().st_size }B)")
    except Exception:
        pass
    return base, diff_path, base / "raw.txt"

# ---------- build a verified, repo-accurate patch ----------
def _verified_patch_from_llm_diff(diff_text: str) -> str:
    root = get_project_root()
    suggestions = _parse_suggestions(diff_text)
    file_diffs: List[str] = []

    for sug in suggestions:
        a_rel = sug.a_path
        b_rel = sug.b_path
        if a_rel.startswith(("a/","b/")): a_rel = a_rel[2:]
        if b_rel.startswith(("a/","b/")): b_rel = b_rel[2:]
        if _is_excluded_path(a_rel) or _is_excluded_path(b_rel): continue

        a_abs = (root / a_rel) if a_rel != "/dev/null" else None
        b_abs = (root / b_rel) if b_rel != "/dev/null" else None
        a_exists = a_abs.exists() if a_abs else False
        b_exists = b_abs.exists() if b_abs else False

        if b_rel == "/dev/null" and a_rel != "/dev/null" and a_exists:
            try:
                data = (a_abs).read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            orig = [ln + "\n" for ln in data.split("\n")]
            if orig and orig[-1] == "\n" and not data.endswith("\n"): orig = orig[:-1]
            file_diffs.append(_make_deleted_file_diff(a_rel, orig))
            continue

        if a_rel == "/dev/null" and b_rel != "/dev/null" and not b_exists:
            new_lines: List[str] = []
            for h in (sug.hunks or []):
                for ln in h[1:]:
                    if ln.startswith("+"):
                        new_lines.append(ln[1:] + "\n")
            file_diffs.append(_make_new_file_diff(b_rel, new_lines))
            continue

        is_rename = (a_rel != b_rel) and a_rel != "/dev/null" and b_rel != "/dev/null" and a_exists and (not b_exists)
        if is_rename:
            try:
                data = (a_abs).read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            data = data.replace("\r\n","\n").replace("\r","\n")
            orig = [ln + "\n" for ln in data.split("\n")]
            if orig and orig[-1] == "\n" and not data.endswith("\n"): orig = orig[:-1]

            if sug.hunks:
                rep = _apply_hunks_to_content(orig, sug.hunks)
                new_content = rep.result
            else:
                new_content = orig[:]

            file_diffs.append(_make_deleted_file_diff(a_rel, orig))
            file_diffs.append(_make_new_file_diff(b_rel, new_content))

            rewrites = _rewrite_imports_for_rename(root, a_rel, b_rel)
            for rel, old_lines, new_lines in rewrites:
                if rel in (a_rel, b_rel):
                    continue
                if old_lines != new_lines:
                    file_diffs.append(_make_unified_diff(rel, old_lines, new_lines))
            continue

        if a_exists:
            try:
                data = (a_abs).read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            data = data.replace("\r\n","\n").replace("\r","\n")
            original = [ln + "\n" for ln in data.split("\n")]
            if original and original[-1] == "\n" and not data.endswith("\n"):
                original = original[:-1]

            rep = _apply_hunks_to_content(original, sug.hunks or [])
            if rep.result != original and rep.applied:
                dst_rel = b_rel or a_rel
                file_diffs.append(_make_unified_diff(dst_rel, original, rep.result))
            continue

        continue

    joined = ("\n".join(file_diffs) + ("\n" if file_diffs else ""))
    if joined and _ALLOW_WHITESPACE_ONLY is False and _diff_is_whitespace_only(joined):
        # discard whitespace-only patch unless allowed
        return ""
    return joined

# ---------- public API ----------
def propose_and_apply_refactor(max_files: int = 5, retries: int = 2) -> Optional[AutoCodeResult]:
    if not _llm_ok(): return None

    root = get_project_root()
    hotspots = _scan_hotspots(root, max_files=max_files)
    if not hotspots: return None

    # 1) Ask once with the standard prompt
    origin_prompt = _build_prompt(hotspots)
    raw = ask(origin_prompt, max_tokens=2800, temperature=0.15)

    # Try to use a real diff first
    sanitized = _sanitize_to_diff(raw)
    verified = _verified_patch_from_llm_diff(sanitized) if sanitized else ""

    no_op_reason = ""
    if verified:
        if _STRICT_NOOP and _diff_is_typing_noop(verified):
            verified = ""
            no_op_reason = "typing-only"
        elif _MAX_DIFF_LINES > 0 and _diff_line_count(verified) > _MAX_DIFF_LINES:
            verified = ""
            no_op_reason = f"too-large ({_diff_line_count(verified)}>{_MAX_DIFF_LINES})"

    # 2) If no diff verified, attempt CODE/FILE SALVAGE (convert blocks → diffs)
    salvage_diff, salvage_touched, salvage_reasons = ("", [], [])
    if not verified:
        salvage_diff, salvage_touched, salvage_reasons = _diff_from_code_blocks(raw, root)
        if salvage_diff.strip():
            v2 = _verified_patch_from_llm_diff(salvage_diff)
            if v2:
                over = _MAX_DIFF_LINES > 0 and _diff_line_count(v2) > _MAX_DIFF_LINES
                is_noop = _STRICT_NOOP and _diff_is_typing_noop(v2)
                ws_only = _diff_is_whitespace_only(v2) and not _ALLOW_WHITESPACE_ONLY
                if not over and not is_noop and not ws_only:
                    verified = v2
                    sanitized = ""  # we used salvage path

    # 3) Repair loop: if still nothing, re-ask more strictly, with explicit examples
    attempt = 0
    while (not verified) and attempt < max(_REPAIR_ATTEMPTS, retries):
        attempt += 1
        hotspots = _scan_hotspots(root, max_files=max_files)
        fresh_ctx = _build_prompt(hotspots)
        guard_lines = [
            "Your last answer lacked a usable patch.",
            "RULES:",
            " - Output ONE VALID unified diff that modifies only files under bot/ .",
            " - Include 'diff --git a/... b/...', '---', '+++', and at least one '@@' hunk per file.",
            " - If you cannot produce a diff, output one or more FILE BLOCKS exactly like:",
            "   <<<FILE bot/some/file.py>>>",
            "   <entire updated file>",
            "   <<<END>>>",
            " - No commentary besides a diff or FILE blocks.",
        ]
        repair_prompt = textwrap.dedent("\n".join(guard_lines)) + "\n\n" + fresh_ctx
        raw2 = ask(repair_prompt, max_tokens=2400, temperature=0.2)

        sanitized2 = _sanitize_to_diff(raw2)
        v2 = _verified_patch_from_llm_diff(sanitized2) if sanitized2 else ""

        if not v2:
            # retry salvage on the new response
            salv2, _, _reasons2 = _diff_from_code_blocks(raw2, root)
            if salv2.strip():
                v2 = _verified_patch_from_llm_diff(salv2)

        if v2:
            over = _MAX_DIFF_LINES > 0 and _diff_line_count(v2) > _MAX_DIFF_LINES
            is_noop = _STRICT_NOOP and _diff_is_typing_noop(v2)
            ws_only = _diff_is_whitespace_only(v2) and not _ALLOW_WHITESPACE_ONLY
            if not over and not is_noop and not ws_only:
                raw, sanitized, verified = raw2, sanitized2, v2
                salvage_diff = salv2 if sanitized2 == "" else ""
                break

        no_op_reason = ""

    # 4) Optional synth fallback (disabled if STRICT)
    synth_used = False
    if (not verified) and (not _NO_SYNTH) and (not _STRICT_NOOP):
        target = _pick_first_python(hotspots) or (_FORCE_FILE and (root / _FORCE_FILE))
        if target is not None and isinstance(target, pathlib.Path) and target.exists():
            rel = str(target.relative_to(root).as_posix())
            if rel.startswith("bot/") and not _is_excluded_path(rel):
                synth = _synthesize_python_type_checking_noop(rel, target)
                if synth.strip():
                    verified = synth
                    raw = synth
                    synth_used = True

    # 5) Artifacts & status
    art_dir, diff_path, _ = _write_artifacts(origin_prompt, raw, verified, salvage=salvage_diff, salvage_reasons=salvage_reasons)
    status_reason = _empty_reason(raw, sanitized, verified, no_op_reason)
    _write_status(art_dir, {
        "reason": status_reason,
        "sizes": {"raw": len(raw or ""), "sanitized": len(sanitized or ""), "verified": len(verified or "")},
        "strict_noop": bool(_STRICT_NOOP),
        "synth_used": bool(synth_used),
        "salvage_used": bool(bool(salvage_diff)),
        "salvage_touched": salvage_touched,
        "allow_comment_only": bool(_ALLOW_COMMENT_ONLY),
        "allow_whitespace_only": bool(_ALLOW_WHITESPACE_ONLY),
    })
    touched = _touch_list_from_diff(verified) if verified else []

    applied = False
    strategy_report: Dict[str, object] = {}
    if verified:
        applied, strategy_report = _git_apply(diff_path, artifacts_dir=str(art_dir), verbose=_VERBOSE)
        try:
            (pathlib.Path(art_dir) / "apply.json").write_text(json.dumps(strategy_report, indent=2), encoding="utf-8")
        except Exception:
            pass

    note_bits = []
    if synth_used:
        note_bits.append("used synth fallback (TYPE_CHECKING no-op)")
    if salvage_diff:
        note_bits.append("converted code/file blocks → diff")
    note_bits.append("patch applied via git apply" if applied else "no verified changes applied")
    note = "; ".join(note_bits)

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

# ---------- git apply with strategy report ----------
def _summarize_cmd(cmd: List[str]) -> str:
    return " ".join(cmd)

def _git_apply(patch_path: pathlib.Path, *, artifacts_dir: Optional[str]=None, verbose: bool=False) -> Tuple[bool, Dict[str, object]]:
    """
    Strategy (per git-apply docs):
      1) Parse touched paths from the diff.
      2) Probe path tracking + index cleanliness.
      3) Try (in order):
         a) working-tree apply (when untracked/dirty) with whitespace forgiveness;
         b) --index variants when clean;
         c) --3way (for conflicts);
         d) --reject (final check; treated as failure if any .rej created).
    """
    report: Dict[str, object] = {"touched": [], "has_untracked": False, "index_clean": True, "attempts": []}
    if not patch_path.exists() or patch_path.stat().st_size == 0:
        return False, report

    root = get_project_root()
    diff_txt = patch_path.read_text(encoding="utf-8", errors="replace")
    touched = _touch_list_from_diff(diff_txt)
    report["touched"] = touched

    def _run(cmd: list[str]) -> tuple[int, str, str]:
        try:
            p = subprocess.run(cmd, cwd=root, text=True, capture_output=True, check=False)
            return p.returncode, p.stdout, p.stderr
        except Exception as e:
            return 999, "", str(e)

    def _tracked(path: str) -> bool:
        code, _, _ = _run(["git","ls-files","--error-unmatch","--",path])
        return code == 0

    def _paths_clean(paths: list[str]) -> bool:
        if not paths:
            return True
        # correct order: HEAD before the pathspec separator
        code, _, _ = _run(["git","diff-index","--quiet","HEAD","--", *paths])
        return code == 0

    _run(["git","update-index","-q","--refresh"])

    tracked_map = {p: _tracked(p) for p in touched}
    has_untracked = any(not v for v in tracked_map.values())
    index_clean = _paths_clean([p for p, t in tracked_map.items() if t])
    report["has_untracked"] = has_untracked
    report["index_clean"] = index_clean
    if verbose:
        print(f"[autocoder][apply] touched={len(touched)} has_untracked={has_untracked} index_clean={index_clean}")

    def _check(cmd: list[str]) -> bool:
        rc, _, _ = _run(["git","apply","--check", *cmd[2:], str(patch_path)])
        return rc == 0

    def _apply_cmd(cmd: list[str]) -> bool:
        rc, _, _ = _run(cmd + [str(patch_path)])
        return rc == 0

    strategies: list[list[str]] = []
    if has_untracked or not index_clean:
        strategies.append(["git","apply","--whitespace=fix","--ignore-whitespace"])
        strategies.append(["git","apply","--recount","--unidiff-zero","--whitespace=fix","--ignore-whitespace"])
        strategies.append(["git","apply","--3way"])
    else:
        strategies.append(["git","apply","--index","--whitespace=fix"])
        strategies.append(["git","apply","--index","--recount","--unidiff-zero","--whitespace=fix"])
        strategies.append(["git","apply","--3way"])
    strategies.append(["git","apply","--reject","--whitespace=nowarn"])

    chosen = None
    for cmd in strategies:
        attempt: Dict[str, object] = {"cmd": _summarize_cmd(cmd), "check_ok": False, "applied": False, "rej_delta": 0}
        check_ok = _check(cmd)
        attempt["check_ok"] = bool(check_ok)
        if not check_ok:
            report["attempts"].append(attempt)
            continue
        before_rej = list(root.rglob("*.rej"))
        applied = _apply_cmd(cmd)
        after_rej = list(root.rglob("*.rej"))
        attempt["applied"] = bool(applied)
        attempt["rej_delta"] = max(0, len(after_rej) - len(before_rej))
        report["attempts"].append(attempt)
        if applied:
            if "--reject" in cmd and attempt["rej_delta"]:
                # If --reject left .rej files, treat as failure
                continue
            chosen = cmd
            break

    if chosen and ("--index" not in chosen and "--3way" not in chosen) and touched:
        _run(["git","add","--"] + touched)

    ok = chosen is not None
    report["chosen"] = _summarize_cmd(chosen) if chosen else None
    if verbose:
        ch = report["chosen"] or "NONE"
        print(f"[autocoder][apply] result={ok} strategy={ch}")
    if artifacts_dir:
        try:
            summary = (
                f"chosen={report['chosen']}\n"
                f"touched={len(touched)} has_untracked={has_untracked} index_clean={index_clean}\n"
                + "\n".join(
                    f"- try: {a['cmd']} check_ok={a['check_ok']} applied={a['applied']} rej_delta={a['rej_delta']}"
                    for a in report["attempts"]
                ) + "\n"
            )
            pathlib.Path(artifacts_dir, "apply.txt").write_text(summary, encoding="utf-8")
        except Exception:
            pass
    return ok, report
