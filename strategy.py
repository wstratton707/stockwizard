"""
strategy.py — Drawdown-Rebound Mega-Cap Strategy

Rules (long-only, no leverage):
  • Universe: liquid mega-caps that have at some point exceeded ~$200B market cap.
  • BUY  signal when close ≤ (1 - ENTRY_PCT) × running ATH (default: 20% off ATH).
  • SELL signal when close ≥ (1 - EXIT_PCT)  × running ATH (default: within 3% of ATH).
  • Execution: signal fires at end-of-day t, trade fills at close[t+1] (no lookahead;
    realistic next-bar fill).
  • Position sizing: equal-weight at current total portfolio value (compounds wins).
  • Cash earns 0% (conservative — could plug in T-bill rate later).
  • Sharpe ratio uses excess return over the live FRED 3-month T-bill rate.

Survivorship-bias caveat: the universe is today's mega-cap list, so it implicitly
excludes companies that fell out of the top tier. Backtest results are therefore
optimistic versus a fully point-in-time-correct universe.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Optional

from constants import get_risk_free_rate


# Currently >$200B market cap (approximate, curated mid-2025).
# Kept fixed so the strategy is deterministic — universe changes are versioned.
STRATEGY_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO",
    "LLY", "V", "JPM", "WMT", "MA", "XOM", "ORCL", "COST",
    "JNJ", "NFLX", "HD", "PG", "BAC", "ABBV", "CVX", "KO",
    "MRK", "AMD", "TMO", "PEP", "CSCO", "ADBE", "CRM", "ACN",
    "MCD", "ABT", "LIN", "NOW", "IBM", "QCOM", "DIS", "CAT",
]

STRATEGY_VERSION = "drawdown_rebound_v1"
STRATEGY_NAME    = "Mega-Cap Drawdown Rebound"

DEFAULT_PARAMS = {
    "entry_pct":         0.20,    # 20% off ATH triggers buy
    "exit_pct":          0.03,    # within 3% of ATH triggers sell
    "max_positions":     10,
    "starting_capital":  100_000.0,
    "min_history_days":  60,      # don't buy if we haven't seen at least this much history
}


def run_backtest(
    close_df: pd.DataFrame,
    benchmark_close: Optional[pd.Series] = None,
    entry_pct:        float = DEFAULT_PARAMS["entry_pct"],
    exit_pct:         float = DEFAULT_PARAMS["exit_pct"],
    max_positions:    int   = DEFAULT_PARAMS["max_positions"],
    starting_capital: float = DEFAULT_PARAMS["starting_capital"],
    min_history_days: int   = DEFAULT_PARAMS["min_history_days"],
) -> dict:
    """
    Run the drawdown-rebound strategy on a wide DataFrame of close prices.

    close_df: DataFrame indexed by Date with one column per ticker (Close prices).
              Missing values allowed (will be skipped per ticker-day).
    benchmark_close: optional Series (Date-indexed) for buy-and-hold comparison.

    Returns dict with: trades, equity_curve, metrics, open_positions, benchmark_curve.
    """
    if close_df.empty:
        return {"trades": [], "equity_curve": [], "metrics": {},
                "open_positions": [], "benchmark_curve": []}

    close_df = close_df.sort_index().copy()
    # Yesterday's running ATH per ticker (shift(1) avoids lookahead on signal day)
    ath_df = close_df.shift(1).cummax()

    entry_threshold = ath_df * (1.0 - entry_pct)
    exit_threshold  = ath_df * (1.0 - exit_pct)

    # Per-ticker availability mask: True once we've seen min_history_days of data
    available = close_df.notna().cumsum() >= min_history_days

    cash         = float(starting_capital)
    holdings     = {}   # ticker -> {"shares", "buy_price", "buy_date", "buy_ath"}
    trade_log    = []
    equity_curve = []

    # Queues populated at end of day t, executed at close[t+1] — eliminates the
    # lookahead from "see today's close, trade at today's close".
    queued_exits   = []   # list of tickers to sell tomorrow
    queued_entries = []   # list of {"ticker", "signal_date", "signal_ath", "signal_dd"}

    dates    = list(close_df.index)
    n_days   = len(dates)
    date_pos = {d: i for i, d in enumerate(dates)}   # for trading-day hold count

    for i, date in enumerate(dates):
        today_row = close_df.iloc[i]

        # ── 1. Execute yesterday's queued trades at today's close ──────────────
        # SELLS first so cash is freed before BUYS.
        for t in queued_exits:
            if t not in holdings:
                continue
            px = today_row.get(t)
            if pd.isna(px):
                continue   # no quote → keep position, retry next day
            pos      = holdings.pop(t)
            proceeds = pos["shares"] * px
            cash    += proceeds
            buy_idx  = date_pos.get(pd.Timestamp(pos["buy_date"]))
            hold_td  = (i - buy_idx) if buy_idx is not None else 0
            trade_log.append({
                "ticker":      t,
                "side":        "SELL",
                "date":        date.strftime("%Y-%m-%d"),
                "price":       float(px),
                "shares":      float(pos["shares"]),
                "proceeds":    float(proceeds),
                "buy_date":    pos["buy_date"],
                "buy_price":   float(pos["buy_price"]),
                "return_pct":  float((px / pos["buy_price"] - 1) * 100),
                "hold_days":   int(hold_td),   # trading days between buy and sell
            })
        queued_exits = []

        # BUYS — sized to current portfolio value so wins compound.
        if queued_entries:
            positions_value = sum(
                h["shares"] * today_row.get(t, h["buy_price"])
                for t, h in holdings.items()
            )
            total_value = cash + positions_value
            slot_size   = total_value / max_positions
            for entry in queued_entries:
                if len(holdings) >= max_positions:
                    break
                if cash < slot_size:
                    break
                t  = entry["ticker"]
                if t in holdings:
                    continue
                px = today_row.get(t)
                if pd.isna(px):
                    continue
                shares = slot_size / px
                cash  -= slot_size
                holdings[t] = {
                    "shares":    shares,
                    "buy_price": float(px),
                    "buy_date":  date.strftime("%Y-%m-%d"),
                    "buy_ath":   entry["signal_ath"],
                }
                trade_log.append({
                    "ticker":     t,
                    "side":       "BUY",
                    "date":       holdings[t]["buy_date"],
                    "price":      float(px),
                    "shares":     float(shares),
                    "cost":       float(slot_size),
                    "ath_at_buy": entry["signal_ath"],
                    "drawdown":   entry["signal_dd"],
                })
        queued_entries = []

        # ── 2. Mark-to-market equity at today's close (after trade fills) ──────
        positions_value = sum(
            h["shares"] * today_row.get(t, h["buy_price"])
            for t, h in holdings.items()
        )
        equity_curve.append({
            "date":         date.strftime("%Y-%m-%d"),
            "equity":       float(cash + positions_value),
            "cash":         float(cash),
            "n_positions":  len(holdings),
        })

        # ── 3. Compute today's signals → queue for tomorrow's open close ───────
        # No lookahead: ATH is shift(1).cummax() so it only reflects past data,
        # and execution happens on the NEXT bar's close.
        if i + 1 >= n_days:
            continue   # last day — no tomorrow to execute on

        # Exit signals (for currently held positions)
        for t in holdings:
            sig_px    = today_row.get(t)
            ex_thresh = exit_threshold.at[date, t]
            if pd.notna(sig_px) and pd.notna(ex_thresh) and sig_px >= ex_thresh:
                queued_exits.append(t)

        # Entry signals (for tickers we don't already hold and not already queued)
        if len(holdings) + len(queued_entries) < max_positions:
            queued_set = {e["ticker"] for e in queued_entries}
            for t in close_df.columns:
                if t in holdings or t in queued_set:
                    continue
                if not available.at[date, t]:
                    continue
                sig_px    = today_row.get(t)
                en_thresh = entry_threshold.at[date, t]
                if pd.isna(sig_px) or pd.isna(en_thresh):
                    continue
                if sig_px <= en_thresh:
                    sig_ath = float(ath_df.at[date, t]) if pd.notna(ath_df.at[date, t]) else float(sig_px)
                    queued_entries.append({
                        "ticker":      t,
                        "signal_date": date,
                        "signal_ath":  sig_ath,
                        "signal_dd":   float((sig_px / sig_ath - 1) * 100),
                    })
                    if len(holdings) + len(queued_entries) >= max_positions:
                        break

    # ── Open positions snapshot at end of backtest ────────────────────────────
    last_row = close_df.iloc[-1]
    open_positions = []
    for t, h in holdings.items():
        cur_px = last_row.get(t, h["buy_price"])
        open_positions.append({
            "ticker":         t,
            "buy_date":       h["buy_date"],
            "buy_price":      float(h["buy_price"]),
            "current_price":  float(cur_px),
            "unrealized_pct": float((cur_px / h["buy_price"] - 1) * 100),
            "ath_at_buy":     float(h["buy_ath"]),
        })

    # ── Metrics ───────────────────────────────────────────────────────────────
    eq_series = pd.Series([e["equity"] for e in equity_curve],
                          index=pd.to_datetime([e["date"] for e in equity_curve]))
    metrics = _compute_metrics(eq_series, starting_capital, trade_log)

    # ── Benchmark equity curve (SPY buy-and-hold, same dollar) ────────────────
    # Both strategy and benchmark are normalised to the OVERLAP window so the
    # alpha comparison is apples-to-apples (no spurious lead/lag from missing
    # benchmark data at the start of the strategy period).
    benchmark_curve = []
    if benchmark_close is not None and not benchmark_close.empty:
        bm = benchmark_close.reindex(close_df.index).ffill()
        first_valid = bm.first_valid_index()
        if first_valid is not None:
            bm    = bm.loc[first_valid:].dropna()
            bm_eq = (bm / bm.iloc[0]) * starting_capital
            benchmark_curve = [
                {"date": d.strftime("%Y-%m-%d"), "equity": float(v)}
                for d, v in bm_eq.items()
            ]
            bench_total_ret = float((bm.iloc[-1] / bm.iloc[0] - 1) * 100)
            metrics["benchmark_total_return_pct"] = bench_total_ret
            # Strategy return computed over the SAME overlap window so the alpha
            # delta the UI shows ("Strategy X% vs SPY Y% → Alpha (X-Y)%") is honest.
            if first_valid in eq_series.index:
                strat_at_overlap_start = float(eq_series.loc[first_valid])
            else:
                strat_at_overlap_start = float(eq_series.asof(first_valid)) \
                                         if eq_series.index.min() <= first_valid \
                                         else float(eq_series.iloc[0])
            strat_at_end = float(eq_series.iloc[-1])
            if strat_at_overlap_start > 0:
                metrics["total_return_overlap_pct"] = float(
                    (strat_at_end / strat_at_overlap_start - 1) * 100
                )

    return {
        "trades":          trade_log,
        "equity_curve":    equity_curve,
        "metrics":         metrics,
        "open_positions":  open_positions,
        "benchmark_curve": benchmark_curve,
        "params": {
            "entry_pct":         entry_pct,
            "exit_pct":          exit_pct,
            "max_positions":     max_positions,
            "starting_capital":  starting_capital,
            "min_history_days":  min_history_days,
            "universe_size":     len(close_df.columns),
            "universe":          list(close_df.columns),
            "version":           STRATEGY_VERSION,
        },
    }


def _compute_metrics(eq: pd.Series, starting_capital: float, trade_log: list) -> dict:
    if eq.empty:
        return {}
    total_ret      = float(eq.iloc[-1] / starting_capital - 1) * 100
    daily_ret      = eq.pct_change().dropna()
    n_years        = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-6)
    cagr           = float((eq.iloc[-1] / starting_capital) ** (1 / n_years) - 1) * 100
    ann_vol        = float(daily_ret.std() * np.sqrt(252)) * 100 if len(daily_ret) > 1 else 0.0
    # True Sharpe: excess return over the risk-free rate (annualised T-bill yield)
    # divided by annualised vol. Was previously raw return/vol — overstated by
    # ~0.3-0.5 vs the canonical formula and inconsistent with portfolio_builder
    # / portfolio_analysis (which use this same correct formula).
    rfr_daily      = get_risk_free_rate() / 252
    excess_daily   = daily_ret - rfr_daily
    sharpe         = float((excess_daily.mean() * 252) / (excess_daily.std() * np.sqrt(252))) \
                     if excess_daily.std() > 0 else 0.0
    running_max    = eq.cummax()
    drawdown       = (eq / running_max - 1) * 100
    max_dd         = float(drawdown.min())

    closed_sells   = [t for t in trade_log if t["side"] == "SELL"]
    n_round_trips  = len(closed_sells)
    win_rate       = (sum(1 for t in closed_sells if t["return_pct"] > 0) / n_round_trips * 100) \
                     if n_round_trips > 0 else 0.0
    avg_trade_ret  = float(np.mean([t["return_pct"] for t in closed_sells])) if closed_sells else 0.0
    avg_hold_days  = float(np.mean([t["hold_days"]  for t in closed_sells])) if closed_sells else 0.0

    return {
        "total_return_pct":   round(total_ret, 2),
        "cagr_pct":           round(cagr, 2),
        "ann_vol_pct":        round(ann_vol, 2),
        "sharpe":             round(sharpe, 2),
        "max_drawdown_pct":   round(max_dd, 2),
        "n_round_trips":      n_round_trips,
        "n_buys":             sum(1 for t in trade_log if t["side"] == "BUY"),
        "win_rate_pct":       round(win_rate, 1),
        "avg_trade_pct":      round(avg_trade_ret, 2),
        "avg_hold_days":      round(avg_hold_days, 0),
        "final_equity":       round(float(eq.iloc[-1]), 2),
        "n_trading_days":     int(len(eq)),
        "start_date":         eq.index[0].strftime("%Y-%m-%d"),
        "end_date":           eq.index[-1].strftime("%Y-%m-%d"),
    }
