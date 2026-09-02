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


# ── Layer 1: the in-process frame cache ───────────────────────────────────────
# This was write-only. Entries were overwritten when a key repeated and were
# otherwise never removed, and the key embeds today's dates — so every frame the
# process touched yesterday is unreachable today and still resident. One 5-year
# OHLCV frame measures 129 KB, the ranked universe is ~330 names, and a
# 50-holding build walks the whole eligible universe: about 44 MB per day of
# uptime, permanently, on a web dyno that stays up for weeks. Render reported
# the service over its memory limit on 2026-08-26.
#
# Bounded, and swept on write. The cap is well above what one build needs
# (a 124-name candidate pool) so nothing hot gets evicted mid-run.
_PORT_CACHE_MAX = 300          # frames — roughly 39 MB at 129 KB each

# How many freshly-fetched tickers a USER'S request may write back to Layer 2.
# Each write is a ~200 KB body, and a cold 124-name build measured 157 seconds
# of uploads — far worse than the cold fetch it was meant to prevent. Filling
# the layer belongs to precompute, which runs in a GitHub Action with a 90-minute
# budget and no one waiting on it. A handful of writes is still worth doing
# inline, so a ticker precompute happened to miss heals itself on first use.
_MAX_INLINE_CACHE_WRITES = 12


def _port_cache_get(cache_key: str):
    """A cached frame if present and inside its TTL, else None."""
    with _PORT_CACHE_LOCK:
        hit = _PORT_CACHE.get(cache_key)
    if hit and (time.time() - hit["ts"]) < CACHE_TTL:
        return hit["df"]
    return None


def _port_cache_put(cache_key: str, df) -> None:
    """Store a frame, evicting expired entries and then the oldest over the cap."""
    now = time.time()
    with _PORT_CACHE_LOCK:
        _PORT_CACHE[cache_key] = {"ts": now, "df": df}
        if len(_PORT_CACHE) <= _PORT_CACHE_MAX:
            return
        for k in [k for k, v in _PORT_CACHE.items()
                  if now - v["ts"] >= CACHE_TTL and k != cache_key]:
            _PORT_CACHE.pop(k, None)
        over = len(_PORT_CACHE) - _PORT_CACHE_MAX
        if over > 0:
            for k, _v in sorted(_PORT_CACHE.items(),
                                key=lambda kv: kv[1]["ts"])[:over]:
                if k != cache_key:
                    _PORT_CACHE.pop(k, None)


# ── Layer 2: the persistent per-ticker price cache ────────────────────────────
# This layer is documented as the thing that makes a large Portfolio Builder run
# survivable, and until now it did not exist. Two independent defects:
#
#   1. The key was f"{ticker}_{start}_{end}" with both endpoints recomputed from
#      today, so it rotated every calendar day. An entry warmed on Tuesday could
#      never be read on Wednesday, and nothing warmed at all on a weekend.
#   2. The batch prewarm in fetch_portfolio_prices seeds the IN-MEMORY cache
#      first, so _fetch_ohlcv returned at step 1 and the Supabase write at the
#      end of it never ran. precompute's warm therefore wrote the bundle for its
#      own 68-ticker set and not one per-ticker row.
#
# Measured 2026-08-26: ohlcv_{AAPL,MSFT,SPY,JPM,XOM,NEE,AMT,PG,LIN,CAT,GOOGL,
# QQQ,GLD,TLT} were all MISS for that day and for each of the three preceding
# days. Every Portfolio Builder run was going live to the data provider for
# every candidate, which is fine for a 45-name pool and is what fell over at
# 124 (a 50-holding request takes the whole eligible universe).
#
# The key is now the window LENGTH rather than its endpoints, and a frame that
# has fallen a few sessions behind is topped up with one small tail fetch
# instead of being thrown away — the same append-don't-refetch pattern the
# bundle layer already uses.
_OHLCV_TTL_HOURS       = 720   # 30 days
_OHLCV_STALE_TOLERANCE = 5     # calendar days a cached frame may lag before top-up
_OHLCV_COLS            = ["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"]


def _ticker_cache_key(ticker: str, period_years) -> str:
    return f"ohlcv2_{ticker.upper()}_{int(round(float(period_years)))}y"


def _read_ticker_cache(ticker, period_years, start_s, end_s, min_rows=60):
    """Cached OHLCV for one ticker, trimmed to the requested window.

    Returns (df, tail_start). `tail_start` is a YYYY-MM-DD string when the frame
    is usable but behind, so the caller can fetch just the missing days; None
    when it is current. (None, None) means no usable entry.
    """
    try:
        hit = cache_get(_ticker_cache_key(ticker, period_years))
    except Exception:
        return None, None
    if not hit:
        return None, None
    try:
        if isinstance(hit, dict) and hit.get("fmt") == "cols":
            df = pd.DataFrame(hit["cols"])
            df.insert(0, "Date", hit["index"])
        else:
            # Row-records entries, written before the columnar format.
            df = pd.DataFrame(hit["rows"] if isinstance(hit, dict) else hit)
        if df.empty or "Date" not in df.columns:
            return None, None
        df["Date"] = _trading_dates(df["Date"])
        df = df.dropna(subset=["Date"]).sort_values("Date")
        want_start, want_end = pd.Timestamp(start_s), pd.Timestamp(end_s)
        # A frame that starts materially later than the requested window can't
        # stand in for it — the caller wants the full history, not a stub.
        if df["Date"].min() > want_start + pd.Timedelta(days=_OHLCV_STALE_TOLERANCE):
            return None, None
        df = df[df["Date"] >= want_start].reset_index(drop=True)
        # `min_rows` guards the Portfolio Builder, which needs real history
        # before it will trust a covariance. A tracked portfolio asks a different
        # question - what are these worth today - and its window can legitimately
        # be a fortnight old, so it passes a small value rather than being told
        # its own holdings have no data.
        if len(df) < min_rows:
            return None, None
        df["Ticker"] = ticker
        df = df[[c for c in _OHLCV_COLS if c in df.columns]]
        last = df["Date"].max()
        tail = ((last + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                if (want_end - last).days > _OHLCV_STALE_TOLERANCE else None)
        return df, tail
    except Exception:
        return None, None


def _write_ticker_cache_many(frames: dict, period_years) -> int:
    """Persist several tickers at once. Returns how many landed.

    The writes are one HTTPS round-trip each and a cold 124-name build produces
    124 of them; run serially on the request path that is seconds of latency the
    user waits through for a cache only the NEXT visitor benefits from. They are
    pure I/O, so a small pool collapses it.
    """
    if not frames:
        return 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = ex.map(lambda kv: _write_ticker_cache(kv[0], kv[1], period_years),
                         list(frames.items()))
        return sum(1 for r in results if r)


def _write_ticker_cache(ticker, df, period_years) -> bool:
    """Persist one ticker's OHLCV under the stable key. Never raises.

    Columnar for the same reason the bundle is: row-records repeat all six field
    names 1,254 times per ticker, which measured 0.20 MB per row in Supabase and
    turns into a dict per session when read back. Storing one list per field is
    about half the bytes and a fraction of the objects — and the whole ranked
    universe lives in this table, so it is the difference between ~66 MB and
    ~30 MB of a 500 MB free-tier database.
    """
    try:
        out = df[[c for c in _OHLCV_COLS if c in df.columns and c != "Ticker"]].copy()
        payload = {
            "fmt":   "cols",
            "index": list(_trading_dates(out["Date"]).strftime("%Y-%m-%d")),
            "cols":  {c: [None if v != v else v
                          for v in out[c].astype(float).to_numpy().tolist()]
                      for c in out.columns if c != "Date"},
        }
        return bool(cache_set(_ticker_cache_key(ticker, period_years), payload,
                              ttl_hours=_OHLCV_TTL_HOURS))
    except Exception:
        return False


def warm_ticker_cache(tickers, period_years=5, log=print, chunk=60) -> int:
    """Fill Layer 2 for `tickers`. Returns how many were written.

    Called by precompute so the Portfolio Builder reads prices out of Supabase
    whatever candidate set the user's preferences produce, instead of depending
    on one live 124-symbol request landing on the request path.
    """
    from market_data import get_bars_batch
    end     = datetime.today()
    start   = end - timedelta(days=int(period_years * 365))
    end_s   = end.strftime("%Y-%m-%d")
    start_s = start.strftime("%Y-%m-%d")

    todo = [t for t in dict.fromkeys(tickers) if t]
    written = 0
    for i in range(0, len(todo), chunk):
        batch = todo[i:i + chunk]
        try:
            got = get_bars_batch(batch, start_s, end_s, "day")
        except Exception as e:
            log(f"   ⚠ warm batch {i//chunk + 1} failed ({e})")
            continue
        written += _write_ticker_cache_many(
            {t: df for t, df in got.items() if df is not None and len(df) > 60},
            period_years)
        log(f"   Warmed {written}/{min(i + chunk, len(todo))} of {len(todo)} tickers")
    return written


def _fetch_ohlcv(ticker, start, end, api_key, log=print, cache_years=None,
                 persist=False):
    """One ticker's OHLCV, memory cache → Layer 2 → live fetch.

    `cache_years` opts into READING the persistent layer, and is the window
    length the entry is keyed by. It is deliberately absent on the bundle-append
    path, which asks for a one- or two-day window: caching those under a
    ticker's key would overwrite five years of history with two rows.
    `persist` additionally opts into writing what was fetched back — see
    _MAX_INLINE_CACHE_WRITES for why that is not the default.
    """
    cache_key = f"{ticker}_{start}_{end}"

    # 1. In-memory cache (fastest) — lock-protected, Streamlit sessions are concurrent
    cached = _port_cache_get(cache_key)
    if cached is not None:
        return cached

    # 2. Supabase persistent cache (survives restarts, shared across users).
    #    This is the fallback for whatever the caller's batch request failed to
    #    return, so a provider outage degrades to slightly-stale prices instead
    #    of an empty portfolio. An entry a few sessions behind is topped up with
    #    a small tail fetch; if even that fails, the stale frame is still better
    #    than nothing and gets returned as-is.
    if cache_years is not None:
        db_df, _tail = _read_ticker_cache(ticker, cache_years, start, end)
        if db_df is not None:
            if _tail:
                try:
                    from market_data import get_bars
                    _t_df = get_bars(ticker, _tail, end, interval="day",
                                     polygon_key=api_key)
                    if _t_df is not None and len(_t_df):
                        _t_df = _t_df.assign(Ticker=ticker)
                        db_df = pd.concat(
                            [db_df, _t_df[[c for c in db_df.columns if c in _t_df.columns]]])
                        db_df["Date"] = _trading_dates(db_df["Date"])
                        db_df = (db_df.drop_duplicates("Date", keep="last")
                                      .sort_values("Date").reset_index(drop=True))
                        if persist:
                            _write_ticker_cache(ticker, db_df, cache_years)
                except Exception:
                    pass
            _port_cache_put(cache_key, db_df)
            return db_df

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
    # Normalise BEFORE de-duplicating: drop_duplicates on raw stamps keeps
    # 00:00 and 04:00 copies of one session as separate rows.
    df["Date"] = _trading_dates(df["Date"])
    df = df.drop_duplicates("Date", keep="last").sort_values("Date").reset_index(drop=True)

    _port_cache_put(cache_key, df)
    if persist and cache_years is not None:
        _write_ticker_cache(ticker, df, cache_years)
    return df


def fetch_portfolio_prices(tickers, period_years=2, api_key="", log=print,
                           persist_cache=False):
    end     = datetime.today()
    start   = end - timedelta(days=period_years*365)
    end_s   = end.strftime("%Y-%m-%d")
    start_s = start.strftime("%Y-%m-%d")

    price_dict, failed = {}, []
    thread_logs = []  # collect logs from threads — Streamlit can't be called from worker threads

    def _seed(ticker, df):
        _port_cache_put(f"{ticker}_{start_s}_{end_s}", df)

    # ── Batch prewarm: one request for everything not already in memory ──────
    # Measured on the 124-name candidate pool a 50-holding request produces:
    # this single batched call returns all of them in ~10s, while consulting the
    # per-ticker Supabase cache first cost 1.2s PER TICKER — 154s to avoid a 10s
    # fetch. So the network is the fast path here and the cache is the fallback,
    # which is the opposite of the usual arrangement and worth stating plainly.
    # Layer 2 is consulted inside _fetch_ohlcv, for whatever this batch fails to
    # return; see the note on _MAX_INLINE_CACHE_WRITES.
    _uncached = [t for t in tickers
                 if _port_cache_get(f"{t}_{start_s}_{end_s}") is None]
    if len(_uncached) > 3:
        try:
            from market_data import get_bars_batch
            _batch = get_bars_batch(_uncached, start_s, end_s, "day")
            _fresh = {}
            for _bt, _bdf in _batch.items():
                _d = _bdf.copy()
                _d["Ticker"] = _bt
                _d = _d[["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"]]
                _seed(_bt, _d)
                _fresh[_bt] = _d
            if persist_cache or len(_fresh) <= _MAX_INLINE_CACHE_WRITES:
                _write_ticker_cache_many(_fresh, period_years)
            thread_logs.append(f"   ⚡ batch-fetched {len(_batch)}/{len(_uncached)} tickers in one request")
            if len(_batch) < len(_uncached):
                # Worth saying out loud: the shortfall is what drops through to
                # the per-ticker path, and a large shortfall is the data provider
                # throttling us, not a set of bad symbols.
                thread_logs.append(
                    f"   ⚠ {len(_uncached) - len(_batch)} ticker(s) missing from the batch "
                    f"— falling back to the cached copy or an individual fetch")
        except Exception as _e:
            thread_logs.append(f"   ⚠ batch prewarm failed ({_e}) — falling back to per-ticker")

    # Same budget as the batch path: if the batch came back empty every name
    # falls through to individual fetches, and writing all of them back would
    # cost more than the fetch did.
    _persist_one = persist_cache or len(_uncached) <= _MAX_INLINE_CACHE_WRITES

    def fetch_one(ticker):
        msgs = []
        df = _fetch_ohlcv(ticker, start_s, end_s, api_key, log=lambda m: msgs.append(m),
                          cache_years=period_years, persist=_persist_one)
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
        # Named distinctly so the UI can tell "the provider refused us" apart
        # from "those aren't real symbols" — every ticker here came from our own
        # ranked universe, so it is never the latter.
        raise ValueError(
            f"upstream price fetch failed: no data for any of {len(tickers)} tickers "
            f"— the market-data provider returned nothing (rate limit or outage)")

    closes = {}
    for t, df in price_dict.items():
        s_ = df.set_index(_trading_dates(df["Date"]))["Close"].rename(t)
        closes[t] = s_[~s_.index.duplicated(keep="last")]
    close_df = pd.DataFrame(closes).sort_index()
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
    close_df, returns_df = _finalise_matrix(close_df, "live fetch", log)

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

    # Rank on the SAME horizon precompute uses (~1 year), not the full 5-year
    # price window this frame carries. Previously the two paths scored on
    # different lookbacks, so whether the nightly cache happened to be warm
    # silently changed which stocks were picked — the same inputs produced
    # different portfolios depending on infrastructure state.
    if len(returns_df) > ann:
        returns_df = returns_df.iloc[-ann:]

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
        # Volatility-adjusted, matching precompute: raw momentum mostly ranks by
        # beta, so the highest-vol name wins by construction.
        mom_adj  = momentum / ann_vol if ann_vol > 0 else 0.0
        return {"sharpe": sharpe, "ann_vol": ann_vol, "momentum": mom_adj}

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
        """Percentile rank, matching precompute. Min-max let a single outlier
        rescale every other name; a rank is immune to that."""
        n = len(vals)
        if n <= 1:
            return {t: 0.5 for t in cand}
        order = sorted(range(n), key=lambda i: vals[i])
        out   = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0
            for k in range(i, j + 1):
                out[order[k]] = avg / (n - 1)
            i = j + 1
        return {t: out[idx] for idx, t in enumerate(cand)}

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

def _finalise_matrix(close_df: pd.DataFrame, label: str, log=None):
    """Collapse to one row per session, ffill, and ASSERT the invariant.

    The assertion is the point. This defect was invisible for as long as it
    existed because every number it corrupted still looked plausible — a
    volatility of 14.91% is not obviously wrong unless you know it should be
    17.21%. A cheap structural check at the boundary is what makes that class
    of corruption loud instead of silent.
    """
    close_df = _dedupe_by_date(close_df, label, log)
    close_df = close_df.ffill().dropna()
    n, uniq = len(close_df.index), close_df.index.nunique()
    if n != uniq:                                    # unreachable after dedupe
        raise AssertionError(f"{label}: {n} rows over {uniq} distinct sessions")
    returns_df = close_df.pct_change().dropna()
    return close_df, returns_df


def _trading_dates(values) -> pd.DatetimeIndex:
    """Coerce anything date-like to tz-naive midnight calendar dates.

    A daily bar identifies a trading DAY, but the sources disagree on how to
    stamp it: some return tz-naive midnight, others a UTC instant. US Eastern
    midnight is 04:00 UTC under EDT and 05:00 under EST, so the same session
    arrives as 00:00, 04:00 or 05:00 depending on ticker, source and time of
    year. Those are three distinct keys to pandas.

    That mattered because the price matrix is built by unioning per-ticker
    Series: mismatched stamps split one session into several index entries,
    ffill() populates the copies, dropna() keeps them, and the return series
    picks up a run of exactly-zero days. Measured on a 45-name fetch: 1,672
    rows over 1,255 real sessions, 25.1% of them zero-return, dragging SPY's
    annualised volatility from 17.21% down to 14.91%. Every covariance,
    correlation, Sharpe and optimiser weight downstream inherited that.

    Normalising to a naive midnight date makes the union key the session
    itself, which is the only thing a daily bar actually identifies.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(values, errors="coerce", utc=False))
    if idx.tz is not None:
        idx = idx.tz_convert(None) if idx.tz is not None else idx
    return idx.normalize()


def _dedupe_by_date(df: pd.DataFrame, label: str = "", log=None) -> pd.DataFrame:
    """Collapse a frame onto one row per calendar date, keeping the last.

    Applied after normalisation, so the duplicates being collapsed are restamped
    copies of one session rather than distinct observations. Keeping the last
    matches the append path, which already resolved overlaps with keep="last".
    """
    df = df.copy()
    df.index = _trading_dates(df.index)
    dupes = int(df.index.duplicated().sum())
    if dupes and log:
        log(f"   Collapsed {dupes} duplicate-date row(s) in {label}")
    return df[~df.index.duplicated(keep="last")].sort_index()


def _close_df_to_payload(close_df: pd.DataFrame, failed: list) -> dict:
    """Serialise the price matrix column-wise rather than as row records.

    to_dict(orient="records") on a 1254 x 123 matrix materialises 1254 dicts of
    123 keys each — 154k dict entries, measured at 8 MB of Python objects and a
    26 MB RSS spike to serialise a frame that occupies 1.2 MB. That ran on the
    request path of a service Render had just reported over its memory limit.
    One list per ticker carries the same information for a third fewer objects
    and ~30% less JSON.
    """
    return {
        "fmt":     "cols",
        "index":   list(_trading_dates(close_df.index).strftime("%Y-%m-%d")),
        "columns": [str(c) for c in close_df.columns],
        "values":  [[None if v != v else v
                     for v in close_df[c].astype(float).to_numpy().tolist()]
                    for c in close_df.columns],
        "failed":  list(failed),
    }


def _payload_to_close_df(cached: dict) -> pd.DataFrame:
    if isinstance(cached, dict) and cached.get("fmt") == "cols":
        close_df = pd.DataFrame(dict(zip(cached["columns"], cached["values"])),
                                index=pd.Index(cached["index"], name="Date"))
    else:
        # Row-records bundles written before the columnar format. They stay
        # readable until their 400-day TTL runs out.
        close_df = pd.DataFrame(cached["close"]).set_index("Date")
    close_df = close_df.apply(pd.to_numeric, errors="coerce")
    # Bundles written before the normalisation fix carry mixed stamps, so the
    # collapse has to happen on read as well as on write.
    return _dedupe_by_date(close_df, "cached bundle")


def _close_df_to_price_dict(close_df: pd.DataFrame) -> dict:
    price_dict = {}
    for t in close_df.columns:
        s = close_df[t].dropna().reset_index()
        s.columns = ["Date", "Close"]
        s["Ticker"] = t
        price_dict[t] = s
    return price_dict


def fetch_portfolio_prices_cached(tickers, period_years=2, api_key="", log=print,
                                  persist_cache=False):
    """
    Fetches portfolio price history with a two-layer cache:

      Layer 1 — Bundle cache  (portfolio_prices_hist_<hash-of-tickers>)
        Fast path for the EXACT same ticker set as a prior call. One Supabase
        read returns a pre-assembled close_df. Changes in sidebar preferences
        change the ticker set and miss this layer.

      Layer 2 — Per-ticker cache  (ohlcv2_<ticker>_<years>y)
        Read up-front in `fetch_portfolio_prices`, before any network call.
        Survives sidebar preference changes because it's keyed per ticker,
        not per set, and survives the calendar because it's keyed by window
        LENGTH, not by today's dates. Warmed daily by precompute.py over the
        whole ranked universe, so any candidate set the preferences produce
        is covered. Entries a few sessions behind are topped up with one
        small tail fetch rather than refetched.

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
                            _s = df.set_index(_trading_dates(df["Date"]))["Close"].rename(ticker)
                            new_dfs[ticker] = _s[~_s.index.duplicated(keep="last")]

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

            close_df, returns_df = _finalise_matrix(close_df, "bundle cache", log)
            price_dict = _close_df_to_price_dict(close_df)
            log(f"   ✅ {len(price_dict)} tickers loaded from bundle cache "
                f"({len(close_df)} sessions)")
            return price_dict, close_df, returns_df, failed

        except Exception as e:
            log(f"   ⚠ Bundle cache parse failed ({e}) — falling back to per-ticker")

    # ── Layers 2+3: per-ticker cache, then a live fetch for what's missing ────
    # fetch_portfolio_prices reads the Supabase per-ticker cache for every name
    # before touching the network (warmed daily by precompute.py). Only tickers
    # missing from that cache trigger a live fetch.
    log(f"   🔍 No matching bundle for this ticker set — checking per-ticker caches "
        f"for {len(tickers)} tickers (warmed tickers return instantly)")
    price_dict, close_df, returns_df, failed = fetch_portfolio_prices(
        tickers, period_years=period_years, api_key=api_key, log=log,
        persist_cache=persist_cache
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


def suggest_peers(ticker: str, sector: str = "", n: int = 4, api_key: str = "",
                  market_cap: float = 0) -> list:
    """Same-sector companies closest in size to `ticker`.

    The Analysis page shipped a "Peer Comparison" checkbox that was ticked by
    default and did nothing: the fetch was gated on `do_peers and peers_list`,
    and peers_list came only from a text box the user had to fill in by hand. So
    for everyone who did not type peer tickers themselves, a control that said it
    was on produced no peer section at all.

    Peers are derived rather than fetched. There is no FMP key configured, and
    Polygon has no peers endpoint, but precompute already publishes a sector and
    a market-cap estimate for the whole ranked universe every weekday - so the
    data to pick sensible peers is already sitting in the cache that the
    Portfolio Builder reads.

    Three sources, in order, because the first one is not always there: the
    rankings read is a Supabase round-trip that intermittently comes back empty
    (measured: one attempt in four), and a peer list that vanishes on a flaky
    read is worse than one that is merely approximate.

      1. Rankings - same sector, ranked by closeness in market cap on a log
         scale, so a mega-cap gets mega-cap peers rather than whatever is
         alphabetically near it.
      2. SECTOR_UNIVERSE - the static in-repo sector map. No size matching, but
         it cannot fail.
      3. Nothing - returns [], and the caller renders no peer section, which is
         exactly the old behaviour.
    """
    import math

    t = (ticker or "").strip().upper()
    if not t:
        return []

    try:
        rankings = get_sharpe_rankings(api_key) or {}
    except Exception:
        rankings = {}

    entry = rankings.get(t) or {}
    sec = entry.get("sector") or (sector or "")
    if not sec or str(sec).lower() in ("unknown", "cryptocurrency", ""):
        return []

    # 1. Ranked universe: same sector, nearest in size.
    pool = [(k, v) for k, v in rankings.items()
            if not str(k).startswith("_")
            and k != t
            and isinstance(v, dict)
            and str(v.get("sector", "")).lower() == str(sec).lower()
            and v.get("is_operating", True)]
    if pool:
        # The subject's own size can be missing even when its peers' are not
        # (measured: XOM has no mcap_est while 25 of the 28 Energy names do), so
        # the caller may supply what it already knows from the company details.
        mc = market_cap or entry.get("mcap_est") or 0
        if mc > 0:
            def _distance(kv):
                other = kv[1].get("mcap_est")
                if not other or other <= 0:
                    return float("inf")     # unsized names sort last, not first
                return abs(math.log(other / mc))
            pool.sort(key=_distance)
        else:
            # No anchor to be close to. Largest-first beats dictionary order:
            # unsorted, XOM came back against HP, PR, SM and APA - four small
            # caps - where the sector's majors are the obvious comparables.
            pool.sort(key=lambda kv: -(kv[1].get("mcap_est") or 0))
        return [k for k, _ in pool[:n]]

    # 2. Static sector map - always available, no size matching.
    for name, tickers in SECTOR_UNIVERSE.items():
        if str(name).lower() == str(sec).lower():
            return [x for x in tickers if x != t][:n]

    return []


# ── Dynamic, rules-based universe ─────────────────────────────────────────────
# SECTOR_UNIVERSE above is a hand-typed list, and it rots: as of 2026-07-28, 10 of
# its 328 names no longer trade (ANSS, HES, K, SEE, IPG, PARA, FI, CTRA, MPW, and
# BF.B via a symbol-format bug). Dead tickers are silently dropped downstream, so
# the effective universe shrinks with nobody noticing.
#
# This builds the universe from Polygon's reference endpoint instead — active US
# common stock on a major exchange — so delistings disappear and new listings
# appear on their own. It does NOT fix survivorship bias in *backtests*: that
# needs point-in-time constituent history, which is a paid product. It fixes rot.

_MAJOR_EXCHANGES = {"XNYS", "XNAS", "ARCX", "BATS"}   # NYSE, Nasdaq, NYSE Arca, Cboe BZX


def build_dynamic_universe(api_key, max_tickers=4000, log=print) -> list:
    """Active US common stock on a major exchange, from Polygon reference data.

    Cached a week — listings change slowly, and this is a paginated crawl.
    Returns [] on failure so callers can fall back to SECTOR_UNIVERSE.
    """
    CK = "dynamic_universe_v1"
    try:
        hit = cache_get(CK)
        if hit:
            log(f"   Universe from cache: {len(hit)} tickers")
            return hit
    except Exception:
        pass

    out, url = [], f"{POLYGON_BASE}/v3/reference/tickers"
    params = {"market": "stocks", "type": "CS", "active": "true",
              "limit": 1000, "apiKey": api_key}
    pages = 0
    try:
        while url and len(out) < max_tickers and pages < 12:
            r = requests.get(url, params=params if pages == 0 else {"apiKey": api_key},
                             timeout=30)
            if r.status_code != 200:
                log(f"   ⚠ universe fetch HTTP {r.status_code}")
                break
            data = r.json()
            for it in data.get("results", []):
                if it.get("primary_exchange") in _MAJOR_EXCHANGES:
                    t = it.get("ticker", "")
                    # Skip warrants/units/preferreds that slip through as CS, and
                    # anything with a class suffix our symbol mapping can't route.
                    if t and t.isalpha() and len(t) <= 5:
                        out.append(t)
            url   = data.get("next_url")
            pages += 1
        out = sorted(set(out))[:max_tickers]
        log(f"   Universe built: {len(out)} active US common stocks ({pages} pages)")
        if out:
            try:
                cache_set(CK, out, ttl_hours=168)   # one week
            except Exception:
                pass
        return out
    except Exception as e:
        log(f"   ⚠ universe build failed ({e}) — falling back to static list")
        return []


def apply_liquidity_screen(price_map, min_dollar_volume=5_000_000, min_price=5.0,
                           log=print) -> dict:
    """Drop names that aren't realistically tradable.

    Uses median daily dollar volume (close × volume) over the fetched window, so
    it costs no extra API calls — the volume already arrived with the prices.
    This is a better tradability test than market cap, and it only becomes
    relevant once the universe is dynamic: the old hand-picked list was all
    mega-caps, which is why a liquidity screen would have been pointless there.
    """
    kept, dropped = {}, []
    for t, df in price_map.items():
        try:
            if "Volume" not in df.columns or "Close" not in df.columns:
                kept[t] = df           # can't judge — keep rather than lose it
                continue
            dv = float((df["Close"] * df["Volume"]).median())
            px = float(df["Close"].iloc[-1])
            if dv >= min_dollar_volume and px >= min_price:
                kept[t] = df
            else:
                dropped.append(t)
        except Exception:
            kept[t] = df
    if dropped:
        log(f"   Liquidity screen: dropped {len(dropped)} of {len(price_map)} "
            f"(< ${min_dollar_volume/1e6:.0f}M median daily volume or < ${min_price:.0f})")
    return kept
