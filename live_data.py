import requests
import pandas as pd
from datetime import datetime, timedelta
import time

POLYGON_BASE = "https://api.polygon.io"

_PRICE_CACHE    = {}
_INTRADAY_CACHE = {}
CACHE_TTL       = 30


def get_live_price(ticker, api_key):
    now    = time.time()
    cached = _PRICE_CACHE.get(ticker)
    if cached and (now - cached["ts"]) < CACHE_TTL:
        return cached
    try:
        r = requests.get(f"{POLYGON_BASE}/v2/last/trade/{ticker}",
                         params={"apiKey": api_key}, timeout=10)
        if r.status_code == 200:
            price = r.json().get("results", {}).get("p", 0)
            prev  = get_prev_close(ticker, api_key)
            change = price - prev if prev else 0
            pct    = (change / prev * 100) if prev else 0
            entry  = {"ts": now, "ticker": ticker, "price": price,
                      "change": change, "pct": pct, "prev": prev,
                      "time": datetime.now().strftime("%H:%M:%S")}
            _PRICE_CACHE[ticker] = entry
            return entry
    except Exception:
        pass
    return _PRICE_CACHE.get(ticker)


def get_prev_close(ticker, api_key):
    # Walk back up to 5 days to handle weekends and market holidays
    for days_back in range(1, 6):
        try:
            date_str = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            r = requests.get(f"{POLYGON_BASE}/v1/open-close/{ticker}/{date_str}",
                             params={"adjusted": "true", "apiKey": api_key}, timeout=10)
            if r.status_code == 200:
                close = r.json().get("close", 0)
                if close:
                    return close
        except Exception:
            pass
    return 0


def get_intraday_data(ticker, api_key, multiplier=5, timespan="minute"):
    cache_key = f"{ticker}_{multiplier}_{timespan}"
    now       = time.time()
    cached    = _INTRADAY_CACHE.get(cache_key)
    if cached and (now - cached["ts"]) < CACHE_TTL:
        return cached["df"]
    try:
        today     = datetime.today().strftime("%Y-%m-%d")
        from_date = (datetime.today() - timedelta(days=3)).strftime("%Y-%m-%d")
        r = requests.get(
            f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_date}/{today}",
            params={"adjusted": "true", "sort": "asc", "limit": 1000, "apiKey": api_key},
            timeout=15)
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                df = pd.DataFrame(results)
                df = df.rename(columns={"t":"Time","o":"Open","h":"High",
                                        "l":"Low","c":"Close","v":"Volume"})
                df["Time"] = pd.to_datetime(df["Time"], unit="ms")
                today_dt   = datetime.today().date()
                df_today   = df[df["Time"].dt.date == today_dt]
                df_out     = df_today if not df_today.empty else df.tail(78)
                _INTRADAY_CACHE[cache_key] = {"ts": now, "df": df_out}
                return df_out
    except Exception:
        pass
    return _INTRADAY_CACHE.get(cache_key, {}).get("df")


_TAPE_CACHE = {"ts": 0, "items": []}
_TAPE_TTL   = 300   # refresh every 5 minutes

TAPE_TICKERS = ["AAPL","TSLA","NVDA","SPY","MSFT","AMZN","GOOGL",
                "META","JPM","QQQ","GLD","BRK.B","DIS","VTI","AMD","NFLX"]

# Grouped-daily snapshot of the two most recent trading days, shared by the
# ticker tape and Market Movers. The real-time snapshot endpoints
# (/v2/snapshot/...) require a higher Polygon plan and return 403 on the
# current tier, so we derive everything from the grouped daily-bars endpoint
# (/v2/aggs/grouped/...), which IS authorized and returns every US ticker for
# a date in a single call.
_GROUPED_CACHE = {"ts": 0, "latest": {}, "prev": {}}
_GROUPED_TTL   = 300


def _recent_grouped_days(api_key):
    """
    Return (latest, prev) dicts keyed by ticker -> daily bar for the two most
    recent trading days. Walks back from today to skip weekends/holidays.
    Cached for 5 minutes and shared across the tape and movers.
    """
    now = time.time()
    if now - _GROUPED_CACHE["ts"] < _GROUPED_TTL and _GROUPED_CACHE["latest"]:
        return _GROUPED_CACHE["latest"], _GROUPED_CACHE["prev"]

    days = []
    for days_back in range(0, 8):
        date_str = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        try:
            r = requests.get(
                f"{POLYGON_BASE}/v2/aggs/grouped/locale/us/market/stocks/{date_str}",
                params={"adjusted": "true", "apiKey": api_key}, timeout=20,
            )
            if r.status_code == 200:
                results = r.json().get("results", [])
                if results:
                    days.append({row["T"]: row for row in results if row.get("T")})
                    if len(days) >= 2:
                        break
        except Exception:
            pass

    if days:
        _GROUPED_CACHE["ts"]     = now
        _GROUPED_CACHE["latest"] = days[0]
        _GROUPED_CACHE["prev"]   = days[1] if len(days) > 1 else {}
    return _GROUPED_CACHE["latest"], _GROUPED_CACHE["prev"]


def get_tape_prices(api_key: str) -> list:
    """
    Build the scrolling ticker tape from the grouped daily endpoint.
    Returns list of (symbol, price_str, change_str, is_up). Change is the
    close-to-close move vs the prior trading day. Cached for 5 minutes.
    """
    now = time.time()
    if now - _TAPE_CACHE["ts"] < _TAPE_TTL and _TAPE_CACHE["items"]:
        return _TAPE_CACHE["items"]

    latest, prev = _recent_grouped_days(api_key)
    items = []
    for sym in TAPE_TICKERS:
        bar = latest.get(sym)
        if not bar:
            continue
        price = bar.get("c") or 0
        if not price:
            continue
        # Close-to-close vs prior day; fall back to same-day open if unavailable.
        prev_close = (prev.get(sym, {}) or {}).get("c") or bar.get("o") or 0
        chg_pct = ((price / prev_close - 1) * 100) if prev_close else 0
        is_up   = chg_pct >= 0
        sign    = "+" if is_up else ""
        items.append((sym, f"${float(price):,.2f}",
                      f"{sign}{float(chg_pct):.2f}%", is_up))

    if items:
        _TAPE_CACHE["ts"]    = now
        _TAPE_CACHE["items"] = items
        return items

    # Fallback — return cached even if stale
    return _TAPE_CACHE["items"] or []


_MOVERS_CACHE = {"ts": 0, "gainers": [], "losers": []}
_MOVERS_TTL   = 300  # refresh every 5 minutes


def get_top_movers(api_key, limit=5):
    """
    Top gainers/losers among a curated large-cap universe (SECTOR_UNIVERSE),
    ranked by close-to-close move. Restricting to recognizable S&P names keeps
    the list professional — the raw market-wide leaders are dominated by
    leveraged ETFs and micro-caps. Cached for 5 minutes.
    """
    now = time.time()
    if now - _MOVERS_CACHE["ts"] < _MOVERS_TTL and (_MOVERS_CACHE["gainers"] or _MOVERS_CACHE["losers"]):
        return _MOVERS_CACHE["gainers"], _MOVERS_CACHE["losers"]

    try:
        from portfolio_data import SECTOR_UNIVERSE
        universe = {t for lst in SECTOR_UNIVERSE.values() for t in lst}
    except Exception:
        universe = set(TAPE_TICKERS)

    latest, prev = _recent_grouped_days(api_key)
    rows = []
    for sym in universe:
        bar = latest.get(sym)
        if not bar:
            continue
        price = bar.get("c") or 0
        prev_close = (prev.get(sym, {}) or {}).get("c") or 0
        if not price or not prev_close:
            continue
        chg_pct = (price / prev_close - 1) * 100
        rows.append((sym, price, chg_pct))

    gainers, losers = [], []
    if rows:
        rows.sort(key=lambda x: x[2])
        for sym, price, chg in rows[-limit:][::-1]:
            gainers.append({"Ticker": sym, "Price": f"${price:,.2f}", "Change": f"+{chg:.2f}%"})
        for sym, price, chg in rows[:limit]:
            losers.append({"Ticker": sym, "Price": f"${price:,.2f}", "Change": f"{chg:.2f}%"})
        _MOVERS_CACHE.update({"ts": now, "gainers": gainers, "losers": losers})

    return _MOVERS_CACHE["gainers"], _MOVERS_CACHE["losers"]
