"""
validate_metrics.py — Data-correctness validation (complements validate.py)

validate.py proves the portfolio BACKTEST engine matches known benchmarks.
This suite guards the classes of bug that validate.py can't see — the ones found
by hand-rendering the reports:

  A. Per-stock metric sanity   — the enriched single-stock metrics that feed the
     stock report are in plausible ranges (catches a -568%/+4038% style blow-up
     at the source).
  B. Cross-source price agreement — yfinance and Polygon agree on recent closes
     within tolerance (catches a silently-wrong data source).
  C. Report display consistency — the built portfolio deck reflects the user's
     actual inputs and never prints an absurd percentage (catches the ×100
     double-scaling and the $10,000-default key-mismatch bugs).

Usage:
    python validate_metrics.py            # exits non-zero if any check fails
"""

import os
import re
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
KEY   = os.getenv("POLYGON_API_KEY", "")
END   = date.today().strftime("%Y-%m-%d")
START = (date.today() - timedelta(days=730)).strftime("%Y-%m-%d")

_PASS, _FAIL = [], []


def check(cond, label, detail=""):
    (_PASS if cond else _FAIL).append(label)
    mark = "✓" if cond else "✗"
    line = f"  {mark} {label}"
    if detail:
        line += f"  ({detail})"
    print(line)
    return bool(cond)


def _quiet(*a, **k):
    pass


# ── A. Per-stock metric sanity ──────────────────────────────────────────────────
# A spread of profiles so the ranges are actually exercised: mega-cap growth,
# defensive staple, long-duration bond ETF (low/negative return), high-vol name.
BASKET = ["AAPL", "KO", "TLT", "NVDA"]


def part_a_stock_metrics():
    print("\nA. Per-stock metric sanity")
    print("=" * 60)
    from data import fetch_stock_data

    for tk in BASKET:
        try:
            df = fetch_stock_data(tk, benchmark_tickers=("SPY",), api_key=KEY, log=_quiet,
                                  start_override=START, end_override=END, bar_size="day")
        except Exception as e:
            check(False, f"{tk}: fetch", str(e)[:60])
            continue
        if df is None or len(df) < 30:
            check(False, f"{tk}: enough history", f"{0 if df is None else len(df)} rows")
            continue

        latest  = df.iloc[-1]
        ret      = df["Daily_Return"].dropna()
        ann_ret  = ret.mean() * 252 * 100
        ann_vol  = ret.std() * np.sqrt(252) * 100
        max_dd   = df["Drawdown_60d"].min() * 100 if "Drawdown_60d" in df.columns else 0.0
        rsi      = latest.get("RSI14")

        check(latest["Close"] > 0,                 f"{tk}: price > 0", f"${latest['Close']:.2f}")
        check(-90 < ann_ret < 300,                 f"{tk}: ann return plausible", f"{ann_ret:+.0f}%")
        check(0 < ann_vol < 150,                   f"{tk}: ann vol plausible",    f"{ann_vol:.0f}%")
        check(-100 < max_dd <= 0.01,               f"{tk}: drawdown in (-100, 0]", f"{max_dd:.0f}%")
        check(rsi is None or pd.isna(rsi) or 0 <= float(rsi) <= 100,
              f"{tk}: RSI in [0,100]", f"{rsi}")
        check((df['Date'].diff().dropna() > pd.Timedelta(0)).all(),
              f"{tk}: dates strictly increasing")


# ── B. Cross-source price agreement ─────────────────────────────────────────────
def part_b_cross_source():
    print("\nB. Cross-source price agreement (yfinance vs Polygon)")
    print("=" * 60)
    if not KEY:
        check(True, "skipped — no POLYGON_API_KEY")
        return
    from market_data import _yahoo_bars, _polygon_bars

    for tk in ["AAPL", "MSFT"]:
        y = _yahoo_bars(tk, START, END, "day")
        p = _polygon_bars(tk, START, END, "day", KEY)
        if y is None or p is None or y.empty or p.empty:
            check(True, f"{tk}: skipped — a source returned no data")
            continue
        # Compare the last common date's close (adjusted vs adjusted) within 2%.
        ym = y.set_index(y["Date"].dt.normalize())["Close"]
        pm = p.set_index(p["Date"].dt.normalize())["Close"]
        common = ym.index.intersection(pm.index)
        if len(common) == 0:
            check(True, f"{tk}: skipped — no overlapping dates")
            continue
        d   = common.max()
        yv, pv = float(ym.loc[d]), float(pm.loc[d])
        diff_pct = abs(yv - pv) / pv * 100 if pv else 99.0
        check(diff_pct <= 2.0, f"{tk}: yf vs Polygon close within 2%",
              f"yf ${yv:.2f} / poly ${pv:.2f} = {diff_pct:.1f}% on {d.date()}")


# ── C. Report display consistency (portfolio deck) ──────────────────────────────
def part_c_report_consistency():
    print("\nC. Portfolio report display consistency")
    print("=" * 60)
    try:
        from pptx import Presentation
    except ImportError:
        check(True, "skipped — python-pptx not installed")
        return
    from portfolio_data import fetch_portfolio_prices, get_ticker_info
    from portfolio_analysis import (compute_stock_metrics, compute_correlation_matrix,
        optimise_portfolio, backtest_portfolio, compute_backtest_metrics,
        run_portfolio_monte_carlo, compute_diversification_score)
    from pptx_builder import build_portfolio_pptx
    import io

    CAP, MO = 50_000, 1_000           # deliberately NOT the deck's old $10k default
    tickers = ["AAPL", "MSFT", "JNJ", "JPM", "XOM", "PG"]
    sector_map = {"AAPL": "Technology", "MSFT": "Technology", "JNJ": "Healthcare",
                  "JPM": "Financials", "XOM": "Energy", "PG": "Consumer Staples"}
    try:
        _, close_df, returns_df, _ = fetch_portfolio_prices(tickers + ["SPY"], period_years=2,
                                                            api_key=KEY, log=_quiet)
        opt_ret = returns_df[[t for t in tickers if t in returns_df.columns]]
        prefs = {"horizon": "10 years", "starting_capital": CAP, "monthly_contribution": MO,
                 "risk_tolerance": 5, "max_per_stock": 0.30, "target_value": 200_000}
        weights = optimise_portfolio(opt_ret, risk_tolerance=5, sector_map=sector_map,
                                     max_weight=0.30)["recommended"]
        bt  = backtest_portfolio(close_df, weights, CAP, MO)
        mcd, mcs, miles = run_portfolio_monte_carlo(opt_ret, weights, CAP, MO,
                                                    forecast_years=10, n_simulations=400, log=_quiet)
        buf = build_portfolio_pptx(
            preferences=prefs, final_weights=weights,
            stock_metrics=compute_stock_metrics(opt_ret),
            backtest_df=bt, backtest_metrics=compute_backtest_metrics(bt, CAP),
            mc_sim_df=mcd, mc_summary=mcs, milestones=miles,
            corr_matrix=compute_correlation_matrix(opt_ret),
            diversification_score=compute_diversification_score(weights, opt_ret),
            ticker_info={t: get_ticker_info(t, KEY) for t in weights})
    except Exception as e:
        check(False, "build portfolio deck", str(e)[:80])
        return

    # Pull every run of text out of the deck.
    prs   = Presentation(io.BytesIO(buf.getvalue()))
    texts = [sh.text_frame.text for sl in prs.slides for sh in sl.shapes if sh.has_text_frame]
    blob  = "\n".join(texts)

    # 1. The deck must reflect the real capital, not the old $10,000 default.
    #    With max_weight 0.30 no holding reaches $50k, and Final Value/milestones
    #    are far larger, so "$50,000" appears ONLY via the investment-amount field —
    #    its presence is a clean signal the deck read starting_capital (not the default).
    check(f"${CAP:,.0f}" in blob,
          f"deck shows the user's capital (${CAP:,.0f}), not the $10k default")

    # 2. No absurd percentages anywhere (the ×100 double-scaling bug printed >2000%).
    pcts = [float(m.replace(",", "")) for m in re.findall(r"([+-]?\d[\d,]*\.?\d*)%", blob)]
    worst = max((abs(p) for p in pcts), default=0.0)
    check(worst <= 300.0, "no absurd percentage in deck text (≤300%)", f"max |%| seen = {worst:.0f}%")


def main():
    print("QuantWizard Data-Correctness Validation")
    print(f"Window: {START} → {END}")
    part_a_stock_metrics()
    part_b_cross_source()
    part_c_report_consistency()

    print("\n" + "=" * 60)
    print(f"Results: {len(_PASS)} passed, {len(_FAIL)} failed")
    if _FAIL:
        print("FAILED:")
        for f in _FAIL:
            print(f"   ✗ {f}")
        sys.exit(1)
    print("✓ All data-correctness checks passed.")


if __name__ == "__main__":
    main()
