from __future__ import annotations
import os, shutil, subprocess, pathlib, json, platform
from typing import Iterable, Dict, Any
from bot.rag.utils import sha256_bytes
from bot.paths import get_project_root

def _which(cmd: str) -> str | None:
    return shutil.which(cmd)

def _candidates_windows() -> list[pathlib.Path]:
    # Common spots if installed via package managers/tools
    return [
        pathlib.Path(r"C:\ProgramData\chocolatey\bin\xdelta3.exe"),
        pathlib.Path(r"C:\msys64\usr\bin\xdelta3.exe"),
        pathlib.Path(r"C:\msys64\mingw64\bin\xdelta3.exe"),
        pathlib.Path(r"C:\Program Files\xdelta3\xdelta3.exe"),
    ]

def ensure_xdelta3() -> str:
    """
    Locate an xdelta3 executable. Supports:
      - EVERABOT_XDELTA3=<absolute path to exe>
      - PATH (xdelta3 / xdelta3.exe)
      - repo-local fallbacks (snapshots/bin, tools/bin)
      - common Windows install locations
    """
    # 0) explicit override
    override = os.getenv("EVERABOT_XDELTA3", "").strip()
    if override:
        p = pathlib.Path(override)
        if p.exists():
            return str(p)

    # 1) PATH
    for name in ("xdelta3", "xdelta3.exe"):
        exe = _which(name)
        if exe:
            return exe

    # 2) repo-local fallbacks
    root = get_project_root()
    local_bins = [
        root / "snapshots" / "bin" / "xdelta3",
        root / "snapshots" / "bin" / "xdelta3.exe",
        root / "tools" / "bin" / "xdelta3",
        root / "tools" / "bin" / "xdelta3.exe",
    ]
    for p in local_bins:
        if p.exists():
            return str(p)

    # 3) Windows common locations
    if os.name == "nt":
        for p in _candidates_windows():
            if p.exists():
                return str(p)

    # 4) no luck
    raise RuntimeError(
        "xdelta3 not found.\n\n"
        "How to install:\n"
        "  • Linux (Debian/Ubuntu):   sudo apt-get update && sudo apt-get install xdelta3\n"
        "  • macOS (Homebrew):        brew install xdelta\n"
        "  • Windows (MSYS2):         install MSYS2, then run: pacman -S xdelta3\n"
        "                              (ensure C:\\msys64\\usr\\bin is on your PATH)\n"
        "  • Or point to a binary via env var EVERABOT_XDELTA3=<full\\path\\to\\xdelta3.exe>\n"
        "\n"
        "After installing, re-run your command."
    )

def sha256_file(p: pathlib.Path) -> str:
    return sha256_bytes(p.read_bytes())

def make_delta(base: pathlib.Path, target: pathlib.Path, delta_out: pathlib.Path) -> None:
    exe = ensure_xdelta3()
    delta_out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([exe, "-e", "-s", str(base), str(target), str(delta_out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"xdelta3 encode failed for {target.name}: {r.stderr or r.stdout}")

def apply_delta(base: pathlib.Path, delta_file: pathlib.Path, out_path: pathlib.Path) -> None:
    exe = ensure_xdelta3()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([exe, "-d", "-s", str(base), str(delta_file), str(out_path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"xdelta3 decode failed for {delta_file.name}: {r.stderr or r.stdout}")

def roundtrip_manifest(base_dir: pathlib.Path, tgt_dir: pathlib.Path,
                       files: Iterable[str], delta_dir: pathlib.Path) -> Dict[str, Any]:
    base_dir = base_dir.resolve(); tgt_dir = tgt_dir.resolve()
    delta_dir = delta_dir.resolve(); recon_dir = (delta_dir / "recon")
    recon_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    for rel in files:
        b = base_dir / rel
        t = tgt_dir / rel
        d = delta_dir / f"{rel}.vcdiff"
        r = recon_dir / rel

        if not b.exists() or not t.exists():
            raise FileNotFoundError(f"Missing base/target file for delta: {rel}")

        make_delta(b, t, d)
        apply_delta(b, d, r)

        entry = {
            "file": rel,
            "base_sha256": sha256_file(b),
            "target_sha256": sha256_file(t),
            "recon_sha256": sha256_file(r),
            "delta_bytes": d.stat().st_size,
            "equal": sha256_file(t) == sha256_file(r),
            "delta_path": str(d),
        }
        entries.append(entry)

    proof = {
        "algorithm": "xdelta3 (RFC 3284 VCDIFF)",
        "base": str(base_dir),
        "target": str(tgt_dir),
        "delta_dir": str(delta_dir),
        "entries": entries,
        "all_equal": all(e["equal"] for e in entries),
    }
    (delta_dir / "PROOF.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    return proof
