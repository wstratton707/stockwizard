import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from constants import RISK_FREE_RATE

POLYGON_BASE = "https://api.polygon.io"
_PORT_CACHE  = {}
CACHE_TTL    = 3600

SECTOR_UNIVERSE = {
    "Technology": [
        "AAPL","MSFT","NVDA","AVGO","GOOGL","META","AMD","CRM","ADBE","QCOM",
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
        "USB","PNC","TFC","COF","DFS","SCHW","FIS","FI","PYPL","CBOE",
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
        "PXD","DVN","FANG","MRO","APA","HAL","BKR","NOV","RRC","EQT",
        "CTRA","OVV","SM","MGY","MTDR","HP","DINO","DKL","TRGP","WMB",
    ],
    "Materials": [
        "LIN","APD","ECL","SHW","NEM","FCX","NUE","VMC","MLM","ALB",
        "CF","MOS","IFF","PPG","RPM","EMN","LYB","DD","DOW","CE",
        "AXTA","PKG","IP","WRK","SEE","SON","GEF","SLVM","TREX","UFPI",
    ],
    "Real Estate": [
        "PLD","AMT","EQIX","CCI","PSA","O","DLR","WELL","SPG","VTR",
        "EXR","AVB","EQR","UDR","CPT","MAA","NNN","VICI","MPW","OHI",
        "PEAK","ARE","BXP","SLG","KIM","REG","FRT","ACC","ELS","SUI",
    ],
    "Utilities": [
        "NEE","DUK","SO","D","AEP","EXC","XEL","ES","WEC","ED",
        "ETR","FE","PPL","AEE","CMS","NI","LNT","EVRG","PNW","SRE",
        "PCG","EIX","AWK","CNP","NRG","AES","DTE","OGE","POR","AVA",
    ],
    "Communication Services": [
        "GOOGL","META","NFLX","DIS","CMCSA","T","VZ","TMUS","EA","TTWO",
        "ATVI","FOXA","IPG","OMC","PARA","WBD","LYV","MTCH","ZM","SNAP",
        "PINS","RBLX","SPOT","ROKU","SIRI","IAC","NLSN","NYT","NWSA","LBRDA",
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


def _fetch_ohlcv(ticker, start, end, api_key, log=print):
    cache_key = f"{ticker}_{start}_{end}"
    cached    = _PORT_CACHE.get(cache_key)
    if cached and (time.time() - cached["ts"]) < CACHE_TTL:
        return cached["df"]
    try:
        r = requests.get(
            f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}",
            params={"adjusted":"true","sort":"asc","limit":50000,"apiKey":api_key},
            timeout=20)
        if r.status_code == 429:
            for _wait in (12, 24, 36):
                log(f"   ⏳ {ticker} rate limited, retrying in {_wait}s...")
                time.sleep(_wait)
                r = requests.get(
                    f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}",
                    params={"adjusted":"true","sort":"asc","limit":50000,"apiKey":api_key},
                    timeout=20)
                if r.status_code != 429:
                    break
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
            else:
                log(f"   ⚠ {ticker} HTTP 200 but empty results")
        else:
            log(f"   ⚠ {ticker} HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        log(f"   ⚠ {ticker} exception: {e}")
    return None


def fetch_portfolio_prices(tickers, period_years=3, api_key="", log=print):
    end     = datetime.today()
    start   = end - timedelta(days=period_years*365)
    end_s   = end.strftime("%Y-%m-%d")
    start_s = start.strftime("%Y-%m-%d")

    price_dict, failed = {}, []
    thread_logs = []  # collect logs from threads — Streamlit can't be called from worker threads

    def fetch_one(ticker):
        msgs = []
        df = _fetch_ohlcv(ticker, start_s, end_s, api_key, log=lambda m: msgs.append(m))
        return ticker, df, msgs

    with ThreadPoolExecutor(max_workers=5) as executor:
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
    # Drop tickers whose history is too short (starts >60 days after the majority)
    earliest_common = close_df.apply(lambda s: s.first_valid_index()).max()
    short_tickers   = [t for t in close_df.columns
                       if close_df[t].first_valid_index() > earliest_common - pd.Timedelta(days=60)]
    if short_tickers and len(short_tickers) < len(close_df.columns):
        log(f"   ⚠ Dropping late-start tickers to preserve history: {short_tickers}")
        close_df = close_df.drop(columns=short_tickers)
        failed.extend(short_tickers)
    close_df   = close_df.ffill().dropna()
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


def select_by_sharpe(returns_df, sector_map, always_keep=None, max_total=18,
                     top_n_per_sector=2):
    """
    Rank candidates by Sharpe ratio (excess return) within each sector.
    Keeps the best `top_n_per_sector` stocks per sector.
    Tickers in always_keep (SPY, QQQ, etc.) are pinned regardless of Sharpe.
    """
    from collections import defaultdict

    if always_keep is None:
        always_keep = {"SPY", "QQQ", "GLD", "TLT"}

    def sharpe(ticker):
        r = returns_df[ticker].dropna()
        ann_ret = r.mean() * 252
        ann_std = r.std() * np.sqrt(252)
        return (ann_ret - RISK_FREE_RATE) / ann_std if ann_std > 0 else -999.0

    sector_groups = defaultdict(list)
    pinned = []

    for ticker in returns_df.columns:
        if ticker in always_keep:
            pinned.append(ticker)
        else:
            sector_groups[sector_map.get(ticker, "Unknown")].append(ticker)

    selected = list(pinned)
    for sector, tickers in sector_groups.items():
        ranked = sorted(tickers, key=sharpe, reverse=True)
        selected.extend(ranked[:top_n_per_sector])

    return selected[:max_total]
