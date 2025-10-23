from __future__ import annotations
import os, sys, subprocess, time, pathlib, textwrap
from typing import Dict, Any, Optional


def _resolve_pyspy(exe_override: Optional[str] = None) -> str:
    """
    Pick a py-spy binary, preferring:
      1) explicit exe_override
      2) $PYSPY_EXE
      3) 'py-spy' (or 'py-spy.exe' on Windows)
    """
    exe = exe_override or os.environ.get("PYSPY_EXE") or "py-spy"
    if os.name == "nt" and not exe.lower().endswith(".exe"):
        exe += ".exe"
    return exe


def _out_svg_path(out_dir: pathlib.Path, label: str) -> pathlib.Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in (label or "profile"))[:40]
    return out_dir / f"{ts}_{safe}.svg"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    # Keep text capture for nice error surfaces
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False
    )


def _write_runner(out_dir: pathlib.Path, argv: list[str]) -> pathlib.Path:
    """
    Create a tiny Python script that runs your CLI *in process*.
    This avoids spawning another Python child that py-spy wouldn't profile.
    Expects argv like: [<py>, "-m", "bot.cli", <mode>, <arg>]
    """
    ts = int(time.time())
    runner = out_dir / f"_pyspy_runner_{ts}.py"

    # Best-effort parse for '-m <module>' style; fallback to run_path
    if len(argv) >= 3 and argv[1] == "-m":
        module = argv[2]
        passthru = argv[3:]
        code = f"""
        import sys, runpy
        sys.argv = { [module, *passthru]!r }
        runpy.run_module({module!r}, run_name="__main__")
        """
    else:
        script = argv[1] if len(argv) >= 2 else ""
        passthru = argv[2:]
        code = f"""
        import sys, runpy
        sys.argv = { [script, *passthru]!r }
        runpy.run_path({script!r}, run_name="__main__")
        """

    runner.write_text(textwrap.dedent(code).strip() + "\n", encoding="utf-8")
    return runner


def record_pid(
    pid: int,
    out_dir: pathlib.Path,
    label: str,
    duration_sec: int = 60,
    rate_hz: int = 100,
    pyspy_exe: Optional[str] = None,
) -> pathlib.Path:
    """
    Attach by PID. Often flaky on Windows/Conda; prefer record_exec() there.
    """
    svg = _out_svg_path(out_dir, label)
    cmd = [
        _resolve_pyspy(pyspy_exe),
        "record",
        "-r", str(rate_hz),
        "-d", str(duration_sec),
        "-f", "flamegraph",
        "-o", str(svg),
        "--pid", str(pid),
    ]
    r = _run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"py-spy failed: {r.stderr or r.stdout}")
    return svg


def record_child(
    argv: list[str],
    out_dir: pathlib.Path,
    label: str,
    duration_sec: int = 60,
    rate_hz: int = 100,
    pyspy_exe: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Launch child and attach by PID (nicer on Linux/macOS).
    For Windows, record_exec() is almost always more reliable.
    """
    proc = subprocess.Popen(argv, stdout=sys.stdout, stderr=sys.stderr, shell=False)
    try:
        svg = record_pid(proc.pid, out_dir, label, duration_sec, rate_hz, pyspy_exe=pyspy_exe)
        return {"pid": proc.pid, "flamegraph": str(svg)}
    finally:
        proc.wait()


def record_exec(
    argv: list[str],
    out_dir: pathlib.Path,
    label: str,
    duration_sec: int = 60,
    rate_hz: int = 100,
    pyspy_exe: Optional[str] = None,
    pyspy_python: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run the command *under* py-spy (`py-spy record -- <cmd>`).
    On Windows, if the first attempt fails, retry via a tiny in-process runner
    launched by an explicit python.org interpreter (pyspy_python).
    """
    svg = _out_svg_path(out_dir, label)
    base = [
        _resolve_pyspy(pyspy_exe),
        "record",
        "-r", str(rate_hz),
        "-d", str(duration_sec),
        "-f", "flamegraph",
        "-o", str(svg),
        "--",
        *argv,
    ]
    r = _run(base)
    if r.returncode == 0:
        return {"flamegraph": str(svg), "mode": "exec"}

    # Windows-specific, common Conda failure path: try a clean interpreter + runner
    if os.name == "nt":
        runner = _write_runner(out_dir, argv)
        launcher = (
            pyspy_python
            or os.environ.get("PYSPY_PYTHON")
            or sys.executable  # last resort
        )
        alt = [
            _resolve_pyspy(pyspy_exe),
            "record",
            "-r", str(rate_hz),
            "-d", str(duration_sec),
            "-f", "flamegraph",
            "-o", str(svg),
            "--",
            launcher,
            str(runner),
        ]
        r2 = _run(alt)
        if r2.returncode == 0:
            return {"flamegraph": str(svg), "mode": "exec(wrapper)"}

        raise RuntimeError(
            "py-spy failed on Windows.\n"
            f"first attempt: {r.stderr or r.stdout}\n\n"
            f"wrapper retry: {r2.stderr or r2.stdout}"
        )

    # Non-Windows: just surface the error
    raise RuntimeError(f"py-spy failed: {r.stderr or r.stdout}")
