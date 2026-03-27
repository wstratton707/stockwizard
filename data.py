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


def fetch_ohlcv(ticker, period, api_key, log=print):
    start, end = _period_to_dates(period)
    log(f"Downloading {period} data for {ticker}...")
    data = _get(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}",
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
    log(f"   {len(df)} trading days fetched.")
    return df


def fetch_stock_data(ticker, period="5y", benchmark_tickers=None, api_key="", log=print):
    df = fetch_ohlcv(ticker, period, api_key, log=log)

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
    for statement in ["income_statement", "balance_sheet", "cash_flow_statement"]:
        try:
            data = _get("/vX/reference/financials", api_key, params={
                "ticker": ticker, "timeframe": "annual", "limit": 4,
                "include_sources": "false"
            })
            if data and data.get("results"):
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
            break
        except Exception as e:
            log(f"   Financials skipped: {e}")
            break
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
