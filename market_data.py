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
    # Share-class tickers: most sources write BRK.B / BF.B, Yahoo wants BRK-B.
    # Without this the request 404s and the ticker is silently dropped — BF.B sat
    # in SECTOR_UNIVERSE looking like a delisting when it trades perfectly well.
    if "." in t:
        return t.replace(".", "-")
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


def consensus_from_recommendation(rec: dict | None) -> dict | None:
    """Turn a Finnhub recommendation row into a single Wall-Street verdict.

    Shared by the on-screen Analyst View and the Excel report so the Buy/Hold/Sell
    label is computed identically in both places. Expects a dict shaped like
    {strongBuy, buy, hold, sell, strongSell, period}; returns None when there are
    no ratings to score. The verdict is analysts' consensus, not QuantWizard's view.
    """
    if not rec:
        return None
    sb = int(rec.get("strongBuy", 0) or 0)
    b  = int(rec.get("buy", 0) or 0)
    h  = int(rec.get("hold", 0) or 0)
    s  = int(rec.get("sell", 0) or 0)
    ss = int(rec.get("strongSell", 0) or 0)
    total = sb + b + h + s + ss
    if total == 0:
        return None
    score = (sb * 2 + b - s - ss * 2) / total
    verdict, color = (
        ("Strong Buy",   "#059669") if score >=  1.0 else
        ("Buy",          "#16a34a") if score >=  0.3 else
        ("Hold",         "#64748b") if score >  -0.3 else
        ("Sell",         "#dc2626") if score >  -1.0 else
        ("Strong Sell",  "#991b1b"))
    return {
        "verdict": verdict, "color": color, "score": score, "total": total,
        "strong_buy": sb, "buy": b, "hold": h, "sell": s, "strong_sell": ss,
        "period": str(rec.get("period", ""))[:7],
    }


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


def get_bars_batch(tickers: list, start: str, end: str, interval: str = "day") -> dict:
    """
    Fetch daily OHLCV for MANY tickers in a single yfinance request — far faster
    than per-ticker calls and avoids Yahoo throttling a burst of sequential
    requests. Returns {ticker: standardized DataFrame}; tickers with no data are
    simply absent (caller falls back to per-ticker get_bars for those).
    """
    if not tickers:
        return {}
    try:
        import yfinance as yf
        yint    = _YF_INTERVAL.get(interval, "1d")
        end_excl = (pd.to_datetime(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d") \
                   if yint == "1d" else end
        sym_map = {to_yahoo_symbol(t): t for t in tickers}      # yahoo symbol -> original
        raw = yf.download(list(sym_map.keys()), start=start, end=end_excl, interval=yint,
                          auto_adjust=True, progress=False, group_by="ticker", threads=True)
        if raw is None or raw.empty:
            return {}
        out: dict = {}
        multi = len(sym_map) > 1
        for ysym, orig in sym_map.items():
            try:
                sub = (raw[ysym] if multi else raw).reset_index()
                date_col = "Datetime" if "Datetime" in sub.columns else "Date"
                df = pd.DataFrame({
                    "Date":   pd.to_datetime(sub[date_col]).dt.tz_localize(None),
                    "Open":   sub["Open"].astype(float),
                    "High":   sub["High"].astype(float),
                    "Low":    sub["Low"].astype(float),
                    "Close":  sub["Close"].astype(float),
                    "Volume": sub["Volume"].fillna(0).astype(float),
                }).dropna(subset=["Close"]).sort_values("Date").reset_index(drop=True)
                if len(df) > 0:
                    out[orig] = df
            except Exception:
                pass
        return out
    except Exception:
        return {}


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


# ── Financial-statement supplement (yfinance) ─────────────────────────────────
# Polygon's cash-flow endpoint returns only net_cash_flow_* aggregates — there is
# no capital-expenditure line — so free cash flow (operating cash flow − capex)
# cannot be derived from it, and every FCF metric came out N/A. Altman-Z fails
# for the same reason: it needs retained earnings and total assets, which the
# Polygon balance sheet doesn't carry either.
#
# yfinance has all of them, and is already a dependency, so it fills the gaps.
# Everything here returns None on failure — a missing supplement must degrade to
# the previous "N/A" behaviour, never break the report.

def _yf_row(df, *names):
    """First matching row from a yfinance statement frame, newest-first."""
    if df is None or getattr(df, "empty", True):
        return None
    for want in names:
        for idx in df.index:
            if str(idx).strip().lower() == want.strip().lower():
                vals = [None if v != v else float(v) for v in df.loc[idx].values]
                return vals
    return None


def get_financials_supplement(ticker: str) -> dict | None:
    """Capex / FCF / balance-sheet fields Polygon doesn't provide.

    Returns newest-first lists so the caller can index [0] for the latest
    period, matching how the Polygon frames are ordered.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(to_yahoo_symbol(ticker))
        cf, bs = t.cashflow, t.balance_sheet
    except Exception:
        return None

    out = {
        "fcf":               _yf_row(cf, "Free Cash Flow"),
        "capex":             _yf_row(cf, "Capital Expenditure"),
        "operating_cf":      _yf_row(cf, "Operating Cash Flow",
                                     "Total Cash From Operating Activities"),
        "retained_earnings": _yf_row(bs, "Retained Earnings"),
        "total_assets":      _yf_row(bs, "Total Assets"),
        "total_liabilities": _yf_row(bs, "Total Liabilities Net Minority Interest",
                                     "Total Liab"),
        "current_assets":    _yf_row(bs, "Current Assets", "Total Current Assets"),
        "current_liabilities": _yf_row(bs, "Current Liabilities",
                                       "Total Current Liabilities"),
        # Cash matters more than it looks. Polygon's balance sheet carries no
        # cash field at all, so when the SEC path is unavailable and the code
        # falls back to Polygon, net debt silently collapses to GROSS debt —
        # for NKE that read $7.96B against a true $0.38B, overstating it by the
        # entire cash balance, inflating enterprise value and understating fair
        # value per share. This is the fallback's only source of cash.
        "cash":              _yf_row(bs, "Cash Cash Equivalents And Short Term Investments",
                                     "Cash And Cash Equivalents",
                                     "CashAndCashEquivalents"),
        "total_debt":        _yf_row(bs, "Total Debt"),
    }
    return out if any(v for v in out.values()) else None


def _yf_profile(ticker: str) -> dict:
    """One ticker's company profile from yfinance .info, normalised and None-safe.

    `ok` records whether the lookup actually succeeded. Without it a rate-limited
    fetch is indistinguishable from a company with no sector, and the caller
    cheerfully caches the emptiness — which is how a whole report came out with
    "Unknown" against all 18 holdings.
    """
    out = {"name": ticker, "sector": None, "industry": None, "pe": None,
           "div_yield": None, "beta": None, "rev_growth": None,
           "eps_growth": None, "market_cap": None, "quote_type": None,
           "ok": False}
    info = None
    for attempt in range(3):
        try:
            import yfinance as yf
            info = yf.Ticker(to_yahoo_symbol(ticker)).get_info() or {}
            if info:
                break
        except Exception:
            info = None
        # Yahoo throttles bursts; a short escalating pause clears most 429s.
        time.sleep(0.6 * (attempt + 1))
    if not info:
        return out
    out["ok"] = True

    def num(*keys):
        for k in keys:
            v = info.get(k)
            if isinstance(v, (int, float)) and v == v:
                return float(v)
        return None

    qt = str(info.get("quoteType") or "").upper()
    out["quote_type"] = qt
    out["name"]       = info.get("longName") or info.get("shortName") or ticker
    is_fund = qt in ("ETF", "MUTUALFUND", "MONEYMARKET", "INDEX")
    # Funds have no sector/industry of their own; label them as a bucket rather
    # than leaving "Unknown", which would read as missing data in the report.
    out["sector"]   = info.get("sector") or ("Fund / ETF" if is_fund else None)
    out["industry"] = info.get("industry") or (info.get("category") if is_fund else None) \
                      or ("Fund / ETF" if is_fund else None)
    out["pe"]         = num("trailingPE")
    out["beta"]       = num("beta", "beta3Year")
    out["market_cap"] = num("marketCap", "totalAssets")
    # yfinance's dividendYield changed convention (fraction → percent) across
    # versions, so prefer rate/price which is unambiguous; the >0.25 heuristic
    # catches the percent form on the fallback (no real yield exceeds 25%).
    rate, px = num("trailingAnnualDividendRate"), num("currentPrice", "regularMarketPrice")
    if rate is not None and px:
        out["div_yield"] = rate / px
    else:
        dy = num("dividendYield", "yield")
        out["div_yield"] = (dy / 100 if dy is not None and dy > 0.25 else dy)
    # YoY fractions as reported (revenueGrowth / earningsGrowth)
    out["rev_growth"] = num("revenueGrowth")
    out["eps_growth"] = num("earningsGrowth")
    return out


def get_ticker_profiles(tickers: list) -> dict:
    """{ticker: profile dict} for the portfolio reports — threaded batch.

    Every field may be None (funds, data gaps, yfinance failures); callers must
    render "N/A" rather than assume coverage, and should check
    `profiles_are_usable()` before caching the result.

    Concurrency is deliberately modest: eight simultaneous .info calls is enough
    to trip Yahoo's rate limiter on a portfolio of ~20 names, and every throttled
    call comes back as a profile with no sector.
    """
    from concurrent.futures import ThreadPoolExecutor
    tickers = list(dict.fromkeys(t.upper() for t in tickers if t))
    if not tickers:
        return {}
    with ThreadPoolExecutor(max_workers=min(4, len(tickers))) as ex:
        return dict(zip(tickers, ex.map(_yf_profile, tickers)))


def profiles_are_usable(profiles: dict, min_share: float = 0.5) -> bool:
    """True when enough of the batch actually resolved to be worth caching.

    A throttled fetch returns structurally valid dicts full of None, so callers
    cannot tell success from failure by looking at the values. Caching that for
    a day means every report until tomorrow says "Unknown".
    """
    if not profiles:
        return False
    ok = sum(1 for p in profiles.values() if p and p.get("ok"))
    return ok >= max(1, int(len(profiles) * min_share))
