from __future__ import annotations
import os, pathlib

def get_project_root() -> pathlib.Path:
    """
    Resolve the Everabot repo root robustly:
      1) $EVERABOT_ROOT if set
      2) Walk up from CWD until we find pyproject.toml + ruleset + test
      3) Fallback to CWD
    Works whether the package runs from site-packages (poetry install) or from the repo.
    """
    env = os.getenv("EVERABOT_ROOT")
    if env:
        return pathlib.Path(env).expanduser().resolve()

    cur = pathlib.Path.cwd().resolve()
    sentinels = ("pyproject.toml", "ruleset", "test")
    for p in (cur, *cur.parents):
        if (p / "pyproject.toml").exists() and (p / "ruleset").exists() and (p / "test").exists():
            return p
    return cur
