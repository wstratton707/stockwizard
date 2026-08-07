"""
horserace.py — Walk-forward comparison of portfolio construction methods.

Run:  python horserace.py [--years 10] [--lookback 756] [--hold 63]

Why
---
The literature's consensus is conditional on universe and period. Nobody can say
which allocator wins on OUR screened universe, so this measures it rather than
arguing about it: same candidate set, same rebalance schedule, same transaction
cost, only the weighting scheme changes.

Methods raced
-------------
    Current       what the live builder ships — CAPM mu + factor alpha,
                  three-anchor blend at the default risk level
    Max Sharpe    the tangency portfolio, unblended
    GMV           constrained global minimum variance
    ERC           equal risk contribution
    HRP           hierarchical risk parity
    1/N           equal weight

WHAT THIS DOES AND DOES NOT ANSWER
----------------------------------
The candidate set is chosen ONCE, today, from current factor rankings, then held
fixed for the whole walk-forward. That is deliberate: every method races on an
identical universe, so the comparison between allocators is clean.

It also means the absolute return levels are optimistic — today's rankings
encode information that was not available at the start of the window, which is
look-ahead bias in the SCREEN. Read the columns against each other, never as a
forecast of what the product would have returned.

To answer "does our screen add value?" you would need point-in-time factor
scores, which precompute does not retain. Different project.
"""

import argparse
import os
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from dotenv import load_dotenv
load_dotenv()

from allocators import (ALLOCATORS, DEFAULT_MAX_SECTOR_WEIGHT,
                        DEFAULT_MAX_WEIGHT, risk_ladder)
from constants import get_risk_free_rate
from portfolio_analysis import (compute_betas, factor_tilted_expected_returns,
                                optimise_portfolio)
from portfolio_data import fetch_portfolio_prices_cached, get_sharpe_rankings

# One-way cost applied to traded notional at each rebalance. Matches the rate
# the backtest already assumes, so the numbers here are comparable to the ones
# the product reports.
COST_PER_SIDE = 0.0010
MAX_TICKERS   = 18
TOP_N_PER_SECTOR = 2
BENCHMARK     = "SPY"


def log(msg):
    # portfolio_data logs with emoji; a cp1252 Windows console raises on them and
    # would kill the run inside a data fetch. Degrade the character, not the job.
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(str(msg).encode(enc, "replace").decode(enc), flush=True)


# ── universe ──────────────────────────────────────────────────────────────────

def build_universe(api_key):
    """Top-N per sector by composite factor score, plus benchmarks — the same
    shape of candidate set the live builder assembles at the default risk level.
    """
    rankings = get_sharpe_rankings(api_key)
    if not rankings:
        raise SystemExit("No precomputed rankings available — cannot build the "
                         "universe the product actually uses. Run precompute.py first.")

    from collections import defaultdict
    by_sector = defaultdict(list)
    for ticker, data in rankings.items():
        if ticker == "_meta":
            continue
        by_sector[data.get("sector", "Unknown")].append(
            (ticker, data.get("score", 0.0)))

    picks = {"SPY", "QQQ"}
    for _sector, names in by_sector.items():
        for t, _s in sorted(names, key=lambda x: x[1], reverse=True)[:TOP_N_PER_SECTOR]:
            picks.add(t)

    ranked = sorted(picks,
                    key=lambda t: float("inf") if t in ("SPY", "QQQ")
                    else rankings.get(t, {}).get("score", 0.0),
                    reverse=True)[:MAX_TICKERS]
    sector_map = {t: rankings.get(t, {}).get("sector", "Unknown") for t in ranked}
    scores     = {t: rankings.get(t, {}).get("score", 0.5) for t in ranked}
    return ranked, sector_map, scores


# ── the two mu-dependent methods, wrapped to the allocator interface ──────────

def _current(returns_df, sector_map=None, scores=None, **_kw):
    """The live builder's default: CAPM + factor alpha, three-anchor blend at
    risk level 5 (the "Balanced" preset most users land on).
    """
    mkt = returns_df["SPY"] if "SPY" in returns_df.columns else None
    mu  = None
    if mkt is not None:
        betas = compute_betas(returns_df, mkt)
        mu    = factor_tilted_expected_returns(betas, scores or {})
    res = optimise_portfolio(returns_df, risk_tolerance=5, sector_map=sector_map,
                             max_sector_weight=DEFAULT_MAX_SECTOR_WEIGHT,
                             max_weight=DEFAULT_MAX_WEIGHT, expected_returns=mu)
    return res["recommended"]


def _max_sharpe(returns_df, sector_map=None, scores=None, **_kw):
    """The tangency portfolio on its own, so the blend and the tangency point
    can be told apart in the results."""
    mkt = returns_df["SPY"] if "SPY" in returns_df.columns else None
    mu  = None
    if mkt is not None:
        betas = compute_betas(returns_df, mkt)
        mu    = factor_tilted_expected_returns(betas, scores or {})
    res = optimise_portfolio(returns_df, risk_tolerance=5, sector_map=sector_map,
                             max_sector_weight=DEFAULT_MAX_SECTOR_WEIGHT,
                             max_weight=DEFAULT_MAX_WEIGHT, expected_returns=mu)
    return res["max_sharpe"]


METHODS = {
    "Current":    _current,
    "Max Sharpe": _max_sharpe,
    "GMV":        ALLOCATORS["GMV"],
    "ERC":        ALLOCATORS["ERC"],
    "HRP":        ALLOCATORS["HRP"],
    "1/N":        ALLOCATORS["1/N"],
}


def _ladder_methods():
    """Race the proposed risk ladder at each end and the middle.

    The claim being tested is monotonicity: turning the slider up must actually
    buy more volatility and a deeper drawdown, otherwise the control is
    decorative — which is precisely the failure the ladder replaces.
    """
    def _at(level):
        def _fn(returns_df, sector_map=None, scores=None, **_kw):
            return risk_ladder(returns_df, risk_tolerance=level,
                               sector_map=sector_map,
                               max_weight=DEFAULT_MAX_WEIGHT,
                               max_sector_weight=DEFAULT_MAX_SECTOR_WEIGHT)
        return _fn
    return {f"Ladder@{lv}": _at(lv) for lv in (1, 3, 5, 7, 10)}


# ── walk-forward engine ───────────────────────────────────────────────────────

def walk_forward(returns_df, sector_map, scores, lookback, hold):
    """Roll through history: fit on the trailing `lookback` days, hold the
    resulting weights for `hold` days, charge cost on what actually traded.

    Weights drift with prices during the hold — that drift IS the thing the
    rebalance corrects, so turnover is measured against the drifted weights, not
    against the previous target. Measuring it against the target would overstate
    trading for every method.
    """
    dates   = returns_df.index
    starts  = list(range(lookback, len(dates) - 1, hold))
    results = {m: {"value": [1.0], "dates": [dates[lookback]], "turnover": []}
               for m in METHODS}

    prev_held = {m: None for m in METHODS}

    for step, i in enumerate(starts, 1):
        train = returns_df.iloc[i - lookback:i]
        fwd   = returns_df.iloc[i:i + hold]
        if fwd.empty:
            break
        log(f"  rebalance {step}/{len(starts)}  fit->{dates[i].date()}  "
            f"hold {len(fwd)}d")

        for name, fn in METHODS.items():
            try:
                w = fn(train, sector_map=sector_map, scores=scores)
            except Exception as e:                       # one method failing must
                log(f"    ! {name} failed: {e}")         # not abandon the race
                w = {c: 1.0 / len(train.columns) for c in train.columns}

            cols  = list(returns_df.columns)
            w_vec = np.array([w.get(c, 0.0) for c in cols])
            s = w_vec.sum()
            w_vec = w_vec / s if s > 0 else np.ones(len(cols)) / len(cols)

            held = prev_held[name]
            turn = 1.0 if held is None else float(np.abs(w_vec - held).sum()) / 2.0
            results[name]["turnover"].append(turn)

            value = results[name]["value"][-1] * (1.0 - turn * 2 * COST_PER_SIDE)

            # Compound the period day by day so the weights drift the way a real
            # unrebalanced book does, instead of being silently reset daily.
            growth = (1.0 + fwd.values).cumprod(axis=0)
            path   = growth @ w_vec
            for d_i, d in enumerate(fwd.index):
                results[name]["value"].append(value * path[d_i])
                results[name]["dates"].append(d)

            end_growth  = growth[-1]
            drifted     = w_vec * end_growth
            prev_held[name] = drifted / drifted.sum() if drifted.sum() > 0 else w_vec

    return results


# ── reporting ─────────────────────────────────────────────────────────────────

def metrics(values, dates, turnovers):
    s = pd.Series(values, index=pd.DatetimeIndex(dates)).groupby(level=0).last()
    r = s.pct_change().dropna()
    years = (s.index[-1] - s.index[0]).days / 365.25
    total = s.iloc[-1] / s.iloc[0] - 1
    cagr  = (s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
    vol   = r.std() * np.sqrt(252)
    rf    = get_risk_free_rate()
    sharpe = (cagr - rf) / vol if vol > 0 else np.nan
    dd    = (s / s.cummax() - 1).min()
    return {"Total": total * 100, "CAGR": cagr * 100, "Vol": vol * 100,
            "Sharpe": sharpe, "MaxDD": dd * 100,
            "Turn/rb": float(np.mean(turnovers)) * 100 if turnovers else 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=10, help="price history to pull")
    ap.add_argument("--lookback", type=int, default=756, help="training days (~3y)")
    ap.add_argument("--hold", type=int, default=63, help="rebalance interval (~1q)")
    ap.add_argument("--ladder", action="store_true",
                    help="race the proposed risk ladder at levels 1/3/5/7/10 instead")
    args = ap.parse_args()
    if args.ladder:
        METHODS.clear()
        METHODS.update(_ladder_methods())

    api_key = os.getenv("POLYGON_API_KEY", "")
    log("Building universe from precomputed factor rankings...")
    tickers, sector_map, scores = build_universe(api_key)
    log(f"  {len(tickers)} names: {', '.join(tickers)}")

    log(f"Fetching {args.years}y of prices...")
    _pd_, close_df, returns_df, failed = fetch_portfolio_prices_cached(
        tickers, period_years=args.years, api_key=api_key, log=log)
    if failed:
        log(f"  dropped: {failed}")
    if BENCHMARK not in returns_df.columns:
        log(f"  ! {BENCHMARK} missing — the CAPM methods will fall back to "
            f"historical means")

    need = args.lookback + args.hold
    if len(returns_df) < need:
        raise SystemExit(f"only {len(returns_df)} trading days; need {need}")

    log(f"Walk-forward: {len(returns_df)} days, {args.lookback}d train, "
        f"{args.hold}d hold, {COST_PER_SIDE*1e4:.0f}bp/side")
    results = walk_forward(returns_df, sector_map, scores, args.lookback, args.hold)

    rows = {name: metrics(r["value"], r["dates"], r["turnover"])
            for name, r in results.items()}
    table = pd.DataFrame(rows).T
    if not args.ladder:
        table = table.sort_values("Sharpe", ascending=False)

    _any = next(iter(results))
    span = f"{results[_any]['dates'][0].date()} to {results[_any]['dates'][-1].date()}"
    print("\n" + "=" * 78)
    print(f"WALK-FORWARD HORSE RACE   {span}   net of {COST_PER_SIDE*1e4:.0f}bp/side")
    print("=" * 78)
    print(table.to_string(float_format=lambda v: f"{v:8.2f}"))
    print("=" * 78)
    print("Total/CAGR/Vol/MaxDD in %, Turn/rb = one-way turnover per rebalance, %")
    print("Universe fixed from today's factor scores — compare columns to each")
    print("other, not to a live forecast. See module docstring.")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       f"horserace_{datetime.now():%Y%m%d}.csv")
    table.to_csv(out)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
