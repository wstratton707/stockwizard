---
name: release-check
description: Pre-push sanity check for the QuantWizard Streamlit app. Syntax-checks every changed Python file, imports the core non-Streamlit modules to catch import-time errors (the class of bug that crashed the app on a missing dep), and summarizes git state. Use before committing/pushing, when the user says "is this safe to push", "check my changes", "run a release check", or after a batch of edits.
---

# Release Check — QuantWizard pre-push validation

A fast, mechanical gate that catches the bug classes that have actually bitten this
project: syntax errors, missing or broken imports (e.g. a dependency present in the
venv but not declared, or a module that crashes on import), and forgetting to push.

## When to use
- Before `git commit` / `git push` after editing Python files.
- When the user asks "is this safe to push?", "check my changes", "release check".
- After any multi-file edit batch, before declaring work done.

## How to run
Run the helper with the project's virtualenv interpreter (NOT global Python — the
venv has the pinned deps):

```bash
.venv/Scripts/python.exe .claude/skills/release-check/scripts/release_check.py
```

(On macOS/Linux use `.venv/bin/python`.)

The script:
1. Finds changed `.py` files via `git diff` (staged + unstaged + untracked).
2. `ast.parse`s each — reports syntax errors with file:line.
3. Imports the core non-Streamlit modules (`data`, `analysis`, `portfolio_data`,
   `portfolio_analysis`, `strategy`, `cached_fetchers`, `excel_builder`,
   `pptx_builder`, `stress_test`) to catch import-time failures.
4. Prints git ahead/behind + uncommitted file count.

## Interpreting results
- **All PASS** → safe to commit/push.
- **Syntax FAIL** → fix the reported file:line before anything else.
- **Import FAIL** → usually a missing dependency (add to `requirements.txt`) or a
  module-level crash; this is exactly the failure that took the app down on a
  fresh environment, so do not push until it's resolved.
- Always finish by reporting the verdict to the user; never push on a FAIL.

## Notes
- This does NOT launch the app or hit the network — it's a static + import gate.
  For visual/behavioral verification, use the `ui-qa` skill.
- Keep it fast: it should run in a few seconds.
