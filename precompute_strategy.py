"""
precompute_strategy.py — Daily strategy backtest precomputation

Fetches 5y of price data for the strategy universe + benchmark, runs the
drawdown-rebound backtest, and writes the result to Supabase under a versioned
cache key. The Streamlit Strategy tab reads from this cache so page loads are
instant.

Run manually:
    python precompute_strategy.py
    python precompute_strategy.py --force   (recompute even if cached today)

Schedule alongside the existing precompute jobs.
"""

import os
import sys
import time
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Force UTF-8 stdout so Unicode glyphs (used in progress output) don't crash
# on Windows cp1252 consoles. Linux/macOS already default to UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv()

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")

from strategy import STRATEGY_UNIVERSE, STRATEGY_VERSION, STRATEGY_NAME, run_backtest
from portfolio_data import _fetch_ohlcv
from database import cache_get, cache_set


CACHE_KEY  = f"strategy_{STRATEGY_VERSION}"
LOOKBACK_YEARS = 5
BENCHMARK = "SPY"


def _build_close_df(tickers: list, years: int) -> pd.DataFrame:
    """Fetch close prices for each ticker via the cached OHLCV fetcher and
    return a wide DataFrame indexed by date."""
    end   = datetime.today()
    start = end - timedelta(days=years * 365 + 30)
    end_s, start_s = end.strftime("%Y-%m-%d"), start.strftime("%Y-%m-%d")

    closes = {}
    for i, t in enumerate(tickers, 1):
        print(f"  [{i:>2}/{len(tickers)}] fetching {t}...", end=" ", flush=True)
        try:
            df = _fetch_ohlcv(t, start_s, end_s, POLYGON_API_KEY, log=lambda m: None)
            if df is not None and len(df) > 60:
                closes[t] = df.set_index("Date")["Close"].rename(t)
                print(f"✓ {len(df)} days")
            else:
                print("✗ insufficient data")
        except Exception as e:
            print(f"✗ {e}")
        time.sleep(0.2)

    if not closes:
        return pd.DataFrame()
    out = pd.DataFrame(closes).sort_index()
    return out


def main():
    if not POLYGON_API_KEY:
        print("ERROR: POLYGON_API_KEY not set.")
        sys.exit(1)

    print(f"StockWizard Strategy Precompute — {STRATEGY_NAME}")
    print(f"Cache key: {CACHE_KEY}")
    print("=" * 60)

    if "--force" not in sys.argv:
        existing = cache_get(CACHE_KEY)
        if existing and existing.get("metrics"):
            computed = existing.get("computed_at", "?")
            age_hr   = "?"
            try:
                ct  = datetime.fromisoformat(computed.replace("Z", ""))
                age_hr = f"{(datetime.utcnow() - ct).total_seconds() / 3600:.1f}h"
            except Exception:
                pass
            print(f"Cached result exists (computed {computed}, age {age_hr}).")
            print("Pass --force to recompute.")
            return

    t0 = time.time()
    print(f"Fetching {LOOKBACK_YEARS}y of close prices for "
          f"{len(STRATEGY_UNIVERSE)} mega-cap tickers + {BENCHMARK}...\n")
    close_df = _build_close_df(STRATEGY_UNIVERSE, LOOKBACK_YEARS)
    if close_df.empty:
        print("ERROR: no price data fetched.")
        sys.exit(1)

    print(f"\nFetching benchmark {BENCHMARK}...", end=" ", flush=True)
    bm_df = None
    try:
        bm_df = _fetch_ohlcv(
            BENCHMARK,
            (datetime.today() - timedelta(days=LOOKBACK_YEARS * 365 + 30)).strftime("%Y-%m-%d"),
            datetime.today().strftime("%Y-%m-%d"),
            POLYGON_API_KEY, log=lambda m: None,
        )
        print(f"✓ {len(bm_df)} days" if bm_df is not None else "✗")
    except Exception as e:
        print(f"✗ {e}")
    bm_close = bm_df.set_index("Date")["Close"] if bm_df is not None else None

    print(f"\nRunning backtest on {len(close_df.columns)} tickers, "
          f"{len(close_df)} trading days...")
    result = run_backtest(close_df, benchmark_close=bm_close)
    result["computed_at"]    = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    result["strategy_name"]  = STRATEGY_NAME
    result["strategy_version"] = STRATEGY_VERSION

    m = result["metrics"]
    print("\n" + "=" * 60)
    print(f"Period:        {m['start_date']} → {m['end_date']} ({m['n_trading_days']} days)")
    print(f"Total return:  {m['total_return_pct']:+.2f}%")
    print(f"CAGR:          {m['cagr_pct']:+.2f}%")
    print(f"Ann. vol:      {m['ann_vol_pct']:.2f}%")
    print(f"Sharpe:        {m['sharpe']:.2f}")
    print(f"Max drawdown:  {m['max_drawdown_pct']:.2f}%")
    print(f"Round trips:   {m['n_round_trips']} (win rate {m['win_rate_pct']:.0f}%)")
    print(f"Avg trade:     {m['avg_trade_pct']:+.2f}%  ({m['avg_hold_days']:.0f} day hold)")
    print(f"Open positions: {len(result['open_positions'])}")
    if "benchmark_total_return_pct" in m:
        print(f"\n{BENCHMARK} buy-hold: {m['benchmark_total_return_pct']:+.2f}%")
        print(f"vs Strategy:    {m['total_return_pct'] - m['benchmark_total_return_pct']:+.2f}% alpha")

    ok = cache_set(CACHE_KEY, result, ttl_hours=48)
    print(f"\nSupabase write: {'✓ success' if ok else '✗ failed'}")
    print(f"Total time: {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
