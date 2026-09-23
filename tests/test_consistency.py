"""Regression tests for the price-consistency and split-basis fixes."""
import numpy as np
import pandas as pd

import valuation as V
from data import append_live_session, _enrich_ohlcv, _add_indicators
from market_data import _fmt_ts


def _facts(entries):
    return {"facts": {"us-gaap": {"EarningsPerShareDiluted": {
        "units": {"USD/shares": entries}}}}}


def test_eps_is_restated_by_filing_date_not_period_end():
    """A 4:1 split on 2020-08-31. Q1-Q3 filed BEFORE it are on the old basis;
    the FY total filed AFTER it is already post-split. Before the fix the
    derived Q4 came out hugely negative and TTM fell toward zero."""
    splits = pd.Series({pd.Timestamp("2020-08-31"): 4.0})
    E = lambda s, e, v, f: {"start": s, "end": e, "val": v, "filed": f}
    q = V._sec_quarterly_eps(_facts([
        E("2019-09-29", "2019-12-28", 4.99, "2020-01-29"),   # pre-split basis
        E("2019-12-29", "2020-03-28", 2.55, "2020-05-01"),
        E("2020-03-29", "2020-06-27", 2.58, "2020-07-31"),
        E("2019-09-29", "2020-09-26", 3.28, "2020-10-30"),   # FY, post-split
    ]), ["EarningsPerShareDiluted"], splits=splits)
    q4 = q[pd.Timestamp("2020-09-26")]
    assert q4 > 0, f"derived Q4 {q4:.2f} should be positive"
    assert abs(sum(q.values()) - 3.28) < 1e-6, "four quarters must sum to the FY"


def _frame(last_day="2026-09-21"):
    d = pd.bdate_range(end=last_day, periods=260)
    c = np.linspace(200, 339, len(d))
    df = pd.DataFrame({"Date": d, "Open": c, "High": c + 1, "Low": c - 1,
                       "Close": c, "Volume": 1e7})
    _enrich_ohlcv(df)
    _add_indicators(df, log=lambda m: None)
    return df


def _quote(day="2026-09-22"):
    ep = int(pd.Timestamp(f"{day} 20:00", tz="UTC").timestamp())
    return {"price": 339.75, "open": 339.1, "high": 345.34, "low": 338.75,
            "prev": 339.0, "epoch": ep}


def test_live_session_is_appended_when_the_feed_lags():
    df = _frame()
    out = append_live_session(df, _quote())
    assert len(out) == len(df) + 1
    last = out.iloc[-1]
    assert last["Close"] == 339.75 and last["High"] == 345.34
    assert last["52W_High"] >= 345.34, "52W range must include today's high"
    assert np.isnan(last["Volume"]), "the quote has no volume; do not invent one"


def test_live_session_is_idempotent_and_skips_same_day():
    once = append_live_session(_frame(), _quote())
    assert len(append_live_session(once, _quote())) == len(once)
    same = _frame("2026-09-22")
    assert len(append_live_session(same, _quote())) == len(same)


def test_live_session_needs_a_full_quote():
    df = _frame()
    q = _quote(); q.pop("high")
    assert len(append_live_session(df, q)) == len(df)
    assert len(append_live_session(df, None)) == len(df)


def test_quote_time_is_eastern_and_labelled():
    ts = int(pd.Timestamp("2026-09-22 20:00", tz="UTC").timestamp())
    assert _fmt_ts(ts) == "4:00 PM ET"
