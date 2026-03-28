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


def get_top_movers(api_key, limit=5):
    gainers, losers = [], []
    try:
        r = requests.get(f"{POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/gainers",
                         params={"apiKey": api_key}, timeout=10)
        if r.status_code == 200:
            for t in r.json().get("tickers", [])[:limit]:
                gainers.append({
                    "Ticker": t.get("ticker",""),
                    "Price":  f"${t.get('lastTrade',{}).get('p',0):,.2f}",
                    "Change": f"+{t.get('todaysChangePerc',0):.2f}%",
                })
    except Exception:
        pass
    try:
        r = requests.get(f"{POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/losers",
                         params={"apiKey": api_key}, timeout=10)
        if r.status_code == 200:
            for t in r.json().get("tickers", [])[:limit]:
                losers.append({
                    "Ticker": t.get("ticker",""),
                    "Price":  f"${t.get('lastTrade',{}).get('p',0):,.2f}",
                    "Change": f"{t.get('todaysChangePerc',0):.2f}%",
                })
    except Exception:
        pass
    return gainers, losers
