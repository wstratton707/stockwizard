import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time

POLYGON_BASE = "https://api.polygon.io"
_PORT_CACHE  = {}
CACHE_TTL    = 300

SECTOR_UNIVERSE = {
    "Technology":             ["AAPL","MSFT","NVDA","GOOGL","META","AVGO","AMD","CRM","INTC","ORCL"],
    "Health Care":            ["UNH","JNJ","LLY","ABBV","MRK","TMO","ABT","DHR","PFE","AMGN"],
    "Financials":             ["JPM","V","MA","BAC","WFC","GS","MS","BLK","AXP","C"],
    "Consumer Discretionary": ["AMZN","TSLA","HD","MCD","NKE","SBUX","TJX","BKNG","CMG","LOW"],
    "Consumer Staples":       ["WMT","PG","KO","COST","PEP","PM","MDLZ","CL","GIS","KHC"],
    "Industrials":            ["CAT","UPS","HON","BA","RTX","GE","DE","MMM","LMT","FDX"],
    "Energy":                 ["XOM","CVX","COP","EOG","SLB","MPC","VLO","PSX","OXY","HES"],
    "Materials":              ["LIN","APD","ECL","SHW","NEM","FCX","NUE","VMC","MLM","ALB"],
    "Real Estate":            ["PLD","AMT","EQIX","CCI","PSA","O","DLR","WELL","SPG","VTR"],
    "Utilities":              ["NEE","DUK","SO","D","AEP","EXC","XEL","ES","WEC","ED"],
    "Communication Services": ["GOOGL","META","NFLX","DIS","CMCSA","T","VZ","TMUS","EA","TTWO"],
}

SECTOR_ETFS = {
    "Technology":"XLK","Health Care":"XLV","Financials":"XLF",
    "Consumer Discretionary":"XLY","Consumer Staples":"XLP",
    "Industrials":"XLI","Energy":"XLE","Materials":"XLB",
    "Real Estate":"XLRE","Utilities":"XLU","Communication Services":"XLC",
}


def _fetch_ohlcv(ticker, start, end, api_key):
    cache_key = f"{ticker}_{start}_{end}"
    cached    = _PORT_CACHE.get(cache_key)
    if cached and (time.time() - cached["ts"]) < CACHE_TTL:
        return cached["df"]
    try:
        r = requests.get(
            f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}",
            params={"adjusted":"true","sort":"asc","limit":50000,"apiKey":api_key},
            timeout=20)
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                df = pd.DataFrame(results)
                df = df.rename(columns={"t":"Date","o":"Open","h":"High",
                                         "l":"Low","c":"Close","v":"Volume"})
                df["Date"]   = pd.to_datetime(df["Date"], unit="ms")
                df["Ticker"] = ticker
                df = df[["Date","Ticker","Open","High","Low","Close","Volume"]]
                df = df.sort_values("Date").reset_index(drop=True)
                _PORT_CACHE[cache_key] = {"ts":time.time(),"df":df}
                return df
    except Exception:
        pass
    return None


def fetch_portfolio_prices(tickers, period_years=3, api_key="", log=print):
    end     = datetime.today()
    start   = end - timedelta(days=period_years*365)
    end_s   = end.strftime("%Y-%m-%d")
    start_s = start.strftime("%Y-%m-%d")

    price_dict, failed = {}, []
    for ticker in tickers:
        log(f"   Fetching {ticker}...")
        df = _fetch_ohlcv(ticker, start_s, end_s, api_key)
        if df is not None and len(df) > 60:
            price_dict[ticker] = df
        else:
            log(f"   ⚠ {ticker} insufficient data, skipping")
            failed.append(ticker)
        time.sleep(0.12)

    if not price_dict:
        raise ValueError("No valid price data retrieved.")

    closes = {t: df.set_index("Date")["Close"].rename(t)
              for t, df in price_dict.items()}
    close_df   = pd.DataFrame(closes).fillna(method="ffill").dropna()
    returns_df = close_df.pct_change().dropna()

    log(f"   ✅ {len(price_dict)} tickers, {len(returns_df)} trading days")
    return price_dict, close_df, returns_df, failed


def get_ticker_info(ticker, api_key):
    try:
        r = requests.get(f"{POLYGON_BASE}/v3/reference/tickers/{ticker}",
                         params={"apiKey":api_key}, timeout=10)
        if r.status_code == 200:
            res = r.json().get("results", {})
            return {
                "name":       res.get("name", ticker),
                "sector":     res.get("sic_description","Unknown"),
                "exchange":   res.get("primary_exchange",""),
                "market_cap": res.get("market_cap", 0),
            }
    except Exception:
        pass
    return {"name":ticker,"sector":"Unknown","exchange":"","market_cap":0}


def build_candidate_universe(preferences, api_key, log=print):
    candidates       = set()
    included_sectors = preferences.get("include_sectors", list(SECTOR_UNIVERSE.keys()))
    excluded_sectors = preferences.get("exclude_sectors", [])
    user_tickers     = [t.upper().strip() for t in preferences.get("user_tickers", [])]
    risk_tolerance   = preferences.get("risk_tolerance", 5)

    for t in user_tickers:
        candidates.add(t)

    for sector in included_sectors:
        if sector in excluded_sectors:
            continue
        if sector in SECTOR_ETFS:
            candidates.add(SECTOR_ETFS[sector])
        stocks = SECTOR_UNIVERSE.get(sector, [])
        n = 2 if risk_tolerance <= 3 else (4 if risk_tolerance <= 6 else 6)
        for s in stocks[:n]:
            candidates.add(s)

    if risk_tolerance <= 3:
        candidates.update(["SPY","GLD","TLT","VNQ"])
    elif risk_tolerance <= 6:
        candidates.update(["SPY","QQQ"])
    else:
        candidates.add("QQQ")

    excluded_tickers = [t.upper() for t in preferences.get("exclude_tickers", [])]
    candidates -= set(excluded_tickers)

    result = list(candidates)[:25]
    log(f"   Built universe of {len(result)} candidates")
    return result
