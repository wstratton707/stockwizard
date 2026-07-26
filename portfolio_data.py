import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from constants import get_risk_free_rate
from database import cache_get, cache_set

POLYGON_BASE    = "https://api.polygon.io"
_PORT_CACHE     = {}
_PORT_CACHE_LOCK = threading.Lock()
CACHE_TTL       = 3600

SECTOR_UNIVERSE = {
    "Technology": [
        "AAPL","MSFT","NVDA","AVGO","AMD","CRM","ADBE","QCOM",
        "TXN","NOW","AMAT","MU","LRCX","KLAC","SNPS","CDNS","PANW","FTNT",
        "CSCO","IBM","ORCL","INTC","HPQ","DELL","STX","KEYS","ANSS","PLTR",
    ],
    "Health Care": [
        "UNH","LLY","JNJ","ABBV","MRK","TMO","ABT","DHR","AMGN","PFE",
        "SYK","ISRG","MDT","BMY","GILD","ZTS","REGN","BSX","HCA","ELV",
        "CI","CVS","MCK","BIIB","ILMN","BAX","VRTX","IQV","CNC","MOH",
    ],
    "Financials": [
        "JPM","V","MA","BAC","WFC","GS","MS","BLK","AXP","C",
        "SPGI","MCO","ICE","CME","CB","PGR","TRV","AFL","MET","PRU",
        "USB","PNC","TFC","COF","BX","SCHW","FIS","FI","PYPL","CBOE",
    ],
    "Consumer Discretionary": [
        "AMZN","TSLA","HD","MCD","NKE","SBUX","TJX","BKNG","CMG","LOW",
        "LULU","ROST","DHI","LEN","NVR","PHM","GM","F","ORLY","AZO",
        "BBY","DG","DLTR","YUM","HLT","MAR","RCL","CCL","NCLH","WYNN",
    ],
    "Consumer Staples": [
        "WMT","PG","KO","COST","PEP","PM","MDLZ","CL","GIS","KHC",
        "SYY","MKC","HRL","TSN","CAG","CPB","K","CHD","CLX","KMB",
        "STZ","BF.B","TAP","MO","BTI","EL","COTY","SPB","POST","CENT",
    ],
    "Industrials": [
        "CAT","UPS","HON","BA","RTX","GE","DE","MMM","LMT","FDX",
        "NOC","GD","ETN","EMR","PH","ROK","AME","FAST","PCAR","CTAS",
        "RSG","WM","IR","XYL","ROP","VRSK","CPRT","EXPD","ODFL","JBHT",
    ],
    "Energy": [
        "XOM","CVX","COP","EOG","SLB","MPC","VLO","PSX","OXY","HES",
        "CHRD","DVN","FANG","PR","APA","HAL","BKR","NOV","RRC","EQT",
        "CTRA","OVV","SM","MGY","MTDR","HP","DINO","DKL","TRGP","WMB",
    ],
    "Materials": [
        "LIN","APD","ECL","SHW","NEM","FCX","NUE","VMC","MLM","ALB",
        "CF","MOS","IFF","PPG","RPM","EMN","LYB","DD","DOW","CE",
        "AXTA","PKG","IP","SW","SEE","SON","GEF","SLVM","TREX","UFPI",
    ],
    "Real Estate": [
        "PLD","AMT","EQIX","CCI","PSA","O","DLR","WELL","SPG","VTR",
        "EXR","AVB","EQR","UDR","CPT","MAA","NNN","VICI","MPW","OHI",
        "DOC","ARE","BXP","SLG","KIM","REG","FRT","INVH","ELS","SUI",
    ],
    "Utilities": [
        "NEE","DUK","SO","D","AEP","EXC","XEL","ES","WEC","ED",
        "ETR","FE","PPL","AEE","CMS","NI","LNT","EVRG","PNW","SRE",
        "PCG","EIX","AWK","CNP","NRG","AES","DTE","OGE","POR","AVA",
    ],
    "Communication Services": [
        "GOOGL","META","NFLX","DIS","CMCSA","T","VZ","TMUS","EA","TTWO",
        "CHTR","FOXA","IPG","OMC","PARA","WBD","LYV","MTCH","ZM","SNAP",
        "PINS","RBLX","SPOT","ROKU","SIRI","IAC","TKO","NYT","NWSA","LBRDA",
    ],
}

SECTOR_ETFS = {
    "Technology":"XLK","Health Care":"XLV","Financials":"XLF",
    "Consumer Discretionary":"XLY","Consumer Staples":"XLP",
    "Industrials":"XLI","Energy":"XLE","Materials":"XLB",
    "Real Estate":"XLRE","Utilities":"XLU","Communication Services":"XLC",
}

BOND_UNIVERSE = {
    "Government":          ["TLT","IEF","SHY","GOVT","VGLT","VGIT","VGSH","TBT","TMF","BIL"],
    "Corporate":           ["LQD","VCIT","VCSH","HYG","JNK","USHY","ANGL","FALN","FLOT","SJNK"],
    "Inflation-Protected": ["TIP","STIP","SCHP","VTIP","PBTP","RINF","TDTT","FISR","LTPZ","WIP"],
    "Municipal":           ["MUB","VTEB","HYD","ITM","SHM","CMF","TFI","HYMB","IBMK","MAIM"],
    "International":       ["BNDX","EMB","PCY","VWOB","IGOV","ISHG","PICB","EMHY","EBND","IAGG"],
    "Broad Market":        ["AGG","BND","BNDW","FBND","IUSB","GBF","SCHZ","SPAB","BOND","TOTL"],
}

BOND_ETFS = {
    "Government":          "TLT",
    "Corporate":           "LQD",
    "Inflation-Protected": "TIP",
    "Municipal":           "MUB",
    "International":       "BNDX",
    "Broad Market":        "AGG",
}

# Approximate duration bucket per ticker (years)
BOND_DURATION_MAP = {
    # Government
    "TLT":"Long (20+ yr)","IEF":"Intermediate (7-10 yr)","SHY":"Short (1-3 yr)",
    "GOVT":"Broad","VGLT":"Long (20+ yr)","VGIT":"Intermediate (5-10 yr)",
    "VGSH":"Short (1-3 yr)","BIL":"Ultra-Short (<1 yr)","TBT":"Long (20+ yr)","TMF":"Long (20+ yr)",
    # Corporate
    "LQD":"Intermediate (7-10 yr)","VCIT":"Intermediate (5-10 yr)","VCSH":"Short (1-5 yr)",
    "HYG":"Intermediate (3-5 yr)","JNK":"Intermediate (3-5 yr)","USHY":"Intermediate (3-5 yr)",
    "ANGL":"Intermediate (3-7 yr)","FALN":"Intermediate (3-7 yr)",
    "FLOT":"Ultra-Short (<1 yr)","SJNK":"Short (1-3 yr)",
    # Inflation-Protected
    "TIP":"Intermediate (7-10 yr)","STIP":"Short (0-5 yr)","SCHP":"Intermediate (5-10 yr)",
    "VTIP":"Short (0-5 yr)","PBTP":"Long (15+ yr)","RINF":"Long (30 yr)",
    "TDTT":"Short (3 yr)","LTPZ":"Long (15+ yr)",
    # Municipal
    "MUB":"Intermediate (6-9 yr)","VTEB":"Intermediate (5-10 yr)","HYD":"Intermediate (8-12 yr)",
    "ITM":"Intermediate (6-10 yr)","SHM":"Short (1-5 yr)","CMF":"Intermediate","TFI":"Intermediate",
    # International
    "BNDX":"Intermediate (5-10 yr)","EMB":"Intermediate (7-12 yr)","PCY":"Intermediate (7-12 yr)",
    "VWOB":"Intermediate (7-10 yr)","IGOV":"Intermediate (7-10 yr)",
    # Broad Market
    "AGG":"Intermediate (6-8 yr)","BND":"Intermediate (6-8 yr)","BNDW":"Intermediate (6-8 yr)",
    "FBND":"Intermediate","IUSB":"Intermediate","SCHZ":"Intermediate (5-7 yr)",
}


def _polygon_fetch_chunk(ticker: str, start: str, end: str, api_key: str,
                          log=print) -> list:
    """
    Fetch one chunk of daily OHLCV from Polygon with retry on 429.
    Polygon free tier caps results per request at ~180 regardless of limit=.
    Call this repeatedly with 6-month windows and concatenate.
    """
    url    = f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
    params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}
    # Returns: list of bars on success (possibly empty); [] for a 403 tier
    # boundary (older data not available on this plan — a legitimate, cacheable
    # "no data here"); None for a transient failure (429 exhausted / error /
    # exception) so the caller knows the fetch is INCOMPLETE and must not cache it.
    for wait in (0, 12, 24, 36):
        if wait:
            log(f"   ⏳ {ticker} rate limited, retrying in {wait}s...")
            time.sleep(wait)
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 200:
                return r.json().get("results", [])
            if r.status_code == 403:
                # Plan doesn't grant this (older) window — treat as empty, not failure.
                return []
            if r.status_code != 429:
                log(f"   ⚠ {ticker} HTTP {r.status_code}: {r.text[:120]}")
                return None
        except Exception as e:
            log(f"   ⚠ {ticker} exception: {e}")
            return None
    log(f"   ✗ {ticker} — rate limited after retries (chunk incomplete)")
    return None


def _week_floor(date_str: str) -> str:
    """Snap a YYYY-MM-DD date to the Monday of its ISO week.
    Used to bucket cache keys so all runs Mon-Sun share the same cached data."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    dt = dt - timedelta(days=dt.weekday())
    return dt.strftime("%Y-%m-%d")


def _fetch_ohlcv(ticker, start, end, api_key, log=print):
    # Cache key uses the real dates. With yfinance (no 5/min limit) we no longer
    # week-bucket to conserve Polygon calls, so data refreshes daily instead of
    # being up to ~6 days stale at week's end (the old _week_floor behaviour).
    cache_key = f"{ticker}_{start}_{end}"

    # 1. In-memory cache (fastest) — lock-protected, Streamlit sessions are concurrent
    with _PORT_CACHE_LOCK:
        cached = _PORT_CACHE.get(cache_key)
    if cached and (time.time() - cached["ts"]) < CACHE_TTL:
        return cached["df"]

    # 2. Supabase persistent cache (survives restarts, shared across users)
    db_hit = cache_get(f"ohlcv_{cache_key}")
    if db_hit is not None:
        try:
            df = pd.DataFrame(db_hit)
            df["Date"] = pd.to_datetime(df["Date"])
            with _PORT_CACHE_LOCK:
                _PORT_CACHE[cache_key] = {"ts": time.time(), "df": df}
            return df
        except Exception:
            pass

    # 3. Fetch via the multi-source router: yfinance (same-day, deep history) →
    #    Polygon fallback. get_bars returns a complete result or None — no partial
    #    chunks to guard against — so a successful result is always safe to cache.
    from market_data import get_bars
    bars = get_bars(ticker, start, end, interval="day", polygon_key=api_key)
    if bars is None or bars.empty:
        log(f"   ⚠ {ticker} — no data returned")
        return None
    df = bars.copy()
    df["Ticker"] = ticker
    df = df[["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"]]
    df = df.drop_duplicates("Date").sort_values("Date").reset_index(drop=True)

    with _PORT_CACHE_LOCK:
        _PORT_CACHE[cache_key] = {"ts": time.time(), "df": df}
    try:
        cache_set(f"ohlcv_{cache_key}",
                  df.assign(Date=df["Date"].astype(str)).to_dict(orient="records"),
                  ttl_hours=720)
    except Exception:
        pass
    return df


def fetch_portfolio_prices(tickers, period_years=2, api_key="", log=print):
    end     = datetime.today()
    start   = end - timedelta(days=period_years*365)
    end_s   = end.strftime("%Y-%m-%d")
    start_s = start.strftime("%Y-%m-%d")

    price_dict, failed = {}, []
    thread_logs = []  # collect logs from threads — Streamlit can't be called from worker threads

    # ── Batch-prewarm (the big speedup) ──────────────────────────────────────
    # Pull every not-yet-cached ticker in ONE yfinance request and seed the
    # in-memory cache, so the per-ticker loop below mostly hits cache. Fetching a
    # ~60-name universe one ticker at a time (and Yahoo throttling the burst)
    # could take minutes; a single batched request is seconds.
    _uncached = []
    for _t in tickers:
        _ck = f"{_t}_{start_s}_{end_s}"
        with _PORT_CACHE_LOCK:
            _hit = _PORT_CACHE.get(_ck)
        if not (_hit and (time.time() - _hit["ts"]) < CACHE_TTL):
            _uncached.append(_t)
    if len(_uncached) > 3:
        try:
            from market_data import get_bars_batch
            _batch = get_bars_batch(_uncached, start_s, end_s, "day")
            for _bt, _bdf in _batch.items():
                _d = _bdf.copy()
                _d["Ticker"] = _bt
                _d = _d[["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"]]
                with _PORT_CACHE_LOCK:
                    _PORT_CACHE[f"{_bt}_{start_s}_{end_s}"] = {"ts": time.time(), "df": _d}
            thread_logs.append(f"   ⚡ batch-fetched {len(_batch)}/{len(_uncached)} tickers in one request")
        except Exception as _e:
            thread_logs.append(f"   ⚠ batch prewarm failed ({_e}) — falling back to per-ticker")

    def fetch_one(ticker):
        msgs = []
        df = _fetch_ohlcv(ticker, start_s, end_s, api_key, log=lambda m: msgs.append(m))
        return ticker, df, msgs

    # yfinance (now the primary bar source) has no per-minute cap, so the few
    # tickers the batch missed can be parallelised without the old Polygon-429 risk.
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_one, t): t for t in tickers}
        for future in as_completed(futures):
            ticker, df, msgs = future.result()
            thread_logs.extend(msgs)
            if df is not None and len(df) > 60:
                price_dict[ticker] = df
                log(f"   ✓ {ticker} ({len(df)} days)")
            else:
                if df is not None:
                    log(f"   ⚠ {ticker} skipped — only {len(df)} days")
                failed.append(ticker)

    for msg in thread_logs:
        log(msg)

    if not price_dict:
        if not api_key:
            raise ValueError("Polygon API key is missing. Check your environment variables.")
        raise ValueError(f"No valid price data retrieved. All {len(tickers)} tickers failed — check API key and rate limits.")

    closes = {t: df.set_index("Date")["Close"].rename(t)
              for t, df in price_dict.items()}
    close_df = pd.DataFrame(closes)
    # Keep the common window as long as possible. The ffill/dropna below aligns every
    # ticker to a shared window, so one young name (recent IPO) would otherwise
    # truncate the whole matrix. Instead, drop tickers whose history doesn't reach
    # near the requested window start — but only if enough mature names remain, so a
    # young-heavy candidate set still returns something usable.
    window_start = pd.Timestamp(start)
    first_valid  = close_df.apply(lambda s: s.first_valid_index())
    mature = [t for t in close_df.columns
              if pd.notna(first_valid[t]) and first_valid[t] <= window_start + pd.Timedelta(days=120)]
    short  = [t for t in close_df.columns if t not in mature]
    if mature and len(mature) >= max(5, len(close_df.columns) // 2):
        if short:
            log(f"   ⚠ Excluding {len(short)} ticker(s) with insufficient history "
                f"(need ~{period_years}y): {short}")
            failed.extend(short)
        close_df = close_df[mature]
    close_df   = close_df.ffill().dropna()
    returns_df = close_df.pct_change().dropna()

    # Normalize price_dict shape to match the cached path — callers should rely on
    # a single {ticker: DataFrame(Date, Ticker, Close)} contract regardless of path.
    price_dict = _close_df_to_price_dict(close_df)

    log(f"   ✅ {len(price_dict)} tickers, {len(returns_df)} trading days")
    return price_dict, close_df, returns_df, failed


def get_ticker_info(ticker, api_key):
    # Persistent Supabase cache (shared across sessions/users, survives restarts).
    # Company metadata barely changes, so a 30-day TTL avoids re-hitting the
    # rate-limited Polygon reference endpoint on every fresh session — the
    # Portfolio Builder fetches this for ~18 tickers per build.
    _ck = f"tinfo_{ticker.upper()}"
    try:
        hit = cache_get(_ck)
        if hit:
            return hit
    except Exception:
        pass
    try:
        r = requests.get(f"{POLYGON_BASE}/v3/reference/tickers/{ticker}",
                         params={"apiKey":api_key}, timeout=10)
        if r.status_code == 200:
            res = r.json().get("results", {})
            info = {
                "name":       res.get("name", ticker),
                "sector":     res.get("sic_description","Unknown"),
                "exchange":   res.get("primary_exchange",""),
                "market_cap": res.get("market_cap", 0),
            }
            try:
                cache_set(_ck, info, ttl_hours=720)
            except Exception:
                pass
            return info
    except Exception:
        pass
    return {"name":ticker,"sector":"Unknown","exchange":"","market_cap":0}


def build_candidate_universe(preferences, api_key, log=print):
    """Returns (tickers, sector_map) where sector_map = {ticker: sector}.
    Fetches top 3 per sector so we can rank by Sharpe after price data arrives."""
    risk_tolerance   = preferences.get("risk_tolerance", 5)
    included_sectors = preferences.get("include_sectors", list(SECTOR_UNIVERSE.keys()))
    excluded_sectors = preferences.get("exclude_sectors", [])
    user_tickers     = [t.upper().strip() for t in preferences.get("user_tickers", [])]
    included_bonds   = preferences.get("include_bond_categories", [])
    excluded_tickers = set(t.upper() for t in preferences.get("exclude_tickers", []))

    GROWTH_SECTORS    = {"Technology", "Consumer Discretionary", "Communication Services", "Financials"}
    DEFENSIVE_SECTORS = {"Consumer Staples", "Utilities", "Health Care", "Real Estate"}
    ALWAYS_KEEP       = {"SPY", "QQQ", "GLD", "TLT"}

    candidates    = []   # ordered
    sector_map    = {}   # ticker → sector label
    skipped_sectors = [] # sectors excluded due to risk profile

    def add(ticker, sector="Market"):
        if ticker not in excluded_tickers and ticker not in candidates:
            candidates.append(ticker)
            sector_map[ticker] = sector

    # 1. User picks always first
    for t in user_tickers:
        add(t, "User")

    # 2. SPY always (backtest benchmark)
    add("SPY", "Market")
    if risk_tolerance >= 4:
        add("QQQ", "Market")
    if risk_tolerance <= 3:
        add("GLD", "Commodities")
        add("TLT", "Government")

    # 3. Top 5 candidates per sector — Sharpe ranking picks best 2 after price fetch
    for sector in included_sectors:
        if sector in excluded_sectors:
            continue
        stocks = SECTOR_UNIVERSE.get(sector, [])
        if not stocks:
            continue
        if risk_tolerance <= 3 and sector not in DEFENSIVE_SECTORS:
            skipped_sectors.append(sector)
            continue  # Conservative: skip growth sectors
        n = 5  # fetch top 5 so Sharpe ranking has a meaningful pool to choose from
        for s in stocks[:n]:
            add(s, sector)

    if skipped_sectors:
        log(f"   ⚠ Conservative profile: skipped growth sectors — {', '.join(skipped_sectors)}")

    # 4. Bond ETFs (representative only — 1 per category)
    bond_slots = 3 if risk_tolerance <= 3 else (2 if risk_tolerance <= 6 else 0)
    for category in included_bonds[:bond_slots]:
        etf = BOND_ETFS.get(category)
        if etf:
            add(etf, f"Bond-{category}")

    # Cap at 65 for fetching — Sharpe filter will trim to ~18 after prices arrive
    result = candidates[:65]
    log(f"   Scanning {len(result)} candidates across sectors for Sharpe ranking...")
    return result, sector_map, skipped_sectors


def select_by_factors(returns_df, sector_map, always_keep=None, max_total=18,
                      top_n_per_sector=2):
    """
    Rank candidates by the SAME multi-factor score the precompute path uses, so
    the fallback (when the precompute cache is unavailable) selects stocks on the
    same basis as the primary path — momentum + low-volatility + risk-adjusted
    return — rather than trailing Sharpe alone. Quality (fundamentals) can't be
    derived from price history here, so it's left neutral, matching precompute's
    neutral-when-missing rule (a constant term that doesn't affect ranking).

    Keeps the best `top_n_per_sector` per sector; always_keep tickers
    (SPY, QQQ, ...) are pinned regardless of score.
    """
    from collections import defaultdict

    if always_keep is None:
        always_keep = {"SPY", "QQQ", "GLD", "TLT"}

    ann = 252

    def _factors(ticker):
        r = returns_df[ticker].dropna()
        if len(r) < 30:
            return None
        ann_vol = float(r.std() * np.sqrt(ann))
        sharpe  = ((r.mean() * ann) - get_risk_free_rate()) / ann_vol if ann_vol > 0 else 0.0
        # 12-1 momentum: compound return over the trailing year excluding the most
        # recent ~month (short-term reversal). The slice auto-clamps for shorter
        # histories; fall back to the full window when there's very little data.
        window   = r.iloc[-ann:-21] if len(r) > 63 else r
        momentum = float((1 + window).prod() - 1)
        return {"sharpe": sharpe, "ann_vol": ann_vol, "momentum": momentum}

    pinned, cand, facts = [], [], {}
    for ticker in returns_df.columns:
        if ticker in always_keep:
            pinned.append(ticker)
            continue
        f = _factors(ticker)
        if f is not None:
            facts[ticker] = f
            cand.append(ticker)

    def _norm(vals):
        lo, hi = min(vals), max(vals)
        rng = hi - lo
        return {t: ((v - lo) / rng if rng > 0 else 0.5) for t, v in zip(cand, vals)}

    if cand:
        n_sharpe = _norm([facts[t]["sharpe"]   for t in cand])
        n_vol    = _norm([facts[t]["ann_vol"]  for t in cand])
        n_mom    = _norm([facts[t]["momentum"] for t in cand])
        score = {t: (0.30 * n_mom[t] + 0.30 * 0.5 +            # quality neutral
                     0.20 * (1 - n_vol[t]) + 0.20 * n_sharpe[t])
                 for t in cand}
    else:
        score = {}

    sector_groups = defaultdict(list)
    for ticker in cand:
        sector_groups[sector_map.get(ticker, "Unknown")].append(ticker)

    selected = list(pinned)
    for sector, tickers in sector_groups.items():
        ranked = sorted(tickers, key=lambda t: score.get(t, 0.0), reverse=True)
        selected.extend(ranked[:top_n_per_sector])

    return selected[:max_total]


# ── Supabase-cached portfolio price fetcher ───────────────────────────────────

def _close_df_to_payload(close_df: pd.DataFrame, failed: list) -> dict:
    reset = close_df.reset_index()
    reset["Date"] = reset["Date"].astype(str)
    return {"close": reset.to_dict(orient="records"), "failed": failed}


def _payload_to_close_df(cached: dict) -> pd.DataFrame:
    close_df = pd.DataFrame(cached["close"]).set_index("Date")
    close_df.index = pd.to_datetime(close_df.index)
    return close_df.apply(pd.to_numeric, errors="coerce")


def _close_df_to_price_dict(close_df: pd.DataFrame) -> dict:
    price_dict = {}
    for t in close_df.columns:
        s = close_df[t].dropna().reset_index()
        s.columns = ["Date", "Close"]
        s["Ticker"] = t
        price_dict[t] = s
    return price_dict


def fetch_portfolio_prices_cached(tickers, period_years=2, api_key="", log=print):
    """
    Fetches portfolio price history with a two-layer cache:

      Layer 1 — Bundle cache  (portfolio_prices_hist_<hash-of-tickers>)
        Fast path for the EXACT same ticker set as a prior call. One Supabase
        read returns a pre-assembled close_df. Changes in sidebar preferences
        change the ticker set and miss this layer.

      Layer 2 — Per-ticker cache  (ohlcv_<ticker>_<monday>_<monday>)
        Hit via `_fetch_ohlcv` inside `fetch_portfolio_prices`. Survives
        sidebar preference changes because it's keyed per ticker, not per
        set. Warmed nightly by precompute.py's `warm_portfolio_cache`.

      Layer 3 — Polygon fetch
        Cold-path for any ticker that misses both layers above.
    """
    import hashlib

    tk_key   = hashlib.md5(",".join(sorted(tickers)).encode()).hexdigest()[:10]
    hist_key = f"portfolio_prices_hist_{tk_key}"

    # ── Layer 1: bundle cache for exact-same ticker set ────────────────────────
    cached = cache_get(hist_key)
    if cached is not None:
        try:
            close_df = _payload_to_close_df(cached)
            failed   = cached.get("failed", [])

            # Check freshness — append any missing trading days
            latest   = close_df.index.max()
            today    = pd.Timestamp.today().normalize()
            cutoff   = today - pd.Timedelta(days=1)

            if latest < cutoff:
                fetch_start = (latest + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                fetch_end   = today.strftime("%Y-%m-%d")
                log(f"   🔄 Bundle cache is {(cutoff - latest).days} day(s) stale — "
                    f"fetching {fetch_start} → {fetch_end}")

                new_dfs = {}
                thread_logs = []

                def _fetch_new(ticker):
                    msgs = []
                    df = _fetch_ohlcv(ticker, fetch_start, fetch_end, api_key,
                                      log=lambda m: msgs.append(m))
                    return ticker, df, msgs

                with ThreadPoolExecutor(max_workers=2) as ex:
                    for ticker, df, msgs in ex.map(_fetch_new, close_df.columns):
                        thread_logs.extend(msgs)
                        if df is not None and len(df) > 0:
                            new_dfs[ticker] = df.set_index("Date")["Close"].rename(ticker)

                for msg in thread_logs:
                    log(msg)

                if new_dfs:
                    new_close = pd.DataFrame(new_dfs)
                    close_df  = pd.concat([close_df, new_close])
                    close_df  = close_df[~close_df.index.duplicated(keep="last")].sort_index()
                    cutoff_date = today - pd.Timedelta(days=int(period_years * 365.25))
                    close_df    = close_df[close_df.index >= cutoff_date]
                    log(f"   ✅ Appended {len(new_close)} new row(s) — "
                        f"{len(close_df)} total trading days in cache")
                    try:
                        cache_set(hist_key, _close_df_to_payload(close_df, failed),
                                  ttl_hours=400 * 24)
                    except Exception as e:
                        log(f"   ⚠ Could not update bundle cache: {e}")
                else:
                    log("   ⚡ No new trading data available yet — using existing bundle")
            else:
                log(f"   ⚡ Bundle cache current (latest: {latest.date()})")

            returns_df = close_df.pct_change().dropna()
            price_dict = _close_df_to_price_dict(close_df)
            log(f"   ✅ {len(price_dict)} tickers loaded from bundle cache")
            return price_dict, close_df, returns_df, failed

        except Exception as e:
            log(f"   ⚠ Bundle cache parse failed ({e}) — falling back to per-ticker")

    # ── Layers 2+3: per-ticker cache via _fetch_ohlcv, then Polygon for misses ─
    # fetch_portfolio_prices calls _fetch_ohlcv per ticker, which checks Supabase
    # per-ticker cache first (warmed nightly by precompute.py). Only tickers
    # missing from that cache trigger a Polygon fetch.
    log(f"   🔍 No matching bundle for this ticker set — checking per-ticker caches "
        f"for {len(tickers)} tickers (warmed tickers return instantly)")
    price_dict, close_df, returns_df, failed = fetch_portfolio_prices(
        tickers, period_years=period_years, api_key=api_key, log=log
    )

    # Save the assembled bundle so next call with identical prefs is instant
    try:
        if cache_set(hist_key, _close_df_to_payload(close_df, failed),
                     ttl_hours=400 * 24):
            log("   ✅ Bundle cached for future runs with the same ticker set")
    except Exception as e:
        log(f"   ⚠ Could not write bundle cache: {e}")

    return price_dict, close_df, returns_df, failed


def get_sharpe_rankings(api_key: str = "") -> dict:
    """
    Returns the most recent pre-computed Sharpe rankings from Supabase, walking
    back up to ~5 days.

    The precompute job runs on weekday mornings, so before that day's run (overnight)
    and all weekend, today's key doesn't exist yet. Falling back to the latest
    available rankings keeps the Portfolio Builder on the full ranked universe
    instead of silently degrading to the slow live-candidate path. Rankings move
    slowly day-to-day, so the most recent set is a fine stand-in.
    Format: {ticker: {ticker, sector, sharpe, ann_return, ann_vol, ...}}.
    Returns {} only if nothing recent is cached at all.
    """
    for days_back in range(0, 6):
        date_str = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        rankings = cache_get(f"sharpe_rankings_{date_str}")
        if rankings:
            return rankings
    return {}
