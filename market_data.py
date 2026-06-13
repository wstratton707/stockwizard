"""
market_data.py — multi-source market-data router.

A stateless provider layer that picks the best source per data type and falls
back automatically, returning a standardized schema so callers never care who
answered:

  • get_quote(ticker)        live quote   → Finnhub (real-time) → Polygon last-trade
  • get_bars(ticker, s, e)   OHLCV bars   → yfinance (same-day, deep) → Polygon

Higher layers (data.py / portfolio_data.py / live_data.py) keep their caching and
enrichment and simply call into here for the raw fetch.

Why this split of sources:
  - Finnhub free gives real-time US quotes (Polygon's free tier 403s last-trade).
  - yfinance gives same-day closes and decades of history with no 2-year cap and
    no 5/min limit — but it's unofficial and can break, so Polygon stays as a net.
  - Polygon's grouped/bulk endpoint (tape, movers, 328-stock rankings) is still
    best for "every ticker in one call" and stays in live_data.py / precompute.

FINNHUB_API_KEY is optional: without it, quotes fall back to Polygon, so the app
works unchanged and real-time quotes light up the moment the key is set.
"""

import os
import time
import datetime as _dt

import pandas as pd
import requests

FINNHUB_BASE = "https://finnhub.io/api/v1"
POLYGON_BASE = "https://api.polygon.io"


def finnhub_key() -> str:
    return os.environ.get("FINNHUB_API_KEY", "").strip()


# ── Symbol mapping ────────────────────────────────────────────────────────────
# Polygon crypto uses "X:BTCUSD"; yfinance uses "BTC-USD"; Finnhub uses plain
# stock symbols. Stocks/ETFs pass through unchanged.

def to_yahoo_symbol(ticker: str) -> str:
    t = (ticker or "").upper()
    if t.startswith("X:") and t.endswith("USD"):
        return f"{t[2:-3]}-USD"          # X:BTCUSD -> BTC-USD
    return t


def to_finnhub_symbol(ticker: str) -> str:
    t = (ticker or "").upper()
    if t.startswith("X:") and t.endswith("USD"):
        return f"BINANCE:{t[2:-3]}USDT"  # crypto on Finnhub (best-effort)
    return t


def _fmt_ts(ts) -> str:
    try:
        return _dt.datetime.fromtimestamp(int(ts)).strftime("%H:%M:%S")
    except Exception:
        return _dt.datetime.now().strftime("%H:%M:%S")


# ── Live quote ────────────────────────────────────────────────────────────────

def get_quote(ticker: str, polygon_key: str = "") -> dict | None:
    """
    Standardized live quote, or None if no source has it.
    Returns: {price, change, pct, prev, open, high, low, time, source}
    Order: Finnhub (real-time) → Polygon last-trade.
    """
    key = finnhub_key()
    if key:
        q = _finnhub_quote(ticker, key)
        if q:
            return q
    if polygon_key:
        q = _polygon_quote(ticker, polygon_key)
        if q:
            return q
    return None


def _finnhub_quote(ticker: str, key: str) -> dict | None:
    try:
        r = requests.get(f"{FINNHUB_BASE}/quote",
                         params={"symbol": to_finnhub_symbol(ticker), "token": key},
                         timeout=8)
        if r.status_code == 200:
            d = r.json()
            price = d.get("c")
            if price:   # c == 0 means "no data"
                prev = float(d.get("pc") or 0)
                return {
                    "price":  float(price),
                    "change": float(d.get("d") or 0),
                    "pct":    float(d.get("dp") or 0),
                    "prev":   prev,
                    "open":   float(d.get("o") or 0),
                    "high":   float(d.get("h") or 0),
                    "low":    float(d.get("l") or 0),
                    "time":   _fmt_ts(d.get("t")),
                    "source": "finnhub",
                }
    except Exception:
        pass
    return None


def _polygon_prev_close(ticker: str, key: str) -> float:
    for days_back in range(1, 6):
        date_str = (_dt.date.today() - _dt.timedelta(days=days_back)).strftime("%Y-%m-%d")
        try:
            r = requests.get(f"{POLYGON_BASE}/v1/open-close/{ticker}/{date_str}",
                             params={"adjusted": "true", "apiKey": key}, timeout=8)
            if r.status_code == 200:
                c = r.json().get("close", 0)
                if c:
                    return float(c)
        except Exception:
            pass
    return 0.0


def _polygon_quote(ticker: str, key: str) -> dict | None:
    try:
        r = requests.get(f"{POLYGON_BASE}/v2/last/trade/{ticker}",
                         params={"apiKey": key}, timeout=8)
        if r.status_code == 200:
            price = r.json().get("results", {}).get("p")
            if price:
                prev = _polygon_prev_close(ticker, key)
                chg  = price - prev if prev else 0.0
                return {
                    "price":  float(price),
                    "change": float(chg),
                    "pct":    float(chg / prev * 100) if prev else 0.0,
                    "prev":   float(prev),
                    "open": 0.0, "high": 0.0, "low": 0.0,
                    "time":   time.strftime("%H:%M:%S"),
                    "source": "polygon",
                }
    except Exception:
        pass
    return None


# ── OHLCV bars ────────────────────────────────────────────────────────────────

# ── Analyst data (Finnhub free tier) ──────────────────────────────────────────

def get_analyst_data(ticker: str) -> dict:
    """
    Wall-Street analyst consensus + earnings-surprise history from Finnhub's free
    tier. Returns {} if no key, and omits any piece that isn't available:
      recommendation: {strongBuy, buy, hold, sell, strongSell, period}
      earnings:       [{period, actual, estimate, surprisePercent}, ...]  (recent first)
    (Price targets are a premium Finnhub endpoint, so they're intentionally skipped.)
    """
    key = finnhub_key()
    if not key:
        return {}
    sym = to_finnhub_symbol(ticker)
    out: dict = {}
    try:
        r = requests.get(f"{FINNHUB_BASE}/stock/recommendation",
                         params={"symbol": sym, "token": key}, timeout=8)
        if r.status_code == 200:
            data = r.json()
            if data:
                out["recommendation"] = data[0]   # most recent period
    except Exception:
        pass
    try:
        r = requests.get(f"{FINNHUB_BASE}/stock/earnings",
                         params={"symbol": sym, "token": key}, timeout=8)
        if r.status_code == 200:
            data = r.json()
            if data:
                out["earnings"] = data[:4]         # last 4 quarters
    except Exception:
        pass
    return out


_YF_INTERVAL = {"day": "1d", "1day": "1d", "week": "1wk", "month": "1mo",
                "1min": "1m", "5min": "5m", "15min": "15m", "1hour": "1h",
                "minute": "1m", "hour": "1h"}
_POLY_SPAN   = {"day": (1, "day"), "week": (1, "week"), "month": (1, "month"),
                "1min": (1, "minute"), "5min": (5, "minute"), "15min": (15, "minute"),
                "1hour": (1, "hour"), "minute": (1, "minute"), "hour": (1, "hour")}


def get_bars(ticker: str, start: str, end: str, interval: str = "day",
             polygon_key: str = "") -> pd.DataFrame | None:
    """
    Standardized OHLCV: columns [Date, Open, High, Low, Close, Volume], ascending,
    tz-naive dates. yfinance first (same-day, deep history), Polygon as fallback.
    """
    df = _yahoo_bars(ticker, start, end, interval)
    if df is not None and len(df) > 0:
        return df
    if polygon_key:
        df = _polygon_bars(ticker, start, end, interval, polygon_key)
        if df is not None and len(df) > 0:
            return df
    return None


def _yahoo_bars(ticker: str, start: str, end: str, interval: str) -> pd.DataFrame | None:
    try:
        import yfinance as yf
        yint = _YF_INTERVAL.get(interval, "1d")
        # yfinance 'end' is exclusive for daily — nudge it forward a day so today's
        # bar is included.
        end_excl = (pd.to_datetime(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d") \
                   if yint == "1d" else end
        raw = yf.Ticker(to_yahoo_symbol(ticker)).history(
            start=start, end=end_excl, interval=yint, auto_adjust=True)
        if raw is None or raw.empty:
            return None
        raw = raw.reset_index()
        date_col = "Datetime" if "Datetime" in raw.columns else "Date"
        out = pd.DataFrame({
            "Date":   pd.to_datetime(raw[date_col]).dt.tz_localize(None),
            "Open":   raw["Open"].astype(float),
            "High":   raw["High"].astype(float),
            "Low":    raw["Low"].astype(float),
            "Close":  raw["Close"].astype(float),
            "Volume": raw["Volume"].fillna(0).astype(float),
        })
        return out.dropna(subset=["Close"]).sort_values("Date").reset_index(drop=True)
    except Exception:
        return None


def _polygon_bars(ticker: str, start: str, end: str, interval: str, key: str) -> pd.DataFrame | None:
    mult, tspan = _POLY_SPAN.get(interval, (1, "day"))
    try:
        r = requests.get(
            f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/{mult}/{tspan}/{start}/{end}",
            params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key},
            timeout=20)
        if r.status_code == 200:
            res = r.json().get("results", [])
            if res:
                df = pd.DataFrame(res).rename(columns={
                    "t": "Date", "o": "Open", "h": "High", "l": "Low",
                    "c": "Close", "v": "Volume"})
                df["Date"] = pd.to_datetime(df["Date"], unit="ms")
                return df[["Date", "Open", "High", "Low", "Close", "Volume"]] \
                    .sort_values("Date").reset_index(drop=True)
    except Exception:
        pass
    return None
