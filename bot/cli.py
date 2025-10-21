from __future__ import annotations
import typer, sys, pathlib, json
from rich import print
from bot.security.guard import install_guard

app = typer.Typer(help="Everabot CLI")

# Install FS guard early
install_guard(deny_dirs={"ruleset", "test"})

@app.command()
def eval():
    """
    Run baseline root tests (stubs for now).
    """
    from subprocess import run
    r = run([sys.executable, "test/run_root_tests.py"])
    raise typer.Exit(code=r.returncode)

@app.command()
def selfcheck():
    """
    Verify write-protection (expect PermissionError when touching ruleset/test).
    """
    base = pathlib.Path(".")
    tests = [base/"ruleset"/"__probe.txt", base/"test"/"__probe.txt"]
    ok = True
    for p in tests:
        try:
            p.write_text("should fail")
            print(f"[red]ERROR:[/red] write unexpectedly succeeded: {p}")
            ok = False
        except Exception as e:
            print(f"[green]OK[/green] blocked write to {p} -> {type(e).__name__}")
    raise typer.Exit(code=0 if ok else 1)

@app.command()
def learn(topic: str):
    """
    Stub: RAG research → distill → practice → eval → decide (to be implemented).
    """
    print(f"[cyan]learn[/cyan] → {topic}")
    # TODO: implement research, distillation, practice harnesses
    print("[yellow]Not implemented yet.[/yellow]")

@app.command()
def self_improve():
    """
    Stub: profile & propose hotspot rewrites (py-spy/pybind11 etc.).
    """
    print("[cyan]self-improve[/cyan] starting (stub)")
    # TODO: integrate py-spy and generate flamegraph artifacts
    # py-spy ref: https://github.com/benfred/py-spy
    print("[yellow]Not implemented yet.[/yellow]")

@app.command()
def snapshot(action: str = typer.Argument("list", help="list|promote|checkout|diff")):
    """
    Stub: snapshot manager.
    """
    print(f"[cyan]snapshot[/cyan] {action} (stub)")

if __name__ == "__main__":
    app()
