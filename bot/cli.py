from __future__ import annotations
import typer, sys, pathlib, subprocess
from rich import print
from bot.security.guard import install_guard

app = typer.Typer(help="Everabot CLI")

# Compute project root from this file location:
# .../Everabot/bot/cli.py  → root = parents[1]
_THIS = pathlib.Path(__file__).resolve()
_ROOT = _THIS.parents[1]
_PROTECTED_DIRS = {str(_ROOT / "ruleset"), str(_ROOT / "test")}

# Install FS guard early using absolute roots
install_guard(deny_dirs=_PROTECTED_DIRS)

@app.command()
def eval():
    """Run baseline root tests (stubs)."""
    tests_py = _ROOT / "test" / "run_root_tests.py"
    if not tests_py.exists():
        print(f"[red]Missing root tests at[/red] {tests_py}")
        raise typer.Exit(code=2)
    r = subprocess.run([sys.executable, str(tests_py)])
    raise typer.Exit(code=r.returncode)

@app.command()
def selfcheck():
    """Verify write-protection (expect PermissionError on ruleset/test)."""
    ok = True
    for rel in ("ruleset/__probe.txt", "test/__probe.txt"):
        p = _ROOT / rel
        try:
            p.write_text("should fail")
            print(f"[red]ERROR:[/red] write unexpectedly succeeded: {p}")
            ok = False
        except Exception as e:
            print(f"[green]OK[/green] blocked write to {p} -> {type(e).__name__}")
    raise typer.Exit(code=0 if ok else 1)

@app.command()
def learn(topic: str):
    print(f"[cyan]learn[/cyan] → {topic}")
    print("[yellow]Not implemented yet.[/yellow]")

@app.command()
def self_improve():
    print("[cyan]self-improve[/cyan] starting (stub)")
    print("[yellow]Not implemented yet.[/yellow]")

@app.command()
def snapshot(action: str = typer.Argument("list", help="list|promote|checkout|diff")):
    print(f"[cyan]snapshot[/cyan] {action} (stub)")

if __name__ == "__main__":
    app()
