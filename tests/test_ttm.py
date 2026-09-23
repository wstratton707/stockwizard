"""Trailing-twelve-month fundamentals (Sep 2026 audit, fix #1).

The report paired a September 2026 price with September 2025 earnings although
three 10-Qs had been filed since. These pin the quarter reconstruction, the
overlay onto the fiscal-year frames, and the market cap built from the filing's
share count. Synthetic companyfacts, no network.
"""
import pandas as pd
import pytest

import analysis as A
import data as D


# ── companyfacts builder ──────────────────────────────────────────────────────

def _dur(start, end, val, form="10-Q", filed=None, fp="Q"):
    return {"start": start, "end": end, "val": val, "form": form, "fp": fp,
            "filed": filed or end, "accn": f"a-{end}-{form}"}


def _inst(end, val, form="10-Q", filed=None):
    return {"end": end, "val": val, "form": form, "filed": filed or end, "accn": f"a-{end}"}


def _cumulative(fy_start, q_ends, q_vals, fy_end_form="10-K"):
    """YTD facts the way filings report them: 3, 6, 9 and 12 months from the
    fiscal-year start, plus each standalone 3-month quarter."""
    rows, run = [], 0.0
    for i, (e, v) in enumerate(zip(q_ends, q_vals)):
        run += v
        form = fy_end_form if i == 3 else "10-Q"
        rows.append(_dur(fy_start, e, run, form=form, fp="FY" if i == 3 else "Q"))
    return rows


def _facts(rev_rows, ni_rows=None, cfo_rows=None, capex_rows=None, assets=None, cover=None,
           eps_rows=None):
    g = {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rev_rows}}}
    if ni_rows:
        g["NetIncomeLoss"] = {"units": {"USD": ni_rows}}
    if cfo_rows:
        g["NetCashProvidedByUsedInOperatingActivities"] = {"units": {"USD": cfo_rows}}
    if capex_rows:
        g["PaymentsToAcquirePropertyPlantAndEquipment"] = {"units": {"USD": capex_rows}}
    if assets:
        g["Assets"] = {"units": {"USD": assets}}
    if eps_rows:
        g["EarningsPerShareDiluted"] = {"units": {"USD/shares": eps_rows}}
    out = {"facts": {"us-gaap": g}}
    if cover:
        out["facts"]["dei"] = {"EntityCommonStockSharesOutstanding": {"units": {"shares": cover}}}
    return out


FY24 = ("2023-10-01", ["2023-12-30", "2024-03-30", "2024-06-29", "2024-09-28"])
FY25 = ("2024-09-29", ["2024-12-28", "2025-03-29", "2025-06-28", "2025-09-27"])
FY26 = ("2025-09-28", ["2025-12-27", "2026-03-28", "2026-06-27"])


def _two_years_and_three_quarters(vals24, vals25, vals26):
    rows = _cumulative(FY24[0], FY24[1], vals24) + _cumulative(FY25[0], FY25[1], vals25)
    run = 0.0
    for e, v in zip(FY26[1], vals26):
        run += v
        rows.append(_dur(FY26[0], e, run))
    return rows


# ── quarter reconstruction ────────────────────────────────────────────────────

def test_quarters_are_recovered_from_cumulative_facts():
    """Q4 exists only as the full year less nine months, and the cash-flow
    statement is reported only cumulatively - both must come back per quarter."""
    rows = _cumulative(FY25[0], FY25[1], [10.0, 20.0, 30.0, 40.0])
    q = D._sec_quarterly_flow(_facts(rows), ["RevenueFromContractWithCustomerExcludingAssessedTax"])
    assert [round(v, 6) for v in q.values()] == [10.0, 20.0, 30.0, 40.0]


def test_a_later_filing_restates_the_earlier_one():
    rows = _cumulative(FY25[0], FY25[1], [10.0, 20.0, 30.0, 40.0])
    rows.append(_dur(FY25[0], FY25[1][1], 33.0, filed="2026-01-01"))   # H1 restated 30 -> 33
    q = D._sec_quarterly_flow(_facts(rows), ["RevenueFromContractWithCustomerExcludingAssessedTax"])
    vals = list(q.values())
    assert vals[1] == pytest.approx(23.0) and vals[2] == pytest.approx(27.0)


def test_ttm_sums_the_latest_four_quarters_and_keeps_the_prior_four():
    rev = _two_years_and_three_quarters([1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3])
    fx = _facts(rev, ni_rows=rev,
                assets=[_inst("2025-09-27", 100.0, "10-K"), _inst("2026-06-27", 120.0)])
    t = D._sec_ttm(fx, "2025-09-27")
    assert t["flows_end"] == "2026-06-27"
    assert t["income"]["revenues"] == pytest.approx(2 + 3 + 3 + 3)
    assert t["prior"]["revenues"] == pytest.approx(1 + 2 + 2 + 2)
    assert t["balance_end"] == "2026-06-27" and t["balance"]["assets"] == 120.0


def test_a_stale_cover_share_count_is_dropped():
    """Berkshire's companyfacts cover count is from 2011; priced today it would
    value the company at a fraction of its worth."""
    rev = _two_years_and_three_quarters([1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3])
    old = _facts(rev, cover=[_inst("2011-04-29", 1.6e6)])
    assert D._sec_ttm(old, "2025-09-27")["shares_outstanding"] is None
    fresh = _facts(rev, cover=[_inst("2026-07-17", 14.594e9)])
    assert D._sec_ttm(fresh, "2025-09-27")["shares_outstanding"] == pytest.approx(14.594e9)


# ── overlay onto the fiscal-year frames ───────────────────────────────────────

def _fin(ttm=None):
    inc = pd.DataFrame([
        {"Period": "2025-09-27", "revenues": 400.0, "gross_profit": 180.0,
         "operating_income_loss": 120.0, "net_income_loss": 100.0,
         "diluted_earnings_per_share": 6.0, "diluted_shares": 16.0,
         "depreciation_amortization": 10.0},
        {"Period": "2024-09-28", "revenues": 380.0, "gross_profit": 170.0,
         "operating_income_loss": 110.0, "net_income_loss": 90.0,
         "diluted_earnings_per_share": 5.0, "diluted_shares": 17.0,
         "depreciation_amortization": 9.0}])
    bal = pd.DataFrame([
        {"Period": "2025-09-27", "assets": 350.0, "equity": 70.0, "current_assets": 150.0,
         "current_liabilities": 160.0, "long_term_debt": 80.0, "cash": 30.0,
         "liabilities": 280.0, "retained_earnings": -10.0},
        {"Period": "2024-09-28", "assets": 340.0, "equity": 60.0, "current_assets": 140.0,
         "current_liabilities": 170.0, "long_term_debt": 90.0, "cash": 25.0,
         "liabilities": 280.0, "retained_earnings": -15.0}])
    cf = pd.DataFrame([
        {"Period": "2025-09-27", "net_cash_flow_from_operating_activities": 110.0, "capex": 12.0},
        {"Period": "2024-09-28", "net_cash_flow_from_operating_activities": 100.0, "capex": 10.0}])
    out = {"income_statement": inc, "balance_sheet": bal, "cash_flow_statement": cf,
           "source": "SEC EDGAR"}
    if ttm is not None:
        out["ttm"] = ttm
    return out


def _ttm(**over):
    t = {"flows_end": "2026-06-27", "fy_end": "2025-09-27", "balance_end": "2026-06-27",
         "income": {"revenues": 460.0, "gross_profit": 225.0, "operating_income_loss": 150.0,
                    "net_income_loss": 125.0, "depreciation_amortization": 12.0,
                    "eps_diluted": 8.5},
         "cash_flow": {"net_cash_flow_from_operating_activities": 145.0, "capex": 10.0},
         "prior": {"revenues": 400.0, "net_income_loss": 100.0, "eps_diluted": 6.5},
         "fcf_windows": [96.0, 104.0, 135.0],
         "balance": {"assets": 360.0, "equity": 100.0, "current_assets": 160.0,
                     "current_liabilities": 150.0, "long_term_debt": 70.0, "cash": 40.0,
                     "liabilities": 260.0, "retained_earnings": None},
         "shares_outstanding": 14.5, "shares_date": "2026-07-17"}
    t.update(over)
    return t


def test_multiples_use_the_latest_twelve_months():
    f = A.compute_fundamentals(_fin(_ttm()), market_cap=4800.0, price=330.0)
    assert f["basis"]["kind"] == "ttm"
    assert f["income"]["revenue"] == 460.0 and f["income"]["net_income"] == 125.0
    assert f["valuation"]["pe"] == pytest.approx(f["market_cap"] / 125.0, abs=0.01)
    assert f["fcf"]["fcf"] == pytest.approx(135.0)
    assert f["balance"]["equity"] == 100.0
    # growth over complete fiscal years keeps using them
    assert f["growth"]["revenue_cagr"] == pytest.approx((400 / 380 - 1) * 100, abs=0.1)
    # year-on-year compares twelve months with twelve months
    assert f["growth"]["revenue_yoy"] == pytest.approx(15.0)
    assert f["growth"]["yoy_basis"] == "ttm"
    assert "Trailing 12 months to 27 Jun 2026" in A.fundamentals_basis_label(f)


def test_a_field_missing_from_the_new_balance_sheet_is_not_borrowed_from_the_old():
    """Retained earnings absent at the latest date must not pair with a year-old
    figure inside one ratio - Altman Z goes blank rather than mixing dates."""
    f = A.compute_fundamentals(_fin(_ttm()), market_cap=4800.0, price=330.0)
    assert f["quality"]["z_score"] is None


def test_no_newer_quarter_means_the_fiscal_year_as_before():
    f = A.compute_fundamentals(_fin(_ttm(flows_end="2025-09-27", balance_end="2025-09-27")),
                               market_cap=4800.0, price=330.0)
    assert f["basis"]["kind"] == "fy"
    assert f["income"]["net_income"] == 100.0
    assert A.fundamentals_basis_label(f) == "FY ending 27 Sep 2025"


def test_market_cap_is_cover_shares_times_the_report_price():
    f = A.compute_fundamentals(_fin(_ttm()), market_cap=4958.4, price=336.95)
    assert f["market_cap"] == pytest.approx(14.5 * 336.95)
    assert f["market_cap_basis"] == "filing"


def test_a_cover_count_far_from_the_vendor_figure_is_not_trusted():
    """A multi-class filer's cover can list one class; 40% of the vendor figure
    says the count is partial, and the vendor figure stands."""
    f = A.compute_fundamentals(_fin(_ttm(shares_outstanding=5.8)), market_cap=4958.4,
                               price=336.95)
    assert f["market_cap"] == 4958.4 and f["market_cap_basis"] == "vendor"


def test_eps_growth_across_a_split_is_not_quoted():
    """As-filed EPS quartered by a 4:1 split mid-window reads as a collapse."""
    t = _ttm(prior={"revenues": 400.0, "net_income_loss": 100.0, "eps_diluted": 26.0})
    f = A.compute_fundamentals(_fin(t), market_cap=4800.0, price=330.0)
    assert f["growth"]["eps_yoy"] == pytest.approx(20.0)      # fiscal-year fallback
    assert f["growth"]["yoy_basis"] == "ttm_eps_fy"


def test_dcf_base_uses_the_twelve_month_windows_only_when_all_are_positive():
    f = A.compute_fundamentals(_fin(_ttm()), market_cap=4800.0, price=330.0)
    d = A.dcf_valuation(f, 330.0, wacc=0.09)
    assert d["base_fcf_basis"] == "ttm"
    assert d["base_fcf"] == pytest.approx((96 + 104 + 135) / 3)
    f2 = A.compute_fundamentals(_fin(_ttm(fcf_windows=[-5.0, 104.0, 135.0])),
                                market_cap=4800.0, price=330.0)
    assert A.dcf_valuation(f2, 330.0, wacc=0.09)["base_fcf_basis"] == "fiscal-year"
