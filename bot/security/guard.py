from __future__ import annotations
import builtins, io, os, pathlib, functools, sys

# Keep originals so we can delegate
_ORIG_OPEN = builtins.open
_ORIG_IO_OPEN = io.open
_ORIG_OS_OPEN = os.open
_ORIG_PATH_OPEN = pathlib.Path.open

_PROTECTED: list[str] = []  # normalized absolute roots (lowercased on Windows)

def _norm(p: str | os.PathLike) -> str:
    # absolute, normalized; lower for case-insensitive filesystems
    s = str(pathlib.Path(p).expanduser().resolve(strict=False))
    return s.lower() if os.name == "nt" else s

def _under_protected(target: str | os.PathLike) -> bool:
    t = _norm(target)
    for root in _PROTECTED:
        # allow exact match or any child path
        if t == root or t.startswith(root + os.sep):
            return True
    return False

def _mode_is_write(mode: str) -> bool:
    # any write/append/update modes
    return any(m in mode for m in ("w", "a", "+", "x"))

def _guard_pathlike(path) -> None:
    # path can be int fd, str, or path-like
    if isinstance(path, int):
        return  # we can’t resolve FDs here (handled by os.open wrapper)
    if _under_protected(path):
        raise PermissionError(f"Writes are denied in protected dirs: {path}")

def _patched_open(file, mode="r", *args, **kwargs):
    if _mode_is_write(mode):
        _guard_pathlike(file)
    return _ORIG_OPEN(file, mode, *args, **kwargs)

def _patched_io_open(file, mode="r", *args, **kwargs):
    if _mode_is_write(mode):
        _guard_pathlike(file)
    return _ORIG_IO_OPEN(file, mode, *args, **kwargs)

def _patched_os_open(path, flags, *args, **kwargs):
    # block low-level create/write flags too
    # O_WRONLY=1, O_RDWR=2, O_APPEND=1024, O_CREAT=64 on POSIX; Windows differs but flags>0 means intent to modify.
    if flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_TRUNC", 0)):
        _guard_pathlike(path)
    return _ORIG_OS_OPEN(path, flags, *args, **kwargs)

def _patched_path_open(self: pathlib.Path, mode="r", *args, **kwargs):
    if _mode_is_write(mode):
        _guard_pathlike(self)
    return _ORIG_PATH_OPEN(self, mode, *args, **kwargs)

def install_guard(deny_dirs: set[str] | set[os.PathLike]):
    """Install FS write guard for protected roots (absolute or relative)."""
    global _PROTECTED
    # Normalize to absolute roots
    roots = []
    for d in deny_dirs:
        p = pathlib.Path(d)
        if not p.is_absolute():
            p = (pathlib.Path.cwd() / p)
        roots.append(_norm(p))
    _PROTECTED = sorted(set(roots), key=len)  # short-to-long is fine here

    # Monkey-patch multiple fronts
    builtins.open = _patched_open  # type: ignore[assignment]
    io.open = _patched_io_open     # type: ignore[assignment]
    os.open = _patched_os_open     # type: ignore[assignment]
    pathlib.Path.open = _patched_path_open  # type: ignore[assignment]
