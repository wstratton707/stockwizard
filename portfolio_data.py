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
    included_bonds   = preferences.get("include_bond_categories", [])

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

    # Add bond ETFs based on user-selected bond categories
    for category in included_bonds:
        if category in BOND_ETFS:
            candidates.add(BOND_ETFS[category])
        bonds = BOND_UNIVERSE.get(category, [])
        # Conservative: more bonds; aggressive: just the category ETF
        n_bonds = 3 if risk_tolerance <= 3 else (2 if risk_tolerance <= 6 else 1)
        for b in bonds[:n_bonds]:
            candidates.add(b)

    if risk_tolerance <= 3:
        candidates.update(["SPY","GLD","TLT","VNQ"])
    elif risk_tolerance <= 6:
        candidates.update(["SPY","QQQ"])
    else:
        candidates.add("QQQ")

    excluded_tickers = [t.upper() for t in preferences.get("exclude_tickers", [])]
    candidates -= set(excluded_tickers)

    result = list(candidates)[:30]
    log(f"   Built universe of {len(result)} candidates")
    return result
