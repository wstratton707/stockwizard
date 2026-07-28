"""Walk-forward evaluation for the portfolio model.

Why this exists
---------------
Every tunable number in the builder was chosen by judgement: the +/-2% factor
alpha cap, the 30/30/20/20 factor weights, the 1/4 sector floor and ceiling, the
2%/25% weight bounds. None of them had been measured. This turns them into
questions with answers.

It also exists because a claim already failed here. Ledoit-Wolf shrinkage was
added on the reasoning that it would stabilise weights; measured, it didn't
(5.9% turnover sample vs 6.2% shrunk). That is exactly the kind of plausible,
wrong belief that ships silently without a harness.

What it can and cannot tell you
-------------------------------
CAN: weight stability / turnover, realised out-of-sample return and volatility,
drawdown, and whether a parameter change moves any of them.

CANNOT: whether the strategy beats the market. The universe is composed of names
that exist *today*, so any long-horizon return figure is survivorship-inflated.
Point-in-time constituents are a paid product. Treat return numbers here as
*relative* comparisons between configurations run over the identical window and
universe — never as evidence of edge.

Usage
-----
    .venv/Scripts/python.exe scripts/evaluate_portfolio_model.py
    .venv/Scripts/python.exe scripts/evaluate_portfolio_model.py --compare alpha
    .venv/Scripts/python.exe scripts/evaluate_portfolio_model.py --years 4 --hold 63
"""
import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

import portfolio_analysis as pa
from market_data import get_bars_batch
from portfolio_data import SECTOR_UNIVERSE

TRADING_DAYS = 252


def load_panel(n_per_sector=6, years=5):
    """Close prices for a sector-spread book, plus the SPY column."""
    sm, tks = {}, []
    for sector, names in SECTOR_UNIVERSE.items():
        for t in names[:n_per_sector]:
            sm[t] = sector
            tks.append(t)
    sm["SPY"] = "Market"
    tks.append("SPY")

    end   = datetime.today()
    start = end - timedelta(days=int(years * 365.25))
    frames = {}
    for i in range(0, len(tks), 120):
        frames.update(get_bars_batch(tks[i:i + 120], start.strftime("%Y-%m-%d"),
                                     end.strftime("%Y-%m-%d"), "day"))
    close = pd.DataFrame({t: d.set_index("Date")["Close"]
                          for t, d in frames.items()}).ffill().dropna()
    sm = {t: s for t, s in sm.items() if t in close.columns}
    return close, sm


def _factor_scores(returns_window, sector_map):
    """Recompute the selection composite from a price window ONLY.

    Deliberately not read from the precompute cache: that cache reflects today,
    and using it at an earlier evaluation date would leak future information into
    the ranking — the classic lookahead bias that makes a bad model look good.
    Quality needs fundamentals we can't get point-in-time, so it's held neutral
    for every name, which keeps the comparison fair across configurations.
    """
    from collections import defaultdict
    ann = TRADING_DAYS
    facts = {}
    for t in returns_window.columns:
        r = returns_window[t].dropna()
        if len(r) < 60:
            continue
        vol = float(r.std() * np.sqrt(ann))
        if vol <= 0:
            continue
        shp = ((r.mean() * ann) - pa.get_risk_free_rate()) / vol
        win = r.iloc[-ann:-21] if len(r) > 84 else r
        mom = float((1 + win).prod() - 1) / vol      # vol-adjusted, as in precompute
        facts[t] = {"mom": mom, "vol": vol, "shp": shp}
    if not facts:
        return {}

    def pct(vals):
        n = len(vals)
        if n <= 1:
            return [0.5] * n
        order = sorted(range(n), key=lambda i: vals[i])
        out = [0.0] * n
        for rank, idx in enumerate(order):
            out[idx] = rank / (n - 1)
        return out

    ts = list(facts)
    groups = defaultdict(list)
    for t in ts:
        groups[sector_map.get(t, "Unknown")].append(t)

    g_mom = {}
    for g, members in groups.items():
        if len(members) < 3:
            for t in members:
                g_mom[t] = 0.5
        else:
            for t, r in zip(members, pct([facts[t]["mom"] for t in members])):
                g_mom[t] = r

    a_shp = dict(zip(ts, pct([facts[t]["shp"] for t in ts])))
    a_vol = dict(zip(ts, pct([facts[t]["vol"] for t in ts])))
    return {t: 0.30 * g_mom[t] + 0.30 * 0.5 + 0.20 * (1 - a_vol[t]) + 0.20 * a_shp[t]
            for t in ts}


def run_config(close, sector_map, *, alpha_max, shrink, hold, n_hold=18,
               risk_tolerance=5, warmup=TRADING_DAYS * 2):
    """Walk forward: rank and optimise on data up to T, hold to T+`hold`, roll."""
    rets = close.pct_change().dropna()
    dates = rets.index
    starts = list(range(warmup, len(dates) - hold, hold))
    if not starts:
        return None

    _orig = pa.shrunk_covariance
    if not shrink:
        pa.shrunk_covariance = lambda df, annualise=True: (
            df.dropna().cov().values * (TRADING_DAYS if annualise else 1))

    prev_w, turnovers, seg_returns = None, [], []
    try:
        for s in starts:
            train = rets.iloc[:s]
            scores = _factor_scores(train, sector_map)
            if not scores:
                continue
            # Top-N by score, benchmark pinned — mirrors the builder's trim step.
            picks = [t for t, _ in sorted(scores.items(), key=lambda x: -x[1])][:n_hold]
            if "SPY" in train.columns and "SPY" not in picks:
                picks = picks[:-1] + ["SPY"]
            sub = train[picks]

            betas = pa.compute_betas(sub, train["SPY"]) if "SPY" in train.columns else \
                    {t: 1.0 for t in picks}
            mu = pa.factor_tilted_expected_returns(
                betas, {t: scores[t] for t in picks}, alpha_max=alpha_max)
            res = pa.optimise_portfolio(sub, risk_tolerance=risk_tolerance,
                                        sector_map=sector_map, expected_returns=mu)
            w = res["recommended"]

            if prev_w is not None:
                keys = set(w) | set(prev_w)
                turnovers.append(sum(abs(w.get(k, 0) - prev_w.get(k, 0)) for k in keys) / 2)
            prev_w = w

            fwd = rets.iloc[s:s + hold]
            wv  = np.array([w.get(c, 0.0) for c in fwd.columns])
            seg_returns.append((1 + (fwd.values @ wv)).prod() - 1)
    finally:
        pa.shrunk_covariance = _orig

    if not seg_returns:
        return None
    seg = np.array(seg_returns)
    per_year = TRADING_DAYS / hold
    cum = float((1 + seg).prod())
    yrs = len(seg) / per_year
    equity = np.cumprod(1 + seg)
    peak = np.maximum.accumulate(equity)
    return {
        "periods":   len(seg),
        "ann_return": (cum ** (1 / yrs) - 1) * 100 if yrs > 0 else 0.0,
        "ann_vol":    float(seg.std() * np.sqrt(per_year)) * 100,
        "worst":      float(seg.min()) * 100,
        "max_dd":     float(((equity - peak) / peak).min()) * 100,
        "hit_rate":   float((seg > 0).mean()) * 100,
        "turnover":   float(np.mean(turnovers)) * 100 if turnovers else 0.0,
    }


def _print_table(rows):
    hdr = f'{"config":26}{"ann ret":>9}{"vol":>8}{"maxDD":>8}{"worst":>8}{"hit":>7}{"turnover":>10}'
    print(hdr)
    print("-" * len(hdr))
    for label, r in rows:
        if r is None:
            print(f"{label:26}   (insufficient history)")
            continue
        print(f'{label:26}{r["ann_return"]:>8.1f}%{r["ann_vol"]:>7.1f}%'
              f'{r["max_dd"]:>7.1f}%{r["worst"]:>7.1f}%{r["hit_rate"]:>6.0f}%'
              f'{r["turnover"]:>9.1f}%')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=5)
    ap.add_argument("--hold", type=int, default=63, help="trading days held between re-optimisations")
    ap.add_argument("--compare", choices=["alpha", "shrink", "all"], default="all")
    args = ap.parse_args()

    print(f"Loading panel ({args.years}y)...")
    close, sm = load_panel(years=args.years)
    print(f"  {len(close.columns)} tickers, {len(close)} trading days, "
          f"{len(set(sm.values()))} sectors")
    print(f"  walk-forward: re-optimise every {args.hold} trading days "
          f"(~{args.hold/21:.0f} months)\n")

    rows = []
    if args.compare in ("shrink", "all"):
        for shrink in (False, True):
            label = f'cov={"Ledoit-Wolf" if shrink else "sample"}'
            rows.append((label, run_config(close, sm, alpha_max=0.02,
                                           shrink=shrink, hold=args.hold)))
    if args.compare in ("alpha", "all"):
        for a in (0.0, 0.01, 0.02, 0.04, 0.08):
            rows.append((f"factor alpha ±{a*100:.0f}%",
                         run_config(close, sm, alpha_max=a, shrink=True, hold=args.hold)))

    _print_table(rows)
    print("\nReturn figures are survivorship-inflated (the universe is today's "
          "survivors) — compare configurations against each other, never treat "
          "an absolute number as evidence of edge.")


if __name__ == "__main__":
    main()
