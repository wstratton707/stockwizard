"""
release_check.py — QuantWizard pre-push validation gate.

Run with the project's virtualenv interpreter from the repo root:
    .venv/Scripts/python.exe .claude/skills/release-check/scripts/release_check.py   (Windows)
    .venv/bin/python .claude/skills/release-check/scripts/release_check.py            (macOS/Linux)

Checks (static + import only — no network, no app launch):
  1. Syntax-check every changed .py file (git diff: staged + unstaged + untracked).
  2. Import the core non-Streamlit modules to catch import-time failures.
  3. Summarize git state (uncommitted files, ahead/behind origin).

Exit code 0 = all clear; 1 = something failed (do not push).
"""
import ast
import importlib
import subprocess
import sys
from pathlib import Path

# Windows consoles default to cp1252 and choke on box-drawing / ✓ glyphs.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Resolve repo root from this file's location (.claude/skills/release-check/scripts/)
REPO = Path(__file__).resolve().parents[4]

# Core modules that must import cleanly (exclude app.py / payments.py — they call
# Streamlit at import time and need a running ScriptRunContext).
CORE_MODULES = [
    "constants", "data", "analysis", "portfolio_data", "portfolio_analysis",
    "cached_fetchers", "excel_builder", "pptx_builder",
    "stress_test", "live_data", "disclaimers", "news_research",
]

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def _run(*args):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True)


def changed_py_files():
    out = set()
    for cmd in (["git", "diff", "--name-only"],
                ["git", "diff", "--name-only", "--cached"],
                ["git", "ls-files", "--others", "--exclude-standard"]):
        r = _run(*cmd)
        for line in r.stdout.splitlines():
            if line.strip().endswith(".py"):
                out.add(line.strip())
    return sorted(out)


def check_syntax(files):
    print(f"\n{DIM}── Syntax check ({len(files)} changed .py files) ──{RESET}")
    ok = True
    if not files:
        print("  (no changed Python files)")
    for f in files:
        p = REPO / f
        if not p.exists():
            continue
        try:
            ast.parse(p.read_text(encoding="utf-8"))
            print(f"  {GREEN}PASS{RESET}  {f}")
        except SyntaxError as e:
            ok = False
            print(f"  {RED}FAIL{RESET}  {f}:{e.lineno}  {e.msg}")
    return ok


def check_imports():
    print(f"\n{DIM}── Import check (core non-Streamlit modules) ──{RESET}")
    sys.path.insert(0, str(REPO))
    ok = True
    for m in CORE_MODULES:
        try:
            importlib.import_module(m)
            print(f"  {GREEN}PASS{RESET}  import {m}")
        except Exception as e:  # noqa: BLE001 — we want to report any import failure
            ok = False
            print(f"  {RED}FAIL{RESET}  import {m}  →  {type(e).__name__}: {e}")
    return ok


def git_state():
    print(f"\n{DIM}── Git state ──{RESET}")
    status = _run("git", "status", "--short").stdout.strip()
    n = len([l for l in status.splitlines() if l.strip()])
    print(f"  uncommitted/untracked files: {n}")
    ahead = _run("git", "rev-list", "--count", "@{upstream}..HEAD")
    if ahead.returncode == 0 and ahead.stdout.strip().isdigit():
        a = int(ahead.stdout.strip())
        print(f"  unpushed commits: {a}" + ("" if a == 0 else f"  {DIM}(push when ready){RESET}"))
    else:
        print("  upstream not configured (can't compute ahead/behind)")


def main():
    print("QuantWizard release-check")
    files = changed_py_files()
    syntax_ok = check_syntax(files)
    imports_ok = check_imports()
    git_state()

    print()
    if syntax_ok and imports_ok:
        print(f"{GREEN}✓ ALL CLEAR — safe to commit/push.{RESET}")
        sys.exit(0)
    print(f"{RED}✗ FAILURES above — fix before pushing.{RESET}")
    sys.exit(1)


if __name__ == "__main__":
    main()
