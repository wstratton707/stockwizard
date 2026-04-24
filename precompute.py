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
from constants import get_risk_free_rate

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
    url     = f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start_s}/{end_s}"
    params  = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": POLYGON_API_KEY}

    # 4 total attempts: initial + 3 retries at 30s / 60s / 90s
    retry_waits = [30, 60, 90]
    for attempt in range(len(retry_waits) + 1):
        try:
            r = requests.get(url, params=params, timeout=20)
        except Exception as e:
            print(f"   ⚠ {ticker}: {e}")
            return ticker, None

        if r.status_code == 200:
            results = r.json().get("results", [])
            if len(results) >= 60:
                df = pd.DataFrame(results)
                df["Date"]  = pd.to_datetime(df["t"], unit="ms")
                prices = df.set_index("Date")["c"]
                return ticker, prices
            return ticker, None  # valid response but not enough data

        if r.status_code == 429:
            if attempt < len(retry_waits):
                wait = retry_waits[attempt]
                print(f"   ⏳ {ticker} rate limited — waiting {wait}s "
                      f"(attempt {attempt + 1}/{len(retry_waits) + 1})...")
                time.sleep(wait)
            else:
                print(f"   ✗ {ticker} — rate limited after {len(retry_waits) + 1} attempts")
                return ticker, None
        else:
            print(f"   ⚠ {ticker} HTTP {r.status_code}")
            return ticker, None

    return ticker, None


# ── Factor computations ────────────────────────────────────────────────────────

def _compute_factors(prices: pd.Series) -> dict | None:
    """Compute Sharpe + momentum factors from a price series."""
    returns = prices.pct_change().dropna()
    if len(returns) < 60:
        return None

    ann_ret = float(returns.mean() * 252)
    ann_vol = float(returns.std() * np.sqrt(252))
    sharpe  = (ann_ret - get_risk_free_rate()) / ann_vol if ann_vol > 0 else -999.0

    # Momentum: price today vs. N trading days ago
    mom_6m = float((prices.iloc[-1] / prices.iloc[max(0, len(prices)-132)] - 1) * 100) \
             if len(prices) >= 132 else 0.0
    mom_3m = float((prices.iloc[-1] / prices.iloc[max(0, len(prices)-69)]  - 1) * 100) \
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

def compute_rankings(sector_filter: list[str] | None = None) -> dict:
    """Fetch prices and compute multi-factor scores for the full universe.

    sector_filter: if given, only fetch tickers in these sectors. Tickers from
    other sectors are pulled from today's existing cache (from an earlier
    workflow run) so the final combined-score normalisation still spans the
    full universe.
    """

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

    # Resume: pull anything already computed today (partial checkpoint or prior
    # workflow batch) and skip re-fetching it.
    raw: dict = {}
    existing = cache_get(CACHE_KEY) or {}
    if isinstance(existing, dict):
        for k, v in existing.items():
            if k != "_meta" and isinstance(v, dict) and "sharpe" in v:
                raw[k] = v
        if raw:
            print(f"Resuming: {len(raw)} tickers already cached for {TODAY}.\n")

    # Decide which tickers this run is responsible for
    if sector_filter:
        allowed = {t for s in sector_filter for t in SECTOR_UNIVERSE.get(s, [])}
        # Always include the small extras (bond ETFs + index proxies) so a
        # single-sector test run still produces a valid combined score.
        allowed |= set(BOND_ETFS.values()) | {"SPY", "QQQ", "GLD", "TLT", "IEF"}
        run_tickers = [t for t in universe if t in allowed and t not in raw]
        print(f"Sector filter: {sector_filter} → {len(run_tickers)} tickers to fetch")
    else:
        run_tickers = [t for t in universe if t not in raw]

    if not run_tickers:
        print("Nothing to fetch — everything is already cached.")
        return _add_combined_scores(raw)

    print(f"Computing multi-factor rankings for {len(run_tickers)} tickers...")
    print(f"Factors: Sharpe (50%) · 6M Momentum (30%) · 3M Momentum (20%)\n")

    done = 0
    CHECKPOINT_EVERY = 50  # write partial results to Supabase every N tickers

    with ThreadPoolExecutor(max_workers=1) as ex:
        futures = {ex.submit(_fetch_year, t): t for t in run_tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            done  += 1
            try:
                _, prices = future.result()
                if prices is not None:
                    factors = _compute_factors(prices)
                    if factors:
                        raw[ticker] = {"ticker": ticker, "sector": universe[ticker], **factors}
                        print(f"  [{done:>3}/{len(run_tickers)}] ✓ {ticker:<6}  "
                              f"Sharpe={factors['sharpe']:+.2f}  "
                              f"6M={factors['mom_6m']:+.1f}%  "
                              f"3M={factors['mom_3m']:+.1f}%")
                    else:
                        print(f"  [{done:>3}/{len(run_tickers)}] ⚠ {ticker} — insufficient data")
                else:
                    print(f"  [{done:>3}/{len(run_tickers)}] ✗ {ticker} — fetch failed")
            except Exception as e:
                print(f"  [{done:>3}/{len(run_tickers)}] ✗ {ticker} — {e}")

            # Checkpoint: save partial results so a killed run isn't wasted
            if done % CHECKPOINT_EVERY == 0 and raw:
                partial = _add_combined_scores(dict(raw))
                partial["_meta"] = {"computed_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                                    "partial": True, "tickers_done": done, "tickers_total": len(run_tickers)}
                cache_set(CACHE_KEY, partial, ttl_hours=26)
                print(f"  💾 Checkpoint saved — {len(partial) - 1} tickers ranked so far")

    # Add normalised combined score
    return _add_combined_scores(raw)


# ── Entry point ────────────────────────────────────────────────────────────────

def _parse_sectors_arg() -> list[str] | None:
    """Parse --sectors=A,B,C from sys.argv. Returns None if not present."""
    for arg in sys.argv[1:]:
        if arg.startswith("--sectors="):
            return [s.strip() for s in arg.split("=", 1)[1].split(",") if s.strip()]
    return None


def main():
    if not POLYGON_API_KEY:
        print("ERROR: POLYGON_API_KEY not set.")
        sys.exit(1)

    sector_filter = _parse_sectors_arg()

    print(f"StockWizard Daily Precompute — {TODAY}")
    print("=" * 55)

    # When a sector filter is given, this is one half of a split run — skip the
    # "already cached today" guard so the second batch always runs.
    existing = cache_get(CACHE_KEY)
    if existing and "--force" not in sys.argv and not sector_filter:
        meta = existing.get("_meta", {}) if isinstance(existing, dict) else {}
        if not meta.get("partial"):
            print(f"Rankings already cached for {TODAY} ({len(existing)} tickers).")
            print("Pass --force to recompute.")
            return

    t0       = time.time()
    rankings = compute_rankings(sector_filter=sector_filter)

    if not rankings:
        print("ERROR: No rankings computed — check API key and connectivity.")
        sys.exit(1)

    # Stamp when rankings were computed so the UI can show freshness.
    # If this was a sector-filtered run that didn't cover the whole universe,
    # flag it as partial so the next workflow batch knows to merge in.
    full_universe_size = sum(len(v) for v in SECTOR_UNIVERSE.values()) + len(BOND_ETFS) + 5
    is_partial = sector_filter is not None and len(rankings) < full_universe_size * 0.9
    rankings["_meta"] = {
        "computed_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "partial": is_partial,
        "tickers_done": len(rankings),
    }
    ok      = cache_set(CACHE_KEY, rankings, ttl_hours=26)
    elapsed = time.time() - t0

    print(f"\n{'='*55}")
    print(f"Done in {elapsed:.0f}s — {len(rankings)} tickers ranked")
    print(f"Supabase write: {'✓ success' if ok else '✗ failed (check credentials)'}")

    # Pre-warm portfolio price cache so first user run is instant.
    # Skip on partial batches — top-per-sector picks would be incomplete.
    if not is_partial:
        warm_portfolio_cache(rankings)
    else:
        print("Skipping portfolio cache warm — partial batch, will run after final batch.")

    # Top 15 by combined score
    top = sorted(rankings.values(), key=lambda x: x.get("score", 0), reverse=True)[:15]
    print(f"\nTop 15 by combined score (Sharpe + Momentum):")
    for r in top:
        print(f"  {r['ticker']:<6}  {r['sector']:<28}  "
              f"Score={r.get('score',0):.3f}  "
              f"Sharpe={r['sharpe']:+.2f}  "
              f"6M={r['mom_6m']:+.1f}%")


def warm_portfolio_cache(rankings: dict):
    """
    Pre-fetch 2-year price data for the default top-18 portfolio so the
    portfolio builder is instant even on the very first user run of the day.
    """
    from collections import defaultdict
    from portfolio_data import fetch_portfolio_prices_cached

    print("\nUpdating portfolio price cache (bootstrap once, append daily)...")

    # Top 6 per sector covers every sidebar preference combo (risk tolerance 10
    # asks for 6/sector; lower tolerances ask for fewer). Warming all 6 means
    # the Portfolio Builder gets per-ticker cache hits regardless of user prefs.
    # 11 sectors × 6 + SPY + QQQ ≈ 68 tickers.
    sector_groups: dict = defaultdict(list)
    for ticker, data in rankings.items():
        sector = data.get("sector", "Unknown")
        if sector.startswith("Bond"):
            continue
        sector_groups[sector].append((ticker, data.get("score", 0)))

    candidates = ["SPY", "QQQ"]
    for sector, ticker_scores in sector_groups.items():
        ranked = sorted(ticker_scores, key=lambda x: x[1], reverse=True)
        for t, _ in ranked[:6]:
            if t not in candidates:
                candidates.append(t)

    print(f"Pre-fetching 2-year prices for {len(candidates)} tickers "
          f"(top 6 per sector)...")

    try:
        _, close_df, _, failed = fetch_portfolio_prices_cached(
            candidates, period_years=2, api_key=POLYGON_API_KEY, log=print
        )
        print(f"Portfolio cache warmed — {len(close_df.columns)} tickers ready")
        if failed:
            print(f"Failed: {failed}")
    except Exception as e:
        print(f"Portfolio cache warm failed: {e}")


if __name__ == "__main__":
    main()
