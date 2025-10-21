from __future__ import annotations
import time, threading, pathlib, psutil, os

_BYTES = 1024 * 1024

def _dir_size_bytes(path: pathlib.Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += (pathlib.Path(root) / f).stat().st_size
            except Exception:
                pass
    return total

class BudgetEnforcer:
    """Wall-time, RAM, and snapshot-disk usage watcher. Soft-fail: records violations."""
    def __init__(self, limits: dict, snapshot_dir: pathlib.Path):
        self.max_wall = int(limits.get("wall_time_sec_max", 0)) or None
        self.max_ram  = int(limits.get("ram_mb_max", 0)) * _BYTES or None
        self.max_disk = int(limits.get("disk_mb_max", 0)) * _BYTES or None
        self.snap_dir = snapshot_dir
        self._proc = psutil.Process()
        self._start = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.violations: list[dict] = []

    def __enter__(self):
        self._start = time.time()
        self._thread = threading.Thread(target=self._watchdog, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _watchdog(self):
        while not self._stop.is_set():
            now = time.time()
            if self.max_wall and self._start and (now - self._start) > self.max_wall:
                self.violations.append({"type": "wall_time", "limit_sec": self.max_wall})
                self._stop.set()
                break

            try:
                rss = self._proc.memory_info().rss
                if self.max_ram and rss > self.max_ram:
                    self.violations.append({"type": "ram", "rss_bytes": rss, "limit_bytes": self.max_ram})
            except Exception:
                pass

            try:
                used = _dir_size_bytes(self.snap_dir)
                if self.max_disk and used > self.max_disk:
                    self.violations.append({"type": "disk", "dir_bytes": used, "limit_bytes": self.max_disk})
            except Exception:
                pass

            self._stop.wait(0.5)  # ~2 Hz

    def exceeded(self) -> bool:
        return len(self.violations) > 0

    def summary(self) -> dict:
        return {
            "limits": {
                "wall_time_sec_max": self.max_wall,
                "ram_mb_max": (self.max_ram // _BYTES) if self.max_ram else None,
                "disk_mb_max": (self.max_disk // _BYTES) if self.max_disk else None,
            },
            "violations": self.violations,
            "elapsed_sec": None if self._start is None else (time.time() - self._start),
        }
