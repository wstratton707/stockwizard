import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time

POLYGON_BASE = "https://api.polygon.io"

SECTOR_ETF_MAP = {
    "Technology": "XLK", "Health Care": "XLV", "Financials": "XLF",
    "Consumer Discretionary": "XLY", "Consumer Staples": "XLP",
    "Industrials": "XLI", "Energy": "XLE", "Materials": "XLB",
    "Real Estate": "XLRE", "Utilities": "XLU", "Communication Services": "XLC",
}


def _get(endpoint, api_key, params=None):
    if params is None:
        params = {}
    params["apiKey"] = api_key
    r = requests.get(f"{POLYGON_BASE}{endpoint}", params=params, timeout=30)
    if r.status_code == 200:
        return r.json()
    return None


def validate_ticker(ticker, api_key):
    data = _get(f"/v3/reference/tickers/{ticker.upper()}", api_key)
    if data and data.get("status") == "OK":
        info = data.get("results", {})
        return True, info
    return False, "Ticker not found or invalid."


def _period_to_dates(period):
    end = datetime.today()
    mapping = {"1y": 365, "2y": 730, "5y": 1825, "10y": 3650}
    days = mapping.get(period, 1825)
    start = end - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def fetch_ohlcv(ticker, period, api_key, log=print,
                start_override=None, end_override=None, bar_size="day"):
    if start_override and end_override:
        start, end = start_override, end_override
    else:
        start, end = _period_to_dates(period)
    log(f"Downloading data for {ticker} ({start} → {end}, {bar_size})...")
    data = _get(
        f"/v2/aggs/ticker/{ticker}/range/1/{bar_size}/{start}/{end}",
        api_key,
        params={"adjusted": "true", "sort": "asc", "limit": 50000},
    )
    if not data or not data.get("results"):
        raise ValueError(f"No price data for '{ticker}'. Check the symbol.")

    rows = data["results"]
    df = pd.DataFrame(rows)
    df = df.rename(columns={"t": "Date", "o": "Open", "h": "High",
                             "l": "Low", "c": "Close", "v": "Volume"})
    df["Date"] = pd.to_datetime(df["Date"], unit="ms")
    df = df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
    df = df.sort_values("Date").reset_index(drop=True)
    log(f"   {len(df)} bars fetched.")
    return df


def fetch_stock_data(ticker, period="5y", benchmark_tickers=None, api_key="", log=print,
                     start_override=None, end_override=None, bar_size="day"):
    df = fetch_ohlcv(ticker, period, api_key, log=log,
                     start_override=start_override, end_override=end_override, bar_size=bar_size)

    df["Daily_Return"]     = df["Close"].pct_change()
    df["Cumulative_Index"] = (1 + df["Daily_Return"].fillna(0)).cumprod() * 100

    for ma in [20, 50, 200]:
        df[f"MA{ma}"]          = df["Close"].rolling(ma).mean()
        df[f"Close_vs_MA{ma}"] = (df["Close"] / df[f"MA{ma}"] - 1).where(df[f"MA{ma}"].notna())

    df["Vol_MA20"]      = df["Volume"].rolling(20).mean()
    df["Volume_vs_Avg"] = np.where(df["Vol_MA20"] > 0, df["Volume"] / df["Vol_MA20"], np.nan)

    try:
        import ta
        df["RSI14"]       = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
        macd              = ta.trend.MACD(df["Close"])
        df["MACD"]        = macd.macd()
        df["MACD_Signal"] = macd.macd_signal()
        df["MACD_Hist"]   = df["MACD"] - df["MACD_Signal"]
        bb                = ta.volatility.BollingerBands(df["Close"], window=20, window_dev=2)
        df["BB_Upper"]    = bb.bollinger_hband()
        df["BB_Middle"]   = bb.bollinger_mavg()
        df["BB_Lower"]    = bb.bollinger_lband()
        df["BB_Width"]    = (df["BB_Upper"] - df["BB_Lower"]) / df["BB_Middle"]
        df["BB_Pct"]      = bb.bollinger_pband()
    except Exception as e:
        log(f"   Technical indicators skipped: {e}")

    df["Volatility_20d"] = df["Daily_Return"].rolling(20).std() * np.sqrt(252)
    df["Drawdown_20d"]   = df["Cumulative_Index"] / df["Cumulative_Index"].rolling(20).max() - 1
    df["Drawdown_60d"]   = df["Cumulative_Index"] / df["Cumulative_Index"].rolling(60).max() - 1
    df["52W_High"]       = df["Close"].rolling(252).max()
    df["52W_Low"]        = df["Close"].rolling(252).min()
    df["Pct_From_52W_High"] = df["Close"] / df["52W_High"] - 1
    df["Pct_From_52W_Low"]  = df["Close"] / df["52W_Low"]  - 1

    if benchmark_tickers:
        for bench in benchmark_tickers:
            log(f"   Benchmark: {bench}")
            try:
                bdf = fetch_ohlcv(bench, period, api_key, log=lambda m: None,
                                  start_override=start_override, end_override=end_override,
                                  bar_size=bar_size)
                bdf[f"{bench}_Return"]     = bdf["Close"].pct_change()
                bdf[f"{bench}_Cumulative"] = (1 + bdf[f"{bench}_Return"].fillna(0)).cumprod() * 100
                df = pd.merge(df, bdf[["Date", f"{bench}_Return", f"{bench}_Cumulative"]],
                              on="Date", how="left")
            except Exception as e:
                log(f"   Benchmark {bench} failed: {e}")

        first_b = benchmark_tickers[0]
        col_b   = f"{first_b}_Return"
        if col_b in df.columns:
            df["Rolling_Beta_60d"] = (
                df["Daily_Return"].rolling(60).cov(df[col_b]) /
                df[col_b].rolling(60).var()
            )

    ret      = df["Daily_Return"].dropna()
    ann_ret  = ret.mean() * 252
    ann_std  = ret.std() * np.sqrt(252)
    downside = ret[ret < 0].std() * np.sqrt(252)
    df["Sharpe_Ratio"]  = ann_ret / ann_std  if ann_std  else np.nan
    df["Sortino_Ratio"] = ann_ret / downside if downside else np.nan

    return df.sort_values("Date").reset_index(drop=True)


def fetch_company_details(ticker, api_key, log=print):
    log(f"Fetching company details for {ticker}...")
    data = _get(f"/v3/reference/tickers/{ticker}", api_key)
    if not data:
        return {}
    r = data.get("results", {})
    return {
        "Ticker":      ticker,
        "Name":        r.get("name", "N/A"),
        "Sector":      r.get("sic_description", "N/A"),
        "Industry":    r.get("sic_description", "N/A"),
        "Exchange":    r.get("primary_exchange", "N/A"),
        "Market Cap":  r.get("market_cap", "N/A"),
        "Employees":   r.get("total_employees", "N/A"),
        "Description": r.get("description", "N/A"),
        "Website":     r.get("homepage_url", "N/A"),
        "Country":     r.get("locale", "N/A"),
    }


def fetch_financials(ticker, api_key, log=print):
    log(f"Fetching financials for {ticker}...")
    results = {}
    try:
        data = _get("/vX/reference/financials", api_key, params={
            "ticker": ticker, "timeframe": "annual", "limit": 4,
            "include_sources": "false"
        })
        if data and data.get("results"):
            for statement in ["income_statement", "balance_sheet", "cash_flow_statement"]:
                rows = []
                for r in data["results"]:
                    period_end = r.get("end_date", "")
                    fin = r.get("financials", {}).get(statement, {})
                    row = {"Period": period_end}
                    for k, v in fin.items():
                        row[k] = v.get("value", None)
                    rows.append(row)
                if rows:
                    results[statement] = pd.DataFrame(rows)
                    log(f"   {statement}: {len(rows)} periods")
    except Exception as e:
        log(f"   Financials skipped: {e}")
    return results


def fetch_news(ticker, api_key, log=print):
    log(f"Fetching news for {ticker}...")
    data = _get("/v2/reference/news", api_key, params={
        "ticker": ticker, "limit": 15, "order": "desc", "sort": "published_utc"
    })
    news_list = []
    if data and data.get("results"):
        for item in data["results"]:
            news_list.append({
                "Date":      item.get("published_utc", "")[:16].replace("T", " "),
                "Headline":  item.get("title", ""),
                "Publisher": item.get("publisher", {}).get("name", ""),
                "URL":       item.get("article_url", ""),
            })
        log(f"   {len(news_list)} news items")
    return news_list


def fetch_peer_comparison(ticker, peer_tickers, api_key, log=print):
    if not peer_tickers:
        return None
    all_tickers = [ticker] + peer_tickers[:4]
    log(f"Fetching peer comparison: {all_tickers}...")
    rows = []
    for t in all_tickers:
        try:
            data = _get(f"/v3/reference/tickers/{t}", api_key)
            if data and data.get("results"):
                r  = data["results"]
                mc = r.get("market_cap")
                rows.append({
                    "Ticker":          t,
                    "Company":         r.get("name", t),
                    "Exchange":        r.get("primary_exchange", "N/A"),
                    "Market Cap ($B)": round(mc / 1e9, 2) if mc else "N/A",
                    "Employees":       r.get("total_employees", "N/A"),
                    "Country":         r.get("locale", "N/A"),
                })
                log(f"   {t} OK")
            time.sleep(0.2)
        except Exception as e:
            log(f"   {t} skipped: {e}")
    return pd.DataFrame(rows) if rows else None


def fetch_bond_data(ticker, period="5y", benchmark_tickers=None, api_key="", log=print,
                    start_override=None, end_override=None, bar_size="day"):
    """Fetch and enrich bond ETF data.  Mirrors fetch_stock_data but uses
    bond-relevant metrics (duration label, yield proxy, spread proxy) instead
    of equity-focused indicators like MACD / RSI."""
    from portfolio_data import BOND_DURATION_MAP

    df = fetch_ohlcv(ticker, period, api_key, log=log,
                     start_override=start_override, end_override=end_override, bar_size=bar_size)

    df["Daily_Return"]     = df["Close"].pct_change()
    df["Cumulative_Index"] = (1 + df["Daily_Return"].fillna(0)).cumprod() * 100

    for ma in [20, 50, 200]:
        df[f"MA{ma}"]          = df["Close"].rolling(ma).mean()
        df[f"Close_vs_MA{ma}"] = (df["Close"] / df[f"MA{ma}"] - 1).where(df[f"MA{ma}"].notna())

    df["Vol_MA20"]      = df["Volume"].rolling(20).mean()
    df["Volume_vs_Avg"] = np.where(df["Vol_MA20"] > 0, df["Volume"] / df["Vol_MA20"], np.nan)

    df["Volatility_20d"] = df["Daily_Return"].rolling(20).std() * np.sqrt(252)
    df["Drawdown_20d"]   = df["Cumulative_Index"] / df["Cumulative_Index"].rolling(20).max() - 1
    df["Drawdown_60d"]   = df["Cumulative_Index"] / df["Cumulative_Index"].rolling(60).max() - 1
    df["52W_High"]       = df["Close"].rolling(252).max()
    df["52W_Low"]        = df["Close"].rolling(252).min()
    df["Pct_From_52W_High"] = df["Close"] / df["52W_High"] - 1
    df["Pct_From_52W_Low"]  = df["Close"] / df["52W_Low"]  - 1

    # Annualised price return as a rough total-return yield proxy
    df["Return_1Y_Proxy"] = df["Close"].pct_change(252)

    # Rolling 20-day price momentum vs volatility (carry-like signal for bonds)
    df["Price_Momentum_20d"] = df["Close"].pct_change(20)

    # Duration label from static map
    df["Duration_Bucket"] = BOND_DURATION_MAP.get(ticker.upper(), "Unknown")

    if benchmark_tickers:
        for bench in benchmark_tickers:
            log(f"   Benchmark: {bench}")
            try:
                bdf = fetch_ohlcv(bench, period, api_key, log=lambda m: None)
                bdf[f"{bench}_Return"]     = bdf["Close"].pct_change()
                bdf[f"{bench}_Cumulative"] = (1 + bdf[f"{bench}_Return"].fillna(0)).cumprod() * 100
                df = pd.merge(df, bdf[["Date", f"{bench}_Return", f"{bench}_Cumulative"]],
                              on="Date", how="left")
            except Exception as e:
                log(f"   Benchmark {bench} failed: {e}")

        first_b = benchmark_tickers[0]
        col_b   = f"{first_b}_Return"
        if col_b in df.columns:
            df["Rolling_Beta_60d"] = (
                df["Daily_Return"].rolling(60).cov(df[col_b]) /
                df[col_b].rolling(60).var()
            )

    ret      = df["Daily_Return"].dropna()
    ann_ret  = ret.mean() * 252
    ann_std  = ret.std() * np.sqrt(252)
    downside = ret[ret < 0].std() * np.sqrt(252)
    df["Sharpe_Ratio"]  = ann_ret / ann_std  if ann_std  else np.nan
    df["Sortino_Ratio"] = ann_ret / downside if downside else np.nan

    return df.sort_values("Date").reset_index(drop=True)


def fetch_sector_data(ticker, period, api_key, sector, log=print):
    etf = SECTOR_ETF_MAP.get(sector)
    if not etf:
        return None
    log(f"Fetching sector ETF: {etf}...")
    try:
        etf_df = fetch_ohlcv(etf, period, api_key, log=lambda m: None)
        etf_df["Sector_Return"]         = etf_df["Close"].pct_change()
        etf_df["Sector_ETF_Cumulative"] = (1 + etf_df["Sector_Return"].fillna(0)).cumprod() * 100
        return etf_df[["Date", "Sector_ETF_Cumulative", "Sector_Return"]]
    except Exception as e:
        log(f"   Sector ETF failed: {e}")
        return None


def fetch_next_earnings(ticker, api_key):
    """Returns the next earnings date string for a ticker, or None if unavailable."""
    try:
        data = _get("/vX/reference/financials", api_key, params={
            "ticker": ticker, "timeframe": "quarterly", "limit": 1,
            "include_sources": "false"
        })
        if data and data.get("results"):
            return data["results"][0].get("end_date", None)
    except Exception:
        pass
    return None


# ── Crypto & ETF support ─────────────────────────────────────────────────────

# Maps user-facing symbol → (Polygon ticker, CoinGecko id)
CRYPTO_TICKERS = {
    "BTC":   ("X:BTCUSD",   "bitcoin"),
    "ETH":   ("X:ETHUSD",   "ethereum"),
    "SOL":   ("X:SOLUSD",   "solana"),
    "BNB":   ("X:BNBUSD",   "binancecoin"),
    "XRP":   ("X:XRPUSD",   "ripple"),
    "ADA":   ("X:ADAUSD",   "cardano"),
    "AVAX":  ("X:AVAXUSD",  "avalanche-2"),
    "DOGE":  ("X:DOGEUSD",  "dogecoin"),
    "DOT":   ("X:DOTUSD",   "polkadot"),
    "LINK":  ("X:LINKUSD",  "chainlink"),
    "MATIC": ("X:MATICUSD", "matic-network"),
    "LTC":   ("X:LTCUSD",   "litecoin"),
    "SHIB":  ("X:SHIBUSD",  "shiba-inu"),
    "UNI":   ("X:UNIUSD",   "uniswap"),
    "ATOM":  ("X:ATOMUSD",  "cosmos"),
}

# Static metadata for common ETFs (expense ratio %, AUM $B, index tracked)
ETF_METADATA = {
    "SPY":  {"name":"SPDR S&P 500 ETF","expense":0.0945,"aum_b":500,"category":"Large Blend","index":"S&P 500","holdings":503},
    "QQQ":  {"name":"Invesco QQQ Trust","expense":0.20,"aum_b":220,"category":"Large Growth","index":"Nasdaq-100","holdings":101},
    "IWM":  {"name":"iShares Russell 2000 ETF","expense":0.19,"aum_b":65,"category":"Small Blend","index":"Russell 2000","holdings":2000},
    "VOO":  {"name":"Vanguard S&P 500 ETF","expense":0.03,"aum_b":450,"category":"Large Blend","index":"S&P 500","holdings":503},
    "VTI":  {"name":"Vanguard Total Stock Market ETF","expense":0.03,"aum_b":380,"category":"Large Blend","index":"CRSP Total Market","holdings":3700},
    "VEA":  {"name":"Vanguard FTSE Developed Markets ETF","expense":0.05,"aum_b":110,"category":"Foreign Large Blend","index":"FTSE Dev ex US","holdings":3900},
    "VWO":  {"name":"Vanguard FTSE Emerging Markets ETF","expense":0.08,"aum_b":80,"category":"Diversified Emerging","index":"FTSE Emerging","holdings":5800},
    "EFA":  {"name":"iShares MSCI EAFE ETF","expense":0.32,"aum_b":60,"category":"Foreign Large Blend","index":"MSCI EAFE","holdings":790},
    "EEM":  {"name":"iShares MSCI Emerging Markets ETF","expense":0.68,"aum_b":18,"category":"Diversified Emerging","index":"MSCI Emerging Markets","holdings":1200},
    "GLD":  {"name":"SPDR Gold Shares","expense":0.40,"aum_b":55,"category":"Commodities - Gold","index":"Gold Spot Price","holdings":1},
    "GDX":  {"name":"VanEck Gold Miners ETF","expense":0.51,"aum_b":13,"category":"Equity Precious Metals","index":"NYSE Arca Gold Miners","holdings":55},
    "SLV":  {"name":"iShares Silver Trust","expense":0.50,"aum_b":12,"category":"Commodities - Silver","index":"Silver Spot Price","holdings":1},
    "USO":  {"name":"United States Oil Fund","expense":0.76,"aum_b":1,"category":"Commodities - Oil","index":"Crude Oil Futures","holdings":1},
    "TLT":  {"name":"iShares 20+ Year Treasury Bond ETF","expense":0.15,"aum_b":40,"category":"Long Government Bond","index":"ICE US Treasury 20+yr","holdings":40},
    "IEF":  {"name":"iShares 7-10 Year Treasury Bond ETF","expense":0.15,"aum_b":28,"category":"Intermediate Government","index":"ICE US Treasury 7-10yr","holdings":12},
    "SHY":  {"name":"iShares 1-3 Year Treasury Bond ETF","expense":0.15,"aum_b":22,"category":"Short Government","index":"ICE US Treasury 1-3yr","holdings":70},
    "AGG":  {"name":"iShares Core U.S. Aggregate Bond ETF","expense":0.03,"aum_b":100,"category":"Intermediate Core Bond","index":"Bloomberg US Aggregate","holdings":10000},
    "BND":  {"name":"Vanguard Total Bond Market ETF","expense":0.03,"aum_b":100,"category":"Intermediate Core Bond","index":"Bloomberg Float Adj","holdings":17600},
    "HYG":  {"name":"iShares iBoxx $ High Yield Corp Bond ETF","expense":0.49,"aum_b":16,"category":"High Yield Bond","index":"iBoxx $ Liquid HY","holdings":1200},
    "JNK":  {"name":"SPDR Bloomberg High Yield Bond ETF","expense":0.40,"aum_b":8,"category":"High Yield Bond","index":"Bloomberg LY Liquid","holdings":1200},
    "LQD":  {"name":"iShares iBoxx $ IG Corp Bond ETF","expense":0.14,"aum_b":30,"category":"Corporate Bond","index":"iBoxx $ Liquid IG","holdings":2500},
    "XLK":  {"name":"Technology Select Sector SPDR","expense":0.09,"aum_b":68,"category":"Technology","index":"Technology Select Sector","holdings":65},
    "XLV":  {"name":"Health Care Select Sector SPDR","expense":0.09,"aum_b":40,"category":"Health Care","index":"Health Care Select Sector","holdings":65},
    "XLF":  {"name":"Financial Select Sector SPDR","expense":0.09,"aum_b":42,"category":"Financials","index":"Financial Select Sector","holdings":73},
    "XLE":  {"name":"Energy Select Sector SPDR","expense":0.09,"aum_b":32,"category":"Energy","index":"Energy Select Sector","holdings":23},
    "XLY":  {"name":"Consumer Discr Select Sector SPDR","expense":0.09,"aum_b":20,"category":"Consumer Discretionary","index":"Consumer Discr Select Sector","holdings":52},
    "XLP":  {"name":"Consumer Staples Select Sector SPDR","expense":0.09,"aum_b":15,"category":"Consumer Staples","index":"Consumer Staples Select Sector","holdings":38},
    "XLI":  {"name":"Industrial Select Sector SPDR","expense":0.09,"aum_b":22,"category":"Industrials","index":"Industrial Select Sector","holdings":79},
    "XLB":  {"name":"Materials Select Sector SPDR","expense":0.09,"aum_b":7,"category":"Materials","index":"Materials Select Sector","holdings":28},
    "XLRE": {"name":"Real Estate Select Sector SPDR","expense":0.09,"aum_b":5,"category":"Real Estate","index":"Real Estate Select Sector","holdings":31},
    "XLU":  {"name":"Utilities Select Sector SPDR","expense":0.09,"aum_b":14,"category":"Utilities","index":"Utilities Select Sector","holdings":30},
    "XLC":  {"name":"Communication Svcs Select Sector SPDR","expense":0.09,"aum_b":18,"category":"Communication Services","index":"Communication Svcs Select Sector","holdings":22},
    "ARKK": {"name":"ARK Innovation ETF","expense":0.75,"aum_b":7,"category":"Mid-Cap Growth","index":"Active - Disruptive Innovation","holdings":30},
    "ARKW": {"name":"ARK Next Generation Internet ETF","expense":0.88,"aum_b":2,"category":"Large Growth","index":"Active - Next Gen Internet","holdings":30},
    "DIA":  {"name":"SPDR Dow Jones Industrial Avg ETF","expense":0.16,"aum_b":32,"category":"Large Value","index":"Dow Jones Industrial Average","holdings":30},
    "VNQ":  {"name":"Vanguard Real Estate ETF","expense":0.12,"aum_b":60,"category":"Real Estate","index":"MSCI US REIT","holdings":165},
    "SCHD": {"name":"Schwab US Dividend Equity ETF","expense":0.06,"aum_b":54,"category":"Large Value","index":"Dow Jones US Dividend 100","holdings":100},
    "VIG":  {"name":"Vanguard Dividend Appreciation ETF","expense":0.06,"aum_b":70,"category":"Large Blend","index":"S&P US Dividend Growers","holdings":315},
    "VXUS": {"name":"Vanguard Total Intl Stock ETF","expense":0.08,"aum_b":65,"category":"Foreign Large Blend","index":"FTSE Global ex US","holdings":8500},
    "JEPI": {"name":"JPMorgan Equity Premium Income ETF","expense":0.35,"aum_b":32,"category":"Large Value","index":"Active - S&P 500 + Covered Calls","holdings":130},
    "JEPQ": {"name":"JPMorgan Nasdaq Equity Premium Income ETF","expense":0.35,"aum_b":15,"category":"Large Growth","index":"Active - Nasdaq 100 + Covered Calls","holdings":90},
    "TQQQ": {"name":"ProShares UltraPro QQQ (3x Leveraged)","expense":0.88,"aum_b":22,"category":"Trading - Leveraged","index":"Nasdaq-100 (3x)","holdings":101},
    "SQQQ": {"name":"ProShares UltraPro Short QQQ (3x Inverse)","expense":0.95,"aum_b":3,"category":"Trading - Inverse","index":"Nasdaq-100 (-3x)","holdings":0},
    "SPXL": {"name":"Direxion Daily S&P 500 Bull 3X","expense":0.94,"aum_b":4,"category":"Trading - Leveraged","index":"S&P 500 (3x)","holdings":503},
}

# Top 10 holdings for major ETFs (static fallback)
ETF_TOP_HOLDINGS = {
    "SPY":  [("AAPL",7.0),("MSFT",6.4),("NVDA",5.8),("AMZN",3.7),("META",2.5),("GOOGL",2.1),("GOOG",1.8),("BRK.B",1.7),("LLY",1.5),("AVGO",1.4)],
    "QQQ":  [("MSFT",8.6),("AAPL",8.3),("NVDA",7.2),("AMZN",5.2),("META",4.7),("TSLA",3.3),("GOOGL",2.8),("GOOG",2.6),("AVGO",2.2),("COST",2.1)],
    "VOO":  [("AAPL",7.0),("MSFT",6.4),("NVDA",5.8),("AMZN",3.7),("META",2.5),("GOOGL",2.1),("GOOG",1.8),("BRK.B",1.7),("LLY",1.5),("AVGO",1.4)],
    "IWM":  [("FTAI",0.43),("VRRM",0.40),("CAVA",0.39),("SAIA",0.38),("TREX",0.37),("SMCI",0.35),("CELH",0.34),("INSP",0.33),("TGTX",0.32),("LBRT",0.31)],
    "XLK":  [("MSFT",21.5),("AAPL",21.0),("NVDA",18.5),("AVGO",5.4),("CRM",3.1),("ORCL",2.9),("AMD",2.6),("ACN",2.2),("CSCO",2.1),("IBM",1.8)],
    "XLV":  [("UNH",12.3),("LLY",10.5),("JNJ",6.8),("ABBV",6.1),("MRK",5.2),("TMO",4.9),("ABT",4.3),("DHR",3.8),("PFE",2.9),("AMGN",2.8)],
    "XLF":  [("BRK.B",12.4),("JPM",10.8),("V",8.9),("MA",6.3),("BAC",4.1),("WFC",3.8),("GS",2.6),("MS",2.4),("BLK",2.3),("SPGI",2.2)],
    "XLE":  [("XOM",22.5),("CVX",16.3),("COP",8.2),("EOG",5.4),("SLB",5.1),("MPC",4.8),("PXD",4.2),("VLO",3.9),("PSX",3.7),("OXY",3.1)],
    "XLY":  [("AMZN",22.3),("TSLA",13.4),("HD",9.2),("MCD",5.1),("NKE",4.4),("SBUX",4.1),("TJX",3.8),("BKNG",3.5),("CMG",3.2),("LOW",3.0)],
    "XLP":  [("WMT",15.6),("PG",12.3),("KO",9.1),("COST",8.8),("PEP",8.4),("PM",6.3),("MDLZ",4.5),("CL",3.8),("GIS",2.7),("KHC",2.4)],
    "XLI":  [("CAT",5.2),("UPS",5.0),("HON",4.9),("GE",4.8),("RTX",4.6),("DE",4.2),("MMM",3.1),("LMT",3.0),("FDX",2.9),("WM",2.7)],
    "DIA":  [("UNH",7.8),("GS",7.1),("MSFT",6.4),("HD",5.6),("CAT",5.2),("AMGN",4.9),("MCD",4.5),("V",4.2),("CRM",3.9),("AAPL",3.4)],
    "SCHD": [("AVGO",4.3),("HD",4.2),("VZ",4.1),("ABBV",4.0),("PFE",3.9),("KO",3.8),("CVX",3.7),("LMT",3.6),("IBM",3.5),("MO",3.4)],
    "GLD":  [("Gold Bullion",100.0)],
    "TLT":  [("US Treasury 20yr+",99.5)],
    "AGG":  [("US Treasury",41.4),("MBS Pass-Through",24.1),("Corp IG",27.3),("Agency",4.2),("Other",3.0)],
}


def detect_asset_type(ticker, api_key=""):
    """Returns 'crypto', 'etf', or 'stock' for a given ticker symbol."""
    from portfolio_data import BOND_UNIVERSE
    t = ticker.upper()

    if t in CRYPTO_TICKERS:
        return "crypto"

    # Bond ETFs from the universe map
    all_bond_etfs = {tk for tks in BOND_UNIVERSE.values() for tk in tks}
    if t in all_bond_etfs or t in ETF_METADATA:
        return "etf"

    # Ask Polygon reference API as fallback (rate-limited — best-effort)
    if api_key:
        try:
            data = _get(f"/v3/reference/tickers/{t}", api_key)
            if data and data.get("results"):
                if data["results"].get("type") in ("ETF", "ETP"):
                    return "etf"
        except Exception:
            pass

    return "stock"


def fetch_crypto_data(symbol, period="1y", api_key="", log=print,
                      start_override=None, end_override=None, bar_size="day"):
    """Fetch OHLCV + technicals for a crypto symbol (e.g. BTC → X:BTCUSD)."""
    poly_ticker, _ = CRYPTO_TICKERS.get(symbol.upper(), (f"X:{symbol.upper()}USD", None))
    df = fetch_ohlcv(poly_ticker, period, api_key, log=log,
                     start_override=start_override, end_override=end_override, bar_size=bar_size)

    df["Daily_Return"]     = df["Close"].pct_change()
    df["Cumulative_Index"] = (1 + df["Daily_Return"].fillna(0)).cumprod() * 100

    for ma in [20, 50, 200]:
        df[f"MA{ma}"]          = df["Close"].rolling(ma).mean()
        df[f"Close_vs_MA{ma}"] = (df["Close"] / df[f"MA{ma}"] - 1).where(df[f"MA{ma}"].notna())

    df["Vol_MA20"]      = df["Volume"].rolling(20).mean()
    df["Volume_vs_Avg"] = np.where(df["Vol_MA20"] > 0, df["Volume"] / df["Vol_MA20"], np.nan)

    try:
        import ta
        df["RSI14"]       = ta.momentum.RSIIndicator(df["Close"], window=14).rsi()
        macd              = ta.trend.MACD(df["Close"])
        df["MACD"]        = macd.macd()
        df["MACD_Signal"] = macd.macd_signal()
        df["MACD_Hist"]   = df["MACD"] - df["MACD_Signal"]
        bb                = ta.volatility.BollingerBands(df["Close"], window=20, window_dev=2)
        df["BB_Upper"]    = bb.bollinger_hband()
        df["BB_Middle"]   = bb.bollinger_mavg()
        df["BB_Lower"]    = bb.bollinger_lband()
        df["BB_Width"]    = (df["BB_Upper"] - df["BB_Lower"]) / df["BB_Middle"]
        df["BB_Pct"]      = bb.bollinger_pband()
    except Exception as e:
        log(f"   Technical indicators skipped: {e}")

    df["Volatility_20d"]    = df["Daily_Return"].rolling(20).std() * np.sqrt(252)
    df["Drawdown_20d"]      = df["Cumulative_Index"] / df["Cumulative_Index"].rolling(20).max() - 1
    df["Drawdown_60d"]      = df["Cumulative_Index"] / df["Cumulative_Index"].rolling(60).max() - 1
    df["52W_High"]          = df["Close"].rolling(252).max()
    df["52W_Low"]           = df["Close"].rolling(252).min()
    df["Pct_From_52W_High"] = df["Close"] / df["52W_High"] - 1
    df["Pct_From_52W_Low"]  = df["Close"] / df["52W_Low"]  - 1

    ret      = df["Daily_Return"].dropna()
    ann_ret  = ret.mean() * 252
    ann_std  = ret.std() * np.sqrt(252)
    downside = ret[ret < 0].std() * np.sqrt(252)
    df["Sharpe_Ratio"]  = ann_ret / ann_std  if ann_std  else np.nan
    df["Sortino_Ratio"] = ann_ret / downside if downside else np.nan

    return df.sort_values("Date").reset_index(drop=True)


def fetch_crypto_details(symbol):
    """Fetch live market data from CoinGecko (free, no API key required)."""
    _, cg_id = CRYPTO_TICKERS.get(symbol.upper(), (None, None))
    if not cg_id:
        return {}
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{cg_id}",
            params={"localization": "false", "tickers": "false",
                    "community_data": "false", "developer_data": "false"},
            headers={"Accept": "application/json"},
            timeout=15,
        )
        if r.status_code == 200:
            d   = r.json()
            mkt = d.get("market_data", {})
            return {
                "name":               d.get("name", symbol),
                "symbol":             d.get("symbol", "").upper(),
                "market_cap_usd":     mkt.get("market_cap", {}).get("usd", 0),
                "market_cap_rank":    d.get("market_cap_rank", 0),
                "circulating_supply": mkt.get("circulating_supply", 0),
                "total_supply":       mkt.get("total_supply", 0),
                "max_supply":         mkt.get("max_supply", 0),
                "ath":                mkt.get("ath", {}).get("usd", 0),
                "ath_date":           (mkt.get("ath_date", {}).get("usd", "") or "")[:10],
                "ath_pct":            mkt.get("ath_change_percentage", {}).get("usd", 0),
                "price_change_24h":   mkt.get("price_change_percentage_24h", 0),
                "price_change_7d":    mkt.get("price_change_percentage_7d", 0),
                "price_change_30d":   mkt.get("price_change_percentage_30d", 0),
                "volume_24h":         mkt.get("total_volume", {}).get("usd", 0),
                "description":        (d.get("description", {}).get("en", "") or "")[:600],
            }
    except Exception:
        pass
    return {}


def fetch_etf_details(ticker, fmp_key=""):
    """Return ETF holdings and metadata. Uses static map; upgrades via FMP API if key provided."""
    t        = ticker.upper()
    meta     = dict(ETF_METADATA.get(t, {}))
    holdings = list(ETF_TOP_HOLDINGS.get(t, []))

    if fmp_key:
        try:
            r = requests.get(
                f"https://financialmodelingprep.com/api/v3/etf-holder/{t}",
                params={"apikey": fmp_key}, timeout=15,
            )
            if r.status_code == 200 and r.json():
                raw      = r.json()[:10]
                holdings = [(h.get("asset", "?"), round(h.get("weightPercentage", 0), 2))
                            for h in raw if h.get("asset")]
        except Exception:
            pass

        if not meta:
            try:
                r2 = requests.get(
                    "https://financialmodelingprep.com/api/v3/etf-info",
                    params={"symbol": t, "apikey": fmp_key}, timeout=15,
                )
                if r2.status_code == 200 and r2.json():
                    info = r2.json()[0]
                    meta = {
                        "name":     info.get("name", t),
                        "expense":  info.get("expenseRatio", 0),
                        "aum_b":    round((info.get("aum", 0) or 0) / 1e9, 1),
                        "category": info.get("category", ""),
                        "index":    info.get("trackingIndex", ""),
                        "holdings": info.get("numberOfHoldings", 0),
                    }
            except Exception:
                pass

    return {"meta": meta, "holdings": holdings}
