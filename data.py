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


def _get(endpoint, api_key, params=None, raise_on_error=False, max_attempts=3):
    if params is None:
        params = {}
    params["apiKey"] = api_key

    # Cache key built from endpoint + non-key params so same call is never repeated
    cache_key = endpoint + str(sorted((k, v) for k, v in params.items() if k != "apiKey"))
    cached = _API_CACHE.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _API_CACHE_TTL:
        return cached["data"]

    for attempt in range(max_attempts):
        r = requests.get(f"{POLYGON_BASE}{endpoint}", params=params, timeout=30)
        if r.status_code == 200:
            result = r.json()
            _API_CACHE[cache_key] = {"ts": time.time(), "data": result}
            return result
        if r.status_code == 429:
            if attempt + 1 >= max_attempts:
                break
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
    # From the daily High and Low, not the close. Every other platform quotes
    # the 52-week range intraday, and a closes-only range put AAPL's "52-week
    # high" at $339.79 on a page whose own day range showed $345.34 - a
    # range that the price had already been outside of.
    _hi = df["High"] if "High" in df.columns else df["Close"]
    _lo = df["Low"]  if "Low"  in df.columns else df["Close"]
    df["52W_High"]           = _hi.rolling(252, min_periods=w52_min_periods).max()
    df["52W_Low"]            = _lo.rolling(252, min_periods=w52_min_periods).min()
    df["Pct_From_52W_High"]  = df["Close"] / df["52W_High"] - 1
    df["Pct_From_52W_Low"]   = df["Close"] / df["52W_Low"]  - 1
    return df


def _attach_risk_ratios(df):
    """Attach the scalar Sharpe / Sortino columns from the Daily_Return series.

    One implementation for stocks, bonds and crypto. This block was pasted
    verbatim in all three fetchers, which is how the crypto path kept a raw
    (non-excess) Sharpe long after the other two were corrected — a copy is a
    place a fix can fail to land.

    Sortino divides by the annualised downside deviation about zero, not by the
    standard deviation of the negative returns (see analysis.downside_deviation
    for why that distinction matters).
    """
    from analysis import downside_deviation
    ret      = df["Daily_Return"].dropna()
    ann_ret  = ret.mean() * 252
    ann_std  = ret.std() * np.sqrt(252)
    downside = downside_deviation(ret)
    rfr      = get_risk_free_rate()
    df["Sharpe_Ratio"]  = (ann_ret - rfr) / ann_std  if ann_std  else np.nan
    df["Sortino_Ratio"] = (ann_ret - rfr) / downside if downside else np.nan
    return df


def _add_indicators(df, log=print):
    """RSI, MACD and Bollinger columns, in place. Shared by the initial fetch and
    by append_live_session, so an appended bar gets the same indicators."""
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


_OHLCV_BASE = ("Date", "Open", "High", "Low", "Close", "Volume")


def append_live_session(df, live):
    """Add the live quote's session as a bar when the daily feed hasn't got it yet.

    The daily feed lags the quote by a session: after the close, AAPL's quote
    read $339.75 while the newest daily bar was the previous day's $338.98. The
    header used the quote and everything else used the bar, so one page showed
    two current prices - the verdict said "most recently closing at $338.98",
    the chart label said $338.98, and the day range topped out below the price
    in the header. Appending the session here makes every consumer agree.

    Only a quote carrying its own timestamp and open/high/low is used, and only
    when it is from a LATER session than the last bar. Volume is not in the
    quote, so the new bar's volume is NaN; readers of volume fall back to the
    last session that has one. Benchmark columns carry NaN returns for the new
    bar and a forward-filled cumulative level.
    """
    try:
        if df is None or df.empty or not live:
            return df
        ep = live.get("epoch")
        if not ep or not (live.get("open") and live.get("high") and live.get("low")):
            return df
        q_day = (pd.Timestamp(int(ep), unit="s", tz="UTC")
                 .tz_convert("America/New_York").normalize().tz_localize(None))
        dates = pd.to_datetime(df["Date"])
        last = dates.iloc[-1]
        last = (last.tz_convert("America/New_York").tz_localize(None)
                if getattr(last, "tzinfo", None) else last).normalize()
        if q_day <= last:
            return df
        base = [c for c in _OHLCV_BASE if c in df.columns]
        extra = [c for c in df.columns if c.endswith("_Return") or c.endswith("_Cumulative")]
        raw = df[base + extra].copy()
        new = {"Date": q_day if not getattr(dates.iloc[-1], "tzinfo", None)
                        else q_day.tz_localize(dates.iloc[-1].tzinfo),
               "Open": float(live["open"]), "High": float(live["high"]),
               "Low": float(live["low"]), "Close": float(live["price"]),
               "Volume": np.nan}
        raw = pd.concat([raw, pd.DataFrame([{k: new.get(k, np.nan) for k in raw.columns}])],
                        ignore_index=True)
        for c in extra:
            if c.endswith("_Cumulative"):
                raw[c] = raw[c].ffill()
        _enrich_ohlcv(raw)
        _add_indicators(raw, log=lambda m: None)
        # Rolling beta and anything else fetch_stock_data derived afterwards
        for c in df.columns:
            if c not in raw.columns:
                raw[c] = list(df[c]) + [np.nan]
        return raw
    except Exception:
        return df


def fetch_stock_data(ticker, period="5y", benchmark_tickers=None, api_key="", log=print,
                     start_override=None, end_override=None, bar_size="day"):
    df = fetch_ohlcv(ticker, period, api_key, log=log,
                     start_override=start_override, end_override=end_override, bar_size=bar_size)
    _enrich_ohlcv(df)
    _add_indicators(df, log=log)

    if benchmark_tickers:
        for bench in benchmark_tickers:
            log(f"   Benchmark: {bench}")
            try:
                bdf = fetch_ohlcv(bench, period, api_key, log=lambda m: None,
                                  start_override=start_override, end_override=end_override,
                                  bar_size=bar_size)
                bdf[f"{bench}_Return"]     = bdf["Close"].pct_change()
                bdf[f"{bench}_Cumulative"] = (1 + bdf[f"{bench}_Return"].fillna(0)).cumprod() * 100
                # Join on the calendar date, not the timestamp: the stock and the
                # benchmark can come from different sources, and one stamps a
                # session at midnight while the other used 04:00 UTC. An exact
                # join left every benchmark cell empty.
                if bar_size in ("day", "week", "month"):
                    df["Date"]  = pd.to_datetime(df["Date"]).dt.normalize()
                    bdf["Date"] = pd.to_datetime(bdf["Date"]).dt.normalize()
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

    _attach_risk_ratios(df)

    return df.sort_values("Date").reset_index(drop=True)


def _company_details_yf(ticker, log=print):
    """Company profile from yfinance. Same keys as the Polygon shape.

    Used when Polygon returns nothing. That is not a rare case: the free tier
    allows five calls a minute, so a busy moment, an expired key or an outage
    all land here - and every one of them used to return {}, which took out the
    whole valuation slide, every market-cap-derived ratio (P/E, P/S, P/B,
    EV/EBITDA, FCF yield, Altman Z) and the entire company snapshot. Three of
    thirteen slides reading "N/A" because one lookup failed is a bad trade for a
    fallback this cheap.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
    except Exception as e:
        log(f"   yfinance company details failed for {ticker}: {type(e).__name__}")
        return {}
    if not info.get("longName") and not info.get("marketCap"):
        return {}
    _sector = info.get("sector") or info.get("industry") or "N/A"
    return {
        "Ticker":      ticker,
        "Name":        info.get("longName") or info.get("shortName") or "N/A",
        "Sector":      _sector,
        "Industry":    info.get("industry") or _sector,
        "Exchange":    info.get("exchange") or "N/A",
        "Market Cap":  info.get("marketCap") or "N/A",
        "Employees":   info.get("fullTimeEmployees") or "N/A",
        "Description": info.get("longBusinessSummary") or "N/A",
        "Website":     info.get("website") or "N/A",
        "Country":     (info.get("country") or "N/A"),
        "source":      "Yahoo Finance",
    }


def fetch_company_details(ticker, api_key, log=print):
    log(f"Fetching company details for {ticker}...")
    data = _get(f"/v3/reference/tickers/{ticker}", api_key)
    if not data:
        log(f"   Polygon returned nothing for {ticker}; trying Yahoo Finance")
        return _company_details_yf(ticker, log=log)
    r = data.get("results", {})
    # Polygon's sic_description arrives ALL-CAPS ("RUBBER & PLASTICS FOOTWEAR")
    # and reads like a data glitch in client-facing reports; locale is lowercase.
    _sic = r.get("sic_description") or "N/A"
    if _sic.isupper():
        _sic = _sic.title()
    # A Polygon record with no market cap still breaks every valuation ratio,
    # so top that one field up rather than discarding an otherwise good record.
    _mcap = r.get("market_cap")
    if not _mcap:
        try:
            import yfinance as yf
            _mcap = (yf.Ticker(ticker).info or {}).get("marketCap")
        except Exception:
            _mcap = None
    return {
        "Ticker":      ticker,
        "Name":        r.get("name", "N/A"),
        "Sector":      _sic,
        "Industry":    _sic,
        "Exchange":    r.get("primary_exchange", "N/A"),
        "Market Cap":  _mcap or "N/A",
        "Employees":   r.get("total_employees", "N/A"),
        "Description": r.get("description", "N/A"),
        "Website":     r.get("homepage_url", "N/A"),
        "Country":     (r.get("locale") or "N/A").upper(),
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
    # Order matters — first tag with data wins, so the operating-company tags stay
    # first and nothing changes for them. The trailing three are what banks,
    # brokers and insurers actually file: Goldman Sachs reports none of the four
    # above, which is why its Revenue row rendered as em-dashes across all ten
    # years and took Revenue YoY, Revenue CAGR, every margin and P/S down with it.
    # RevenuesNetOfInterestExpense is GS's "Total net revenues" line.
    "revenues": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                 "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax",
                 "RevenuesNetOfInterestExpense", "InterestAndDividendIncomeOperating",
                 "NoninterestIncome"],
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
    "long_term_debt": ["LongTermDebtNoncurrent",
                       "LongTermDebtAndCapitalLeaseObligations",
                       "LongTermDebt"],
    # Current debt comes in two shapes and they must not be mixed. DebtCurrent
    # is the ROLL-UP - when a filer reports it, it already contains the current
    # portion of long-term debt, commercial paper and any other short-term
    # borrowing, so it is used alone. Filers that do not report it (Apple is
    # one) report the pieces separately, and the pieces must be SUMMED.
    #
    # The old single list had LongTermDebtCurrent first and DebtCurrent second
    # as a fallback, which is backwards twice over: it preferred the narrow tag
    # to the roll-up, and a first-match-wins list can never add two co-existing
    # components. Apple's FY2025 commercial paper - $7.979B, 8.8% of its total
    # debt - was silently dropped, understating gross debt in the WACC weight
    # and net debt in the DCF bridge.
    # A filer that reports one of these has stated its ENTIRE interest-bearing
    # debt in a single fact, current maturities included. Verizon tags it
    # LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities
    # ($157.7B at FY2025) and General Motors the same ($131.6B); neither reports
    # LongTermDebtNoncurrent at all, so before this both had their entire
    # non-current debt dropped - Verizon's total read $18.6B against a true
    # ~$158B, an 88% understatement straight into the WACC weight and the DCF
    # bridge.
    "debt_total_incl_current": ["LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                                "DebtLongtermAndShorttermCombinedAmount"],
    "debt_current_total": ["DebtCurrent"],
    # Pick-one, broadest first: these OVERLAP rather than add. Verizon reports
    # LongTermDebtCurrent 18,618 and LongTermDebtAndCapitalLeaseObligationsCurrent
    # 18,177 for the same debt; summing them would double-count.
    "debt_current": ["LongTermDebtAndCapitalLeaseObligationsCurrent",
                     "LongTermDebtCurrent"],
    # Genuinely additive - a separate instrument, not a different label for the
    # same one. Only added when the DebtCurrent roll-up is absent, since that
    # roll-up already contains it.
    "commercial_paper": ["CommercialPaper"],
    "short_term_borrowings": ["ShortTermBorrowings", "OtherShortTermBorrowings",
                              "ShortTermNonBankLoansAndNotesPayable"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    # Short-term investments count toward cash in a net-debt calculation: they
    # are liquid claims available to retire debt, which is what net debt is
    # asking about. Excluding them was not a decision, it was an omission -
    # Apple carries $18.763B of them at FY2025 against $35.934B of cash, so net
    # debt was overstated by more than half the cash balance. Non-current
    # marketable securities are deliberately NOT included: they are not
    # short-term claims and including them is a more aggressive convention than
    # this model should adopt silently.
    "short_term_investments": ["MarketableSecuritiesCurrent",
                               "ShortTermInvestments",
                               "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    # Liquid investment-grade securities held beyond a year. Apple carries
    # $77.7B of them and counts them in its own net-cash figure; leaving them
    # out understated its net cash by more than the whole cash line.
    "lt_securities": ["MarketableSecuritiesNoncurrent",
                      "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent"],
    "net_cash_flow_from_operating_activities":
        ["NetCashProvidedByUsedInOperatingActivities",
         "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "net_cash_flow_from_investing_activities": ["NetCashProvidedByUsedInInvestingActivities"],
    "net_cash_flow_from_financing_activities": ["NetCashProvidedByUsedInFinancingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    # Microsoft and Alphabet file no combined D&A fact, only the parts. Used
    # only when no combined tag exists.
    "depreciation_only": ["Depreciation"],
    "amortization_intangibles": ["AmortizationOfIntangibleAssets"],
    # Capital allocation and the cost free cash flow leaves out: stock pay is a
    # real expense that operating cash flow adds back (Apple FY2025: $12.9B).
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    # For one-off tax items: a year whose effective rate sits far from the
    # company's usual one had something non-recurring in it (Apple FY2024:
    # 24.1% against ~16%, the $10.2B EU State Aid charge).
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends_paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    # The report workbook's capital-structure block and clean-EPS rows.
    # Preferred stock is a claim senior to the common: Alphabet's $18.0B
    # mandatory convertible (June 2026) is tagged with the convertible tag.
    "preferred": ["ConvertiblePreferredStockNonredeemableOrRedeemableIssuerOptionValue",
                  "PreferredStockValue", "TemporaryEquityCarryingAmountAttributableToParent"],
    # Private stakes carried at cost-less-impairment ("measurement alternative")
    # and equity-method investments - value free cash flow does not capture.
    "nonmkt_securities": ["EquitySecuritiesWithoutReadilyDeterminableFairValueAmount"],
    "equity_method": ["EquityMethodInvestments"],
    # Mark-to-market gains on equity holdings run through net income; Alphabet's
    # added $9.44 to trailing EPS. NVIDIA files its under the broader tag.
    "equity_gains": ["EquitySecuritiesFvNiGainLoss", "GainLossOnInvestments",
                     "EquitySecuritiesFvNiUnrealizedGainLoss"],
    "other_income": ["NonoperatingIncomeExpense"],
    # For filers with no operating-income line (Nike reports pre-tax income
    # straight after its expenses): operating income = pre-tax income less
    # these non-operating items.
    "nonop_interest": ["InterestIncomeExpenseNonoperatingNet"],
    "nonop_other": ["OtherNonoperatingIncomeExpense"],
    # Operating drivers the Segments tab shows when the company tags them.
    "rpo": ["RevenueRemainingPerformanceObligation"],
    "deferred_revenue": ["ContractWithCustomerLiabilityCurrent"],
    "inventory": ["InventoryNet"],
}
_SEC_TAGS_EPS    = ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"]
_SEC_TAGS_SHARES = ["WeightedAverageNumberOfDilutedSharesOutstanding",
                    "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
                    "WeightedAverageNumberOfSharesOutstandingBasic"]


def _sec_cik_for(ticker, log=print):
    """CIK for `ticker`, tolerating the two ways a share class gets written.

    EDGAR writes class shares with a hyphen (BRK-B, BF-B); price feeds and this
    codebase use a dot (BRK.B, BF.B). Looking up the dotted form returned nothing
    and the caller quietly rendered no filings at all - the same symbol-format
    trap that once dropped BF.B from the ranked universe.
    """
    m = _sec_load_cik_map(log=log)
    t = (ticker or "").upper().strip()
    return m.get(t) or m.get(t.replace(".", "-")) or m.get(t.replace("-", "."))


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


# ── EDGAR filings index ───────────────────────────────────────────────────────
# What a company is legally required to publish, as opposed to what has been
# written about it. On the News page this is the primary source: an 8-K IS the
# news, and every headline about an earnings beat is commentary on a 10-Q that
# is already public and already here.
#
# Same free endpoint family as the financials above, same CIK map, no key.

# Forms worth surfacing, in the order a reader cares about them, with what each
# one actually is. Anything not listed still appears - it is just not explained.
FILING_FORMS = {
    "10-K":     "Annual report - audited financials for the full year",
    "10-Q":     "Quarterly report - unaudited financials for the quarter",
    "8-K":      "Material event - something the company must disclose promptly",
    "DEF 14A":  "Proxy statement - executive pay and what shareholders vote on",
    "S-1":      "Registration - a new offering of securities",
    "S-3":      "Shelf registration - securities the company may sell later",
    "4":        "Insider transaction - an officer or director bought or sold",
    "3":        "Insider's opening position",
    "5":        "Insider transactions not reported during the year",
    "SC 13D":   "Activist stake - over 5%, with intent to influence",
    "SC 13G":   "Passive stake - over 5%, held passively",
    "20-F":     "Annual report from a foreign private issuer",
    "6-K":      "Interim report from a foreign private issuer",
    "11-K":     "Employee stock purchase or savings plan report",
    "424B2":    "Prospectus supplement - pricing for an offering",
}


def fetch_sec_filings(ticker, limit=0, log=print):
    """Recent EDGAR filings for `ticker`, newest first.

    Returns a list of dicts: form, description, filed, period, accession, url,
    primary_doc. Empty list when the ticker has no CIK (ETFs, crypto, most
    foreign tickers) or EDGAR is unreachable - the caller renders nothing rather
    than an error, because a missing filings list should never break a page.
    """
    cik = _sec_cik_for(ticker, log=log)
    if not cik:
        return []
    try:
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                         headers=SEC_HEADERS, timeout=20)
        if r.status_code != 200:
            log(f"   EDGAR submissions HTTP {r.status_code} for {ticker}")
            return []
        data = r.json()
    except Exception as e:
        log(f"   EDGAR submissions failed for {ticker}: {e}")
        return []

    recent = (data.get("filings") or {}).get("recent") or {}
    forms  = recent.get("form") or []
    if not forms:
        return []

    accs    = recent.get("accessionNumber") or []
    dates   = recent.get("filingDate") or []
    periods = recent.get("reportDate") or []
    docs    = recent.get("primaryDocument") or []
    cik_int = str(int(cik))          # EDGAR's archive paths drop the zero padding

    # No truncation by default. EDGAR returns its whole recent block in one
    # response, and cutting it here loses the filings that matter: JPM files
    # hundreds of 424B2 prospectus supplements, so its 10-K sits well past the
    # first 250 entries and a truncated list yielded zero key filings for it.
    # The caller slices for display; selection needs everything.
    n = len(forms) if not limit else min(len(forms), limit)
    out = []
    for i in range(n):
        acc = accs[i] if i < len(accs) else ""
        doc = docs[i] if i < len(docs) else ""
        acc_plain = acc.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_plain}"
        out.append({
            "form":        forms[i],
            "description": FILING_FORMS.get(forms[i], ""),
            "filed":       dates[i] if i < len(dates) else "",
            "period":      periods[i] if i < len(periods) else "",
            "accession":   acc,
            # The filing itself when EDGAR names a primary document, otherwise
            # the index page for that accession, which always exists.
            "url":         f"{base}/{doc}" if doc else f"{base}/{acc}-index.htm",
        })
    return out


# The reports people mean when they say "the filings". A raw newest-first list is
# dominated by Form 4s and, for a bank like JPM, by hundreds of 424B2 prospectus
# supplements - real filings, but not what someone researching the company wants
# first. These are pulled out and shown above the full list.
KEY_FORMS = ["10-K", "10-Q", "8-K", "DEF 14A", "20-F", "6-K"]


def key_filings(filings, forms=None):
    """The most recent filing of each key form, in KEY_FORMS order.

    Matches amendments too (a 10-K/A is still the annual report) but prefers the
    original when both are present, since the amendment alone is rarely what a
    reader wants to open first.
    """
    forms = forms or KEY_FORMS
    out = []
    for want in forms:
        exact = next((f for f in filings if f.get("form") == want), None)
        amended = next((f for f in filings
                        if str(f.get("form", "")).startswith(want + "/")), None)
        best = exact or amended
        if best:
            out.append(best)
    return out



# ── Trailing twelve months, from the quarterly filings ────────────────────────
# Annual statements alone left every ratio a fiscal year behind the price: AAPL's
# report paired a September 2026 price with September 2025 earnings, although
# three 10-Qs had been filed since. Flows are rebuilt quarter by quarter and the
# latest four summed; the balance sheet is the latest filed one.
#
# Filings report flows CUMULATIVELY from the start of the fiscal year (3, 6, 9,
# 12 months), and the cash-flow statement only that way. Grouping facts by their
# start date and differencing consecutive cumulative values recovers each
# quarter, including Q4 as the full year less nine months - for income and cash
# flow alike. Dollar amounts need no split adjustment.
_TTM_INCOME = ("revenues", "cost_of_revenue", "gross_profit", "operating_income_loss",
               "net_income_loss", "research_and_development", "depreciation_amortization",
               "pretax_income", "income_tax")
_TTM_CASH   = ("net_cash_flow_from_operating_activities", "capex", "sbc", "buybacks",
               "dividends_paid")
_BAL_FIELDS = ("assets", "current_assets", "liabilities", "current_liabilities", "equity",
               "long_term_debt", "debt_current", "debt_total_incl_current",
               "debt_current_total", "commercial_paper", "short_term_borrowings",
               "cash", "short_term_investments", "retained_earnings", "lt_securities",
               "preferred", "nonmkt_securities", "equity_method", "rpo", "deferred_revenue",
               "inventory")


def _sec_quarterly_flow(facts, tags, unit="USD"):
    """{quarter_end: value} for a duration concept, one entry per fiscal quarter."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    out = {}
    for tag in tags:                                   # priority order
        node = gaap.get(tag)
        if not node:
            continue
        best = {}                                      # (start, end) -> (val, filed)
        for e in node.get("units", {}).get(unit) or []:
            s, d, v = e.get("start"), e.get("end"), e.get("val")
            if not s or not d or v is None:
                continue
            f = e.get("filed", "")
            if (s, d) not in best or f >= best[(s, d)][1]:
                best[(s, d)] = (float(v), f)           # later filing = restatement
        by_start = {}
        for (s, d), (v, _f) in best.items():
            by_start.setdefault(s, []).append((pd.Timestamp(d), v))
        for s, lst in by_start.items():
            s_ts = pd.Timestamp(s)
            lst.sort()
            prev_end, prev_val = s_ts - pd.Timedelta(days=1), 0.0
            for d, v in lst:
                if (d - s_ts).days > 380:
                    break
                if 80 <= (d - prev_end).days <= 100:
                    out.setdefault(d, v - prev_val)    # higher-priority tag wins
                prev_end, prev_val = d, v
    return dict(sorted(out.items()))


def _sec_latest_balance(facts):
    """(date, {field: value}) for the most recent balance-sheet date filed.

    Every field is read at that ONE date; a field not reported on it is None
    rather than an older figure, so no ratio mixes two balance sheets."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    per_field = {}
    for field in _BAL_FIELDS:
        vals = {}
        for tag in _SEC_TAGS.get(field, []):
            node = gaap.get(tag)
            if not node:
                continue
            for e in node.get("units", {}).get("USD") or []:
                if e.get("start") or not e.get("end") or e.get("val") is None:
                    continue
                d = pd.Timestamp(e["end"])
                f = e.get("filed", "")
                cur = vals.get(d)
                if cur is None or (cur[2] == tag and f >= cur[1]):
                    vals[d] = (float(e["val"]), f, tag)
        per_field[field] = vals
    dates = sorted(per_field.get("assets", {}))
    if not dates:
        return None, {}
    d = dates[-1]
    return d, {f: (per_field[f][d][0] if d in per_field[f] else None) for f in _BAL_FIELDS}


def _sec_instant_series(facts, field):
    """{date: value} for a balance-sheet (instant) field, first tag with data
    at each date winning, latest filing at that date winning."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    vals = {}
    for tag in _SEC_TAGS.get(field, []):
        for e in (gaap.get(tag, {}).get("units", {}).get("USD") or []):
            if e.get("start") or not e.get("end") or e.get("val") is None:
                continue
            d = pd.Timestamp(e["end"])
            f = e.get("filed", "")
            cur = vals.get(d)
            if cur is None or (cur[2] == tag and f >= cur[1]):
                vals[d] = (float(e["val"]), f, tag)
    return {d: v[0] for d, v in sorted(vals.items())}


def _sec_cover_shares(facts):
    """(date, shares outstanding) from the latest filing's cover page.

    The market cap should be THIS count times the price. Backing shares out of a
    vendor's market cap gave Apple 14.715B - its weighted-average diluted count -
    against 14.594B on the 10-Q cover, and a market cap 0.8% high. Several
    values in one filing at one date are share classes and are summed."""
    node = facts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding")
    rows = [e for e in ((node or {}).get("units", {}).get("shares") or [])
            if e.get("val") and e.get("end")]
    if not rows:
        return None, None
    last = max(rows, key=lambda e: (e.get("filed", ""), e["end"]))
    vals = {float(e["val"]) for e in rows
            if e.get("accn") == last.get("accn") and e["end"] == last["end"]}
    return last["end"], sum(vals)


def _etr_q(pre_q, tax_q, d, near):
    if not pre_q or not tax_q or d is None:
        return None
    pre, tax = near(pre_q, d), near(tax_q, d)
    return (tax / pre) if (pre and pre > 0 and tax is not None) else None


def fiscal_quarter_label(end, fy_end_month):
    """("Q4", 2025) for a quarter ending in the fiscal year's last month, etc.

    The fiscal year is named for the calendar year it ends in - Apple's FY2026
    runs October 2025 to September 2026 - and quarters count back from the
    fiscal year-end month."""
    end = pd.Timestamp(end)
    # 52/53-week years end a few days either side of a month-end; the nearest
    # month-end names the quarter, so 27 Sep and 2 Oct both count as September.
    anchor = end if end.day >= 15 else end - pd.Timedelta(days=end.day + 1)
    m, y = anchor.month, anchor.year
    back = (fy_end_month - m) % 12
    q = 4 - back // 3
    fy = y if m <= fy_end_month else y + 1
    return f"Q{q}", fy


def _sec_quarter_table(facts, rev_q, gp_q, fy_end, pre_q=None, tax_q=None, extra=None):
    """The latest four quarters: revenue, diluted EPS, gross profit and growth
    on the same quarter a year earlier, oldest first.

    EPS prefers the 3-month fact the filing states; a fourth quarter has none
    (10-Ks report the year), so it is the full year less nine months.

    `extra` is {name: {quarter_end: value}} - net income, operating income,
    other income, equity-security gains, R&D - each read for the quarter and
    for the same quarter a year earlier (`name_prev`)."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    exact = {}
    for tag in _SEC_TAGS_EPS:
        for e in (gaap.get(tag, {}).get("units", {}).get("USD/shares") or []):
            s, d = e.get("start"), e.get("end")
            if not s or not d or e.get("val") is None:
                continue
            if 80 <= (pd.Timestamp(d) - pd.Timestamp(s)).days <= 100:
                k = pd.Timestamp(d)
                if k not in exact or e.get("filed", "") >= exact[k][1]:
                    exact[k] = (float(e["val"]), e.get("filed", ""))
    diffd = _sec_quarterly_flow(facts, _SEC_TAGS_EPS, unit="USD/shares")
    eps_q = {k: v for k, v in diffd.items()}
    eps_q.update({k: v for k, (v, _f) in exact.items()})

    def near(series, d):
        if d in series:
            return series[d]
        m = [v for k, v in series.items() if abs((k - d).days) <= 7]
        return m[0] if m else None

    ends = list(rev_q)[-4:]
    fy_month = pd.Timestamp(fy_end).month
    rows = []
    for d in ends:
        prev = [k for k in rev_q if 350 <= (d - k).days <= 380]
        p = prev[-1] if prev else None
        rev, eps, gp = near(rev_q, d), near(eps_q, d), near(gp_q, d)
        rev_p = near(rev_q, p) if p is not None else None
        eps_p = near(eps_q, p) if p is not None else None
        q, fy = fiscal_quarter_label(d, fy_month)
        rows.append({
            "end": str(d.date()), "label": f"{q} FY{str(fy)[2:]}", "fiscal_year": fy,
            "revenue": rev, "eps": eps, "gross_profit": gp,
            "rev_yoy": (rev / rev_p - 1) if (rev is not None and rev_p) else None,
            "eps_yoy": (eps / eps_p - 1) if (eps is not None and eps_p and eps_p > 0) else None,
            "eps_exact": d in exact,
            # effective tax rates, this quarter and a year earlier - a one-off
            # tax charge in the base quarter distorts the growth rate
            "etr": _etr_q(pre_q, tax_q, d, near),
            "etr_prev": _etr_q(pre_q, tax_q, p, near) if p is not None else None,
            "prev_label": ("{} FY{}".format(*[(a, str(b)[2:]) for a, b in
                                              [fiscal_quarter_label(p, fy_month)]][0])
                           if p is not None else None),
            "rev_prev": rev_p, "eps_prev": eps_p,
        })
        for name, series in (extra or {}).items():
            rows[-1][name] = near(series, d) if series else None
            rows[-1][name + "_prev"] = (near(series, p) if (series and p is not None) else None)
    return rows


def _derived_operating_income(pre, total, interest, other):
    """{period: operating income} = pre-tax income less non-operating items,
    for periods where the filer tags pre-tax income and at least one
    non-operating line but no operating income. Banks tag neither line as
    non-operating, so they are not given one."""
    out = {}
    for k, p in (pre or {}).items():
        if total and k in total:
            out[k] = p - total[k]
        elif (interest and k in interest) or (other and k in other):
            out[k] = p - (interest or {}).get(k, 0.0) - (other or {}).get(k, 0.0)
    return out


def _sec_ttm(facts, fy_end):
    """Latest trailing-twelve-month flows, latest balance sheet, and the last
    three non-overlapping twelve-month FCF windows. None when fewer than four
    consecutive quarters exist."""
    _cache = {}

    def _q(field):
        if field not in _cache:
            s = _sec_quarterly_flow(facts, _SEC_TAGS[field])
            if field == "operating_income_loss" and not s:
                s = _derived_operating_income(
                    _q("pretax_income"), _sec_quarterly_flow(facts, _SEC_TAGS["other_income"]),
                    _sec_quarterly_flow(facts, _SEC_TAGS["nonop_interest"]),
                    _sec_quarterly_flow(facts, _SEC_TAGS["nonop_other"]))
            _cache[field] = s
        return _cache[field]

    rev_q = _q("revenues") or _q("net_income_loss")
    all_ends = list(rev_q)
    ends = all_ends[-4:]
    if len(ends) < 4 or any(not (80 <= (b - a).days <= 100) for a, b in zip(ends, ends[1:])):
        return None
    t_end = ends[-1]
    # The four quarters before, for growth that compares twelve months with
    # twelve months. Only when all eight are back to back.
    prior_ends = all_ends[-8:-4]
    if len(prior_ends) < 4 or any(not (80 <= (b - a).days <= 100)
                                  for a, b in zip(all_ends[-8:], all_ends[-7:])):
        prior_ends = None

    def _sum4(series, which=None):
        which = ends if which is None else which
        pts = [series.get(e) for e in which]
        if any(v is None for v in pts):
            # tolerate a few days' disagreement between concepts on a quarter end
            pts = []
            for e in which:
                near = [v for k, v in series.items() if abs((k - e).days) <= 7]
                if not near:
                    return None
                pts.append(near[0])
        return float(sum(pts))

    income = {f: _sum4(_q(f)) for f in _TTM_INCOME}
    if income.get("depreciation_amortization") is None:
        _dep, _amo = _sum4(_q("depreciation_only")), _sum4(_q("amortization_intangibles"))
        if _dep is not None:
            income["depreciation_amortization"] = _dep + (_amo or 0.0)
    # Diluted EPS summed over the quarters. Per-share figures are as filed, so a
    # split inside the eight quarters makes the two sums incomparable; the
    # caller checks EPS growth against net-income growth before trusting it.
    _eps_q = _sec_quarterly_flow(facts, _SEC_TAGS_EPS, unit="USD/shares")
    income["eps_diluted"] = _sum4(_eps_q)
    prior = None
    if prior_ends:
        prior = {"revenues": _sum4(rev_q, prior_ends),
                 "net_income_loss": _sum4(_q("net_income_loss"), prior_ends),
                 "eps_diluted": _sum4(_eps_q, prior_ends),
                 "pretax_income": _sum4(_q("pretax_income"), prior_ends),
                 "income_tax": _sum4(_q("income_tax"), prior_ends)}
    if income.get("gross_profit") is None and income.get("revenues") is not None \
            and income.get("cost_of_revenue") is not None:
        income["gross_profit"] = income["revenues"] - income["cost_of_revenue"]
    cfo_q, cx_q = _q("net_cash_flow_from_operating_activities"), _q("capex")
    cash = {"net_cash_flow_from_operating_activities": _sum4(cfo_q),
            "capex": _sum4(cx_q)}
    for f in _TTM_CASH[2:]:
        cash[f] = _sum4(_q(f))

    # FCF windows: consecutive quarters where both CFO and capex exist.
    fq = [(d, cfo_q[d] - cx_q[d]) for d in cfo_q if d in cx_q]
    fq.sort()
    windows = []
    k = len(fq)
    while k - 4 >= 0 and len(windows) < 3:
        blk = fq[k - 4:k]
        if all(80 <= (b[0] - a[0]).days <= 100 for a, b in zip(blk, blk[1:])):
            windows.insert(0, float(sum(v for _d, v in blk)))
            k -= 4
        else:
            break

    quarters = _sec_quarter_table(facts, rev_q, _q("gross_profit"), fy_end,
                                  _q("pretax_income"), _q("income_tax"),
                                  extra={"net_income": _q("net_income_loss"),
                                         "operating_income": _q("operating_income_loss"),
                                         "other_income": _q("other_income"),
                                         "equity_gains": _q("equity_gains"),
                                         "rnd": _q("research_and_development"),
                                         "da": (_q("depreciation_amortization")
                                                or _q("depreciation_only"))})
    _dps = _sec_quarterly_flow(facts, ["CommonStockDividendsPerShareDeclared",
                                       "CommonStockDividendsPerShareCashPaid"], unit="USD/shares")
    dps_latest = (list(_dps.items())[-1] if _dps else None)
    if dps_latest and dps_latest[0] < t_end - pd.Timedelta(days=200):
        dps_latest = None                      # a dividend that stopped is not a yield

    b_end, balance = _sec_latest_balance(facts)
    # Balance-sheet drivers now and a year earlier (backlog, deferred revenue,
    # inventory) for the Segments tab's operating-driver table.
    drivers_bal = {}
    if b_end is not None:
        for fld in ("rpo", "deferred_revenue", "inventory"):
            ser = _sec_instant_series(facts, fld)
            now = ser.get(b_end)
            ago = [v for k, v in ser.items() if 350 <= (b_end - k).days <= 380]
            if now is not None:
                drivers_bal[fld] = {"now": now, "ago": ago[-1] if ago else None}
    sh_end, shares = _sec_cover_shares(facts)
    # The 10-K cover count just after the fiscal year-end, for the workbook's
    # "shares then vs now" line (None for multi-class filers, whose cover
    # counts companyfacts omits).
    shares_fy = None
    try:
        _node = facts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding")
        _fy_ts = pd.Timestamp(fy_end)
        _rows = [e for e in ((_node or {}).get("units", {}).get("shares") or [])
                 if e.get("val") and e.get("end") and str(e.get("form", "")).startswith("10-K")
                 and 0 <= (pd.Timestamp(e["end"]) - _fy_ts).days <= 150]
        if _rows:
            _first = min(_rows, key=lambda e: e["end"])
            shares_fy = sum({float(e["val"]) for e in _rows
                             if e.get("accn") == _first.get("accn") and e["end"] == _first["end"]})
    except Exception:
        shares_fy = None
    # Berkshire's last companyfacts cover count is from 2011. A count that old
    # would price the company at a fraction of its value; drop it.
    if sh_end and (pd.Timestamp(sh_end) < t_end - pd.Timedelta(days=120)):
        sh_end, shares = None, None
    return {"flows_end": str(t_end.date()), "fy_end": str(fy_end)[:10],
            "income": income, "cash_flow": cash, "fcf_windows": windows,
            "prior": prior,
            "balance_end": str(b_end.date()) if b_end is not None else None,
            "balance": balance,
            "shares_outstanding": shares, "shares_date": sh_end, "shares_fy": shares_fy,
            "quarters": quarters, "drivers_bal": drivers_bal,
            "dps_quarter": (float(dps_latest[1]) if dps_latest else None),
            "dps_quarter_end": (str(dps_latest[0].date()) if dps_latest else None),
            "flows_q": {"cfo": {str(k.date()): v for k, v in cfo_q.items()},
                        "capex": {str(k.date()): v for k, v in cx_q.items()}}}


def fetch_sec_financials(ticker, years=10, log=print):
    """
    Up to `years` of annual statements from SEC EDGAR's companyfacts API.

    Returns {income_statement, balance_sheet, cash_flow_statement} as newest-first
    DataFrames whose columns match the Polygon shape (so compute_fundamentals works
    unchanged) plus extras (capex, retained_earnings, debt_current, cash, diluted_shares,
    depreciation_amortization) used for FCF / Piotroski F-Score / Altman Z-Score.
    Returns {} when unavailable (ETF, crypto, or a foreign filer without us-gaap XBRL).
    """
    cik = _sec_cik_for(ticker, log=log)
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

    by_end = {}

    def _ends(field):
        """{period end: value} from every 10-K fact for the field, comparatives
        included. NVIDIA filed capex under a custom tag until FY2022; that
        10-K restates FY2020-21 under the standard one, and reading only each
        filing's own year dropped them."""
        if field not in by_end:
            gaap = facts.get("facts", {}).get("us-gaap", {})
            out = {}
            for tag in _SEC_TAGS.get(field, []):
                for e in (gaap.get(tag, {}).get("units", {}).get("USD") or []):
                    if not str(e.get("form", "")).startswith(("10-K", "20-F")) or e.get("val") is None:
                        continue
                    s, d = e.get("start"), e.get("end")
                    if s and not (350 <= (pd.Timestamp(d) - pd.Timestamp(s)).days <= 380):
                        continue
                    out.setdefault(d, float(e["val"]))
            by_end[field] = out
        return by_end[field]

    def col(field, fy):
        m = fmap.get(field, {})
        if fy in m:
            return m[fy][1]
        return _ends(field).get(end_of(fy))

    def end_of(fy):
        for f in ("net_income_loss", "revenues", "assets"):
            if fy in fmap.get(f, {}):
                return fmap[f][fy][0]
        return str(fy)

    def _da(fy):
        v = col("depreciation_amortization", fy)
        if v is None:
            # Depreciation plus amortization where the filer tags it; Alphabet
            # files amortization only in some quarters, and it is 2% of the total.
            d, a = col("depreciation_only", fy), col("amortization_intangibles", fy)
            v = (d + (a or 0.0)) if d is not None else None
        return v

    def _oi(fy):
        v = col("operating_income_loss", fy)
        if v is not None:
            return v
        pre = col("pretax_income", fy)
        if pre is None:
            return None
        tot = col("other_income", fy)
        if tot is not None:
            return pre - tot
        i, o = col("nonop_interest", fy), col("nonop_other", fy)
        return (pre - (i or 0.0) - (o or 0.0)) if (i is not None or o is not None) else None

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
            "gross_profit": gp, "operating_income_loss": _oi(fy),
            "net_income_loss": col("net_income_loss", fy),
            "research_and_development": col("research_and_development", fy),
            "depreciation_amortization": _da(fy),
            "pretax_income": col("pretax_income", fy),
            "income_tax": col("income_tax", fy),
            "diluted_earnings_per_share": eps_map.get(fy, (None, None))[1],
            "diluted_shares": shares_map.get(fy, (None, None))[1],
        })
        bal_rows.append({
            "Period": period,
            "assets": col("assets", fy), "current_assets": col("current_assets", fy),
            "liabilities": col("liabilities", fy),
            "current_liabilities": col("current_liabilities", fy),
            "equity": col("equity", fy), "long_term_debt": col("long_term_debt", fy),
            "debt_current": col("debt_current", fy),
            "debt_total_incl_current": col("debt_total_incl_current", fy),
            "debt_current_total": col("debt_current_total", fy),
            "commercial_paper": col("commercial_paper", fy),
            "short_term_borrowings": col("short_term_borrowings", fy),
            "cash": col("cash", fy),
            "short_term_investments": col("short_term_investments", fy),
            "lt_securities": col("lt_securities", fy),
            "retained_earnings": col("retained_earnings", fy),
            "preferred": col("preferred", fy),
            "nonmkt_securities": col("nonmkt_securities", fy),
            "equity_method": col("equity_method", fy),
        })
        cf_rows.append({
            "Period": period,
            "net_cash_flow_from_operating_activities": col("net_cash_flow_from_operating_activities", fy),
            "net_cash_flow_from_investing_activities": col("net_cash_flow_from_investing_activities", fy),
            "net_cash_flow_from_financing_activities": col("net_cash_flow_from_financing_activities", fy),
            "capex": col("capex", fy),
            "sbc": col("sbc", fy),
            "buybacks": col("buybacks", fy),
            "dividends_paid": col("dividends_paid", fy),
        })

    log(f"   EDGAR: {len(fys)} fiscal years for {ticker} ({fys[-1]}–{fys[0]})")
    try:
        ttm = _sec_ttm(facts, end_of(fys[0]))
    except Exception as e:
        log(f"   TTM not built for {ticker}: {type(e).__name__}")
        ttm = None
    if ttm is not None and not ttm.get("shares_outstanding"):
        # A multi-class filer's cover count is dimensioned, so companyfacts has
        # none; read it from the latest filing itself (one extra request, only
        # for these companies).
        try:
            lx = fetch_sec_latest_xbrl(ticker, log=log)
            if lx.get("cover_shares"):
                ttm["shares_outstanding"] = lx["cover_shares"]
                ttm["shares_date"] = lx["cover_date"]
                ttm["shares_source"] = "filing instance (all classes)"
        except Exception as e:
            log(f"   cover shares not read for {ticker}: {type(e).__name__}")
    return {
        "income_statement":    pd.DataFrame(inc_rows),
        "balance_sheet":       pd.DataFrame(bal_rows),
        "cash_flow_statement": pd.DataFrame(cf_rows),
        "source": "SEC EDGAR",
        "ttm": ttm,
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

        ins        = next((i for i in insights if i.get("ticker", "").upper() == tkr), None)
        sentiment  = (ins.get("sentiment") if ins else None) or ""
        n_tickers  = len(tickers)

        # Relevance is title-primary: an article is "about" the company when the
        # ticker or a company-name token is in the TITLE (not just the body — a
        # passing mention in the description is why round-ups leaked through
        # before). A Polygon-analysed article that spans very few tickers also
        # counts as focused. Everything else (multi-name round-ups, comparisons,
        # tangential mentions) is Low and dropped.
        title_l   = title.lower()
        title_hit = tkr.lower() in title_l or any(tok in title_l for tok in name_tokens)
        focused   = ins is not None and n_tickers <= 2
        if title_hit or focused:
            relevance = "High"
        elif ins is not None and n_tickers <= 4:
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

    # Prefer title-relevant (High) articles; only top up with Medium when there
    # are too few Highs to fill the section, and fall back to the raw list if
    # nothing scored (so the section never disappears). Most-relevant first,
    # recency preserved within a tier.
    high = [n for n in scored if n["Relevance"] == "High"]
    med  = [n for n in scored if n["Relevance"] == "Medium"]
    kept = high if len(high) >= 3 else (high + med)
    kept = kept or scored
    rank = {"High": 0, "Medium": 1, "Low": 2}
    kept.sort(key=lambda n: rank[n["Relevance"]])
    log(f"   {len(kept)}/{len(scored)} relevant news items")
    return kept[:12]


def fetch_peer_comparison(ticker, peer_tickers, api_key, log=print):
    """Name, exchange, market cap, headcount and country for the subject and
    up to four peers.

    This was five sequential Polygon reference calls, and it was the whole cost
    of an Excel export: by the time a reader clicks Export the page has spent
    the free tier's five calls a minute, so every call here sat in 12s, 24s and
    36s back-offs - 43s of a 57s export, measured. Yahoo is asked for all five
    at once instead; Polygon gets one attempt, with no back-off, for any Yahoo
    misses. A row is never dropped: a peer with no profile still appears, so
    the table and the peer fundamentals beside it list the same companies."""
    if not peer_tickers:
        return None
    from concurrent.futures import ThreadPoolExecutor
    all_tickers = [ticker] + list(peer_tickers)[:4]
    log(f"Fetching peer comparison: {all_tickers}...")

    def _one(t):
        d = _company_details_yf(t, log=lambda *a, **k: None)
        if not d:
            data = _get(f"/v3/reference/tickers/{t}", api_key, max_attempts=1)
            r = (data or {}).get("results") or {}
            if r:
                d = {"Name": r.get("name"), "Exchange": r.get("primary_exchange"),
                     "Market Cap": r.get("market_cap"), "Employees": r.get("total_employees"),
                     "Country": (r.get("locale") or "").upper() or None}
        return t, d or {}

    with ThreadPoolExecutor(max_workers=5) as ex:
        got = dict(ex.map(_one, all_tickers))
    try:
        from analysis import exchange_name as _xn
    except Exception:
        _xn = None
    rows = []
    for t in all_tickers:
        d = got.get(t) or {}
        mc = d.get("Market Cap")
        ex_code = d.get("Exchange")
        rows.append({
            "Ticker":          t,
            "Company":         (d.get("Name") if d.get("Name") not in (None, "N/A") else t),
            "Exchange":        ((_xn(ex_code) if (_xn and ex_code not in (None, "N/A")) else None)
                                or ex_code or "N/A"),
            "Market Cap ($B)": round(mc / 1e9, 2) if isinstance(mc, (int, float)) and mc else "N/A",
            "Employees":       d.get("Employees") if d.get("Employees") not in (None, "N/A") else "N/A",
            "Country":         d.get("Country") or "N/A",
        })
        log(f"   {t} {'OK' if d else 'no profile'}")
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
                # Forward the date overrides. Without them a custom range fetched
                # the bond over that window but its benchmark over the default
                # `period`, so the merge aligned two different windows and the
                # rolling beta was computed against a mismatched series.
                bdf = fetch_ohlcv(bench, period, api_key, log=lambda m: None,
                                  start_override=start_override,
                                  end_override=end_override, bar_size=bar_size)
                bdf[f"{bench}_Return"]     = bdf["Close"].pct_change()
                bdf[f"{bench}_Cumulative"] = (1 + bdf[f"{bench}_Return"].fillna(0)).cumprod() * 100
                # Join on the calendar date, not the timestamp: the stock and the
                # benchmark can come from different sources, and one stamps a
                # session at midnight while the other used 04:00 UTC. An exact
                # join left every benchmark cell empty.
                if bar_size in ("day", "week", "month"):
                    df["Date"]  = pd.to_datetime(df["Date"]).dt.normalize()
                    bdf["Date"] = pd.to_datetime(bdf["Date"]).dt.normalize()
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

    _attach_risk_ratios(df)

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


def fetch_street_view(ticker, log=print):
    """Next earnings date and revenue consensus from Yahoo Finance, for the
    report's catalyst calendar and its 'Street vs model' block.

    {"earnings_date": Timestamp | None, "fy0": $, "fy1": $, "q0": $, "q1": $,
     "analysts": int, "as_of": date, "source": str} - fields None when absent;
    {} when Yahoo returns nothing. Unofficial, and labelled so wherever shown."""
    out = {}
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        try:
            cal = tk.calendar or {}
            ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if isinstance(ed, (list, tuple)) and ed:
                ed = ed[0]
            out["earnings_date"] = pd.Timestamp(ed) if ed is not None else None
        except Exception:
            out["earnings_date"] = None
        try:
            est = tk.revenue_estimate
            if est is not None and len(est):
                def _g(period):
                    try:
                        v = est.loc[period, "avg"]
                        return float(v) if v == v else None
                    except Exception:
                        return None
                out.update({"fy0": _g("0y"), "fy1": _g("+1y"), "q0": _g("0q"), "q1": _g("+1q")})
                try:
                    out["analysts"] = int(est.loc["0y", "numberOfAnalysts"])
                except Exception:
                    out["analysts"] = None
        except Exception:
            pass
        out["as_of"] = pd.Timestamp.today().normalize()
        out["source"] = "Yahoo Finance analyst estimates (unofficial)"
    except Exception as e:
        log(f"   street view unavailable for {ticker}: {type(e).__name__}")
        return {}
    return out


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

    # Same excess-return Sharpe/Sortino as the stock and bond paths. Crypto was
    # once the only raw ann_ret/vol one; sharing the helper is what stops that
    # from happening again.
    _attach_risk_ratios(df)

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


# ── Revenue by segment, product and geography (latest 10-K) ───────────────────
# companyfacts carries only undimensioned facts, so every breakdown a 10-K
# reports - iPhone against Services, the Americas against Greater China - is
# absent from it. The breakdowns live in the filing's XBRL instance, keyed by
# dimension. This reads the latest 10-K's instance and label file, keeps revenue
# facts on a single breakdown axis, drops subtotal members, and accepts an axis
# only when its members add back to reported revenue: a breakdown that doesn't
# reconcile is left out rather than shown wrong.
_SEG_AXES = {
    "ProductOrServiceAxis":          "Products & services",
    "StatementBusinessSegmentsAxis": "Reportable segments",
    "StatementGeographicalAxis":     "Geography",
}
# ASU 2023-07 filings tag segment facts with this second dimension as well.
_SEG_OK_EXTRA = {("ConsolidationItemsAxis", "OperatingSegmentsMember")}
_NS = {"xbrli": "http://www.xbrl.org/2003/instance",
       "xbrldi": "http://xbrl.org/2006/xbrldi",
       "link": "http://www.xbrl.org/2003/linkbase",
       "xlink": "http://www.w3.org/1999/xlink"}


def _local(qname):
    return qname.split(":")[-1].split("}")[-1]


def _humanize_member(qname):
    """us-gaap:ServiceMember -> 'Service' when the filing gives no label."""
    import re as _re
    loc = _local(qname)
    loc = loc[:-6] if loc.endswith("Member") else loc
    words = _re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", loc).strip().split()
    small = {"And": "and", "Of": "of", "The": "the", "For": "for", "In": "in"}
    words = [small.get(w, w) if i else w for i, w in enumerate(words)]
    out = " ".join("US" if w in ("Us", "U S") else w for w in words)
    return {"Non US": "Outside the US"}.get(out, out)


def _tidy_label(text):
    """A label filed in capitals ('UNITED STATES') reads as shouting in a table."""
    return text.title() if (text.isupper() and len(text) > 3) else text


def _seg_labels(lab_bytes):
    """{'aapl_IPhoneMember': 'iPhone'} from a label linkbase, terse label first."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(lab_bytes)
    X = "{%s}" % _NS["xlink"]
    out, pri = {}, {}
    for link in root.iter("{%s}labelLink" % _NS["link"]):
        loc2id = {l.get(X + "label"): l.get(X + "href", "").split("#")[-1]
                  for l in link.iter("{%s}loc" % _NS["link"])}
        lab2text = {}
        for lab in link.iter("{%s}label" % _NS["link"]):
            role = lab.get(X + "role", "").rsplit("/", 1)[-1]
            lab2text.setdefault(lab.get(X + "label"), []).append((role, (lab.text or "").strip()))
        for arc in link.iter("{%s}labelArc" % _NS["link"]):
            cid = loc2id.get(arc.get(X + "from"))
            for role, text in lab2text.get(arc.get(X + "to"), []):
                rank = {"terseLabel": 0, "label": 1}.get(role, 9)
                if cid and text and rank < pri.get(cid, 99):
                    out[cid], pri[cid] = text, rank
    return out


_LATEST_XBRL = {}
_SEG_OI_TAGS = ["OperatingIncomeLoss", "SegmentReportingInformationOperatingIncomeLoss"]


def fetch_sec_latest_xbrl(ticker, log=print):
    """What only the latest 10-Q / 10-K's own XBRL instance carries.

    companyfacts drops every dimensioned fact, which removes two things the
    report needs: the cover-page share count of a multi-class filer (Alphabet
    files Class A, B and C separately and companyfacts shows none of them) and
    each business segment's revenue and operating income. Returns
      {"form", "period", "filed", "url",
       "cover_shares", "cover_date",            # all classes summed, or None
       "segments": {"basis": "quarter"|"year", "cur_end", "prev_end",
                    "rows": [{"name", "rev", "rev_prev", "oi", "oi_prev"}],
                    "total_rev", "total_rev_prev", "total_oi", "total_oi_prev"} | None}
    or {} when the instance can't be read. Cached per filing."""
    import xml.etree.ElementTree as ET
    try:
        periodic = [f for f in fetch_sec_filings(ticker, log=log)
                    if f.get("form") in ("10-Q", "10-K")]
    except Exception:
        periodic = []
    if not periodic:
        return {}
    f = periodic[0]
    key = (ticker.upper(), f.get("url"))
    if key in _LATEST_XBRL:
        return _LATEST_XBRL[key]
    base, doc = f["url"].rsplit("/", 1)
    stem = doc.rsplit(".", 1)[0]
    try:
        inst = requests.get(f"{base}/{stem}_htm.xml", headers=SEC_HEADERS, timeout=30)
        if inst.status_code != 200:
            return {}
        root = ET.fromstring(inst.content)
        lab = requests.get(f"{base}/{stem}_lab.xml", headers=SEC_HEADERS, timeout=30)
        labels = _seg_labels(lab.content) if lab.status_code == 200 else {}
    except Exception as e:
        log(f"   latest XBRL: {type(e).__name__} for {ticker}")
        return {}

    ctx = {}
    for c in root.iter("{%s}context" % _NS["xbrli"]):
        p = c.find("xbrli:period", _NS)
        s, e, i = (p.find("xbrli:startDate", _NS), p.find("xbrli:endDate", _NS),
                   p.find("xbrli:instant", _NS))
        dims = {_local(m.get("dimension")): (m.text or "").strip()
                for m in c.iter("{%s}explicitMember" % _NS["xbrldi"])}
        ctx[c.get("id")] = ((s.text if s is not None else None),
                            (e.text if e is not None else (i.text if i is not None else None)),
                            dims)

    # Cover shares: every class's count on the latest cover date, summed. Classes
    # of very different economic weight (Berkshire's A is worth 1,500 B) cannot
    # be added, so a class under 1% of the largest one voids the sum.
    cover = {}
    for el in root:
        if el.tag.split("}")[-1] != "EntityCommonStockSharesOutstanding":
            continue
        cx = ctx.get(el.get("contextRef"))
        try:
            v = float(el.text)
        except (TypeError, ValueError):
            continue
        if cx and cx[1]:
            cover.setdefault(cx[1], {})[el.get("contextRef")] = v
    cover_date, cover_shares = None, None
    if cover:
        cover_date = max(cover)
        vals = list(cover[cover_date].values())
        if vals and min(vals) >= 0.01 * max(vals):
            cover_shares = sum(vals)

    # Segment results on the business-segment axis.
    rev_tags, oi_tags = set(_SEC_TAGS["revenues"]), set(_SEG_OI_TAGS)
    facts = {}          # (kind, start, end, member or None) -> value
    for el in root:
        tag = el.tag.split("}")[-1]
        kind = "rev" if tag in rev_tags else "oi" if tag in oi_tags else None
        if kind is None or el.get("contextRef") not in ctx:
            continue
        try:
            val = float(el.text)
        except (TypeError, ValueError):
            continue
        s, e, dims = ctx[el.get("contextRef")]
        if not s or not e:
            continue
        seg = dims.get("StatementBusinessSegmentsAxis")
        extra = {(a, _local(m)) for a, m in dims.items() if a != "StatementBusinessSegmentsAxis"}
        if not extra <= _SEG_OK_EXTRA:
            continue
        k = (kind, s, e, seg)
        # revenue tags in priority order: keep the first seen for a key
        facts.setdefault(k, val)

    def _days(s, e):
        return (pd.Timestamp(e) - pd.Timestamp(s)).days

    segments = None
    period_end = f.get("period")
    for basis, lo, hi in (("quarter", 80, 100), ("year", 350, 380)):
        if f.get("form") == "10-K" and basis == "quarter":
            continue
        cur = {k: v for k, v in facts.items()
               if k[3] and lo <= _days(k[1], k[2]) <= hi and k[2] == period_end}
        if not any(k[0] == "rev" for k in cur):
            continue
        cur_end = pd.Timestamp(period_end)
        prev_keys = {k: v for k, v in facts.items()
                     if lo <= _days(k[1], k[2]) <= hi
                     and 350 <= (cur_end - pd.Timestamp(k[2])).days <= 380}
        members = sorted({k[3] for k in cur if k[0] == "rev"})

        def _pick(src, kind, m):
            for k, v in src.items():
                if k[0] == kind and k[3] == m:
                    return v
            return None

        def _total(src, kind):
            for k, v in src.items():
                if k[0] == kind and k[3] is None:
                    return v
            return None
        tot_now = {k: v for k, v in facts.items()
                   if lo <= _days(k[1], k[2]) <= hi and k[2] == period_end and k[3] is None}
        rows = []
        for m in members:
            rows.append({"name": _tidy_label(labels.get(m.replace(":", "_")) or _humanize_member(m)),
                         "member": m,
                         "rev": _pick(cur, "rev", m), "rev_prev": _pick(prev_keys, "rev", m),
                         "oi": _pick(cur, "oi", m), "oi_prev": _pick(prev_keys, "oi", m)})
        total_rev = _total(tot_now, "rev")
        if total_rev:
            revs = {r["member"]: r["rev"] for r in rows if r["rev"]}
            keep = _drop_subtotals(revs, total_rev)
            rows = [r for r in rows if r["member"] in keep]
        if len(rows) < 1:
            continue
        rows.sort(key=lambda r: -(r["rev"] or 0))
        segments = {"basis": basis, "cur_end": period_end,
                    "prev_end": max((k[2] for k in prev_keys), default=None),
                    "rows": rows, "total_rev": total_rev,
                    "total_rev_prev": _total(prev_keys, "rev"),
                    "total_oi": _total(tot_now, "oi"),
                    "total_oi_prev": _total(prev_keys, "oi")}
        break

    out = {"form": f.get("form"), "period": period_end, "filed": f.get("filed"),
           "url": f.get("url"), "cover_shares": cover_shares, "cover_date": cover_date,
           "segments": segments}
    _LATEST_XBRL[key] = out
    return out


def _drop_subtotals(members, total):
    """Members that are the sum of other members, or the total itself, go."""
    from itertools import combinations
    names = [m for m in members if members[m] > 0]
    out = dict(members)
    for m in names:
        v = members[m]
        if total and abs(v - total) <= 0.005 * total:
            out.pop(m, None)
            continue
        others = [o for o in names if o != m and members[o] < v]
        found = False
        for k in range(2, min(len(others), 8) + 1):
            for combo in combinations(others, k):
                if abs(sum(members[o] for o in combo) - v) <= 0.002 * v:
                    found = True
                    break
            if found:
                break
        if found:
            out.pop(m, None)
    return out


def fetch_sec_segments(ticker, log=print):
    """Revenue breakdowns from the latest 10-K, or {} when none reconciles.

    {"fy_end", "filed", "url", "axes": {title: [{"name", "value", "prior",
    "share", "growth"}, ...]}} - each axis's members sum to reported revenue
    within 2%."""
    import xml.etree.ElementTree as ET
    try:
        tenks = [f for f in fetch_sec_filings(ticker, log=log) if f["form"] == "10-K"]
    except Exception:
        tenks = []
    if not tenks:
        return {}
    f = tenks[0]
    base, doc = f["url"].rsplit("/", 1)
    stem = doc.rsplit(".", 1)[0]
    try:
        inst = requests.get(f"{base}/{stem}_htm.xml", headers=SEC_HEADERS, timeout=30)
        if inst.status_code != 200:
            return {}
        lab = requests.get(f"{base}/{stem}_lab.xml", headers=SEC_HEADERS, timeout=30)
        labels = _seg_labels(lab.content) if lab.status_code == 200 else {}
        root = ET.fromstring(inst.content)
    except Exception as e:
        log(f"   segments: {type(e).__name__} for {ticker}")
        return {}

    # contexts: id -> (start, end, {axis_local: member_qname})
    ctx = {}
    for c in root.iter("{%s}context" % _NS["xbrli"]):
        p = c.find("xbrli:period", _NS)
        s, e = p.find("xbrli:startDate", _NS), p.find("xbrli:endDate", _NS)
        if s is None or e is None:
            continue
        dims = {_local(m.get("dimension")): (m.text or "").strip()
                for m in c.iter("{%s}explicitMember" % _NS["xbrldi"])}
        ctx[c.get("id")] = (s.text, e.text, dims)

    rev_tags = _SEC_TAGS["revenues"]
    # tag -> {(start, end, axis or None, member or None): value}
    by_tag = {}
    for el in root:
        tag = el.tag.split("}")[-1]
        if tag not in rev_tags or el.get("contextRef") not in ctx:
            continue
        try:
            val = float(el.text)
        except (TypeError, ValueError):
            continue
        s, e, dims = ctx[el.get("contextRef")]
        axis_dims = {a: m for a, m in dims.items() if a in _SEG_AXES}
        extra = {(a, _local(m)) for a, m in dims.items() if a not in _SEG_AXES}
        if len(axis_dims) > 1 or not extra <= _SEG_OK_EXTRA:
            continue
        key = ((s, e) + next(iter(axis_dims.items()))) if axis_dims else (s, e, None, None)
        by_tag.setdefault(tag, {})[key] = val

    fy_end = f.get("period")
    axes = {}
    for tag in rev_tags:                         # priority order, first that works
        facts = by_tag.get(tag)
        if not facts:
            continue
        def _annual(k):
            try:
                return 350 <= (pd.Timestamp(k[1]) - pd.Timestamp(k[0])).days <= 380
            except Exception:
                return False
        totals = {k[1]: v for k, v in facts.items() if k[2] is None and _annual(k)}
        if fy_end not in totals:
            continue
        prior_end = max((d for d in totals if d < fy_end), default=None)
        for axis, title in _SEG_AXES.items():
            if title in axes:
                continue
            cur = {k[3]: v for k, v in facts.items() if k[2] == axis and k[1] == fy_end and _annual(k)}
            if len(cur) < 2:
                continue
            cur = _drop_subtotals(cur, totals[fy_end])
            if len(cur) < 2 or abs(sum(cur.values()) - totals[fy_end]) > 0.02 * totals[fy_end]:
                continue
            prv = {k[3]: v for k, v in facts.items()
                   if k[2] == axis and k[1] == prior_end and _annual(k)} if prior_end else {}
            rows = []
            for m, v in sorted(cur.items(), key=lambda kv: -kv[1]):
                pv = prv.get(m)
                rows.append({
                    "name": _tidy_label(labels.get(m.replace(":", "_")) or _humanize_member(m)),
                    "value": v, "prior": pv,
                    "share": v / totals[fy_end],
                    "growth": (v / pv - 1) if (pv and pv > 0) else None,
                })
            axes[title] = rows
        if axes:
            break
    if not axes:
        return {}
    return {"fy_end": fy_end, "filed": f.get("filed"), "url": f.get("url"), "axes": axes}
