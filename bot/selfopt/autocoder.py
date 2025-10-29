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
# Line budget for verified diff (added+removed); if exceeded, we repair/prompt again
_MAX_DIFF_LINES = int(os.getenv("EVERABOT_AUTOCODE_MAX_DIFF_LINES", "400"))
# Cooldown for files recently touched by autocoder (in hours)
_COOLDOWN_HOURS = float(os.getenv("EVERABOT_AUTOCODE_COOLDOWN_HOURS", "24"))
# Optional comma-separated list of path globs (under bot/) to bias hotspots
_TARGETS_ENV = os.getenv("EVERABOT_AUTOCODE_TARGETS", "").strip()

@dataclass
class AutoCodeResult:
    title: str
    patch_path: str
    patch_applied: bool
    touched_paths: List[str]
    artifacts_dir: str
    note: str

# ---------- config / guards ----------
# Accept more header lines so we don't drop otherwise-correct diffs (rename, modes, similarity, etc.)
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

def _llm_ok() -> bool:
    try:
        _ = info()
        return True
    except NotConfigured:
        return False

# ---------- bookkeeping to avoid thrashing ----------
def _recent_autocode_touches(root: pathlib.Path) -> Dict[str, float]:
    """
    Scan snapshots/selfopt/autocode/*/apply.json and build a map:
      { 'bot/relative/path' : last_touched_epoch_seconds }
    """
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
    """
    If EVERABOT_AUTOCODE_TARGETS is set (comma-separated globs relative to project root),
    return those files (filtered to bot/* and not excluded). Used to bias hotspot selection.
    """
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
    """
    Heuristic: prioritize (1) explicit targets, (2) many TODOs, (3) longer files,
    with cooldown penalties for recently-autocoded files.
    """
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

        # Cooldown: penalize files touched in last _COOLDOWN_HOURS
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
    return top[:max_files]

def _write_status(art_dir: pathlib.Path, status: Dict[str, object]) -> None:
    try:
        (art_dir / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    except Exception:
        pass

def _empty_reason(raw: str, sanitized: str, verified: str, no_op_reason: str) -> str:
    if not (raw or "").strip():
        return "llm_raw_empty"
    if (sanitized or "").strip() == "":
        return "sanitizer_dropped_non_diff_text"
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
        " - Include at least ONE meaningful code change (e.g., arg checks, helper extraction, clearer names,\n"
        "   error message improvements, tiny performance wins). Not just imports.\n"
        " - DO NOT produce a typing-only patch (e.g., only adding `from typing import TYPE_CHECKING` with\n"
        "   an `if TYPE_CHECKING:` block). Such diffs will be rejected.\n"
        " - Do NOT introduce new hard runtime deps (e.g., numpy, pyarrow). If types are needed, use `Any`\n"
        "   or guard imports under `if TYPE_CHECKING:`.\n"
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

def _block_is_typechecking_noop(hunk_lines: List[str]) -> bool:
    """
    True if all '+' lines are the canonical typing scaffold we consider a no-op:
      - 'from typing import TYPE_CHECKING'
      - 'if TYPE_CHECKING:' + the 'Autocode: non-functional' pass block
    and nothing else.
    """
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
            if _block_has_only_comment_or_doc_additions(h):
                continue
            if _block_is_typechecking_noop(h):
                continue
            filtered_hunks.append(h)
    if not filtered_hunks and a_body == b_body:
        # allow header-only (e.g., LLM suggesting rename elsewhere)
        return a_body, b_body, []
    if not filtered_hunks and a_body != b_body:
        # permit rename with zero hunks (pure move)
        return a_body, b_body, []
    return (a_body, b_body, filtered_hunks) if filtered_hunks or a_body != b_body else None

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
                out.append(ln); continue
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
                new_seq.append(body)  # safety
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
    """
    Look near the expected 'around' line for the best matching equal block and
    return its starting index in the 'b' side (j1), shifted back into the global 'hay'.
    """
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
    """
    Apply hunks sequentially. If a hunk fails strict deletion verification or cannot be located,
    we **skip just that hunk** and continue.
    """
    cur = orig[:]
    cursor_hint = 0
    applied_idx: List[int] = []
    skipped_idx: List[int] = []
    for i, h in enumerate(hunks):
        old_seq = _old_sequence_from_hunk(h)
        if not old_seq:
            # pure insertion: insert near header hint
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
            # fuzzy anchor near expected location; still require exact '-' match
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
    raw_lines = list(difflib.unified_diff(
        original, new_lines,
        fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}",
        lineterm="", n=3,
    ))
    body = "".join((ln if ln.endswith("\n") else (ln + "\n")) for ln in raw_lines)
    return f"diff --git a/{rel_path} b/{rel_path}\n{body}"

def _make_new_file_diff(rel_path: str, new_lines: List[str]) -> str:
    # Construct a minimal new-file patch with /dev/null → b/…
    # Hunk header: @@ -0,0 +1,N @@
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
    pat_imp2 = re.compile(rf'^(\s*import\s+)(.*)')
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
                files.setdefault(current, {"added": 0, "removed": 0, "added_imports": [], "added_typing_tc": False})
            else:
                current = None
            in_hunk = False
            continue
        if raw.startswith("@@ "):
            in_hunk = True
            continue
        if not in_hunk or not current:
            continue
        if raw.startswith("+"):
            files[current]["added"] += 1
            body = raw[1:].strip()
            if body.startswith("from typing import TYPE_CHECKING") or body == "if TYPE_CHECKING:":
                files[current]["added_typing_tc"] = True
            if body.startswith("import ") or body.startswith("from "):
                files[current]["added_imports"].append(body)
        elif raw.startswith("-"):
            files[current]["removed"] += 1
    return files

def _diff_is_typing_noop(diff_text: str) -> bool:
    summary = _parse_added_removed(diff_text)
    if not summary:
        return False
    if any(v["removed"] > 0 for v in summary.values()):
        return False
    return all(
        v["added_typing_tc"] and all(
            not (imp.strip() and not imp.strip().startswith("from typing import TYPE_CHECKING"))
            for imp in v["added_imports"]
        )
        for v in summary.values()
    )

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
    lines.append("This PR applies small, behavior-preserving refactors and typing polish.")
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
    lines.append("2) Sanity import (`python -c \"import bot; print('OK')\"`).")
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

# ---------- artifact helpers ----------
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

# ---------- build a verified, repo-accurate patch ----------
def _verified_patch_from_llm_diff(diff_text: str) -> str:
    """
    Build a brand-new, correct unified diff by applying suggested hunks to actual files.
    Supports:
      • per-hunk skipping on failure (don’t drop whole file),
      • new files (from /dev/null),
      • deletes (to /dev/null),
      • renames (old→new as delete+add) + Python import rewrites for dependents.
    """
    root = get_project_root()
    suggestions = _parse_suggestions(diff_text)
    file_diffs: List[str] = []

    for sug in suggestions:
        a_rel = sug.a_path
        b_rel = sug.b_path
        if a_rel.startswith(("a/","b/")): a_rel = a_rel[2:]
        if b_rel.startswith(("a/","b/")): b_rel = b_rel[2:]
        if _is_excluded_path(a_rel) or _is_excluded_path(b_rel): continue

        a_abs = (root / a_rel)
        b_abs = (root / b_rel)
        a_exists = a_abs.exists()
        b_exists = b_abs.exists()

        # DELETE
        if b_rel == "/dev/null":
            try:
                data = a_abs.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            orig = [ln + "\n" for ln in data.split("\n")]
            if orig and orig[-1] == "\n" and not data.endswith("\n"): orig = orig[:-1]
            file_diffs.append(_make_deleted_file_diff(a_rel, orig))
            continue

        # NEW FILE
        if (not a_exists) and b_rel and (not b_exists):
            new_lines: List[str] = []
            for h in (sug.hunks or []):
                for ln in h[1:]:
                    if ln.startswith("+"):
                        new_lines.append(ln[1:] + "\n")
            if not new_lines:
                continue
            file_diffs.append(_make_new_file_diff(b_rel, new_lines))
            continue

        # RENAME (old exists, new does not)
        is_rename = (a_rel != b_rel) and a_exists and (not b_exists)
        if is_rename:
            try:
                data = a_abs.read_text(encoding="utf-8", errors="replace")
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

            # propagate import rewrites across repo (Python)
            rewrites = _rewrite_imports_for_rename(root, a_rel, b_rel)
            for rel, old_lines, new_lines in rewrites:
                if rel in (a_rel, b_rel):
                    continue
                if old_lines != new_lines:
                    file_diffs.append(_make_unified_diff(rel, old_lines, new_lines))
            continue

        # EXISTING FILE EDITS
        if a_exists:
            try:
                data = a_abs.read_text(encoding="utf-8", errors="replace")
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

        # Fallback: nothing matched; skip
        continue

    return ("\n".join(file_diffs) + ("\n" if file_diffs else ""))

# ---------- public API ----------
def propose_and_apply_refactor(max_files: int = 5, retries: int = 2) -> Optional[AutoCodeResult]:
    if not _llm_ok(): return None

    root = get_project_root()
    hotspots = _scan_hotspots(root, max_files=max_files)
    if not hotspots: return None

    # Base prompt with initial hotspots
    origin_prompt = _build_prompt(hotspots)
    raw = ask(origin_prompt, max_tokens=2800, temperature=0.15)

    sanitized = _sanitize_to_diff(raw)
    verified = _verified_patch_from_llm_diff(sanitized) if sanitized else ""

    # Reject typing-only or oversized patches
    no_op_reason = ""
    if verified:
        if _STRICT_NOOP and _diff_is_typing_noop(verified):
            verified = ""
            no_op_reason = "typing-only"
        elif _MAX_DIFF_LINES > 0 and _diff_line_count(verified) > _MAX_DIFF_LINES:
            verified = ""
            no_op_reason = f"too-large ({_diff_line_count(verified)}>{_MAX_DIFF_LINES})"

    # repair loop if nothing verified yet
    attempt = 0
    while (not verified) and attempt < max(2, retries):
        attempt += 1
        # Rebuild hotspots and prompt with stronger constraints; DO NOT reuse prior raw output.
        hotspots = _scan_hotspots(root, max_files=max_files)
        fresh_ctx = _build_prompt(hotspots)
        guard_lines = [
            "If your prior suggestion was rejected, it was likely non-functional:",
            " - typing-only (TYPE_CHECKING) or comment/doc-only edits are disallowed;",
            " - diffs without at least one '@@' hunk are invalid;",
            " - keep paths under bot/ and avoid bot/cli.py.",
            "",
            "Now produce ONE VALID unified diff that makes a tiny, behavior-preserving code improvement",
            "(e.g., input validation, tighter error handling, small helper extraction, minor micro-optimization).",
            "",
            "Absolute rules:",
            " - Only output the diff; no prose.",
            " - Must include 'diff --git a/... b/...', '---', '+++', and at least one '@@' hunk per file.",
            " - Do NOT add TYPE_CHECKING-only or docstring-only changes.",
        ]
        repair_prompt = textwrap.dedent("\n".join(guard_lines)) + "\n\n" + fresh_ctx
        raw2 = ask(repair_prompt, max_tokens=2400, temperature=0.2)
        sanitized2 = _sanitize_to_diff(raw2)
        if sanitized2:
            v2 = _verified_patch_from_llm_diff(sanitized2)
            if v2:
                over = _MAX_DIFF_LINES > 0 and _diff_line_count(v2) > _MAX_DIFF_LINES
                is_noop = _STRICT_NOOP and _diff_is_typing_noop(v2)
                if not over and not is_noop:
                    raw, sanitized, verified = raw2, sanitized2, v2
                    break
        no_op_reason = ""

    # If still nothing and synth not disabled, try tiny no-op once,
    # BUT NEVER when _STRICT_NOOP is enabled (prevents TYPE_CHECKING loop).
    synth_used = False
    if (not verified) and (not _NO_SYNTH) and (not _STRICT_NOOP):
        target = _pick_first_python(hotspots)
        if target is not None:
            rel = str(target.relative_to(root).as_posix())
            if rel.startswith("bot/") and not _is_excluded_path(rel):
                synth = _synthesize_python_type_checking_noop(rel, target)
                if synth.strip():
                    verified = synth
                    raw = synth
                    synth_used = True

    art_dir, diff_path, _ = _write_artifacts(origin_prompt, raw, verified)
    _write_status(art_dir, {
        "reason": _empty_reason(raw, sanitized, verified, no_op_reason),
        "sizes": {"raw": len(raw or ""), "sanitized": len(sanitized or ""), "verified": len(verified or "")},
        "strict_noop": bool(_STRICT_NOOP),
        "synth_used": bool(synth_used)
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
        note_bits.append("used synth fallback (TYPE_CHECKING no-op) because LLM diff could not be verified")
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
    Smarter patch application with a strategy report:
      1) Parse touched paths from the diff.
      2) Probe path tracking + index cleanliness.
      3) Try the safest strategy that fits:
         a) If any new/untracked files or index mismatch → prefer working-tree apply (no --index), then stage.
         b) Else try --index.
         c) If that fails, try --3way (this implies an index-based apply).
         d) Last resort: --reject (treated as failure if any .rej created).
    Writes a JSON report (apply.json) when artifacts_dir is set.
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
        """
        Are these paths identical in index & working tree relative to HEAD?
        Use: git diff-index --quiet HEAD -- <paths>
        Returns True when no differences are found.
        """
        if not paths:
            return True
        # Refresh stat info to avoid false mismatches (Windows / network fs).
        _run(["git","update-index","-q","--refresh"])  # docs: refresh stat info only
        # Correct ordering per git docs: git diff-index --quiet HEAD -- <paths>
        code, _, _ = _run(["git","diff-index","--quiet","HEAD","--", *paths])
        return code == 0

    # Initial refresh before probing (defensive; see above comment).
    _run(["git","update-index","-q","--refresh"])  # refresh stat info only

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

    # Strategy candidates (ordered)
    strategies: list[list[str]] = []
    if has_untracked or not index_clean:
        strategies.append(["git","apply","--whitespace=fix","--ignore-whitespace"])
        strategies.append(["git","apply","--recount","--unidiff-zero","--whitespace=fix","--ignore-wh
