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


_MOVERS_CACHE = {"ts": 0, "gainers": [], "losers": []}
_MOVERS_TTL   = 300  # refresh every 5 minutes


def _parse_ticker_snapshot(t, positive=True):
    """Extract price and change from a Polygon snapshot ticker — handles multiple API formats."""
    ticker = t.get("ticker", "")
    # Price: try lastTrade.p, then day.c, then lastQuote.P
    price = (t.get("lastTrade", {}).get("p")
             or t.get("day", {}).get("c")
             or t.get("lastQuote", {}).get("P")
             or 0)
    # Change %: try todaysChangePerc, then compute from day open/close
    chg_pct = t.get("todaysChangePerc")
    if chg_pct is None:
        day_open  = t.get("day", {}).get("o", 0)
        day_close = t.get("day", {}).get("c", 0)
        chg_pct   = ((day_close / day_open - 1) * 100) if day_open else 0
    sign = "+" if positive else ""
    return {
        "Ticker": ticker,
        "Price":  f"${float(price):,.2f}" if price else "N/A",
        "Change": f"{sign}{float(chg_pct):.2f}%",
    }


def get_top_movers(api_key, limit=5):
    now = time.time()
    if now - _MOVERS_CACHE["ts"] < _MOVERS_TTL:
        return _MOVERS_CACHE["gainers"], _MOVERS_CACHE["losers"]

    gainers, losers = [], []
    for url_end, is_positive, target in [
        ("gainers", True,  gainers),
        ("losers",  False, losers),
    ]:
        try:
            r = requests.get(
                f"{POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/{url_end}",
                params={"apiKey": api_key}, timeout=10,
            )
            if r.status_code == 200:
                for t in r.json().get("tickers", [])[:limit]:
                    entry = _parse_ticker_snapshot(t, positive=is_positive)
                    if entry["Ticker"]:
                        target.append(entry)
        except Exception:
            pass

    if gainers or losers:
        _MOVERS_CACHE.update({"ts": now, "gainers": gainers, "losers": losers})
    return gainers, losers
