"""
precompute.py — Daily S&P 500 multi-factor ranking precomputation

Fetches 1-year price history for every ticker in the QuantWizard universe
(~330 stocks + bond ETFs), computes a multi-factor score combining:
  - Sharpe ratio        (risk-adjusted return)
  - 6-month momentum   (trend strength)
  - 3-month momentum   (recent acceleration)

Rankings are stored in Supabase so the portfolio builder can consider ALL
stocks without doing live API calls at build time.

Run manually:
    python precompute.py
    python precompute.py --force   (recompute even if cached today)

Scheduled by GitHub Actions, NOT by the host — the Streamlit Cloud app has no
cron. Two workflows split the universe so each fits the job limit:
    .github/workflows/precompute.yml     AM batch, cron '0 14 * * 1-5'
    .github/workflows/precompute-pm.yml  PM batch (the remaining sectors)
Both accept a manual `workflow_dispatch` from the Actions tab.

Runtime: minutes, since prices are batched via market_data.get_bars_batch
(~16 tickers/sec). The old serial per-ticker Polygon loop took 10-20 min for 341.
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

# This module's progress output is full of ✓ ⚠ ✗ ⏳ 💾 → characters. A Windows
# console defaults to cp1252, which cannot encode any of them, so a *print* —
# not the computation — raised UnicodeEncodeError and killed the whole run
# partway through the factor loop. Force UTF-8 and degrade rather than raise.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

POLYGON_BASE    = "https://api.polygon.io"
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")

from portfolio_data import SECTOR_UNIVERSE, BOND_ETFS
from database import cache_get, cache_set
from constants import get_risk_free_rate

TODAY     = datetime.today().strftime("%Y-%m-%d")
CACHE_KEY = f"sharpe_rankings_{TODAY}"

# Rankings must outlive a weekend. portfolio_data.get_sharpe_rankings walks back
# up to 5 days looking for the most recent set — precisely so that Sunday, and
# Monday before the 14:00-UTC run, still get a full ranked universe instead of
# silently falling through to the slow live-candidate path. A 26h TTL made that
# walk-back impossible: Friday's key expired Saturday afternoon, so from then
# until Monday's run there was nothing to find. The date is in the cache key, so
# a longer TTL can never be mistaken for today's rankings — the UI reads
# `_meta.computed_at` and shows the age.
RANKINGS_TTL_HOURS = 7 * 24

# Widen the universe beyond the hand-typed SECTOR_UNIVERSE. Practical only because
# the fetch is batched now (~16 tickers/sec): 4,000 names is ~4 min, where the old
# serial Polygon loop took 10-20 min for 341. Set False to pin to the static list.
USE_DYNAMIC_UNIVERSE = True
MAX_UNIVERSE         = 4000


# ── Price fetch ────────────────────────────────────────────────────────────────

_FETCH_BATCH = 120     # tickers per batched download


def _fetch_year_batch(tickers: list) -> dict:
    """{ticker: close Series} for ~1 year of daily bars, fetched in batches.

    Replaces a per-ticker Polygon loop that ran at ThreadPoolExecutor(max_workers=1)
    — serial by necessity, because the free tier rate-limits hard — and took
    10-20 minutes for 341 tickers. market_data.get_bars_batch issues one threaded
    yfinance download per batch and benchmarks at ~16 tickers/sec, i.e. ~20s for
    the same universe and ~3 min for 3,000. This is what makes a large,
    dynamically-built universe practical at all.

    Falls back to Polygon per-ticker only for names the batch missed.
    """
    from market_data import get_bars, get_bars_batch

    end     = datetime.today()
    start   = end - timedelta(days=400)   # buffer for weekends/holidays
    end_s   = end.strftime("%Y-%m-%d")
    start_s = start.strftime("%Y-%m-%d")

    out: dict = {}
    for i in range(0, len(tickers), _FETCH_BATCH):
        chunk = tickers[i:i + _FETCH_BATCH]
        try:
            got = get_bars_batch(chunk, start_s, end_s, "day")
        except Exception as e:
            print(f"   ⚠ batch {i//_FETCH_BATCH + 1} failed ({e}) — per-ticker fallback")
            got = {}
        for t, df in got.items():
            if df is not None and len(df) >= 60:
                out[t] = df.set_index("Date")      # full frame — volume feeds the
                                                   # liquidity screen downstream

        # Anything the batch didn't return: one retry each via the router
        # (yfinance → Polygon). A delisted ticker legitimately returns nothing.
        # Only worth doing for a small miss list — on a 4,000-name universe the
        # misses are mostly genuinely dead symbols, not transient failures.
        missed = [t for t in chunk if t not in out]
        if len(missed) <= 25:
            for t in missed:
                try:
                    df = get_bars(t, start_s, end_s, interval="day",
                                  polygon_key=POLYGON_API_KEY)
                    if df is not None and len(df) >= 60:
                        out[t] = df.set_index("Date")
                except Exception:
                    pass
        print(f"   batch {i//_FETCH_BATCH + 1}: {len(chunk)} requested, "
              f"{sum(1 for t in chunk if t in out)} with data")

    return out


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
    # All three skip the most recent ~month. That's the academic momentum
    # construction (short-term reversal makes the last month a *negative*
    # predictor), and previously only the 12-month leg did it — so 12-1 ended one
    # month ago while 6m and 3m ended today. Averaging series with different
    # endpoints mixes a momentum signal with a reversal signal.
    SKIP = 21
    def _mom(lookback):
        if len(prices) < lookback + SKIP:
            return None
        return float((prices.iloc[-SKIP] / prices.iloc[-(lookback + SKIP)] - 1) * 100)

    mom_12m = _mom(252)
    mom_6m  = _mom(126)
    mom_3m  = _mom(63)
    # Fall back down the ladder rather than to 0.0 — a zero is not "no momentum",
    # it's a neutral reading that a young name hasn't earned.
    mom_6m  = mom_6m  if mom_6m  is not None else (mom_3m or 0.0)
    mom_12m = mom_12m if mom_12m is not None else (mom_6m or 0.0)
    mom_3m  = mom_3m  if mom_3m  is not None else 0.0

    # Volatility-adjusted: raw momentum rewards whatever moved most, which is
    # usually just the highest-beta name. Dividing by realised vol makes it a
    # return-per-unit-risk ranking and is markedly more stable period to period.
    _volpct = ann_vol * 100 if ann_vol > 0 else None
    mom_12m_adj = (mom_12m / _volpct) if _volpct else 0.0

    return {
        "sharpe":      round(sharpe,  4),
        "ann_return":  round(ann_ret * 100, 2),
        "ann_vol":     round(ann_vol * 100, 2),
        "mom_12m":     round(mom_12m, 2),
        "mom_6m":      round(mom_6m, 2),
        "mom_3m":      round(mom_3m, 2),
        "mom_12m_adj": round(mom_12m_adj, 4),
    }


def _fetch_fundamentals(ticker: str):
    """Per-ticker fundamental components from SEC EDGAR, cached 30 days.

    Returns a dict of raw metrics (each may be None) or None for non-filers
    (ETFs) and fetch misses — every use downstream treats missing as neutral,
    never as a penalty.

    This used to return a single 0-1 "quality" scalar, which meant
    compute_fundamentals was called and then almost everything it computed was
    thrown away. The scoring rework needs the components themselves: quality,
    value, growth and health are separate factors now, and each is
    percentile-ranked WITHIN its sector, which cannot be done to a pre-blended
    scalar. Same single EDGAR call, same 30-day cache economics (fundamentals
    change on quarterly filings); only the cache key changes (fund_, not
    quality_), so the first cron after this ships re-pays ~150s once to rebuild
    the cache and is then warm for a month.
    """
    # fund2, not fund: phase 3 added the cap-structure fields (shares, ni,
    # ebitda, debt, cash, fcf) to this dict, and a phase-1 "fund_" entry served
    # from cache would pin those to None for its whole 30-day TTL. Bumping the
    # key is the cache-versioning: old entries simply expire unread.
    _ck = f"fund2_{ticker.upper()}"
    try:
        hit = cache_get(_ck)
        if isinstance(hit, dict) and "c" in hit:
            # Stored as {"c": dict_or_None} so a cached "no data" result is
            # distinguishable from a cache miss and isn't re-fetched daily.
            return hit["c"]
    except Exception:
        pass

    _val = _compute_fundamentals(ticker)
    try:
        cache_set(_ck, {"c": _val}, ttl_hours=720)
    except Exception:
        pass
    return _val


def _compute_fundamentals(ticker: str):
    """Uncached fundamentals computation — see _fetch_fundamentals."""
    try:
        from data import fetch_sec_financials
        from analysis import compute_fundamentals
        fin = fetch_sec_financials(ticker, log=lambda *a, **k: None)
        if not fin:
            return None
        f = compute_fundamentals(fin)          # market_cap omitted — see `eps` note
        if not f.get("ok"):
            return None
        marg, ret_, grw = f.get("margins", {}), f.get("returns", {}), f.get("growth", {})
        lev,  q,   inc  = f.get("leverage", {}), f.get("quality", {}), f.get("income", {})
        # Cap-structure components. EDGAR already carries diluted share count,
        # D&A and capex, so the full valuation-ratio set costs nothing extra:
        # market cap and the yields are assembled at SCORING time from these
        # plus the day's price, so a 30-day-old cache entry never freezes a
        # 30-day-old P/E.
        from analysis import _fin_val
        _inc_f = fin.get("income_statement")
        _cf_f  = fin.get("cash_flow_statement")
        _bal   = f.get("balance", {})
        _oi    = f.get("income", {}).get("operating_income")
        _da    = _fin_val(_inc_f, "depreciation_amortization")
        _ocf   = f.get("cashflow", {}).get("operating")
        _capex = _fin_val(_cf_f, "capex")
        return {
            "net_margin": marg.get("net"),
            "roe":        ret_.get("roe"),
            "f_score":    q.get("f_score"),
            "rev_cagr":   grw.get("revenue_cagr"),
            "eps_cagr":   grw.get("eps_cagr"),
            "d2e":        lev.get("debt_to_equity"),
            "cur_ratio":  lev.get("current_ratio"),
            "eps":        inc.get("eps_diluted"),
            "shares":     _fin_val(_inc_f, "diluted_shares"),
            "ni":         f.get("income", {}).get("net_income"),
            "ebitda":     (_oi + _da) if (_oi is not None and _da is not None) else None,
            "debt":       _bal.get("long_term_debt"),
            "cash":       _bal.get("cash"),
            "fcf":        (_ocf - _capex) if (_ocf is not None and _capex is not None) else None,
        }
    except Exception:
        return None


def _quality_scalar(c):
    """Legacy 0-1 quality blend, now derived from the stored components so the
    `quality` field every existing consumer reads keeps its exact meaning."""
    if not c:
        return None
    bits = []
    if c.get("net_margin") is not None:
        bits.append(max(0.0, min(1.0, c["net_margin"] / 25.0)))          # 25%+ net margin -> 1
    if c.get("roe") is not None:
        bits.append(max(0.0, min(1.0, c["roe"] / 25.0)))                 # 25%+ ROE -> 1
    if c.get("rev_cagr") is not None:
        bits.append(max(0.0, min(1.0, (c["rev_cagr"] + 5) / 25.0)))      # -5%..20%
    if c.get("f_score") is not None:
        bits.append(c["f_score"] / 9.0)                                  # Piotroski 0-9
    return round(sum(bits) / len(bits), 4) if bits else None


def _fetch_analyst(ticker: str):
    """Wall-Street consensus mapped to [0,1], from Finnhub's free recommendation
    endpoint. Cached 7 days: opinions move faster than filings, slower than
    prices. Returns None (neutral, uncached) when there is no key, so a keyless
    local run can never poison the cron's cache with a week of empty results;
    None (cached) when coverage is under 3 analysts, because a rating from one
    or two desks is anecdote, not consensus.
    """
    _ck = f"analyst_{ticker.upper()}"
    try:
        hit = cache_get(_ck)
        if isinstance(hit, dict) and "a" in hit:
            return hit["a"]
    except Exception:
        pass

    try:
        from market_data import get_analyst_data, finnhub_key
        if not finnhub_key():
            return None                      # no key -> no fetch, no cache write
        rec = (get_analyst_data(ticker) or {}).get("recommendation")
    except Exception:
        rec = None

    val = None
    if rec:
        sb  = int(rec.get("strongBuy", 0) or 0)
        b   = int(rec.get("buy", 0) or 0)
        h   = int(rec.get("hold", 0) or 0)
        sl  = int(rec.get("sell", 0) or 0)
        ssl = int(rec.get("strongSell", 0) or 0)
        n   = sb + b + h + sl + ssl
        if n >= 3:
            raw = (2 * sb + b - sl - 2 * ssl) / (2.0 * n)     # [-1, 1]
            val = round((raw + 1.0) / 2.0, 4)
    try:
        cache_set(_ck, {"a": val}, ttl_hours=168)
    except Exception:
        pass
    # Finnhub free tier allows 60 calls/min and this stage runs single-threaded;
    # the pause only costs on cache misses, i.e. one cold pass per week.
    time.sleep(1.0)
    return val


def _add_combined_scores(rankings: dict) -> dict:
    """Delegate to factor_model.score_universe.

    The scoring logic used to live here as a 150-line closure over `rankings`,
    which meant it could only ever be exercised by running the whole cron. It
    now lives in factor_model.py as a pure function of the ranking dict, so the
    sector-relative behaviour, the missing/invalid/negative taxonomy, the
    financials branch and the eligible-universe cut are all unit-testable on
    synthetic input with no network.

    Kept as a wrapper because three call sites (resume, checkpoint, final) and
    the tests all reference it.
    """
    from factor_model import score_universe
    return score_universe(rankings)


# ── Main computation ───────────────────────────────────────────────────────────

def compute_rankings(sector_filter: list[str] | None = None) -> dict:
    """Fetch prices and compute multi-factor scores for the full universe.

    sector_filter: if given, only fetch tickers in these sectors. Tickers from
    other sectors are pulled from today's existing cache (from an earlier
    workflow run) so the final combined-score normalisation still spans the
    full universe.
    """

    # Build ticker → sector map. The hand-typed SECTOR_UNIVERSE supplies sector
    # labels (Polygon's SIC descriptions are too granular to bucket cleanly); the
    # dynamic list widens the cross-section beyond it. Names not in the static map
    # get "Unknown" and are still scored — percentile ranks over thousands of names
    # are far more informative than over 341.
    universe: dict[str, str] = {}
    for sector, tickers in SECTOR_UNIVERSE.items():
        for t in tickers:
            universe[t] = sector

    if USE_DYNAMIC_UNIVERSE and not sector_filter:
        from portfolio_data import build_dynamic_universe
        dyn = build_dynamic_universe(POLYGON_API_KEY, max_tickers=MAX_UNIVERSE)
        for t in dyn:
            universe.setdefault(t, "Unknown")
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
    from factor_model import WEIGHTS as _W, FACTOR_MODEL_VERSION as _V
    print("Factor model v%d — %s\n" % (
        _V, " / ".join(f"{k} {v}%" for k, v in _W.items())))

    CHECKPOINT_EVERY = 200  # write partial results to Supabase every N tickers

    # Prices first, in batches — this is now seconds rather than minutes.
    print("Fetching prices...")
    frames = _fetch_year_batch(run_tickers)
    dead = [t for t in run_tickers if t not in frames]
    if dead:
        # Delisted / renamed / bad symbols. Worth surfacing: on the old hardcoded
        # universe this silently shrank the effective candidate pool.
        print(f"\n   {len(dead)} ticker(s) returned no usable data "
              f"(delisted, renamed, or too short): {', '.join(dead[:20])}"
              f"{' …' if len(dead) > 20 else ''}")

    # Tradability screen — free, since volume arrived with the prices.
    from portfolio_data import apply_liquidity_screen
    frames    = apply_liquidity_screen(frames, log=print)
    price_map = {t: df["Close"] for t, df in frames.items()}

    print(f"\nComputing factors for {len(price_map)} tickers...")
    done = 0
    # Quality needs one SEC call per ticker, so it stays threaded — but at a real
    # pool size now that price fetching isn't the bottleneck.
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_fetch_fundamentals, t): t for t in price_map}
        fund_map = {}
        for future in as_completed(futures):
            t = futures[future]
            try:
                fund_map[t] = future.result()
            except Exception:
                fund_map[t] = None

    # Analyst consensus, single-threaded on purpose: Finnhub's free tier is
    # 60 calls/min, and with the 7-day cache all but one run a week is warm.
    analyst_map = {}
    try:
        for _t in price_map:
            analyst_map[_t] = _fetch_analyst(_t)
    except Exception:
        pass

    for ticker, prices in price_map.items():
        done += 1
        factors = _compute_factors(prices)
        if not factors:
            print(f"  [{done:>4}/{len(price_map)}] ⚠ {ticker} — insufficient data")
            continue
        # Additive fundamental components; None (neutral) for ETFs/non-filers
        # or any fetch miss — never blocks the run. The legacy scalar is kept so
        # everything that reads `quality` is unaffected by the component split.
        _fund = fund_map.get(ticker)
        factors["fund"]       = _fund
        factors["quality"]    = _quality_scalar(_fund)
        factors["analyst"]    = analyst_map.get(ticker)
        factors["last_price"] = round(float(prices.iloc[-1]), 4)
        raw[ticker] = {"ticker": ticker, "sector": universe.get(ticker, "Unknown"), **factors}
        if done % 25 == 0 or done <= 5:
            _q = factors["quality"]
            print(f"  [{done:>4}/{len(price_map)}] ✓ {ticker:<6}  "
                  f"Sharpe={factors['sharpe']:+.2f}  "
                  f"12M={factors['mom_12m']:+.1f}%  "
                  f"Q={('%.2f' % _q) if _q is not None else '—'}")

        # Checkpoint: save partial results so a killed run isn't wasted
        if done % CHECKPOINT_EVERY == 0 and raw:
            partial = _add_combined_scores(dict(raw))
            from factor_model import FACTOR_MODEL_VERSION, METHODOLOGY_NAME
            partial["_meta"] = {"computed_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                                "partial": True, "tickers_done": done,
                                "tickers_total": len(price_map),
                                "factor_model_version": FACTOR_MODEL_VERSION,
                                "methodology": METHODOLOGY_NAME,
                                "as_of_date": TODAY,
                                "data_timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z"}
            cache_set(CACHE_KEY, partial, ttl_hours=RANKINGS_TTL_HOURS)
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

    print(f"QuantWizard Daily Precompute — {TODAY}")
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
    from factor_model import FACTOR_MODEL_VERSION, METHODOLOGY_NAME
    rankings["_meta"] = {
        "computed_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "partial": is_partial,
        "tickers_done": len(rankings),
        # Versioning so the UI can never describe a model the cache does not
        # contain. The builder reads these and describes what is actually
        # present. Bump FACTOR_MODEL_VERSION on any factor, weight, direction
        # or sector-treatment change.
        "factor_model_version": FACTOR_MODEL_VERSION,
        "methodology": METHODOLOGY_NAME,
        "as_of_date": TODAY,
        "data_timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    ok      = cache_set(CACHE_KEY, rankings, ttl_hours=RANKINGS_TTL_HOURS)

    # Point-in-time archive: one retained snapshot per month, kept for years.
    #
    # The daily key expires after a week, so nothing survives long enough to
    # measure whether the factors predict anything. That gap is not academic —
    # an information-coefficient study run on the current fundamentals snapshot
    # applied to past dates produced a t-statistic of 4.6 for the fundamental
    # factors, which is not a signal, it is look-ahead: today's known margins
    # and growth used to rank stocks three years ago. The two factors that COULD
    # be reconstructed point-in-time from prices (momentum, low volatility) came
    # out at an information coefficient of 0.004 and -0.068 respectively.
    #
    # Until real point-in-time fundamentals accumulate, no claim about the
    # predictive value of this screen can be tested. Archiving monthly starts
    # that clock. Monthly rather than daily because filings change quarterly and
    # a daily archive would be almost entirely redundant rows.
    try:
        _arch_key = f"rankings_archive_{TODAY[:7]}"
        if cache_get(_arch_key) is None:
            cache_set(_arch_key, rankings, ttl_hours=24 * 365 * 3)
            print(f"Point-in-time archive written: {_arch_key}")
    except Exception as _e:
        print(f"Archive write skipped: {_e}")

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
    print(f"\nTop 15 by combined multi-factor score:")
    for r in top:
        _q = r.get("quality")
        print(f"  {r['ticker']:<6}  {r['sector']:<28}  "
              f"Score={r.get('fundamental_score') or 0:.1f}  "
              f"Sharpe={r['sharpe']:+.2f}  "
              f"12M={r.get('mom_12m', 0):+.1f}%  "
              f"Q={('%.2f' % _q) if _q is not None else '—'}")


def warm_portfolio_cache(rankings: dict):
    """
    Pre-fetch price history so the Portfolio Builder is instant even on the very
    first user run of the day, in two passes: the per-ticker cache for every
    ranked name (covers ANY candidate set the user's preferences produce), then
    the bundle for the default portfolio (covers the common case in one read).

    The window must match portfolio_builder._PRICE_HISTORY_YEARS (5 years) or
    neither warmed artefact satisfies the builder's request and it cold-fetches
    anyway.
    """
    from collections import defaultdict
    from portfolio_data import fetch_portfolio_prices_cached, warm_ticker_cache

    print("\nUpdating portfolio price cache (bootstrap once, append daily)...")

    # Per-ticker cache for the WHOLE ranked universe, not just the default
    # portfolio's candidates. The bundle warmed below only ever hits when a
    # user's preferences reproduce this exact ticker set, so it does nothing
    # for a non-default run -- and a 50-holding request takes the entire
    # eligible universe as its candidate pool. Warming per ticker is what
    # lets those runs read prices out of Supabase instead of firing ~124 live
    # requests from the web dyno, which is how the builder fell over on
    # 2026-08-26.
    _all = [t for t in rankings if not str(t).startswith('_')]
    print(f'Warming per-ticker price cache for {len(_all)} ranked tickers...')
    try:
        _n = warm_ticker_cache(_all, period_years=5, log=print)
        print(f'Per-ticker cache warmed -- {_n}/{len(_all)} tickers written')
    except Exception as e:
        print(f'Per-ticker cache warm failed: {e}')

    # The bundle pass: top 6 per sector covers every sidebar preference combo
    # (risk tolerance 10 asks for 6/sector; lower tolerances ask for fewer), so
    # a default-ish run resolves in a single cache read rather than 45 of them.
    # 11 sectors × 6 + SPY + QQQ ≈ 68 tickers.
    sector_groups: dict = defaultdict(list)
    for ticker, data in rankings.items():
        sector = data.get("sector", "Unknown")
        if sector.startswith("Bond"):
            continue
        sector_groups[sector].append((ticker, data.get("score", 0)))

    # SPY/QQQ are always pinned; GLD/TLT are pinned for conservative profiles
    # (risk tolerance ≤ 3), so warm them too or those builds cold-fetch.
    candidates = ["SPY", "QQQ", "GLD", "TLT"]
    for sector, ticker_scores in sector_groups.items():
        ranked = sorted(ticker_scores, key=lambda x: x[1], reverse=True)
        for t, _ in ranked[:6]:
            if t not in candidates:
                candidates.append(t)

    print(f"Pre-fetching 5-year prices for {len(candidates)} tickers "
          f"(top 6 per sector)...")

    try:
        # Must match portfolio_builder._PRICE_HISTORY_YEARS so the warmed bundle
        # actually satisfies the builder's request (else cold 5yr fetches).
        _, close_df, _, failed = fetch_portfolio_prices_cached(
            candidates, period_years=5, api_key=POLYGON_API_KEY, log=print,
            persist_cache=True      # this job is the one that pays for writes
        )
        print(f"Portfolio cache warmed — {len(close_df.columns)} tickers ready")
        if failed:
            print(f"Failed: {failed}")
    except Exception as e:
        print(f"Portfolio cache warm failed: {e}")


if __name__ == "__main__":
    main()
