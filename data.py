import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time

from constants import get_risk_free_rate

POLYGON_BASE  = "https://api.polygon.io"
_API_CACHE    = {}
_API_CACHE_TTL = 300  # seconds — reuse responses for 5 minutes

SECTOR_ETF_MAP = {
    "Technology": "XLK", "Health Care": "XLV", "Financials": "XLF",
    "Consumer Discretionary": "XLY", "Consumer Staples": "XLP",
    "Industrials": "XLI", "Energy": "XLE", "Materials": "XLB",
    "Real Estate": "XLRE", "Utilities": "XLU", "Communication Services": "XLC",
}


def _get(endpoint, api_key, params=None, raise_on_error=False):
    if params is None:
        params = {}
    params["apiKey"] = api_key

    # Cache key built from endpoint + non-key params so same call is never repeated
    cache_key = endpoint + str(sorted((k, v) for k, v in params.items() if k != "apiKey"))
    cached = _API_CACHE.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _API_CACHE_TTL:
        return cached["data"]

    for attempt in range(3):
        r = requests.get(f"{POLYGON_BASE}{endpoint}", params=params, timeout=30)
        if r.status_code == 200:
            result = r.json()
            _API_CACHE[cache_key] = {"ts": time.time(), "data": result}
            return result
        if r.status_code == 429:
            wait = (attempt + 1) * 12   # 12s, 24s, 36s
            time.sleep(wait)
            continue
        if raise_on_error:
            if r.status_code == 403:
                raise ValueError("Polygon API key invalid or unauthorized (HTTP 403).")
        return None
    if raise_on_error:
        raise ValueError("Polygon API rate limit — please try again in a moment.")
    return None


def validate_ticker(ticker, api_key):
    try:
        data = _get(f"/v3/reference/tickers/{ticker.upper()}", api_key)
        if data and data.get("status") == "OK":
            info = data.get("results", {})
            return True, info
    except Exception:
        pass
    return False, "Ticker not found or invalid."


def _period_to_dates(period):
    end = datetime.today()
    mapping = {"1y": 365, "2y": 730, "5y": 1825, "10y": 3650}
    days = mapping.get(period, 1825)
    start = end - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def fetch_ohlcv(ticker, period, api_key, log=print,
                start_override=None, end_override=None, bar_size="day"):
    if bar_size not in ("day", "week", "month"):
        bar_size = "day"
    if start_override and end_override:
        start, end = start_override, end_override
    else:
        start, end = _period_to_dates(period)
    log(f"Downloading data for {ticker} ({start} → {end}, {bar_size})...")
    # Multi-source: yfinance (same-day, deep history) → Polygon fallback. Returns
    # the standardized [Date,Open,High,Low,Close,Volume] schema either way.
    from market_data import get_bars
    df = get_bars(ticker, start, end, interval=bar_size, polygon_key=api_key)
    if df is None or df.empty:
        raise ValueError(f"No price data for '{ticker}'. Check the symbol.")
    log(f"   {len(df)} bars fetched ({get_bars.__module__}).")
    return df


def _enrich_ohlcv(df, w52_min_periods=21):
    """Add standard derived columns to a raw OHLCV DataFrame in-place.

    Called by fetch_stock_data, fetch_bond_data, and fetch_crypto_data to avoid
    repeating the same 14-line block verbatim in each function.
    w52_min_periods: min_periods for the 52-week rolling window (stock/bond=21,
                     crypto=None which pandas resolves to the full window size).
    """
    df["Daily_Return"]     = df["Close"].pct_change()
    df["Cumulative_Index"] = (1 + df["Daily_Return"].fillna(0)).cumprod() * 100

    for ma in [20, 50, 200]:
        df[f"MA{ma}"]          = df["Close"].rolling(ma).mean()
        df[f"Close_vs_MA{ma}"] = (df["Close"] / df[f"MA{ma}"] - 1).where(df[f"MA{ma}"].notna())

    df["Vol_MA20"]           = df["Volume"].rolling(20).mean()
    df["Volume_vs_Avg"]      = np.where(df["Vol_MA20"] > 0, df["Volume"] / df["Vol_MA20"], np.nan)
    df["Volatility_20d"]     = df["Daily_Return"].rolling(20).std() * np.sqrt(252)
    df["Drawdown_20d"]       = df["Cumulative_Index"] / df["Cumulative_Index"].rolling(20).max() - 1
    df["Drawdown_60d"]       = df["Cumulative_Index"] / df["Cumulative_Index"].rolling(60).max() - 1
    df["52W_High"]           = df["Close"].rolling(252, min_periods=w52_min_periods).max()
    df["52W_Low"]            = df["Close"].rolling(252, min_periods=w52_min_periods).min()
    df["Pct_From_52W_High"]  = df["Close"] / df["52W_High"] - 1
    df["Pct_From_52W_Low"]   = df["Close"] / df["52W_Low"]  - 1
    return df


def fetch_stock_data(ticker, period="5y", benchmark_tickers=None, api_key="", log=print,
                     start_override=None, end_override=None, bar_size="day"):
    df = fetch_ohlcv(ticker, period, api_key, log=log,
                     start_override=start_override, end_override=end_override, bar_size=bar_size)
    _enrich_ohlcv(df)

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
    rfr      = get_risk_free_rate()
    df["Sharpe_Ratio"]  = (ann_ret - rfr) / ann_std  if ann_std  else np.nan
    df["Sortino_Ratio"] = (ann_ret - rfr) / downside if downside else np.nan

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


# ── SEC EDGAR fundamentals (free, no key, 10+ years, authoritative) ───────────
# EDGAR's companyfacts API exposes the full XBRL filed in 10-Ks/20-Fs — deeper
# and more reliable than Polygon's free 4-period limit, with no rate cap. Used
# as the PRIMARY fundamentals source; Polygon's fetch_financials stays as a
# fallback for filers EDGAR doesn't cover.
SEC_HEADERS  = {"User-Agent": "QuantWizard/1.0 (equity research; support@quantwizard.app)"}
_SEC_CIK_MAP = None

# XBRL us-gaap tag candidates — first one with data wins (tags vary by filer/era).
_SEC_TAGS = {
    "revenues": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                 "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "cost_of_revenue": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income_loss": ["OperatingIncomeLoss"],
    "net_income_loss": ["NetIncomeLoss", "ProfitLoss"],
    "research_and_development": ["ResearchAndDevelopmentExpense"],
    "depreciation_amortization": ["DepreciationDepletionAndAmortization",
                                  "DepreciationAndAmortization",
                                  "DepreciationAmortizationAndAccretionNet",
                                  "DepreciationAmortizationAndOther"],
    "assets": ["Assets"],
    "current_assets": ["AssetsCurrent"],
    "liabilities": ["Liabilities"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "debt_current": ["LongTermDebtCurrent", "DebtCurrent"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "net_cash_flow_from_operating_activities":
        ["NetCashProvidedByUsedInOperatingActivities",
         "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "net_cash_flow_from_investing_activities": ["NetCashProvidedByUsedInInvestingActivities"],
    "net_cash_flow_from_financing_activities": ["NetCashProvidedByUsedInFinancingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
}
_SEC_TAGS_EPS    = ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"]
_SEC_TAGS_SHARES = ["WeightedAverageNumberOfDilutedSharesOutstanding",
                    "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
                    "WeightedAverageNumberOfSharesOutstandingBasic"]


def _sec_load_cik_map(log=print):
    """Lazy-load and cache EDGAR's ticker→CIK map (free, ~10k entries)."""
    global _SEC_CIK_MAP
    if _SEC_CIK_MAP is not None:
        return _SEC_CIK_MAP
    try:
        r = requests.get("https://www.sec.gov/files/company_tickers.json",
                         headers=SEC_HEADERS, timeout=20)
        if r.status_code == 200:
            _SEC_CIK_MAP = {row["ticker"].upper(): str(row["cik_str"]).zfill(10)
                            for row in r.json().values()}
            return _SEC_CIK_MAP
    except Exception as e:
        log(f"   SEC CIK map failed: {e}")
    _SEC_CIK_MAP = {}
    return _SEC_CIK_MAP


def _sec_fy_series(facts, tags, unit="USD"):
    """
    {fiscal_year: (period_end, value)} from annual 10-K/20-F filings only,
    MERGED across the candidate tags in priority order — so a series stays
    continuous even when a company switched XBRL tags mid-history (e.g. the
    2018 revenue-recognition change from SalesRevenueNet → RevenueFromContract).
    For each fiscal year, the highest-priority tag with data wins.
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    out = {}
    for tag in tags:                       # priority order
        node = gaap.get(tag)
        if not node:
            continue
        series = node.get("units", {}).get(unit)
        if not series:
            continue
        per_tag = {}
        for e in series:
            if e.get("fp") != "FY":
                continue
            form = e.get("form", "")
            if not (form.startswith("10-K") or form.startswith("20-F")):
                continue
            fy, end, val = e.get("fy"), e.get("end", ""), e.get("val")
            if fy is None or val is None:
                continue
            if fy not in per_tag or end > per_tag[fy][0]:   # latest-ending wins within a tag
                per_tag[fy] = (end, val)
        for fy, ev in per_tag.items():
            out.setdefault(fy, ev)         # earlier (higher-priority) tag keeps the year
    return out


def fetch_sec_financials(ticker, years=10, log=print):
    """
    Up to `years` of annual statements from SEC EDGAR's companyfacts API.

    Returns {income_statement, balance_sheet, cash_flow_statement} as newest-first
    DataFrames whose columns match the Polygon shape (so compute_fundamentals works
    unchanged) plus extras (capex, retained_earnings, debt_current, cash, diluted_shares,
    depreciation_amortization) used for FCF / Piotroski F-Score / Altman Z-Score.
    Returns {} when unavailable (ETF, crypto, or a foreign filer without us-gaap XBRL).
    """
    cik = _sec_load_cik_map(log=log).get(ticker.upper())
    if not cik:
        return {}
    try:
        r = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                         headers=SEC_HEADERS, timeout=25)
        if r.status_code != 200:
            log(f"   EDGAR companyfacts HTTP {r.status_code} for {ticker}")
            return {}
        facts = r.json()
    except Exception as e:
        log(f"   EDGAR fetch failed for {ticker}: {e}")
        return {}

    fmap       = {f: _sec_fy_series(facts, _SEC_TAGS[f]) for f in _SEC_TAGS}
    eps_map    = _sec_fy_series(facts, _SEC_TAGS_EPS, unit="USD/shares")
    shares_map = _sec_fy_series(facts, _SEC_TAGS_SHARES, unit="shares")

    fys = sorted(set(fmap["net_income_loss"]) | set(fmap["revenues"]), reverse=True)[:years]
    if not fys:
        return {}

    def col(field, fy):
        m = fmap.get(field, {})
        return m[fy][1] if fy in m else None

    def end_of(fy):
        for f in ("net_income_loss", "revenues", "assets"):
            if fy in fmap.get(f, {}):
                return fmap[f][fy][0]
        return str(fy)

    inc_rows, bal_rows, cf_rows = [], [], []
    for fy in fys:
        period = end_of(fy)
        gp = col("gross_profit", fy)
        if gp is None:
            rev, cogs = col("revenues", fy), col("cost_of_revenue", fy)
            gp = (rev - cogs) if (rev is not None and cogs is not None) else None
        inc_rows.append({
            "Period": period,
            "revenues": col("revenues", fy), "cost_of_revenue": col("cost_of_revenue", fy),
            "gross_profit": gp, "operating_income_loss": col("operating_income_loss", fy),
            "net_income_loss": col("net_income_loss", fy),
            "research_and_development": col("research_and_development", fy),
            "depreciation_amortization": col("depreciation_amortization", fy),
            "diluted_earnings_per_share": eps_map.get(fy, (None, None))[1],
            "diluted_shares": shares_map.get(fy, (None, None))[1],
        })
        bal_rows.append({
            "Period": period,
            "assets": col("assets", fy), "current_assets": col("current_assets", fy),
            "liabilities": col("liabilities", fy),
            "current_liabilities": col("current_liabilities", fy),
            "equity": col("equity", fy), "long_term_debt": col("long_term_debt", fy),
            "debt_current": col("debt_current", fy), "cash": col("cash", fy),
            "retained_earnings": col("retained_earnings", fy),
        })
        cf_rows.append({
            "Period": period,
            "net_cash_flow_from_operating_activities": col("net_cash_flow_from_operating_activities", fy),
            "net_cash_flow_from_investing_activities": col("net_cash_flow_from_investing_activities", fy),
            "net_cash_flow_from_financing_activities": col("net_cash_flow_from_financing_activities", fy),
            "capex": col("capex", fy),
        })

    log(f"   EDGAR: {len(fys)} fiscal years for {ticker} ({fys[-1]}–{fys[0]})")
    return {
        "income_statement":    pd.DataFrame(inc_rows),
        "balance_sheet":       pd.DataFrame(bal_rows),
        "cash_flow_statement": pd.DataFrame(cf_rows),
        "source": "SEC EDGAR",
    }


_NAME_STOPWORDS = {"inc", "inc.", "corp", "corp.", "corporation", "company", "co",
                   "holdings", "group", "ltd", "plc", "class", "the", "&", "and",
                   "technologies", "technology", "international", "systems"}


def fetch_news(ticker, api_key, company_name=None, log=print, limit=30):
    """Ticker news from Polygon, scored for relevance and tagged with sentiment.

    Polygon returns any article that *mentions* the ticker, so "top 10 stocks"
    round-ups (that name the company only in a long ticker list) leak in and make
    the feed look uncurated. We rank each article High/Medium/Low using Polygon's
    per-ticker `insights` (the ticker being analysed = the article is about it),
    a company-name/ticker match in the title, and how many tickers the article
    spans; broad round-ups (Low) are dropped. The target ticker's Polygon
    sentiment is surfaced so the report reads as curated, not a raw feed."""
    log(f"Fetching news for {ticker}...")
    data = _get("/v2/reference/news", api_key, params={
        "ticker": ticker, "limit": limit, "order": "desc", "sort": "published_utc"
    })
    if not data or not data.get("results"):
        return []

    tkr = ticker.upper()
    name_tokens = [w for w in (company_name or "").lower().replace(",", " ").split()
                   if len(w) > 2 and w not in _NAME_STOPWORDS]

    scored = []
    for item in data["results"]:
        tickers  = [t.upper() for t in (item.get("tickers") or [])]
        insights = item.get("insights") or []
        title    = item.get("title", "") or ""
        hay      = (title + " " + (item.get("description", "") or "")).lower()

        ins        = next((i for i in insights if i.get("ticker", "").upper() == tkr), None)
        sentiment  = (ins.get("sentiment") if ins else None) or ""
        name_hit   = tkr.lower() in title.lower() or any(tok in hay for tok in name_tokens)
        n_tickers  = len(tickers)

        if name_hit or ins is not None or n_tickers <= 3:
            relevance = "High"
        elif n_tickers <= 8:
            relevance = "Medium"
        else:
            relevance = "Low"

        scored.append({
            "Date":          item.get("published_utc", "")[:16].replace("T", " "),
            "Headline":      title,
            "Publisher":     item.get("publisher", {}).get("name", ""),
            "URL":           item.get("article_url", ""),
            "Sentiment":     sentiment.capitalize(),
            "Relevance":     relevance,
            "Also_Mentions": max(0, n_tickers - 1),
        })

    # Drop broad round-ups; if that leaves nothing, fall back to the raw list so the
    # section never disappears. Most-relevant first, recency preserved within a tier.
    kept = [n for n in scored if n["Relevance"] != "Low"] or scored
    rank = {"High": 0, "Medium": 1, "Low": 2}
    kept.sort(key=lambda n: rank[n["Relevance"]])
    log(f"   {len(kept)}/{len(scored)} relevant news items")
    return kept[:15]


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
    _enrich_ohlcv(df)

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
    rfr      = get_risk_free_rate()
    df["Sharpe_Ratio"]  = (ann_ret - rfr) / ann_std  if ann_std  else np.nan
    df["Sortino_Ratio"] = (ann_ret - rfr) / downside if downside else np.nan

    return df.sort_values("Date").reset_index(drop=True)


def fetch_sector_data(ticker, api_key, sector, log=print,
                      start_override=None, end_override=None, bar_size="day"):
    etf = SECTOR_ETF_MAP.get(sector)
    if not etf:
        return None
    log(f"Fetching sector ETF: {etf}...")
    try:
        etf_df = fetch_ohlcv(etf, "5y", api_key, log=lambda m: None,
                             start_override=start_override, end_override=end_override,
                             bar_size=bar_size)
        etf_df["Sector_Return"]         = etf_df["Close"].pct_change()
        etf_df["Sector_ETF_Cumulative"] = (1 + etf_df["Sector_Return"].fillna(0)).cumprod() * 100
        return etf_df[["Date", "Sector_ETF_Cumulative", "Sector_Return"]]
    except Exception as e:
        log(f"   Sector ETF failed: {e}")
        return None


def fetch_next_earnings(ticker, api_key):
    # NOTE: Polygon's free tier does not provide guaranteed forward earnings dates.
    # If next_earnings_date is unavailable, filing_date is returned as a proxy.
    # For precise forward dates, integrate a dedicated calendar API such as
    # Nasdaq earnings calendar or Finnhub at https://finnhub.io/docs/api/earnings-calendar
    try:
        data = _get("/vX/reference/financials", api_key, params={
            "ticker": ticker, "timeframe": "quarterly",
            "sort": "filing_date", "order": "desc", "limit": 1,
        })
        if data and data.get("results"):
            result    = data["results"][0]
            next_date = result.get("next_earnings_date") or result.get("filing_date")
            if next_date:
                return next_date
    except Exception:
        pass
    return "N/A"


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
    # w52_min_periods=None preserves original rolling(252) behaviour (no early values)
    _enrich_ohlcv(df, w52_min_periods=None)

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

    ret      = df["Daily_Return"].dropna()
    ann_ret  = ret.mean() * 252
    ann_std  = ret.std() * np.sqrt(252)
    downside = ret[ret < 0].std() * np.sqrt(252)
    # Excess-return Sharpe/Sortino (subtract the risk-free rate) to match the
    # stock and bond paths — crypto was previously the only raw ann_ret/vol one.
    rfr      = get_risk_free_rate()
    df["Sharpe_Ratio"]  = (ann_ret - rfr) / ann_std  if ann_std  else np.nan
    df["Sortino_Ratio"] = (ann_ret - rfr) / downside if downside else np.nan

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
