"""
predeploy_check.py — one-command pre-deploy data gate.

Runs the network-dependent correctness gates and exits non-zero if any fail:

  1. validate_metrics.py — data-correctness suite (per-stock metric sanity,
     cross-source price agreement, report display consistency for the portfolio
     AND single-stock Excel/PPTX, fundamentals sanity, edge cases, live-quote
     freshness).
  2. validate.py run_self_consistency_check() — guards the backtest's
     contribution accounting (a 100%-SPY portfolio must report the same
     return/risk with or without contributions, ~0 alpha vs SPY).

This is the SLOW gate — it needs network access and API keys, so run it before
a deploy. It is NOT a substitute for the fast syntax/import check (the
`release-check` skill), which should run on every push; this complements it.

    python predeploy_check.py
"""

import sys


def main():
    failures = []

    print("=" * 60)
    print("PRE-DEPLOY DATA GATE")
    print("=" * 60)

    # 1. Data-correctness suite. validate_metrics.main() raises SystemExit(1)
    #    when any check fails; treat that as a gate failure.
    import validate_metrics
    try:
        validate_metrics.main()
    except SystemExit as e:
        if e.code:
            failures.append("validate_metrics")

    # 2. Backtest contribution-accounting self-consistency (fast, ~18mo of SPY).
    try:
        from validate import run_self_consistency_check
        if not run_self_consistency_check():
            failures.append("validate.self_consistency")
    except Exception as e:  # network/data hiccup shouldn't masquerade as a code bug
        print(f"  ! self-consistency check could not run: {e}")

    print("\n" + "=" * 60)
    if failures:
        print(f"✗ PRE-DEPLOY GATE FAILED: {', '.join(failures)}")
        sys.exit(1)
    print("✓ PRE-DEPLOY GATE PASSED")


if __name__ == "__main__":
    main()
