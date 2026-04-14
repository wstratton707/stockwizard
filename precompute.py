"""
precompute.py — Daily S&P 500 multi-factor ranking precomputation

Fetches 1-year price history for every ticker in the StockWizard universe
(~330 stocks + bond ETFs), computes a multi-factor score combining:
  - Sharpe ratio        (risk-adjusted return)
  - 6-month momentum   (trend strength)
  - 3-month momentum   (recent acceleration)

Rankings are stored in Supabase so the portfolio builder can consider ALL
stocks without doing live API calls at build time.

Run manually:
    python precompute.py
    python precompute.py --force   (recompute even if cached today)

Schedule on Railway (cron job):
    Command: python precompute.py
    Schedule: 0 14 * * 1-5   (9 AM Eastern = 14:00 UTC, weekdays only)

Runtime: ~10-20 min on Polygon free tier, ~2-3 min on paid tier.
"""

import os
import sys
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()

POLYGON_BASE    = "https://api.polygon.io"
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")

from portfolio_data import SECTOR_UNIVERSE, BOND_ETFS
from database import cache_get, cache_set
from constants import RISK_FREE_RATE

TODAY     = datetime.today().strftime("%Y-%m-%d")
CACHE_KEY = f"sharpe_rankings_{TODAY}"


# ── Price fetch ────────────────────────────────────────────────────────────────

def _fetch_year(ticker: str) -> tuple:
    """Fetch ~1 year of daily closes. Returns (ticker, pd.Series | None)."""
    import requests

    end     = datetime.today()
    start   = end - timedelta(days=400)   # buffer for weekends/holidays
    end_s   = end.strftime("%Y-%m-%d")
    start_s = start.strftime("%Y-%m-%d")

    try:
        r = requests.get(
            f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start_s}/{end_s}",
            params={"adjusted": "true", "sort": "asc", "limit": 50000,
                    "apiKey": POLYGON_API_KEY},
            timeout=20,
        )
        if r.status_code == 429:
            print(f"   ⏳ {ticker} rate limited — waiting 30s...")
            time.sleep(30)
            r = requests.get(
                f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start_s}/{end_s}",
                params={"adjusted": "true", "sort": "asc", "limit": 50000,
                        "apiKey": POLYGON_API_KEY},
                timeout=20,
            )
        if r.status_code == 200:
            results = r.json().get("results", [])
            if len(results) >= 60:
                df = pd.DataFrame(results)
                df["Date"]  = pd.to_datetime(df["t"], unit="ms")
                prices = df.set_index("Date")["c"]
                return ticker, prices
    except Exception as e:
        print(f"   ⚠ {ticker}: {e}")
    return ticker, None


# ── Factor computations ────────────────────────────────────────────────────────

def _compute_factors(prices: pd.Series) -> dict | None:
    """Compute Sharpe + momentum factors from a price series."""
    returns = prices.pct_change().dropna()
    if len(returns) < 60:
        return None

    ann_ret = float(returns.mean() * 252)
    ann_vol = float(returns.std() * np.sqrt(252))
    sharpe  = (ann_ret - RISK_FREE_RATE) / ann_vol if ann_vol > 0 else -999.0

    # Momentum: skip most recent week (avoids short-term reversal noise)
    mom_6m = float((prices.iloc[-6]  / prices.iloc[max(0, len(prices)-132)] - 1) * 100) \
             if len(prices) >= 132 else 0.0
    mom_3m = float((prices.iloc[-6]  / prices.iloc[max(0, len(prices)-69)]  - 1) * 100) \
             if len(prices) >= 69  else 0.0

    return {
        "sharpe":     round(sharpe,  4),
        "ann_return": round(ann_ret * 100, 2),
        "ann_vol":    round(ann_vol * 100, 2),
        "mom_6m":     round(mom_6m, 2),
        "mom_3m":     round(mom_3m, 2),
    }


def _add_combined_scores(rankings: dict) -> dict:
    """
    Normalise each factor to [0,1] across the universe then compute a
    weighted combined score per ticker.

    Weights:
      50%  Sharpe ratio      (risk-adjusted return — most important)
      30%  6-month momentum  (trend confirmation)
      20%  3-month momentum  (recent acceleration)
    """
    if not rankings:
        return rankings

    def _norm(values: list) -> list:
        lo, hi = min(values), max(values)
        rng = hi - lo
        return [(v - lo) / rng if rng > 0 else 0.5 for v in values]

    tickers  = list(rankings.keys())
    sharpes  = [rankings[t]["sharpe"]  for t in tickers]
    mom_6ms  = [rankings[t]["mom_6m"]  for t in tickers]
    mom_3ms  = [rankings[t]["mom_3m"]  for t in tickers]

    n_sharpe = _norm(sharpes)
    n_6m     = _norm(mom_6ms)
    n_3m     = _norm(mom_3ms)

    for i, t in enumerate(tickers):
        score = 0.50 * n_sharpe[i] + 0.30 * n_6m[i] + 0.20 * n_3m[i]
        rankings[t]["score"] = round(score, 4)

    return rankings


# ── Main computation ───────────────────────────────────────────────────────────

def compute_rankings() -> dict:
    """Fetch prices and compute multi-factor scores for the full universe."""

    # Build ticker → sector map from entire universe
    universe: dict[str, str] = {}
    for sector, tickers in SECTOR_UNIVERSE.items():
        for t in tickers:
            universe[t] = sector
    for category, etf in BOND_ETFS.items():
        universe[etf] = f"Bond-{category}"
    for t, s in {"SPY": "Market", "QQQ": "Market", "GLD": "Commodities",
                 "TLT": "Government", "IEF": "Government"}.items():
        universe.setdefault(t, s)

    all_tickers = list(universe.keys())
    print(f"Computing multi-factor rankings for {len(all_tickers)} tickers...")
    print(f"Factors: Sharpe (50%) · 6M Momentum (30%) · 3M Momentum (20%)\n")

    raw: dict = {}
    done = 0

    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(_fetch_year, t): t for t in all_tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            done  += 1
            try:
                _, prices = future.result()
                if prices is not None:
                    factors = _compute_factors(prices)
                    if factors:
                        raw[ticker] = {"ticker": ticker, "sector": universe[ticker], **factors}
                        print(f"  [{done:>3}/{len(all_tickers)}] ✓ {ticker:<6}  "
                              f"Sharpe={factors['sharpe']:+.2f}  "
                              f"6M={factors['mom_6m']:+.1f}%  "
                              f"3M={factors['mom_3m']:+.1f}%")
                    else:
                        print(f"  [{done:>3}/{len(all_tickers)}] ⚠ {ticker} — insufficient data")
                else:
                    print(f"  [{done:>3}/{len(all_tickers)}] ✗ {ticker} — fetch failed")
            except Exception as e:
                print(f"  [{done:>3}/{len(all_tickers)}] ✗ {ticker} — {e}")

    # Add normalised combined score
    return _add_combined_scores(raw)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    if not POLYGON_API_KEY:
        print("ERROR: POLYGON_API_KEY not set.")
        sys.exit(1)

    print(f"StockWizard Daily Precompute — {TODAY}")
    print("=" * 55)

    existing = cache_get(CACHE_KEY)
    if existing and "--force" not in sys.argv:
        print(f"Rankings already cached for {TODAY} ({len(existing)} tickers).")
        print("Pass --force to recompute.")
        return

    t0       = time.time()
    rankings = compute_rankings()

    if not rankings:
        print("ERROR: No rankings computed — check API key and connectivity.")
        sys.exit(1)

    ok      = cache_set(CACHE_KEY, rankings, ttl_hours=26)
    elapsed = time.time() - t0

    print(f"\n{'='*55}")
    print(f"Done in {elapsed:.0f}s — {len(rankings)} tickers ranked")
    print(f"Supabase write: {'✓ success' if ok else '✗ failed (check credentials)'}")

    # Top 15 by combined score
    top = sorted(rankings.values(), key=lambda x: x.get("score", 0), reverse=True)[:15]
    print(f"\nTop 15 by combined score (Sharpe + Momentum):")
    for r in top:
        print(f"  {r['ticker']:<6}  {r['sector']:<28}  "
              f"Score={r.get('score',0):.3f}  "
              f"Sharpe={r['sharpe']:+.2f}  "
              f"6M={r['mom_6m']:+.1f}%")


if __name__ == "__main__":
    main()
