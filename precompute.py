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
    _ck = f"fund_{ticker.upper()}"
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
        return {
            "net_margin": marg.get("net"),
            "roe":        ret_.get("roe"),
            "f_score":    q.get("f_score"),
            "rev_cagr":   grw.get("revenue_cagr"),
            "eps_cagr":   grw.get("eps_cagr"),
            "d2e":        lev.get("debt_to_equity"),
            "cur_ratio":  lev.get("current_ratio"),
            # Diluted EPS enables the value factor without a share count:
            # earnings yield = EPS / price. Every other valuation ratio (P/S,
            # EV/EBITDA, FCF yield) needs market cap, i.e. shares outstanding,
            # which EDGAR-only precompute doesn't have. Phase 3.
            "eps":        inc.get("eps_diluted"),
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


def _add_combined_scores(rankings: dict) -> dict:
    """
    Diversified multi-factor score, normalised to [0,1] across the universe.

    A single backward metric (the old 50% trailing-Sharpe weight) is a weak
    predictor of future returns, so the score spreads across four evidence-based
    factors instead of leaning on "what already went up":

      25%  Momentum   (12-1 / 6m / 3m composite — the best-documented factor)
      20%  Quality    (net margin, ROE, Piotroski — sector-relative)
      15%  Value      (earnings yield = diluted EPS / price — sector-relative)
      15%  Growth     (revenue CAGR, EPS CAGR — sector-relative)
      10%  Health     (D/E, current ratio, F-Score — sector-relative)
      15%  Low vol    (the low-vol anomaly — absolute, a calm name is calm
                       regardless of its neighbours)

    Every fundamental factor is percentile-ranked WITHIN its sector: a 15% net
    margin is exceptional for a grocer and mediocre for a software company, so a
    cross-universe rank measures the sector, not the company. Missing data is
    neutral (0.5), never a penalty — ETFs and non-filers ride on momentum and
    volatility alone.

    Sharpe was dropped from the composite: it is momentum divided by
    volatility, and both are already here — a correlated metric acting as a
    second vote for the same information.

    This is a factor *tilt*, not a prediction — no method reliably forecasts
    returns; a low-cost index is the benchmark to beat.
    """
    if not rankings:
        return rankings

    def _pct_rank(values: list) -> list:
        """Percentile rank in [0,1]. Replaces min-max, which is hostage to
        outliers: one name up 400% compresses every other score toward zero, so
        adding or removing a single ticker rescales the whole universe. A rank is
        invariant to that."""
        n = len(values)
        if n == 1:
            return [0.5]
        order = sorted(range(n), key=lambda i: values[i])
        out   = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg_rank = (i + j) / 2.0            # average rank for ties
            for k in range(i, j + 1):
                out[order[k]] = avg_rank / (n - 1)
            i = j + 1
        return out

    tickers = list(rankings.keys())

    def _by_group(field, groups, invert=False):
        """Percentile-rank `field` WITHIN each sector, not across the whole
        universe. Comparing a utility's ROE or volatility against a semiconductor's
        measures the sector, not the company — cross-sectional ranking without
        this systematically selects whole sectors. Groups of one fall back to
        neutral 0.5, since a rank of one thing carries no information."""
        out = {}
        for g, members in groups.items():
            vals = [rankings[t].get(field, 0) or 0 for t in members]
            if len(members) < 3:
                for t in members:
                    out[t] = 0.5
                continue
            ranks = _pct_rank(vals)
            for t, r in zip(members, ranks):
                out[t] = (1.0 - r) if invert else r
        return out

    from collections import defaultdict
    groups = defaultdict(list)
    for t in tickers:
        groups[rankings[t].get("sector", "Unknown")].append(t)

    # Momentum is sector-relative, as before. Volatility stays absolute — a
    # genuinely low-volatility name is low-volatility regardless of what its
    # neighbours do, and that is the property being selected for.
    g_12m = _by_group("mom_12m_adj", groups)
    g_6m  = _by_group("mom_6m",      groups)
    g_3m  = _by_group("mom_3m",      groups)

    a_vol = dict(zip(tickers, _pct_rank([rankings[t].get("ann_vol", 0) or 0 for t in tickers])))

    def _fund_of(t):
        f = rankings[t].get("fund")
        return f if isinstance(f, dict) else {}

    def _by_group_opt(getter, invert=False):
        """Sector-relative percentile rank for OPTIONAL values.

        _by_group coerces missing to 0 (`.get(field, 0) or 0`), which is
        harmless for momentum (0% return sits mid-pack) but poisonous for
        fundamentals: a missing debt/equity coerced to 0 would rank as the
        least-levered name in its sector. Here only names that HAVE the value
        are ranked against each other; everyone else is neutral 0.5.
        """
        out = {}
        for _g, members in groups.items():
            have = [(t, getter(t)) for t in members if getter(t) is not None]
            if len(have) < 3:                # a rank over <3 carries no signal
                for t in members:
                    out[t] = 0.5
                continue
            ranks = _pct_rank([v for _t, v in have])
            for (t, _v), r in zip(have, ranks):
                out[t] = (1.0 - r) if invert else r
            for t in members:
                out.setdefault(t, 0.5)
        return out

    # Value: earnings yield = diluted EPS / price. Negative earnings produce a
    # negative yield and rank at the bottom of their sector, which is the point.
    def _eyield(t):
        eps, px = _fund_of(t).get("eps"), rankings[t].get("last_price")
        return (eps / px) if (eps is not None and px) else None

    g_q    = _by_group_opt(lambda t: rankings[t].get("quality"))
    g_val  = _by_group_opt(_eyield)
    g_revg = _by_group_opt(lambda t: _fund_of(t).get("rev_cagr"))
    g_epsg = _by_group_opt(lambda t: _fund_of(t).get("eps_cagr"))
    g_d2e  = _by_group_opt(lambda t: _fund_of(t).get("d2e"), invert=True)
    g_cur  = _by_group_opt(lambda t: _fund_of(t).get("cur_ratio"))

    for t in tickers:
        f        = _fund_of(t)
        momentum = (g_12m[t] + g_6m[t] + g_3m[t]) / 3.0
        low_vol  = 1.0 - a_vol[t]                        # lower volatility → higher
        quality  = g_q[t]
        value    = g_val[t]
        growth   = (g_revg[t] + g_epsg[t]) / 2.0
        _fs      = f.get("f_score")
        health   = (g_d2e[t] + g_cur[t] + (_fs / 9.0 if _fs is not None else 0.5)) / 3.0
        score = (0.25 * momentum + 0.20 * quality + 0.15 * value +
                 0.15 * growth   + 0.10 * health  + 0.15 * low_vol)
        rankings[t]["score"] = round(score, 4)
        # Keep the components — the UI attributes each holding to them, and
        # without this the reason a name was picked is unrecoverable.
        rankings[t]["f_momentum"] = round(momentum, 4)
        rankings[t]["f_quality"]  = round(quality, 4)
        rankings[t]["f_value"]    = round(value, 4)
        rankings[t]["f_growth"]   = round(growth, 4)
        rankings[t]["f_health"]   = round(health, 4)
        rankings[t]["f_lowvol"]   = round(low_vol, 4)

        # Hard gate — a flag, not a deletion, so the rankings cache shape and
        # the Top Stocks page are untouched. High leverage AND weak fundamentals
        # together is the combination a single shiny metric can't excuse; the
        # builder skips gated names in auto-selection, user picks are exempt.
        d2e = f.get("d2e")
        if (d2e is not None and d2e > 3.0 and (_fs is None or _fs <= 3)):
            rankings[t]["gate"] = f"D/E {d2e:.1f} with F-Score {_fs if _fs is not None else '—'}/9"
        else:
            rankings[t].pop("gate", None)

    return rankings


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
    print("Factors: Momentum 25% · Quality 20% · Value 15% · Growth 15% · "
          "Health 10% · Low-Vol 15% — fundamentals sector-relative\n")

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
            partial["_meta"] = {"computed_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                                "partial": True, "tickers_done": done, "tickers_total": len(price_map)}
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
    rankings["_meta"] = {
        "computed_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "partial": is_partial,
        "tickers_done": len(rankings),
    }
    ok      = cache_set(CACHE_KEY, rankings, ttl_hours=RANKINGS_TTL_HOURS)
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
              f"Score={r.get('score',0):.3f}  "
              f"Sharpe={r['sharpe']:+.2f}  "
              f"12M={r.get('mom_12m', 0):+.1f}%  "
              f"Q={('%.2f' % _q) if _q is not None else '—'}")


def warm_portfolio_cache(rankings: dict):
    """
    Pre-fetch price history for the default top-18 portfolio so the portfolio
    builder is instant even on the very first user run of the day. The window
    must match portfolio_builder._PRICE_HISTORY_YEARS (5 years) or the warmed
    bundle doesn't satisfy the builder's request and it cold-fetches anyway.
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
            candidates, period_years=5, api_key=POLYGON_API_KEY, log=print
        )
        print(f"Portfolio cache warmed — {len(close_df.columns)} tickers ready")
        if failed:
            print(f"Failed: {failed}")
    except Exception as e:
        print(f"Portfolio cache warm failed: {e}")


if __name__ == "__main__":
    main()
