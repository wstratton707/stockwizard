"""Regression tests for the five defects found in the 2026-09-22 data audit.

Each test pins one defect. They use synthetic inputs rather than live filings,
so they are fast, offline, and do not go stale when a company files again — the
real-world figures that motivated each case are recorded in the comments.
"""
import pandas as pd
import pytest

import analysis as A


def _inc(eps_series, revenues=None):
    """A minimal income-statement frame, newest-first (as EDGAR is here)."""
    n = len(eps_series)
    return pd.DataFrame({
        "Period": [f"{2025-i}-12-31" for i in range(n)],
        "diluted_earnings_per_share": list(eps_series),
        "revenues": list(revenues) if revenues else [1e9] * n,
        "net_income_loss": [1e8] * n,
    })


# ── 1. The complex-number crash ──────────────────────────────────────────────

def test_eps_cagr_returns_none_when_latest_is_a_loss():
    """INTC: latest diluted EPS -0.06, oldest 2.12 -> TypeError before the fix.

    A fractional power of a negative number is complex, and round() raises on a
    complex. The whole fundamentals block died and the Financials tab rendered a
    raw traceback; exports silently dropped every fundamental.
    """
    fin = {"income_statement": _inc([-0.06, 1.0, 1.5, 2.0, 2.12]),
           "balance_sheet": pd.DataFrame([{"Period": "2025-12-31"}]),
           "cash_flow_statement": pd.DataFrame([{"Period": "2025-12-31"}])}
    f = A.compute_fundamentals(fin)                      # must not raise
    assert f["ok"]
    assert f["growth"]["eps_cagr"] is None, "a positive->negative swing has no CAGR"


def test_revenue_cagr_survives_a_negative_latest_value():
    """cagr() carried the identical unguarded shape; revenue rarely goes
    negative, but nothing prevented it."""
    fin = {"income_statement": _inc([1.0] * 5, revenues=[-5e8, 1e9, 1e9, 1e9, 2e9]),
           "balance_sheet": pd.DataFrame([{"Period": "2025-12-31"}]),
           "cash_flow_statement": pd.DataFrame([{"Period": "2025-12-31"}])}
    f = A.compute_fundamentals(fin)
    assert f["growth"]["revenue_cagr"] is None


def test_eps_cagr_still_computed_when_both_ends_positive():
    """The guard must not suppress a legitimate CAGR."""
    fin = {"income_statement": _inc([8.0, 6.0, 5.0, 4.0, 2.0]),
           "balance_sheet": pd.DataFrame([{"Period": "2025-12-31"}]),
           "cash_flow_statement": pd.DataFrame([{"Period": "2025-12-31"}])}
    f = A.compute_fundamentals(fin)
    assert f["growth"]["eps_cagr"] == pytest.approx(41.4, abs=0.1)   # (8/2)^(1/4)-1


# ── 2. FCF CAGR must span elapsed years, not surviving entries ───────────────

def _fund_with_fcf(series):
    return {"ok": True, "market_cap": 1e11, "trend": {"fcf": list(series)},
            "net_debt": 0.0, "total_debt": 0.0, "growth": {}}


def test_fcf_cagr_counts_elapsed_years_not_positive_entries():
    """Goldman Sachs: 4 positive years spread over a 6-year span read as 22.56%
    a year when the elapsed-time rate is 10.71% — an 11.86pp overstatement fed
    straight into stage-1 growth."""
    # positives at index 0 and 6 -> 6 elapsed years, not 1
    fcf = [100.0, -50.0, -40.0, -30.0, -20.0, -10.0, 200.0]
    d = A.dcf_valuation(_fund_with_fcf(fcf), price=10.0, wacc=0.09)
    assert d["ok"]
    expected = (200.0 / 100.0) ** (1 / 6) - 1           # 12.25%
    assert d["base_growth"] == pytest.approx(expected, rel=1e-9)
    wrong = (200.0 / 100.0) ** (1 / 1) - 1              # 100% -> clamps to 20%
    assert d["base_growth"] != pytest.approx(min(wrong, 0.20), rel=1e-9)


def test_fcf_cagr_unchanged_when_every_year_is_positive():
    """No gaps means no behaviour change — AAPL must be untouched."""
    fcf = [100.0, 110.0, 120.0, 130.0, 140.0]
    d = A.dcf_valuation(_fund_with_fcf(fcf), price=10.0, wacc=0.09)
    assert d["base_growth"] == pytest.approx((140.0 / 100.0) ** (1 / 4) - 1, rel=1e-9)


# ── 3. Debt concepts: additive parts, roll-ups, and no double-counting ───────

def _fin_with_balance(**cols):
    bal = {"Period": "2025-12-31"}
    bal.update(cols)
    return {"income_statement": _inc([1.0] * 3),
            "balance_sheet": pd.DataFrame([bal]),
            "cash_flow_statement": pd.DataFrame([{"Period": "2025-12-31"}])}


def test_commercial_paper_is_added_when_there_is_no_rollup():
    """AAPL FY2025: 78,328 + 12,350 + 7,979 CP = 98,657. CP was dropped."""
    fin = _fin_with_balance(long_term_debt=78_328e6, debt_current=12_350e6,
                            commercial_paper=7_979e6)
    f = A.compute_fundamentals(fin)
    assert f["total_debt"] == pytest.approx(98_657e6)


def test_rollup_wins_and_commercial_paper_is_not_double_counted():
    """DebtCurrent already contains commercial paper."""
    fin = _fin_with_balance(long_term_debt=100e9, debt_current_total=20e9,
                            debt_current=15e9, commercial_paper=5e9)
    f = A.compute_fundamentals(fin)
    assert f["total_debt"] == pytest.approx(120e9)      # not 125e9, not 140e9


def test_single_fact_total_debt_beats_reassembled_parts():
    """Verizon and GM report their whole debt in one fact and no non-current
    tag at all; VZ read 18.6bn against a true ~158bn before this."""
    fin = _fin_with_balance(debt_total_incl_current=157_709e6,
                            debt_current_total=18_618e6)   # ltd absent, as VZ
    f = A.compute_fundamentals(fin)
    assert f["total_debt"] == pytest.approx(157_709e6)


def test_short_term_investments_count_as_cash_for_net_debt():
    """AAPL FY2025: 35,934 cash + 18,763 short-term investments."""
    fin = _fin_with_balance(long_term_debt=78_328e6, debt_current=12_350e6,
                            commercial_paper=7_979e6,
                            cash=35_934e6, short_term_investments=18_763e6)
    f = A.compute_fundamentals(fin)
    assert f["net_debt"] == pytest.approx(98_657e6 - 54_697e6)


def test_missing_short_term_investments_degrades_to_cash_only():
    """The Polygon fallback has no such column; it must not become zero cash."""
    fin = _fin_with_balance(long_term_debt=50e9, debt_current=10e9, cash=20e9)
    f = A.compute_fundamentals(fin)
    assert f["net_debt"] == pytest.approx(40e9)
