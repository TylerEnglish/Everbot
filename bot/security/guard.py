from __future__ import annotations
import builtins, io, os, pathlib, functools

_DENY_DIRS: set[str] = set()
_ORIG_OPEN = builtins.open
_ORIG_REMOVE = os.remove
_ORIG_RENAME = os.rename

def _is_denied(path: str | os.PathLike) -> bool:
    p = pathlib.Path(path).resolve()
    parts = {part for part in p.parts}
    return any(d in parts for d in _DENY_DIRS)

def _patched_open(file, mode="r", *args, **kwargs):
    # any write/append/update touching a denied dir → block
    if any(ch in mode for ch in ("w", "a", "+")) and _is_denied(file):
        raise PermissionError(f"Writes are denied in protected dirs: {file}")
    return _ORIG_OPEN(file, mode, *args, **kwargs)

def _wrap_modify_fn(fn):
    @functools.wraps(fn)
    def _inner(path, *a, **kw):
        if _is_denied(path):
            raise PermissionError(f"Modifications denied in protected dirs: {path}")
        return fn(path, *a, **kw)
    return _inner

def install_guard(deny_dirs: set[str] | None = None):
    global _DENY_DIRS
    if deny_dirs:
        # store dir basenames only; path check resolves absolute parts
        _DENY_DIRS = {d.strip("/").strip() for d in deny_dirs}
    builtins.open = _patched_open  # type: ignore
    os.remove = _wrap_modify_fn(_ORIG_REMOVE)  # type: ignore
    os.rename = _wrap_modify_fn(_ORIG_RENAME)  # type: ignore
