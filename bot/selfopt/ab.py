from __future__ import annotations
import time, psutil, contextlib

@contextlib.contextmanager
def mem_tracker():
    import os
    p = psutil.Process(os.getpid())
    before = p.memory_info().rss
    t0 = time.perf_counter()
    yield
    dt = time.perf_counter() - t0
    after = p.memory_info().rss
    print({"rss_delta_bytes": int(after - before), "elapsed_sec": dt})
