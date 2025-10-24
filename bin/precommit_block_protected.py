import subprocess
import sys
from pathlib import Path

# match the runtime guard
PROTECTED = ("ruleset/", "test/")

def staged_paths():
    out = subprocess.check_output(["git", "diff", "--cached", "--name-only"], text=True)
    return [p.strip() for p in out.splitlines() if p.strip()]

def main():
    repo_root = Path(__file__).resolve().parents[1]
    blocked = []
    for p in staged_paths():
        # normalize to forward slashes for cross-platform checks
        ps = p.replace("\\", "/")
        if any(ps.startswith(pref) for pref in PROTECTED):
            blocked.append(ps)

    if blocked:
        print("❌ Commit blocked: protected paths are read-only in VCS.\n")
        for b in blocked:
            print(f"  - {b}")
        print("\nIf you intentionally need to change tests or rules, use a separate, explicit governance PR and"
              " remove this hook locally (not recommended).")
        sys.exit(1)

    sys.exit(0)

if __name__ == "__main__":
    main()
