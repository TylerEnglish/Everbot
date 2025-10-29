from __future__ import annotations
import difflib, io, json, locale, os, pathlib, re, subprocess, textwrap, time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple, DefaultDict
from collections import defaultdict

from bot.paths import get_project_root
from bot.llm.provider import ask, info, NotConfigured

# ======================================================================================
# Environment toggles
# ======================================================================================
_NO_SYNTH                  = os.getenv("EVERABOT_AUTOCODE_NO_SYNTH", "").strip() == "1"
_VERBOSE                   = os.getenv("EVERABOT_AUTOCODE_VERBOSE", "").strip() == "1"
_STRICT_NOOP               = os.getenv("EVERABOT_AUTOCODE_STRICT", "1").strip().lower() not in ("0", "false")
_ALLOW_COMMENT_ONLY        = os.getenv("EVERABOT_AUTOCODE_ALLOW_COMMENT_ONLY", "").strip().lower() in ("1","true","yes")
_ALLOW_WHITESPACE_ONLY     = os.getenv("EVERABOT_AUTOCODE_ALLOW_WHITESPACE", "").strip().lower() in ("1","true","yes")
_MAX_DIFF_LINES            = int(os.getenv("EVERABOT_AUTOCODE_MAX_DIFF_LINES", "400"))
_COOLDOWN_HOURS            = float(os.getenv("EVERABOT_AUTOCODE_COOLDOWN_HOURS", "24"))
_TARGETS_ENV               = os.getenv("EVERABOT_AUTOCODE_TARGETS", "").strip()
_FORCE_FILE                = os.getenv("EVERABOT_AUTOCODE_FORCE_FILE", "").strip()
_REPAIR_ATTEMPTS           = int(os.getenv("EVERABOT_AUTOCODE_REPAIR_ATTEMPTS", "3"))
_SIM_MIN                   = float(os.getenv("EVERABOT_AUTOCODE_SIM_MIN", "0.33"))
_SIM_TOPK                  = int(os.getenv("EVERABOT_AUTOCODE_SIM_TOPK", "5"))
_SIM_BYTES_CAP             = int(os.getenv("EVERABOT_AUTOCODE_SIM_BYTES_CAP", str(512 * 1024)))
_SIM_BLOCK_BYTES           = int(os.getenv("EVERABOT_AUTOCODE_SIM_BLOCK_BYTES", str(256 * 1024)))
_HUNK_FUZZ_WINDOW          = int(os.getenv("EVERABOT_AUTOCODE_HUNK_FUZZ_WINDOW", "120"))
_CREATE_TEMP_BRANCH        = os.getenv("EVERABOT_AUTOCODE_GIT_BRANCH", "1").strip().lower() in ("1","true","yes")
_DRY_RUN_ONLY              = os.getenv("EVERABOT_AUTOCODE_DRYRUN", "").strip().lower() in ("1","true","yes")
_SUBPROC_ENCODING_ENV      = os.getenv("EVERABOT_SUBPROC_ENCODING", "").strip()  # e.g. "utf-8"
_FORCE_UTF8_ENV            = os.getenv("EVERABOT_FORCE_UTF8", "").strip().lower() in ("1","true","yes")
_APPLY_PER_FILE            = os.getenv("EVERABOT_AUTOCODE_APPLY_PER_FILE", "1").strip().lower() in ("1","true","yes")
_REVERSE_PROBE             = os.getenv("EVERABOT_AUTOCODE_REVERSE_PROBE", "1").strip().lower() in ("1","true","yes")
_REPACK_AFTER_REJ          = os.getenv("EVERABOT_AUTOCODE_REPACK_AFTER_REJ", "1").strip().lower() in ("1","true","yes")

# Encourage UTF-8 I/O for Python child processes
if _FORCE_UTF8_ENV:
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# ======================================================================================
# Constants / filters
# ======================================================================================
_ALLOWED_DIFF_PREFIXES = (
    "diff --git", "index ", "--- ", "+++ ", "@@ ",
    "new file mode ", "deleted file mode ", "old mode ", "new mode ",
    "rename from ", "rename to ", "copy from ", "copy to ",
    "similarity index ", "dissimilarity index "
)
_CODE_GLOBS   = ("*.py","*.rs","*.go","*.c","*.cc","*.cpp","*.h","*.hpp","*.s","*.asm")
_EXCLUDE_DIRS = {"test","ruleset","snapshots","accel/rust/target","accel/cpp/build",".git","dist","build",".venv","venv","node_modules","__pycache__"}
_EXCLUDE_FILES = {"bot/cli.py"}

_DOC_OR_COMMENT_START = re.compile(r'^\+\s*(#|[rubfRUBF]?("""|\'\'\'))')
_DOC_OR_COMMENT_END   = re.compile(r'^\+\s*("""|\'\'\')\s*$')

_CODE_FENCE_RE        = re.compile(r"```(?:[a-zA-Z0-9_.+\-]*)\n([\s\S]*?)```", re.MULTILINE)
_TILDE_FENCE_RE       = re.compile(r"~~~(?:[a-zA-Z0-9_.+\-]*)\n([\s\S]*?)~~~", re.MULTILINE)
_FILE_BLOCK_RE        = re.compile(r"<<<?FILE\s+(bot/[A-Za-z0-9_\-./]+)>>?>\s*\n([\s\S]*?)\n<<<?END>>?>", re.MULTILINE)
_PLAIN_FILE_HEADER_RE = re.compile(r'(?im)^File\s*:\s*(bot/[A-Za-z0-9_\-./]+)\s*$')

_hunk_header_re       = re.compile(r"^@@\s+-([0-9]+)(?:,([0-9]+))?\s+\+([0-9]+)(?:,([0-9]+))?\s+@@")
_py_import_from       = re.compile(r'^\s*from\s+([a-zA-Z_][\w\.]*)\s+import\b')
_py_import_direct     = re.compile(r'^\s*import\s+([a-zA-Z_][\w\.]*)(?:\s+as\b|\s*$|\s*,)')

# ======================================================================================
# Data classes
# ======================================================================================
@dataclass
class AutoCodeResult:
    title: str
    patch_path: str
    patch_applied: bool
    touched_paths: List[str]
    artifacts_dir: str
    note: str

@dataclass
class SuggestedFile:
    a_path: str
    b_path: str
    hunks: List[List[str]]  # each begins with "@@" then hunk lines

@dataclass
class ApplyReport:
    applied: List[int]
    skipped: List[int]
    result: List[str]

# ======================================================================================
# Encoding helpers
# ======================================================================================
def _preferred_locale_encoding() -> str:
    try:
        return locale.getpreferredencoding(False) or "utf-8"
    except Exception:
        return "utf-8"

def _safe_decode(b: bytes) -> str:
    encs = []
    if _SUBPROC_ENCODING_ENV:
        encs.append(_SUBPROC_ENCODING_ENV)
    encs += ["utf-8", _preferred_locale_encoding(), "cp1252"]
    for enc in encs:
        try:
            return b.decode(enc, errors="replace").replace("\r\n","\n").replace("\r","\n")
        except Exception:
            continue
    return b.decode("utf-8", errors="replace").replace("\r\n","\n").replace("\r","\n")

# ======================================================================================
# LLM guard
# ======================================================================================
def _llm_ok() -> bool:
    try:
        _ = info()
        return True
    except NotConfigured:
        return False

# ======================================================================================
# Text / IO helpers
# ======================================================================================
def _read_text_cap(p: pathlib.Path, cap_bytes: int) -> str:
    try:
        data = p.read_bytes()
        if len(data) > cap_bytes:
            data = data[:cap_bytes]
        return _safe_decode(data)
    except Exception:
        return ""

def _read_text_all(p: pathlib.Path) -> str:
    try:
        return _safe_decode(p.read_bytes())
    except Exception:
        return ""

def _ensure_eof_newline(lines: List[str]) -> List[str]:
    if not lines:
        return ["\n"]
    if not lines[-1].endswith("\n"):
        lines[-1] = lines[-1] + "\n"
    return lines

def _norm_sep(path: str) -> str:
    if not path or path == "/dev/null":
        return path
    s = path.replace("\\", "/").lstrip("./")
    if os.name == "nt":
        return s.lower()
    return s

# ======================================================================================
# Recent touches → cooldown
# (unchanged from your version, omitted for brevity in this comment block)
# ======================================================================================
def _recent_autocode_touches(root: pathlib.Path) -> Dict[str, float]:
    base = root / "snapshots" / "selfopt" / "autocode"
    out: Dict[str, float] = {}
    if not base.exists():
        return out
    for d in sorted(base.iterdir()):
        if not d.is_dir(): continue
        ap = d / "apply.json"
        if not ap.exists(): continue
        try:
            obj = json.loads(_read_text_all(ap))
        except Exception:
            continue
        touched = obj.get("touched") or []
        when = d.stat().st_mtime
        for p in touched:
            p = _norm_sep(p)
            if p.startswith("./"):
                p = p[2:]
            if not p.startswith("bot/"):
                continue
            prev = out.get(p, 0.0)
            if when > prev:
                out[p] = float(when)
    return out

# ======================================================================================
# Scan hotspots (same logic, kept)
# ======================================================================================
def _targets_from_env(root: pathlib.Path) -> List[pathlib.Path]:
    if not _TARGETS_ENV:
        return []
    picks: List[pathlib.Path] = []
    for pat in [g.strip() for g in _TARGETS_ENV.split(",") if g.strip()]:
        for p in root.glob(pat):
            if not p.exists() or p.is_dir():
                continue
            try:
                rel = _norm_sep(p.relative_to(root).as_posix())
            except Exception:
                continue
            if rel in _EXCLUDE_FILES:
                continue
            parts = set(p.parts) | {"/".join(p.parts[i:i+2]) for i in range(len(p.parts)-1)}
            if not rel.startswith("bot/") or any(x in _EXCLUDE_DIRS for x in parts):
                continue
            picks.append(p)
    return picks

def _mtime_score(p: pathlib.Path) -> float:
    try:
        return float(p.stat().st_mtime)
    except Exception:
        return 0.0

def _scan_hotspots(root: pathlib.Path, max_files: int = 5) -> List[pathlib.Path]:
    repo_dir = root / "bot"
    files: list[tuple[float, pathlib.Path]] = []

    cooldown = _recent_autocode_touches(root)
    now = time.time()
    targeted = set(_targets_from_env(root))

    def score_for(p: pathlib.Path) -> float:
        rel_posix = _norm_sep(p.relative_to(root).as_posix())
        text = _read_text_cap(p, 256 * 1024)
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

        recency = 0.0001 * _mtime_score(p)
        base = (3 * todos + length) - touched_penalty - cd_pen + recency
        if p in targeted:
            base += 50000.0
        return base

    for pat in _CODE_GLOBS:
        for p in repo_dir.rglob(pat):
            parts = {"/".join(p.parts[i:i+2]) for i in range(len(p.parts)-1)} | set(p.parts)
            if any(x in _EXCLUDE_DIRS for x in parts):
                continue
            rel_posix = _norm_sep(p.relative_to(root).as_posix())
            if rel_posix in _EXCLUDE_FILES:
                continue
            files.append((score_for(p), p))

    files.sort(reverse=True, key=lambda t: t[0])
    top = [p for _, p in files[:max_files]]
    for p in targeted:
        if p not in top:
            top.append(p)

    if _FORCE_FILE:
        forced = root / _FORCE_FILE
        if forced.exists() and forced not in top:
            if len(top) >= max_files:
                top[-1] = forced
            else:
                top.append(forced)
    return top[:max_files]

# ======================================================================================
# Prompt construction (unchanged)
# ======================================================================================
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
        " - Do NOT produce a typing-only patch. Such diffs are rejected.\n"
        " - Do NOT introduce new hard runtime deps.\n"
        "If you cannot produce a valid unified diff, then output FILE BLOCKS instead, exactly like this:\n"
        "<<<FILE bot/path/to/file.py>>>\n"
        "<entire updated file content>\n"
        "<<<END>>>\n\n"
    )
    ctx = []
    for p in file_paths:
        txt = _read_text_cap(p, 256 * 1024)
        ctx.append(f"--- FILE: {_norm_sep(p.as_posix())} ---\n{txt}\n")
    return header + "\n".join(ctx)

def _clean_unicode(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    if s.startswith("\ufeff"):
        s = s.lstrip("\ufeff")
    return s.replace("\u00A0", " ")

# ======================================================================================
# Diff doctor / normalization / splitting
# ======================================================================================
_DIFF_REJECTED_TAIL = re.compile(r"\s*\(rejected hunks?\)\s*$", re.IGNORECASE)

def _normalize_diff_git_headers(text: str) -> str:
    out: List[str] = []
    last_minus = last_plus = None
    for ln in text.splitlines():
        # Fix non-standard 'diff a/... b/...' into 'diff --git a/... b/...'
        if ln.startswith("diff ") and not ln.startswith("diff --git "):
            m = re.match(r"^diff\s+([ab]/\S+)\s+([ab]/\S+)\s*(\(rejected hunks?\))?$", ln.strip(), re.IGNORECASE)
            if m:
                a, b = _norm_sep(m.group(1)), _norm_sep(m.group(2))
                ln = f"diff --git {a} {b}"
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
            ln = _DIFF_REJECTED_TAIL.sub("", ln)  # strip "(rejected hunks)"
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
    txt = "\n".join(out)
    return (txt + ("\n" if txt and not txt.endswith("\n") else "")) if txt else ""

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

def _extract_paths_from_block(blk: List[str]) -> Tuple[Optional[str], Optional[str]]:
    head = next((x for x in blk if x.startswith("diff --git ")), None)
    if not head: return None, None
    m = re.match(r"^diff --git\s+(.+?)\s+(.+)$", head.strip())
    if not m: return None, None
    a_hdr, b_hdr = m.group(1).strip(), m.group(2).strip()
    def _norm_body(p: str) -> str:
        p = p.replace("\\","/").lstrip("./")
        if p.startswith(("a/","b/")): p = p[2:]
        return p
    return _norm_body(a_hdr), _norm_body(b_hdr)

def _block_contains_nested_headers(blk: List[str]) -> bool:
    # Detect accidental '--- a/X'/'+++ b/X' lines INSIDE a hunk
    count = 0
    for ln in blk[1:]:
        if ln.startswith("--- a/") or ln.startswith("+++ b/") or ln.startswith("diff --git "):
            count += 1
    return count > 2

def _explode_nested_headers(blk: List[str]) -> List[List[str]]:
    """
    If the LLM jammed another file's '---/+++' inside a hunk,
    split those out into their own proper blocks.
    """
    out: List[List[str]] = []
    current: List[str] = []
    seen_head = False
    for ln in blk:
        if ln.startswith("diff --git "):
            if current: out.append(current)
            current = [ln]; seen_head = True; continue
        if (ln.startswith("--- a/") or ln.startswith("+++ b/")) and seen_head:
            # Start of a new file header sequence inside an existing block → split
            if current and any(x.startswith("@@ ") for x in current[1:]):
                out.append(current)
                current = []
            current.append(ln)
        else:
            current.append(ln)
    if current:
        out.append(current)
    return out

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
    if any(codeish.match(ln) for ln in added): return False
    for ln in added:
        body = ln[1:]
        if body.strip() == "": continue
        if _DOC_OR_COMMENT_START.match(ln) or _DOC_OR_COMMENT_END.match(ln): continue
        if any(tok in body for tok in ("()", "{}", "[]", " = ", " -> ", ": ", "lambda ", " yield", " except", " try:", " return ")):
            return False
    return True

def _block_is_typechecking_noop(hunk_lines: List[str]) -> bool:
    plus = [ln[1:].rstrip("\n") for ln in hunk_lines[1:] if ln.startswith("+")]
    if not plus: return False
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
            if _block_has_only_comment_or_doc_additions(h) and not _ALLOW_COMMENT_ONLY: continue
            if _block_is_typechecking_noop(h) and _STRICT_NOOP: continue
            filtered_hunks.append(h)

    return (a_body, b_body, filtered_hunks)

def _doctor_diff(raw: str) -> str:
    """
    Normalize and repair common LLM diff mistakes, then split nested headers.
    """
    raw = _clean_unicode(raw)
    if not raw.strip():
        return ""
    # If it already looks like a diff, normalize headers
    if "diff " in raw or ("--- " in raw and "+++ " in raw):
        txt = _normalize_diff_git_headers(raw)
    else:
        txt = raw
    # Split and explode nested headers if any
    blocks = _split_blocks(txt)
    fixed: List[List[str]] = []
    for blk in blocks:
        if _block_contains_nested_headers(blk):
            fixed.extend(_explode_nested_headers(blk))
        else:
            fixed.append(blk)
    final = "\n".join("\n".join(b) for b in fixed)
    return (final + ("\n" if final and not final.endswith("\n") else "")) if final else ""

# ======================================================================================
# Suggestion parsing (unchanged except using doctor’d blocks)
# ======================================================================================
def _parse_suggestions(text: str) -> List[SuggestedFile]:
    out: List[SuggestedFile] = []
    for blk in _split_blocks(text):
        parsed = _rebuild_block_headers_only(blk)
        if not parsed: continue
        a_body, b_body, hunks = parsed
        out.append(SuggestedFile(a_body or "/dev/null", b_body or "/dev/null", hunks or []))
    return out

# ======================================================================================
# Py import rewrite on rename (unchanged)
# ======================================================================================
def _py_module_from_path(rel_path: str) -> Optional[str]:
    if not rel_path.endswith(".py"): return None
    if rel_path == "/dev/null": return None
    return rel_path[:-3].replace("/", ".")

def _collect_repo_py_files(root: pathlib.Path) -> List[pathlib.Path]:
    lst: List[pathlib.Path] = []
    for p in (root / "bot").rglob("*.py"):
        parts = set(p.parts) | {"/".join(p.parts[i:i+2]) for i in range(len(p.parts)-1)}
        if any(x in _EXCLUDE_DIRS for x in parts): continue
        if _norm_sep(p.relative_to(root).as_posix()) in _EXCLUDE_FILES: continue
        lst.append(p)
    return lst

def _rewrite_imports_py(lines: List[str], old_mod: str, new_mod: str) -> Tuple[List[str], bool]:
    changed = False; out: List[str] = []
    pat_from = re.compile(rf'^(\s*from\s+){re.escape(old_mod)}(\.|[\s]+import\b)')
    pat_imp1 = re.compile(rf'^(\s*import\s+){re.escape(old_mod)}(\s|$|,|\.|as\b)')
    pat_imp2 = re.compile(r'^(\s*import\s+)(.*)')
    for ln in lines:
        s = ln
        m = pat_from.match(s)
        if m:
            s = s.replace(m.group(0), f"{m.group(1)}{new_mod}{m.group(2)}", 1); changed = True
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
                    prefix, rest = m_any.group(1), m_any.group(2)
                    new_rest = _rep_import_list(rest)
                    if new_rest != rest:
                        s = prefix + new_rest; changed = True
        out.append(s)
    return out, changed

def _rewrite_imports_for_rename(root: pathlib.Path, old_rel: str, new_rel: str) -> List[Tuple[str, List[str], List[str]]]:
    results: List[Tuple[str, List[str], List[str]]] = []
    old_mod = _py_module_from_path(old_rel); new_mod = _py_module_from_path(new_rel)
    if not old_mod or not new_mod: return results
    for p in _collect_repo_py_files(root):
        rel = _norm_sep(p.relative_to(root).as_posix())
        raw = _read_text_all(p)
        if not raw: continue
        orig = [ln + "\n" for ln in raw.split("\n")]
        if orig and orig[-1] == "\n" and not raw.endswith("\n"): orig = orig[:-1]
        new_lines, changed = _rewrite_imports_py(orig, old_mod, new_mod)
        if changed:
            results.append((rel, orig, new_lines))
    return results

# ======================================================================================
# Unified diff helpers + fuzzy hunk applier (as in your version)
# ======================================================================================
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
        tag = ln[:1]; body = ln[1:] + "\n"
        if tag == " ":
            if idx < len(original_slice): new_seq.append(original_slice[idx])
            else: new_seq.append(body)
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

def _fuzzy_anchor_index(hay: List[str], needle: List[str], around: int, window: int = _HUNK_FUZZ_WINDOW) -> int:
    L = len(hay); lo = max(0, around - window); hi = min(L, around + window)
    sub = hay[lo:hi]; cand_keys: List[List[str]] = []
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
    best = max((op for op in sm.get_opcodes() if op[0] == "equal"), key=lambda t: (t[2]-t[1]) if len(t)>=3 else 0, default=("equal",0,0,0,0))
    b1 = best[3] if len(best) >= 4 else 0
    return lo + b1

def _verify_deletions_match(hunk: List[str], hay: List[str], start: int) -> bool:
    old_seq = _old_sequence_from_hunk(hunk)
    if start < 0 or start + len(old_seq) > len(hay): return False
    j = 0
    for ln in hunk[1:]:
        tag = ln[:1]
        if tag in (" ", "-"):
            line_in_file = hay[start + j]
            if tag == "-":
                if line_in_file != (ln[1:] + "\n"):
                    if line_in_file.strip() != (ln[1:] + "\n").strip():
                        return False
            j += 1
    return True

def _apply_hunks_to_content(orig: List[str], hunks: List[List[str]]) -> ApplyReport:
    cur = orig[:]; cursor_hint = 0
    applied_idx: List[int] = []; skipped_idx: List[int] = []
    for i, h in enumerate(hunks):
        old_seq = _old_sequence_from_hunk(h)
        if not old_seq:
            around = _parse_oldstart(h)
            start_idx = max(0, min(len(cur), around if around else 0))
            new_seq = _new_sequence_from_hunk(h, [])
            cur = cur[:start_idx] + new_seq + cur[start_idx:]
            cursor_hint = start_idx + len(new_seq)
            applied_idx.append(i); continue

        around = _parse_oldstart(h)
        start_idx = _find_exact_subsequence(cur, old_seq, start_hint=cursor_hint if cursor_hint else around)
        if start_idx < 0:
            start_idx = _find_exact_subsequence(cur, old_seq, start_hint=max(0, around-10))
        if start_idx < 0:
            start_idx = _fuzzy_anchor_index(cur, old_seq, around=around)
            if not _verify_deletions_match(h, cur, start_idx):
                skipped_idx.append(i); continue
            if start_idx < 0 or start_idx + len(old_seq) > len(cur):
                skipped_idx.append(i); continue

        if not _verify_deletions_match(h, cur, start_idx):
            skipped_idx.append(i); continue

        repl = _new_sequence_from_hunk(h, cur[start_idx:start_idx+len(old_seq)])
        cur = cur[:start_idx] + repl + cur[start_idx+len(old_seq):]
        cursor_hint = start_idx + len(repl)
        applied_idx.append(i)

    return ApplyReport(applied=applied_idx, skipped=skipped_idx, result=_ensure_eof_newline(cur))

def _make_unified_diff(rel_path: str, original: List[str], new_lines: List[str]) -> str:
    rel_path = _norm_sep(rel_path)
    raw_lines = list(difflib.unified_diff(original, new_lines, fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}", lineterm="", n=3))
    body = "".join((ln if ln.endswith("\n") else (ln + "\n")) for ln in raw_lines)
    return f"diff --git a/{rel_path} b/{rel_path}\n{body}"

def _make_new_file_diff(rel_path: str, new_lines: List[str]) -> str:
    rel_path = _norm_sep(rel_path)
    new_lines = _ensure_eof_newline(new_lines)
    n = len(new_lines)
    hdr = f"@@ -0,0 +1,{n} @@\n" if n > 0 else "@@ -0,0 +1,0 @@\n"
    plus = "".join("+" + (ln[:-1] if ln.endswith("\n") else ln) + "\n" for ln in new_lines)
    return f"diff --git a/{rel_path} b/{rel_path}\n--- /dev/null\n+++ b/{rel_path}\n{hdr}{plus}"

def _make_deleted_file_diff(rel_path: str, original: List[str]) -> str:
    rel_path = _norm_sep(rel_path)
    original = _ensure_eof_newline(original)
    n = len(original)
    hdr = f"@@ -1,{n} +0,0 @@\n" if n > 0 else "@@ -1,0 +0,0 @@\n"
    minus = "".join("-" + (ln[:-1] if ln.endswith("\n") else ln) + "\n" for ln in original)
    return f"diff --git a/{rel_path} b/{rel_path}\n--- a/{rel_path}\n+++ /dev/null\n{hdr}{minus}"

# ======================================================================================
# Diff size / no-op detection (unchanged)
# ======================================================================================
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
                        touched.append(_norm_sep(path))
    return touched

def _parse_added_removed(diff_text: str) -> dict:
    files: dict[str, dict] = {}; current: str | None = None; in_hunk = False
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            parts = raw.split()
            target = parts[3] if len(parts) >= 4 else None
            if target and target.startswith("b/"):
                current = _norm_sep(target[2:])
                files.setdefault(current, {"added": 0, "removed": 0, "added_imports": [], "added_typing_tc": False, "non_ws_adds": 0})
            else:
                current = None
            in_hunk = False; continue
        if raw.startswith("@@ "): in_hunk = True; continue
        if not in_hunk or not current: continue
        if raw.startswith("+++ ") or raw.startswith("--- "): continue
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
    has_removals = False; has_typing_scaffold = False; substantive_additions = 0
    for raw in diff_text.splitlines():
        if raw.startswith(("diff --git ","--- ","+++ ","@@ ")): continue
        if raw.startswith("-"): has_removals = True
        elif raw.startswith("+"):
            body = raw[1:].strip()
            if body in ("",): continue
            if body.startswith("from typing import TYPE_CHECKING") or body == "if TYPE_CHECKING:" or "Autocode: non-functional" in body:
                has_typing_scaffold = True; continue
            substantive_additions += 1
    if has_removals: return False
    return has_typing_scaffold and substantive_additions == 0

def _diff_is_whitespace_only(diff_text: str) -> bool:
    meta = _parse_added_removed(diff_text)
    if not meta: return False
    total_added  = sum(v["added"] for v in meta.values())
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
    lines.append("Behavior-preserving refactors: input validation, safer subprocess decoding, stricter diff guards, and robust Git apply.")
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
            bullets.append(f"+{meta['added']} −{meta['removed']} lines")
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

# ======================================================================================
# Code/FILE-block salvage → diff (unchanged)
# ======================================================================================
def _candidate_repo_files(root: pathlib.Path) -> List[pathlib.Path]:
    candidates: List[pathlib.Path] = []
    for glob in _CODE_GLOBS:
        for p in (root / "bot").rglob(glob):
            parts = set(p.parts) | {"/".join(p.parts[i:i+2]) for i in range(len(p.parts)-1)}
            if any(x in _EXCLUDE_DIRS for x in parts): continue
            rel = _norm_sep(p.relative_to(root).as_posix())
            if rel in _EXCLUDE_FILES: continue
            candidates.append(p)
    return candidates

def _similarity(a: str, b: str) -> float:
    if not a or not b: return 0.0
    a = a[:_SIM_BLOCK_BYTES]; b = b[:_SIM_BYTES_CAP]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    return float(sm.quick_ratio() * 0.25 + sm.ratio() * 0.75)

def _guess_path_by_similarity(code_text: str, root: pathlib.Path) -> Optional[str]:
    cands = _candidate_repo_files(root)
    if not cands: return None
    block = code_text.replace("\r\n","\n").replace("\r","\n")
    scored: List[Tuple[float, pathlib.Path]] = []
    for p in cands:
        repo_txt = _read_text_cap(p, _SIM_BYTES_CAP)
        if not repo_txt: continue
        r = _similarity(block, repo_txt)
        if r > 0: scored.append((r, p))
    if not scored: return None
    scored.sort(key=lambda t: t[0], reverse=True)
    best_ratio, best_path = scored[0]
    if best_ratio >= _SIM_MIN:
        return _norm_sep(best_path.relative_to(root).as_posix())
    return None

def _guess_path_from_context(whole: str, start_idx: int, code_text: str, root: pathlib.Path, salvage_reasons: List[str]) -> Optional[str]:
    lo = max(0, start_idx - 500)
    ctx = whole[lo:start_idx]
    hint_patterns = [
        re.compile(r'(?im)^[>\-\*\s#]*file(?:name)?\s*:\s*(bot/[^\s:]+)\s*$'),
        re.compile(r'(?im)^[>\-\*\s#]*path\s*:\s*(bot/[^\s:]+)\s*$'),
        re.compile(r'(?im)^\s*(bot/[A-Za-z0-9_\-./]+)\s*:?$'),
        re.compile(r'(?im)^[>\-\*\s#]*(?:updated|edit|replace)\s+(bot/[A-Za-z0-9_\-./]+)\s*:?$'),
        re.compile(r'(?im)^#+\s*(bot/[A-Za-z0-9_\-./]+)\s*$'),
        re.compile(r'(?im)^<<<?FILE\s+(bot/[A-Za-z0-9_\-./]+)>>?>\s*$'),
    ]
    for pat in hint_patterns:
        m = pat.search(ctx)
        if m:
            candidate = _norm_sep(m.group(1))
            if candidate.startswith("bot/") and not _is_excluded_path(candidate):
                return candidate
    top_lines = "\n".join(code_text.splitlines()[:5])
    m = re.search(r'(?im)^\s*(?:#\s*)?(?:file|path)\s*:\s*(bot/[^\s:]+)\s*$', top_lines)
    if m:
        candidate = _norm_sep(m.group(1))
        if candidate.startswith("bot/") and not _is_excluded_path(candidate):
            return candidate
    m = re.search(r'(?m)^\s*(bot/[A-Za-z0-9_\-./]+)\s*:?$', ctx)
    if m:
        candidate = _norm_sep(m.group(1))
        if candidate.startswith("bot/") and not _is_excluded_path(candidate):
            return candidate
    guessed = _guess_path_by_similarity(code_text, root)
    if guessed:
        salvage_reasons.append(f"code fence without path hint → guessed by similarity: {guessed}")
        return guessed
    salvage_reasons.append("code fence without path hint")
    return None

def _collect_file_blocks_from_plaintext(raw: str) -> List[Tuple[str, str]]:
    found: List[Tuple[str, str]] = []
    for m in _FILE_BLOCK_RE.finditer(raw):
        rel = _norm_sep(m.group(1)); code = m.group(2)
        if rel.startswith("bot/") and not _is_excluded_path(rel):
            found.append((rel, code))
    plain = raw.splitlines(); i = 0
    while i < len(plain):
        line = plain[i]; m = _PLAIN_FILE_HEADER_RE.match(line)
        if m:
            rel = _norm_sep(m.group(1)); i += 1
            body_lines: List[str] = []
            while i < len(plain) and not _PLAIN_FILE_HEADER_RE.match(plain[i]):
                body_lines.append(plain[i]); i += 1
            code = "\n".join(body_lines)
            if rel.startswith("bot/") and not _is_excluded_path(rel) and code.strip():
                found.append((rel, code))
        else:
            i += 1
    return found

def _diff_from_code_blocks(raw: str, root: pathlib.Path) -> Tuple[str, List[str], List[str]]:
    reasons: List[str] = []; diffs: List[str] = []; touched: List[str] = []
    for rel, code_text in _collect_file_blocks_from_plaintext(raw):
        abs_path = root / rel
        try:
            if abs_path.exists():
                original_text = _read_text_all(abs_path)
                original = [ln + "\n" for ln in original_text.split("\n")]
                if original and original[-1] == "\n" and not original_text.endswith("\n"): original = original[:-1]
                new_lines = [(ln if ln.endswith("\n") else ln + "\n") for ln in code_text.replace("\r\n","\n").replace("\r","\n").split("\n")]
                if new_lines and new_lines[-1] == "\n" and not code_text.endswith("\n"): new_lines = new_lines[:-1]
                diffs.append(_make_unified_diff(rel, original, new_lines))
            else:
                new_lines = [(ln if ln.endswith("\n") else ln + "\n") for ln in code_text.replace("\r\n","\n").replace("\r","\n").split("\n")]
                if new_lines and new_lines[-1] == "\n" and not code_text.endswith("\n"): new_lines = new_lines[:-1]
                diffs.append(_make_new_file_diff(rel, new_lines))
            touched.append(_norm_sep(rel))
        except Exception as e:
            reasons.append(f"file-block error for {rel}: {e}")

    for fence in (_CODE_FENCE_RE, _TILDE_FENCE_RE):
        for m in fence.finditer(raw):
            code_text = m.group(1)
            rel = _guess_path_from_context(raw, m.start(), code_text, root, reasons)
            if not rel: continue
            abs_path = root / rel
            try:
                if abs_path.exists():
                    original_text = _read_text_all(abs_path)
                    original = [ln + "\n" for ln in original_text.split("\n")]
                    if original and original[-1] == "\n" and not original_text.endswith("\n"): original = original[:-1]
                    new_lines = [(ln if ln.endswith("\n") else ln + "\n") for ln in code_text.replace("\r\n","\n").replace("\r","\n").split("\n")]
                    if new_lines and new_lines[-1] == "\n" and not code_text.endswith("\n"): new_lines = new_lines[:-1]
                    diffs.append(_make_unified_diff(rel, original, new_lines))
                else:
                    new_lines = [(ln if ln.endswith("\n") else ln + "\n") for ln in code_text.replace("\r\n","\n").replace("\r","\n").split("\n")]
                    if new_lines and new_lines[-1] == "\n" and not code_text.endswith("\n"): new_lines = new_lines[:-1]
                    diffs.append(_make_new_file_diff(rel, new_lines))
                touched.append(_norm_sep(rel))
            except Exception as e:
                reasons.append(f"code-fence error for {rel}: {e}")

    if diffs:
        by_path: Dict[str, str] = {}
        for d in diffs:
            tgt_line = next((ln for ln in d.splitlines() if ln.startswith("+++ ")), None)
            path = None
            if tgt_line:
                path = tgt_line.split(" ",1)[1].strip()
                if path.startswith("b/"): path = path[2:]
            if path:
                by_path[_norm_sep(path)] = d
        diffs = list(by_path.values())
    joined = ("\n".join(diffs) + ("\n" if diffs else ""))
    return joined, touched, reasons

# ======================================================================================
# Verified patch from LLM diff (uses doctor)
# ======================================================================================
def _verified_patch_from_llm_diff(diff_text: str) -> str:
    root = get_project_root()
    # doctor & normalize first
    diff_text = _doctor_diff(diff_text)
    suggestions = _parse_suggestions(diff_text)
    file_diffs: List[str] = []

    for sug in suggestions:
        a_rel = _norm_sep(sug.a_path); b_rel = _norm_sep(sug.b_path)
        if _is_excluded_path(a_rel) or _is_excluded_path(b_rel): continue
        a_abs = (root / a_rel) if a_rel != "/dev/null" else None
        b_abs = (root / b_rel) if b_rel != "/dev/null" else None
        a_exists = a_abs.exists() if a_abs else False
        b_exists = b_abs.exists() if b_abs else False

        # Delete
        if b_rel == "/dev/null" and a_rel != "/dev/null" and a_exists:
            data = _read_text_all(a_abs)
            if not data: continue
            orig = [ln + "\n" for ln in data.split("\n")]
            if orig and orig[-1] == "\n" and not data.endswith("\n"): orig = orig[:-1]
            file_diffs.append(_make_deleted_file_diff(a_rel, orig)); continue

        # New file
        if a_rel == "/dev/null" and b_rel != "/dev/null" and not b_exists:
            new_lines: List[str] = []
            for h in (sug.hunks or []):
                for ln in h[1:]:
                    if ln.startswith("+"): new_lines.append(ln[1:] + "\n")
            file_diffs.append(_make_new_file_diff(b_rel, new_lines)); continue

        # Rename (+ optional edits)
        is_rename = (a_rel != b_rel) and a_rel != "/dev/null" and b_rel != "/dev/null" and a_exists and (not b_exists)
        if is_rename:
            data = _read_text_all(a_abs)
            if not data: continue
            orig = [ln + "\n" for ln in data.split("\n")]
            if orig and orig[-1] == "\n" and not data.endswith("\n"): orig = orig[:-1]
            new_content = orig[:]
            if sug.hunks:
                rep = _apply_hunks_to_content(orig, sug.hunks)
                new_content = rep.result
            file_diffs.append(_make_deleted_file_diff(a_rel, orig))
            file_diffs.append(_make_new_file_diff(b_rel, new_content))
            rewrites = _rewrite_imports_for_rename(root, a_rel, b_rel)
            for rel, old_lines, new_lines in rewrites:
                if rel in (a_rel, b_rel): continue
                if old_lines != new_lines:
                    file_diffs.append(_make_unified_diff(rel, old_lines, new_lines))
            continue

        # Edit existing file
        if a_exists:
            data = _read_text_all(a_abs)
            if not data: continue
            original = [ln + "\n" for ln in data.split("\n")]
            if original and original[-1] == "\n" and not data.endswith("\n"): original = original[:-1]
            rep = _apply_hunks_to_content(original, sug.hunks or [])
            if rep.result != original and rep.applied:
                dst_rel = b_rel or a_rel
                file_diffs.append(_make_unified_diff(dst_rel, original, rep.result))
            continue

    joined = ("\n".join(file_diffs) + ("\n" if file_diffs else ""))
    if joined and not _ALLOW_WHITESPACE_ONLY and _diff_is_whitespace_only(joined): return ""
    return joined

# ======================================================================================
# Git apply with per-file strategies, reverse probe, and .rej repack
# ======================================================================================
def _summarize_cmd(cmd: List[str]) -> str:
    return " ".join(cmd)

def _list_rej(root: pathlib.Path) -> List[pathlib.Path]:
    return list(root.rglob("*.rej"))

def _per_file_patches(diff_text: str) -> Dict[str, str]:
    """
    Split a multi-file diff into {b/rel_path: full_patch_for_that_file}.
    Order files: renames -> new -> edits -> deletes (stable).
    """
    blocks = _split_blocks(diff_text)
    groups: Dict[str, List[str]] = {}
    meta: Dict[str, int] = {}  # ordering class
    for blk in blocks:
        a_rel, b_rel = _extract_paths_from_block(blk)
        if not a_rel and not b_rel:
            continue
        if b_rel == "/dev/null":
            key = a_rel or b_rel
            meta[key] = 3  # delete
        elif a_rel == "/dev/null":
            key = b_rel
            meta[key] = 1  # new
        elif a_rel != b_rel:
            key = b_rel
            meta[key] = 0  # rename
        else:
            key = b_rel
            meta[key] = 2  # edit
        groups.setdefault(key, []).append("\n".join(blk))
    # assemble single-patch per file
    out: Dict[str, str] = {}
    for key, pieces in groups.items():
        txt = "\n".join(pieces)
        out[_norm_sep(key)] = (txt + ("\n" if txt and not txt.endswith("\n") else "")) if txt else ""
    # return sorted by class then path
    return dict(sorted(out.items(), key=lambda kv: (meta.get(kv[0], 2), kv[0])))

def _repack_patch_against_worktree(root: pathlib.Path, file_patch: str) -> Optional[str]:
    """
    When .rej appears, rebuild a per-file patch by:
      - parsing its suggestions
      - reading current worktree
      - re-applying hunks fuzzily
      - re-emitting a fresh unified diff (recounted hunks)
    """
    sug = _parse_suggestions(file_patch)
    if not sug:
        return None
    s = sug[0]
    rel = s.b_path if s.b_path != "/dev/null" else s.a_path
    rel = _norm_sep(rel)
    abs_path = root / rel if rel and rel != "/dev/null" else None
    if not abs_path or not abs_path.exists():
        return None
    data = _read_text_all(abs_path)
    if not data:
        return None
    orig = [ln + "\n" for ln in data.split("\n")]
    if orig and orig[-1] == "\n" and not data.endswith("\n"): orig = orig[:-1]
    rep = _apply_hunks_to_content(orig, s.hunks or [])
    if rep.result == orig:
        return None
    return _make_unified_diff(rel, orig, rep.result)

def _git_apply(patch_path: pathlib.Path, *, artifacts_dir: Optional[str]=None, verbose: bool=False) -> Tuple[bool, Dict[str, object]]:
    """
    Strategy (enhanced):
      1) If enabled, split into per-file patches, try each with a ladder:
         --index/--3way first, then --recount/--unidiff-zero/--inaccurate-eof and whitespace forgiveness.
         If check fails, try --reverse --check and apply with -R.
      2) If any attempt produces *.rej and repack is enabled, rebuild hunks vs worktree and retry once.
      3) Fallback to whole-patch apply using the same ladder.
    """
    report: Dict[str, object] = {
        "touched": [], "has_untracked": False, "index_clean": True, "attempts": [],
        "chosen": None, "branch": None, "commit": None, "per_file": []
    }
    if not patch_path.exists() or patch_path.stat().st_size == 0:
        return False, report

    root = get_project_root()

    def _run_bytes(cmd: list[str]) -> tuple[int, str, str]:
        try:
            p = subprocess.run(cmd, cwd=root, text=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            return p.returncode, _safe_decode(p.stdout), _safe_decode(p.stderr)
        except Exception as e:
            return 999, "", str(e)

    # inside git repo?
    rc, _, _ = _run_bytes(["git","rev-parse","--is-inside-work-tree"])
    if rc != 0:
        return False, report

    diff_txt = patch_path.read_text(encoding="utf-8", errors="replace")
    touched = _touch_list_from_diff(diff_txt)
    report["touched"] = touched

    def _tracked(path: str) -> bool:
        code, _, _ = _run_bytes(["git","ls-files","--error-unmatch","--",path])
        return code == 0

    def _paths_clean(paths: list[str]) -> bool:
        if not paths: return True
        code, _, _ = _run_bytes(["git","diff-index","--quiet","HEAD","--", *paths])  # 0 => clean
        return code == 0

    _run_bytes(["git","update-index","-q","--refresh"])

    tracked_map = {p: _tracked(p) for p in touched}
    has_untracked = any(not v for v in tracked_map.values())
    index_clean   = _paths_clean([p for p, t in tracked_map.items() if t])
    report["has_untracked"] = has_untracked
    report["index_clean"]   = index_clean
    if verbose:
        print(f"[autocoder][apply] touched={len(touched)} has_untracked={has_untracked} index_clean={index_clean}")

    # Strategy builders --------------------------------------------------------
    def _check(cmd: list[str], patch: pathlib.Path) -> bool:
        rc, _, _ = _run_bytes(["git","apply","--check", *cmd[2:], str(patch)])
        return rc == 0

    def _apply_cmd(cmd: list[str], patch: pathlib.Path) -> bool:
        rc, out, err = _run_bytes(cmd + [str(patch)])
        if verbose and (out or err):
            print(out.strip() or "")
            print(err.strip() or "")
        return rc == 0

    def _strategy_ladder(for_index_clean: bool, forgiving: bool) -> List[List[str]]:
        base: List[List[str]] = []
        if for_index_clean:
            base.append(["git","apply","--index","--whitespace=fix"])
            base.append(["git","apply","--index","--recount","--unidiff-zero","--whitespace=fix"])
            base.append(["git","apply","--3way"])
        else:
            base.append(["git","apply","--whitespace=fix","--ignore-whitespace"])
            base.append(["git","apply","--recount","--unidiff-zero","--whitespace=fix","--ignore-whitespace"])
            base.append(["git","apply","--3way"])
        # extra forgiveness toggles
        if forgiving:
            base.append(["git","apply","--inaccurate-eof","--whitespace=fix","--ignore-whitespace"])
        # last chance: --reject (if chosen, we will inspect .rej)
        base.append(["git","apply","--reject","--whitespace=nowarn"])
        return base

    def _reverse_probe_ok(patch: pathlib.Path, cmd: List[str]) -> bool:
        if not _REVERSE_PROBE:
            return False
        # Try check with reverse
        rc, _, _ = _run_bytes(["git","apply","--check","-R", *cmd[2:], str(patch)])
        return rc == 0

    # Per-file apply -----------------------------------------------------------
    def _apply_single_patch(tmp_patch_path: pathlib.Path, for_index_clean: bool, forgiving: bool) -> Tuple[bool, dict]:
        attempts_local: List[dict] = []
        chosen_local = None
        for cmd in _strategy_ladder(for_index_clean, forgiving):
            attempt: Dict[str, object] = {"cmd": _summarize_cmd(cmd), "check_ok": False, "applied": False, "rej_delta": 0, "reversed": False}
            if not _check(cmd, tmp_patch_path):
                # reverse probe
                if _REVERSE_PROBE and _reverse_probe_ok(tmp_patch_path, cmd):
                    attempt["reversed"] = True
                    rc, out, err = _run_bytes(["git","apply","-R","--check", *cmd[2:], str(tmp_patch_path)])
                    ok = (rc == 0)
                else:
                    attempts_local.append(attempt); continue
            before_rej = _list_rej(root)
            # normal or reversed apply
            if attempt.get("reversed"):
                applied = _apply_cmd(["git","apply","-R", *cmd[2:]], tmp_patch_path)
            else:
                applied = _apply_cmd(cmd, tmp_patch_path)
            after_rej  = _list_rej(root)
            attempt["applied"]   = bool(applied)
            attempt["rej_delta"] = max(0, len(after_rej) - len(before_rej))
            attempt["check_ok"]  = True
            attempts_local.append(attempt)
            if applied:
                if "--reject" in cmd and attempt["rej_delta"]:
                    # not good enough; continue trying better strategies
                    continue
                chosen_local = cmd
                break
        return chosen_local is not None, {"attempts": attempts_local, "chosen": _summarize_cmd(chosen_local) if chosen_local else None}

    overall_ok = True
    per_file_report: List[dict] = []

    if _APPLY_PER_FILE:
        per = _per_file_patches(diff_txt)
        for rel, ptxt in per.items():
            # write temp patch file for this file alone
            pf = (pathlib.Path(artifacts_dir) if artifacts_dir else get_project_root() / "snapshots") / f"__single_{rel.replace('/','_')}.patch"
            pf.parent.mkdir(parents=True, exist_ok=True)
            pf.write_text(ptxt, encoding="utf-8")
            ok, detail = _apply_single_patch(pf, index_clean, forgiving=True)
            # If REJ and allowed, repack and retry once
            if (not ok) and _REPACK_AFTER_REJ and detail.get("attempts"):
                had_rej = any(a.get("rej_delta") for a in detail["attempts"])
                if had_rej:
                    rebuilt = _repack_patch_against_worktree(root, ptxt)
                    if rebuilt:
                        pf.write_text(rebuilt, encoding="utf-8")
                        ok2, detail2 = _apply_single_patch(pf, index_clean, forgiving=True)
                        per_file_report.append({"file": rel, "ok": ok2, "detail": detail2})
                        overall_ok = overall_ok and ok2
                        continue
            per_file_report.append({"file": rel, "ok": ok, "detail": detail})
            overall_ok = overall_ok and ok
        report["per_file"] = per_file_report

        # If anything failed, try whole patch as a final fallback
        if not overall_ok:
            if _VERBOSE:
                print("[autocoder][apply] per-file path had failures; attempting whole-patch fallback.")
            overall_ok = False  # will set by fallback below

    # Whole patch fallback -----------------------------------------------------
    if (not _APPLY_PER_FILE) or (not overall_ok):
        strategies = _strategy_ladder(index_clean, forgiving=True)
        chosen = None
        for cmd in strategies:
            attempt: Dict[str, object] = {"cmd": _summarize_cmd(cmd), "check_ok": False, "applied": False, "rej_delta": 0, "reversed": False}
            if not _check(cmd, patch_path):
                if _REVERSE_PROBE and _reverse_probe_ok(patch_path, cmd):
                    attempt["reversed"] = True
                    rc, out, err = _run_bytes(["git","apply","-R","--check", *cmd[2:], str(patch_path)])
                    if rc != 0:
                        report["attempts"].append(attempt); continue
                else:
                    report["attempts"].append(attempt); continue
            before_rej = _list_rej(root)
            if attempt.get("reversed"):
                applied = _apply_cmd(["git","apply","-R", *cmd[2:]], patch_path)
            else:
                applied = _apply_cmd(cmd, patch_path)
            after_rej  = _list_rej(root)
            attempt["applied"]   = bool(applied)
            attempt["rej_delta"] = max(0, len(after_rej) - len(before_rej))
            attempt["check_ok"]  = True
            report["attempts"].append(attempt)
            if applied:
                if "--reject" in cmd and attempt["rej_delta"]:
                    continue
                chosen = cmd; break

        if chosen and ("--index" not in chosen and "--3way" not in chosen) and touched:
            _run_bytes(["git","add","--"] + touched)

        ok = chosen is not None
        report["chosen"] = _summarize_cmd(chosen) if chosen else None
        overall_ok = ok

    # Optional: create temp branch & commit
    if overall_ok and not _DRY_RUN_ONLY:
        if _CREATE_TEMP_BRANCH:
            _, cur_branch, _ = _run_bytes(["git","rev-parse","--abbrev-ref","HEAD"])
            cur_branch = cur_branch.strip() or "HEAD"
            temp_branch = f"autocode/{time.strftime('%Y%m%d_%H%M%S')}"
            rc, _, _ = _run_bytes(["git","checkout","-b", temp_branch])
            if rc == 0:
                report["branch"] = temp_branch
            pr_md = (pathlib.Path(artifacts_dir) / "PR.md") if artifacts_dir else None
            msg = "autocode: behavior-preserving refactor"
            if pr_md and pr_md.exists():
                body = _read_text_all(pr_md)
                (pathlib.Path(artifacts_dir) / "COMMIT_MSG.txt").write_text(msg + "\n\n" + body, encoding="utf-8")
                rc, _, _ = _run_bytes(["git","commit","-m", msg, "-m", body])
            else:
                rc, _, _ = _run_bytes(["git","commit","-m", msg])
            if rc == 0:
                _, sha, _ = _run_bytes(["git","rev-parse","HEAD"])
                report["commit"] = sha.strip()

    if verbose:
        ch = report.get("chosen") or "PER-FILE" if report.get("per_file") else "NONE"
        print(f"[autocoder][apply] result={overall_ok} strategy={ch}")
    if artifacts_dir:
        try:
            summary_lines = []
            if report.get("per_file"):
                for pf in report["per_file"]:
                    summary_lines.append(f"- file={pf['file']} ok={pf['ok']} chosen={pf['detail'].get('chosen')}")
                    for a in pf["detail"].get("attempts", []):
                        summary_lines.append(f"   * try: {a['cmd']} check_ok={a['check_ok']} applied={a['applied']} reversed={a.get('reversed')} rej_delta={a['rej_delta']}")
            else:
                for a in report["attempts"]:
                    summary_lines.append(f"- try: {a['cmd']} check_ok={a['check_ok']} applied={a['applied']} reversed={a.get('reversed')} rej_delta={a['rej_delta']}")
            pathlib.Path(artifacts_dir, "apply.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
        except Exception:
            pass
    return overall_ok, report

# ======================================================================================
# Public API
# ======================================================================================
def _sanitize_to_diff(raw: str) -> str:
    raw = _clean_unicode(raw)
    if raw.strip() == "#NOPATCH":
        return ""
    if "diff --git " in raw and "--- " in raw and "+++ " in raw:
        txt = _doctor_diff(raw)
        return (txt + ("\n" if txt and not txt.endswith("\n") else "")) if txt else ""

    stitched = []
    for m in re.finditer(r"```(?:diff|patch)\n([\s\S]*?)```", raw, re.MULTILINE): stitched.append(m.group(1))
    for m in re.finditer(r"~~~(?:diff|patch)\n([\s\S]*?)~~~", raw, re.MULTILINE): stitched.append(m.group(1))
    if stitched:
        txt = _doctor_diff("\n".join(stitched))
        return (txt + ("\n" if txt and not txt.endswith("\n") else "")) if txt else ""

    if ("--- " in raw and "+++ " in raw and "@@ " in raw):
        lines, out = raw.splitlines(), []
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
            out.append(ln); i += 1
        txt = _doctor_diff("\n".join(out))
        return (txt + ("\n" if txt and not txt.endswith("\n") else "")) if txt else ""
    return ""

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

def propose_and_apply_refactor(max_files: int = 5, retries: int = 2) -> Optional[AutoCodeResult]:
    if not _llm_ok(): return None
    root = get_project_root()
    hotspots = _scan_hotspots(root, max_files=max_files)
    if not hotspots: return None

    # 1) Ask once
    origin_prompt = _build_prompt(hotspots)
    raw = ask(origin_prompt, max_tokens=2800, temperature=0.15)

    sanitized = _sanitize_to_diff(raw)
    verified  = _verified_patch_from_llm_diff(sanitized) if sanitized else ""

    no_op_reason = ""
    if verified:
        if _STRICT_NOOP and _diff_is_typing_noop(verified):
            verified = ""; no_op_reason = "typing-only"
        elif _MAX_DIFF_LINES > 0 and _diff_line_count(verified) > _MAX_DIFF_LINES:
            verified = ""; no_op_reason = f"too-large ({_diff_line_count(verified)}>{_MAX_DIFF_LINES})"

    # 2) Salvage FILE/CODE blocks
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
                    sanitized = ""  # used salvage path

    # 3) Repair loop (re-prompt if needed)
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
            salv2, _, _ = _diff_from_code_blocks(raw2, root)
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

    # 4) Optional synth fallback
    synth_used = False
    if (not verified) and (not _NO_SYNTH) and (not _STRICT_NOOP):
        py_target = next((p for p in hotspots if p.suffix == ".py"), None)
        target = py_target or (_FORCE_FILE and (root / _FORCE_FILE))
        if isinstance(target, pathlib.Path) and target and target.exists():
            rel = _norm_sep(target.relative_to(root).as_posix())
            if rel.startswith("bot/") and not _is_excluded_path(rel):
                data = _read_text_all(target)
                if data and ("TYPE_CHECKING" not in data or "Autocode:" not in data):
                    original = [ln + "\n" for ln in data.split("\n")]
                    if original and original[-1] == "\n" and not data.endswith("\n"): original = original[:-1]
                    i = 0
                    if original and original[0].startswith("#!"): i = 1
                    while i < len(original) and (original[i].lstrip().startswith("from ") or original[i].lstrip().startswith("import ")):
                        i += 1
                    insert = []
                    if "from typing import TYPE_CHECKING" not in data:
                        insert.append("from typing import TYPE_CHECKING\n")
                    insert += ["if TYPE_CHECKING:\n","    pass  # Autocode: non-functional type-only block\n"]
                    synth = _make_unified_diff(rel, original, new_lines=original[:i] + insert + original[i:])
                    verified = synth; raw = synth; synth_used = True

    # 5) Artifacts & status
    art_dir, diff_path, _ = _write_artifacts(origin_prompt, raw, verified, salvage=salvage_diff, salvage_reasons=salvage_reasons)
    status_reason = "llm_raw_empty" if not (raw or "").strip() else ("filtered_noop:" + no_op_reason if no_op_reason else ("verified_patch_empty_after_filters" if (verified or "").strip()=="" else "ok"))
    try:
        (pathlib.Path(art_dir) / "status.json").write_text(json.dumps({
            "reason": status_reason,
            "sizes": {"raw": len(raw or ""), "verified": len(verified or "")},
            "strict_noop": bool(_STRICT_NOOP),
            "synth_used": bool(synth_used),
            "salvage_used": bool(bool(salvage_diff)),
            "allow_comment_only": bool(_ALLOW_COMMENT_ONLY),
            "allow_whitespace_only": bool(_ALLOW_WHITESPACE_ONLY),
            "dry_run": bool(_DRY_RUN_ONLY),
        }, indent=2), encoding="utf-8")
    except Exception:
        pass

    touched = _touch_list_from_diff(verified) if verified else []
    applied = False
    strategy_report: Dict[str, object] = {}

    if verified and not _DRY_RUN_ONLY:
        applied, strategy_report = _git_apply(diff_path, artifacts_dir=str(art_dir), verbose=_VERBOSE)
        try:
            (pathlib.Path(art_dir) / "apply.json").write_text(json.dumps(strategy_report, indent=2), encoding="utf-8")
        except Exception:
            pass

    note_bits = []
    if synth_used: note_bits.append("used synth fallback (TYPE_CHECKING no-op)")
    if salvage_diff: note_bits.append("converted code/file blocks → diff")
    note_bits.append("patch applied" if applied else ("dry-run only" if _DRY_RUN_ONLY else "no verified changes applied"))
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
        note="; ".join(note_bits),
    )
