import os

def selected_accel() -> str | None:
    v = os.environ.get("EVERABOT_CHUNK_ACCEL", "").strip().lower()
    return v if v in ("cpp", "rust", "go") else None

def require_equiv() -> bool:
    return os.environ.get("EVERABOT_CHUNK_REQUIRE_EQUIV", "1") == "1"
