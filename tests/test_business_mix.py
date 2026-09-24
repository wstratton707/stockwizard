"""Segments, capital returns and valuation history (item 5 of the report upgrade)."""
import pytest

import analysis as A
import data as D


def test_subtotal_members_are_dropped_before_the_axis_is_reconciled():
    """Apple files Products (= iPhone + Mac + iPad + Wearables) beside its parts
    on the same axis; keeping both double-counts 70% of revenue."""
    m = {"iPhone": 209.6, "Mac": 33.7, "iPad": 28.0, "Wearables": 35.7,
         "Products": 307.0, "Services": 109.2}
    out = D._drop_subtotals(m, total=416.2)
    assert "Products" not in out and set(out) == {"iPhone", "Mac", "iPad", "Wearables", "Services"}
    assert sum(out.values()) == pytest.approx(416.2)


def test_the_consolidated_total_is_not_a_segment():
    out = D._drop_subtotals({"A": 60.0, "B": 40.0, "Total": 100.0}, total=100.0)
    assert set(out) == {"A", "B"}


def test_member_names_read_as_words():
    assert D._humanize_member("msft:ProductivityAndBusinessProcessesMember") == \
        "Productivity and Business Processes"
    assert D._humanize_member("srt:NonUsMember") == "Outside the US"
    assert D._tidy_label("UNITED STATES") == "United States"
    assert D._tidy_label("AWS") == "AWS"


def test_valuation_history_ranks_today_against_past_years():
    vd = {"years": [2021, 2022, 2023, 2024], "eps": [5.0, 6.0, 6.0, 7.0],
          "pe_by_year": [20.0, 25.0, 30.0, 28.0], "high": [120, 180, 200, 230],
          "low": [90, 130, 150, 170]}
    f = {"valuation": {"pe": 27.0},
         "trend": {"periods": ["2023-09", "2024-09"], "net_income": [100.0, 110.0],
                   "fcf": [90.0, 99.0]}}
    h = A.valuation_history(vd, f)
    assert h["median_pe"] == pytest.approx(26.5)
    assert h["percentile"] == pytest.approx(0.5)            # 20 and 25 below 27
    r24 = h["rows"][-1]
    assert r24["pe_high"] == pytest.approx(230 / 7.0)
    assert r24["fcf_yield"] == pytest.approx((99.0 / 110.0) / 28.0)
    assert h["rows"][0]["fcf_yield"] is None                # no cash data that year


def test_valuation_history_needs_some_history():
    vd = {"years": [2024], "eps": [7.0], "pe_by_year": [28.0], "high": [1], "low": [1]}
    assert A.valuation_history(vd, {}) is None


# ── Round 2: tax one-offs, DCF after stock pay, news hygiene ─────────────────

def _fin_with_tax(rates, pretax=100.0):
    """Fiscal-year frames, newest first, with the given effective tax rates."""
    import pandas as pd
    per = [f"{2025 - i}-09-27" for i in range(len(rates))]
    inc = pd.DataFrame([{"Period": p, "revenues": 400.0, "pretax_income": pretax,
                         "income_tax": pretax * r, "net_income_loss": pretax * (1 - r),
                         "diluted_earnings_per_share": pretax * (1 - r) / 10.0,
                         "diluted_shares": 10.0} for p, r in zip(per, rates)])
    bal = pd.DataFrame([{"Period": p, "equity": 50.0} for p in per])
    cf = pd.DataFrame([{"Period": p, "net_cash_flow_from_operating_activities": 90.0,
                        "capex": 10.0} for p in per])
    return {"income_statement": inc, "balance_sheet": bal, "cash_flow_statement": cf}


def test_a_one_off_tax_year_is_taken_out_of_eps_growth():
    """Prior year taxed at 24% against a usual 16%: reported EPS growth is
    growth off a depressed base."""
    f = A.compute_fundamentals(_fin_with_tax([0.16, 0.24, 0.16, 0.15, 0.17]))
    g = f["growth"]
    assert g["eps_yoy"] == pytest.approx((0.84 / 0.76 - 1) * 100, abs=0.1)
    assert g["eps_yoy_ex_tax_one_offs"] == pytest.approx(0.0, abs=0.1)
    assert g["tax_one_offs"][0]["tax_rate"] == pytest.approx(0.24)


def test_ordinary_tax_noise_is_left_alone():
    g = A.compute_fundamentals(_fin_with_tax([0.16, 0.17, 0.155, 0.165, 0.16]))["growth"]
    assert g["eps_yoy_ex_tax_one_offs"] is None and g["tax_one_offs"] == []


def test_dcf_after_stock_pay_scales_enterprise_value():
    f = {"ok": True, "market_cap": 1000.0, "net_debt": 50.0, "total_debt": 100.0,
         "trend": {"fcf": [80.0, 90.0, 100.0]}, "growth": {}, "fcf": {"sbc": 10.0}}
    d = A.dcf_valuation(f, 10.0, wacc=0.09)
    ev, base = d["enterprise_value"], d["base_fcf"]
    assert d["fair_value_after_sbc"] == pytest.approx((ev * (1 - 10.0 / base) - 50.0) / 100.0)
    assert d["fair_value_after_sbc"] < d["fair_value"]


def test_news_drops_quote_pages_and_other_tickers_headlines():
    import news_research as N
    assert N.is_quote_page("AAPL 260925 330.00P (AAPL260925P330000) Stock Options Chain | Quotes & News")
    assert not N.is_quote_page("Apple shares hit record high after iPhone launch")
    assert N._names_other_ticker_only(
        "Should iShares Russell Top 200 Growth ETF (IWY) Be on Your Investing Radar?", "AAPL", ["apple"])
    assert not N._names_other_ticker_only("Could Apple (AAPL) Be Preparing Apple Pay", "AAPL", ["apple"])


def test_sentiment_reads_the_headline_not_substrings():
    import news_research as N
    assert N._keyword_sentiment("AAPL Stock Slips — Why This Analyst Turned Bearish On Apple "
                                "After The iPhone Maker Hit An All-Time High") == "Negative"
    assert N._keyword_sentiment("New laptop tops the charts") == "Neutral"
    assert N._keyword_sentiment("Microsoft beats estimates, raises guidance") == "Positive"
