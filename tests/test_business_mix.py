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
