import numpy as np
import pandas as pd


def detect_support_resistance(df, window=20, num_levels=5):
    highs = df["High"].values
    lows  = df["Low"].values
    n     = len(highs)
    resistance, support = [], []
    for i in range(window, n - window):
        if highs[i] == max(highs[i - window: i + window + 1]):
            resistance.append(round(float(highs[i]), 2))
        if lows[i] == min(lows[i - window: i + window + 1]):
            support.append(round(float(lows[i]), 2))

    def cluster(levels, tol=0.01):
        levels = sorted(set(levels), reverse=True)
        clustered = []
        for lv in levels:
            if not clustered or abs(lv - clustered[-1]) / max(clustered[-1], 1) > tol:
                clustered.append(lv)
        return clustered[:num_levels]

    return cluster(resistance), cluster(support)


def build_correlation_matrix(df, benchmark_tickers=None):
    cols = {"Stock": df["Daily_Return"]}
    if benchmark_tickers:
        for b in benchmark_tickers:
            col = f"{b}_Return"
            if col in df.columns:
                cols[b.replace("^", "").replace("I:", "")] = df[col]
    return pd.DataFrame(cols).dropna().corr()


def run_monte_carlo(df, n_simulations=1000, forecast_days=252, log=print):
    log(f"Monte Carlo: {n_simulations:,} paths x {forecast_days} trading days...")
    returns    = df["Daily_Return"].dropna()
    mu, sigma  = returns.mean(), returns.std()
    last_price = df["Close"].iloc[-1]
    np.random.seed(42)
    rand          = np.random.standard_normal((forecast_days, n_simulations))
    daily_factors = np.exp((mu - 0.5 * sigma ** 2) + sigma * rand)
    paths         = np.zeros((forecast_days + 1, n_simulations))
    paths[0]      = last_price
    for t in range(1, forecast_days + 1):
        paths[t] = paths[t - 1] * daily_factors[t - 1]
    fp   = paths[-1]
    pcts = np.percentile(fp, [5, 25, 50, 75, 95])
    summary = {
        "Last Price":              round(last_price, 2),
        "Forecast Horizon (days)": forecast_days,
        "Simulations":             n_simulations,
        "Mean Forecast":           round(fp.mean(), 2),
        "Median (P50)":            round(pcts[2], 2),
        "Bear Case (P5)":          round(pcts[0], 2),
        "Low Case (P25)":          round(pcts[1], 2),
        "Bull Case (P75)":         round(pcts[3], 2),
        "Best Case (P95)":         round(pcts[4], 2),
        "Prob. of Gain":           f"{(fp > last_price).mean()*100:.1f}%",
        "Ann. Volatility":         f"{sigma * np.sqrt(252) * 100:.2f}%",
    }
    log(f"   P5 ${summary['Bear Case (P5)']:,.2f}  "
        f"P50 ${summary['Median (P50)']:,.2f}  "
        f"P95 ${summary['Best Case (P95)']:,.2f}")
    return pd.DataFrame(paths), summary


def generate_summary_paragraph(ticker, df, company_details, mc_summary, sharpe, sortino):
    latest       = df.iloc[-1]
    first        = df.iloc[0]
    period_ret   = (latest["Close"] / first["Close"] - 1) * 100
    vol_20d      = latest.get("Volatility_20d", np.nan)
    drawdown_60d = df["Drawdown_60d"].min() * 100

    try:
        rsi = float(latest.get("RSI14", np.nan))
    except Exception:
        rsi = np.nan

    ma50_sig = ""
    if "MA50" in df.columns and pd.notna(latest.get("MA50")):
        ma50_sig = ("above its 50-day moving average — bullish"
                    if latest["Close"] > latest["MA50"]
                    else "below its 50-day moving average — cautionary")

    rsi_str = ""
    if pd.notna(rsi):
        if   rsi > 70: rsi_str = f"RSI {rsi:.0f} — overbought territory"
        elif rsi < 30: rsi_str = f"RSI {rsi:.0f} — oversold territory"
        else:          rsi_str = f"RSI {rsi:.0f} — neutral territory"

    w52h = latest.get("52W_High", np.nan)
    w52l = latest.get("52W_Low", np.nan)
    w52_str = ""
    if pd.notna(w52h) and pd.notna(w52l):
        pct_from_high = (latest["Close"] / w52h - 1) * 100
        w52_str = (f"The stock sits {abs(pct_from_high):.1f}% "
                   f"{'below' if pct_from_high < 0 else 'above'} its 52-week high "
                   f"of ${w52h:,.2f} (52-week low: ${w52l:,.2f}).")

    vol_str    = f"20-day annualised volatility: {vol_20d*100:.1f}%." if pd.notna(vol_20d) else ""
    sharpe_str = ""
    if sharpe and pd.notna(sharpe):
        q = "strong" if sharpe > 1 else ("modest" if sharpe > 0.5 else "weak")
        sharpe_str = (f"Sharpe ratio {sharpe:.2f} ({q} risk-adjusted return); "
                      f"Sortino {sortino:.2f} "
                      f"({'well-managed' if sortino > 1 else 'elevated'} downside risk).")

    mc_str = ""
    if mc_summary:
        mc_str = (f"Monte Carlo simulation ({mc_summary['Simulations']:,} paths, "
                  f"{mc_summary['Forecast Horizon (days)']} days): median "
                  f"${mc_summary['Median (P50)']:,.2f} "
                  f"(bear ${mc_summary['Bear Case (P5)']:,.2f} / "
                  f"bull ${mc_summary['Best Case (P95)']:,.2f}), "
                  f"{mc_summary['Prob. of Gain']} probability of gain.")

    company_str = ""
    if company_details:
        company_str = (f"{ticker} ({company_details.get('Name', ticker)}) "
                       f"trades on {company_details.get('Exchange', 'N/A')}.")

    lines = [
        f"{ticker} delivered a cumulative return of {period_ret:+.1f}% over the selected period, "
        f"most recently closing at ${latest['Close']:,.2f}.",
    ]
    for s in [
        ma50_sig and f"Price is currently {ma50_sig}.",
        rsi_str, w52_str,
        vol_str and f"{vol_str} Peak 60-day drawdown: {drawdown_60d:.1f}%.",
        sharpe_str, company_str, mc_str,
        "This report is generated programmatically and does not constitute investment advice."
    ]:
        if s:
            lines.append(s)

    return "  ".join(lines)
