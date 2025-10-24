from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
import os, sys, pathlib, subprocess, time, json

from bot.paths import get_project_root
from bot.security.guard import GuardDenied  # will be raised by the guard if we touch deny-listed dirs
from bot.llm.provider import ask, info, NotConfigured


@dataclass
class AutoCodeResult:
    title: str
    patch_path: str
    patch_applied: bool
    touched_paths: List[str]


def _llm_ok() -> bool:
    try:
        _ = info()  # raises NotConfigured if missing
        return True
    except NotConfigured:
        return False


def _scan_hotspots(root: pathlib.Path, max_files: int = 3) -> List[pathlib.Path]:
    """
    Ultra-light heuristic: prefer longer files with TODO markers.
    """
    bot_dir = root / "bot"
    files: list[tuple[int, int, pathlib.Path]] = []
    for p in bot_dir.rglob("*.py"):
        # skip guard-sensitive or noisy dirs
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


def _write_patch(diff_text: str) -> pathlib.Path:
    out_dir = get_project_root() / "snapshots" / "selfopt" / "patches"
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"autocode_{int(time.time())}.diff"
    p.write_text(diff_text, encoding="utf-8")
    return p


def _git_apply(patch_path: pathlib.Path) -> bool:
    try:
        subprocess.check_call(["git", "apply", "--index", "--whitespace=fix", str(patch_path)])
        return True
    except Exception:
        return False


def _touch_list_from_diff(diff_text: str) -> List[str]:
    touched = []
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            # '+++ b/bot/xxx.py' or '+++ bot/xxx.py'
            path = line.split(" ", 1)[1].strip()
            if path.startswith("b/"):
                path = path[2:]
            touched.append(path)
    return [t for t in touched if t and not t.startswith("/dev/null")]


def propose_and_apply_refactor(max_files: int = 3) -> Optional[AutoCodeResult]:
    if not _llm_ok():
        return None

    root = get_project_root()
    hotspots = _scan_hotspots(root, max_files=max_files)
    if not hotspots:
        return None

    prompt = _build_prompt(hotspots)

    # Ask the LLM for a unified diff (ONLY the diff)
    diff = ask(prompt, max_tokens=2500, temperature=0.2)

    # Basic sanity check: must look like a unified diff
    if ("+++ " not in diff) or ("--- " not in diff) or ("@@ " not in diff):
        # model didn't follow format; skip
        return None

    patch_path = _write_patch(diff)
    touched = _touch_list_from_diff(diff)

    # Let the project guard raise if we try to touch deny-listed paths
    for rel in touched:
        abs_p = (root / rel).resolve()
        # if the guard is installed, touching deny paths will be blocked at write-time
        _ = abs_p  # access triggers no write here; write happens in git apply

    applied = _git_apply(patch_path)
    if not applied:
        return AutoCodeResult(
            title="autocode-refactor",
            patch_path=str(patch_path),
            patch_applied=False,
            touched_paths=touched,
        )

    return AutoCodeResult(
        title="autocode-refactor",
        patch_path=str(patch_path),
        patch_applied=True,
        touched_paths=touched,
    )
