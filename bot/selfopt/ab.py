from __future__ import annotations
import time, tracemalloc, psutil, random
from dataclasses import dataclass

@dataclass
class ABResult:
    equivalent: bool
    python_sec: float
    accel_sec: float
    accel_impl: str
    speedup_x: float
    python_mem: dict
    accel_mem: dict

def _time_and_mem(fn):
    proc = psutil.Process()
    rss0 = proc.memory_info().rss
    tracemalloc.start()
    t0 = time.perf_counter()
    out = fn()
    t1 = time.perf_counter()
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss1 = proc.memory_info().rss
    return out, (t1 - t0), {"rss_delta": int(rss1 - rss0), "py_peak_bytes": int(peak)}

def ab_chunk(store: str | None = None, n: int = 5000, chunk_tokens: int = 400, overlap_tokens: int = 60) -> ABResult:
    # Python spec
    from bot.rag.chunk_spec import chunk_text_equiv as py_spec

    texts = []
    if store:
        import pyarrow.parquet as pq, pathlib
        sdir = pathlib.Path("snapshots/rag") / store
        tab = pq.read_table(sdir / "chunks.parquet")
        pool = [tab["text"][i].as_py() for i in range(tab.num_rows)]
        random.shuffle(pool)
        texts = pool[:min(len(pool), n)]
    else:
        base = "RAG speeds up retrieval by chunking documents into overlapping windows. "
        texts = [base * 20 for _ in range(n)]

    def run_py():
        return [py_spec("doc", t, chunk_tokens, overlap_tokens) for t in texts]
    py_out, py_sec, py_mem = _time_and_mem(run_py)

    accel_impl = "python"
    out_accel, accel_sec, accel_mem = py_out, py_sec, py_mem

    # Try C++ → Rust → Go → else Python
    try:
        import everabot_chunk_cpp as cpp
        def run_cpp():
            return [cpp.chunk_text_equiv("doc", t, chunk_tokens, overlap_tokens) for t in texts]
        out_accel, accel_sec, accel_mem = _time_and_mem(run_cpp)
        accel_impl = "cpp"
    except Exception:
        pass

    if out_accel is py_out:
        try:
            import everabot_chunk_rust as r
            def run_rust():
                return [r.chunk_text_equiv("doc", t, chunk_tokens, overlap_tokens) for t in texts]
            out_accel, accel_sec, accel_mem = _time_and_mem(run_rust)
            accel_impl = "rust"
        except Exception:
            pass

    if out_accel is py_out:
        try:
            from bot.accel.go.go_client import chunk_text_equiv as go_chunk
            def run_go():
                return [go_chunk("doc", t, chunk_tokens, overlap_tokens) for t in texts]
            out_accel, accel_sec, accel_mem = _time_and_mem(run_go)
            accel_impl = "go"
        except Exception:
            pass

    equivalent = (py_out == out_accel)
    sp = (py_sec / accel_sec) if accel_sec > 0 else 1.0
    return ABResult(
        equivalent=bool(equivalent),
        python_sec=py_sec,
        accel_sec=accel_sec,
        accel_impl=accel_impl,
        speedup_x=sp,
        python_mem=py_mem,
        accel_mem=accel_mem,
    )
