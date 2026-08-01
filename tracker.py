"""
tracker.py — forward portfolio tracking (mark-to-market), NOT backtesting.

A tracked portfolio is a list of dated *lots*:

    {"ticker": "AAPL", "shares": 16.84, "added_date": "2026-01-15", "removed_date": None}

We hold exactly what the user entered (no rebalancing) and mark it to market each
trading day from inception to today. Performance is reported on a contribution-free,
time-weighted basis so adding/removing money is never counted as investment return —
the same accounting the backtest engine uses.

Design notes / deliberately out of scope (kept simple on purpose):
  • No rebalancing, no recurring contributions, no tax lots / cost basis beyond a
    simple unrealised-gain figure, no dividends-as-cash.
  • Removing a lot means "you sold it"; the proceeds stay in the book as idle
    cash at 0%. Contributed capital only ever moves on a purchase, so a
    profitable sale can't take more out of it than was put in.
  • Dividends are implicitly reflected via Yahoo's adjusted closes (≈ total return),
    consistent with the rest of the app.

Public API:
  amount_to_shares(ticker, amount, on_date)        -> (shares, fill_date, fill_price)
  dollars_to_lots({ticker: $amount}, inception)    -> [lot, ...]
  track_portfolio(holdings)                         -> {curve, metrics, holdings, ...}
"""

from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd

from market_data import get_bars, get_bars_batch
from portfolio_analysis import compute_backtest_metrics

BENCHMARK = "SPY"


# ── helpers ─────────────────────────────────────────────────────────────────────

def _as_ts(d) -> pd.Timestamp:
    return pd.Timestamp(d).normalize()


def _today_str() -> str:
    return _dt.date.today().strftime("%Y-%m-%d")


def amount_to_shares(ticker: str, amount: float, on_date, api_key: str = ""):
    """
    Convert a dollar amount to a share count using the first available close on or
    after `on_date` (handles weekends/holidays). Returns (shares, fill_date, price)
    or (None, None, None) if no price is available.
    """
    target = _as_ts(on_date)
    # Look back as well as forward so "track from today" works even when today's
    # close isn't out yet (weekend / holiday / market still open) — we then fall
    # back to the most recent available close.
    start = target - pd.Timedelta(days=7)
    end   = target + pd.Timedelta(days=10)
    df = get_bars(ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
                  interval="day", polygon_key=api_key)
    if df is None or df.empty:
        return None, None, None
    df = df.sort_values("Date")
    onafter = df[pd.to_datetime(df["Date"]) >= target]
    row = onafter.iloc[0] if len(onafter) else df.iloc[-1]   # first close ≥ add date, else most recent
    price = float(row["Close"])
    if price <= 0:
        return None, None, None
    return amount / price, pd.Timestamp(row["Date"]).normalize(), price


def dollars_to_lots(allocations: dict, inception_date, api_key: str = "") -> list:
    """
    Turn {ticker: dollar_amount} into lots, all added at inception. Tickers whose
    price can't be fetched are skipped (returned in the second element).
    """
    lots, skipped = [], []
    for tk, amt in allocations.items():
        shares, fill_date, _ = amount_to_shares(tk, float(amt), inception_date, api_key)
        if shares is None:
            skipped.append(tk)
            continue
        lots.append({
            "ticker":       tk.upper(),
            "shares":       float(shares),
            "added_date":   (fill_date or _as_ts(inception_date)).strftime("%Y-%m-%d"),
            "removed_date": None,
        })
    return lots, skipped


def _fetch_closes(tickers: list, start: str, end: str, api_key: str = "") -> dict:
    """{ticker: Series(Close indexed by Date)} via one batch call, per-ticker fallback."""
    out = {}
    batch = get_bars_batch(tickers, start, end, interval="day")
    for tk in tickers:
        df = batch.get(tk)
        if df is None or len(df) == 0:
            df = get_bars(tk, start, end, interval="day", polygon_key=api_key)
        if df is not None and len(df) > 0:
            s = (df.drop_duplicates("Date").set_index(pd.to_datetime(df.drop_duplicates("Date")["Date"]))
                   ["Close"].astype(float).sort_index())
            out[tk] = s
    return out


# ── core ──────────────────────────────────────────────────────────────────────

def track_portfolio(holdings: list, api_key: str = "", benchmark: str = BENCHMARK,
                    end_date: str | None = None) -> dict:
    """
    Mark-to-market a list of lots from inception to `end_date` (default: today).

    Returns:
      {
        "curve":    DataFrame[Date, Portfolio, Contrib, NAV, SP500],
        "metrics":  dict (reuses compute_backtest_metrics — same keys as the backtest),
        "holdings": [ {ticker, shares, last_price, value, weight_pct, cost_basis,
                       gain_pct} ... ]  (currently-held positions),
        "inception_date": "YYYY-MM-DD",
        "warnings": [ ... ],
      }
    or {"error": "..."} if there's nothing to track.
    """
    lots = [dict(h) for h in (holdings or []) if h.get("shares")]
    if not lots:
        return {"error": "No holdings to track."}

    for lot in lots:
        lot["ticker"]   = str(lot["ticker"]).upper()
        lot["_added"]   = _as_ts(lot["added_date"])
        lot["_removed"] = _as_ts(lot["removed_date"]) if lot.get("removed_date") else None
        lot["shares"]   = float(lot["shares"])

    inception = min(lot["_added"] for lot in lots)
    end = _as_ts(end_date) if end_date else _as_ts(_today_str())
    tickers = sorted({lot["ticker"] for lot in lots})

    warnings = []
    closes = _fetch_closes(tickers + [benchmark],
                           inception.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), api_key)

    missing = [t for t in tickers if t not in closes]
    if missing:
        warnings.append(f"No price data for: {', '.join(missing)} (excluded).")
        lots = [lot for lot in lots if lot["ticker"] in closes]
        tickers = [t for t in tickers if t in closes]
    if not lots:
        return {"error": "None of the holdings had price data."}

    # Master trading-day calendar = union of held tickers' dates within [inception, end].
    idx = sorted(set().union(*[set(closes[t].index) for t in tickers]))
    idx = [d for d in idx if inception <= d <= end]
    if len(idx) < 2:
        return {"error": "Not enough price history since inception to chart."}

    # Snap each lot's add/remove date FORWARD onto the master calendar. The loop
    # below matches dates exactly, so a lot dated on a weekend, a holiday, or a
    # day this particular ticker didn't trade was never bought at all — it just
    # silently never appeared in the portfolio. dollars_to_lots snaps to a real
    # bar, but holdings can also arrive hand-edited or from an import.
    def _snap(ts):
        if ts is None:
            return None
        return next((d for d in idx if d >= ts), None)

    for lot in lots:
        lot["_added"]   = _snap(lot["_added"])
        lot["_removed"] = _snap(lot["_removed"])
    # An add date with no bar at or after it is dated past the end of the window —
    # a future-dated lot. Exclude it rather than silently buying it on day one.
    _future = [lot["ticker"] for lot in lots if lot["_added"] is None]
    if _future:
        warnings.append(f"Ignored holding(s) dated after today: {', '.join(sorted(set(_future)))}.")
        lots = [lot for lot in lots if lot["_added"] is not None]
    if not lots:
        return {"error": "No holdings with a usable start date."}

    # Reindex every series onto the master calendar (forward-fill gaps/holidays).
    px = {t: closes[t].reindex(idx).ffill() for t in tickers}
    has_bench = benchmark in closes
    spy = closes[benchmark].reindex(idx).ffill() if has_bench else None
    if not has_bench:
        warnings.append(f"No {benchmark} data — benchmark comparison unavailable.")

    # Walk the calendar, applying each lot's cash flow on its add/remove date and
    # compounding a contribution-free NAV (time-weighted return), mirroring
    # backtest_portfolio so the metrics line up with the rest of the app.
    shares = {t: 0.0 for t in tickers}
    spy_shares = 0.0
    contrib = 0.0
    # Sale proceeds, held as idle cash at 0%. A sale is NOT a negative
    # contribution: `contrib -= shares * today's price` (the old behaviour)
    # removed the position's MARKET VALUE from contributed capital, so selling a
    # doubled holding took out more than had ever been put in — Total Contributed
    # could fall to zero or go negative, and Total Return = gain / contributed
    # then printed nonsense. Contributed capital only ever moves on a purchase;
    # the proceeds stay inside the book as cash so the value curve doesn't cliff
    # and the return stays honest.
    realized = 0.0
    spy_realized = 0.0
    prev_value = 0.0
    nav = 1.0
    portfolio_vals, contrib_vals, nav_vals, spy_vals = [], [], [], []

    for d in idx:
        # Cash is carried in value_pre as well, so it correctly reads as a
        # zero-return asset in the time-weighted NAV rather than vanishing.
        value_pre = sum(shares[t] * float(px[t].loc[d]) for t in tickers) + realized
        if prev_value > 0:
            nav *= (1 + (value_pre / prev_value - 1))   # flow happens *after* this

        for lot in lots:
            p = float(px[lot["ticker"]].loc[d])
            if lot["_added"] == d:                       # buy: cash inflow
                shares[lot["ticker"]] += lot["shares"]
                cost = lot["shares"] * p
                contrib += cost
                if has_bench and float(spy.loc[d]) > 0:
                    spy_shares += cost / float(spy.loc[d])
            if lot["_removed"] is not None and lot["_removed"] == d:   # sell → cash
                shares[lot["ticker"]] -= lot["shares"]
                proceeds  = lot["shares"] * p
                realized += proceeds
                # Benchmark sells the matching dollar amount and holds it as cash
                # too, so the comparison stays like-for-like through the sale.
                if has_bench and float(spy.loc[d]) > 0:
                    _sold = min(spy_shares, proceeds / float(spy.loc[d]))
                    spy_shares   = max(0.0, spy_shares - _sold)
                    spy_realized += _sold * float(spy.loc[d])

        value_post = sum(shares[t] * float(px[t].loc[d]) for t in tickers) + realized
        portfolio_vals.append(value_post)
        contrib_vals.append(contrib)
        nav_vals.append(nav)
        spy_vals.append(spy_shares * float(spy.loc[d]) + spy_realized
                        if has_bench else np.nan)
        prev_value = value_post

    curve = pd.DataFrame(index=pd.to_datetime(idx))
    curve["Portfolio"] = portfolio_vals
    curve["Contrib"]   = contrib_vals
    curve["NAV"]       = nav_vals
    curve["SP500"]     = spy_vals

    starting_capital = next((v for v in contrib_vals if v > 0), 0.0)
    metrics = compute_backtest_metrics(curve, starting_capital)

    # Per-ticker current holdings detail (for the UI table).
    last = idx[-1]
    total_value = sum(shares[t] * float(px[t].loc[last]) for t in tickers) or 1.0
    holdings_detail = []
    for t in tickers:
        if shares[t] <= 1e-9:
            continue
        last_price = float(px[t].loc[last])
        value = shares[t] * last_price
        active = [l for l in lots if l["ticker"] == t and l["_removed"] is None]
        cost_basis = sum(l["shares"] * float(px[t].loc[l["_added"]]) for l in active)
        holdings_detail.append({
            "ticker":     t,
            "shares":     round(shares[t], 4),
            "last_price": round(last_price, 2),
            "value":      round(value, 2),
            "weight_pct": round(value / total_value * 100, 2),
            "cost_basis": round(cost_basis, 2),
            "gain_pct":   round((value / cost_basis - 1) * 100, 2) if cost_basis > 0 else None,
        })
    holdings_detail.sort(key=lambda h: -h["value"])

    return {
        "curve":          curve,
        "metrics":        metrics,
        "holdings":       holdings_detail,
        "inception_date": inception.strftime("%Y-%m-%d"),
        "warnings":       warnings,
    }
