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


# ── Custom Forecast (GARCH + ML + Monte Carlo) ────────────────────────────────


def _fit_garch_volatility(returns, forecast_days):
    """
    Fit GARCH(1,1) on daily returns and produce multi-step ahead volatility forecasts.
    Returns an array of daily sigma values (length = forecast_days).
    """
    from arch import arch_model
    r_pct = returns * 100  # scale for numerical stability
    model = arch_model(r_pct, vol="Garch", p=1, q=1, dist="normal", rescale=False)
    res   = model.fit(disp="off", show_warning=False)

    omega = float(res.params["omega"])
    alpha = float(res.params["alpha[1]"])
    beta  = float(res.params["beta[1]"])

    # Last conditional variance (pct²)
    h0 = float(res.conditional_volatility.iloc[-1]) ** 2

    # If alpha+beta >= 1 (IGARCH), long-run variance is undefined.
    # Fall back to holding current variance constant.
    long_run_var = omega / (1 - alpha - beta) if (alpha + beta) < 1 else h0

    # Correct GARCH(1,1) multi-step ahead variance forecast
    vols = []
    for i in range(1, forecast_days + 1):
        h_i = long_run_var + (alpha + beta) ** i * (h0 - long_run_var)
        vols.append(np.sqrt(max(h_i, 1e-12)) / 100)  # convert back to decimal

    return np.array(vols)


def _engineer_features(df):
    """Build ML feature matrix from OHLCV dataframe."""
    ret   = df["Daily_Return"]
    close = df["Close"]
    f     = pd.DataFrame(index=df.index)

    for lag in [1, 2, 3, 5, 10]:
        f[f"ret_lag{lag}"] = ret.shift(lag)

    for w in [10, 20, 60]:
        f[f"vol_{w}d"] = ret.rolling(w).std()

    # RSI-14
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    f["rsi14"] = 100 - 100 / (1 + gain / (loss + 1e-8))

    # Price / moving-average ratios
    f["ma20_ratio"] = close / close.rolling(20).mean()
    f["ma50_ratio"] = close / close.rolling(50).mean()

    # Volume ratio vs 20-day average
    f["vol_ratio"] = df["Volume"] / (df["Volume"].rolling(20).mean() + 1e-8)

    # Target: next-day return
    f["target"] = ret.shift(-1)

    return f.dropna()


def _train_ml_drift(df, log=print):
    """
    Train a Random Forest + XGBoost ensemble on engineered features.
    Returns a predicted daily drift (float) for use in the Monte Carlo simulation.
    """
    try:
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.preprocessing import StandardScaler

        feat_df = _engineer_features(df)
        if len(feat_df) < 60:
            log("  ML: Not enough data — using historical mean drift.")
            return float(df["Daily_Return"].mean())

        X = feat_df.drop(columns=["target"]).values
        y = feat_df["target"].values

        split     = int(len(X) * 0.8)
        X_train   = X[:split]
        y_train   = y[:split]

        scaler       = StandardScaler()
        X_train_s    = scaler.fit_transform(X_train)
        X_last_s     = scaler.transform(X[-1:])

        rf = RandomForestRegressor(n_estimators=150, random_state=42, n_jobs=-1)
        rf.fit(X_train_s, y_train)
        pred_rf = float(rf.predict(X_last_s)[0])

        try:
            import xgboost as xgb
            gb = xgb.XGBRegressor(n_estimators=150, learning_rate=0.05,
                                   random_state=42, verbosity=0)
        except ImportError:
            from sklearn.ensemble import GradientBoostingRegressor
            gb = GradientBoostingRegressor(n_estimators=150, random_state=42)

        gb.fit(X_train_s, y_train)
        pred_gb = float(gb.predict(X_last_s)[0])

        drift = (pred_rf + pred_gb) / 2
        log(f"  ML drift — RF: {pred_rf*100:.3f}%  GB: {pred_gb*100:.3f}%  Ensemble: {drift*100:.3f}%")

        # Drift is capped within ±1 std of historical mean to prevent
        # regime extrapolation over the full forecast horizon.
        hist_mean = y_train.mean()
        hist_std  = y_train.std()
        ml_drift  = np.clip(drift, hist_mean - hist_std, hist_mean + hist_std)
        return ml_drift

    except Exception as exc:
        log(f"  ML training failed ({exc}) — using historical mean drift.")
        return float(df["Daily_Return"].mean())


def run_custom_forecast(df, n_simulations=1000, forecast_days=252, log=print):
    """
    Custom Forecast: GARCH(1,1) volatility + ML-ensemble drift + Monte Carlo paths.
    Uses the pre-fetched Polygon dataframe (same data as the rest of the analysis).

    Returns
    -------
    paths_df    : pd.DataFrame  shape (forecast_days+1, n_simulations)
    garch_vols  : np.ndarray    daily volatility curve (length = forecast_days)
    ml_drift    : float         ML-predicted daily drift
    summary     : dict          same key structure as run_monte_carlo summary
    """
    log("Custom Forecast: preparing data...")
    if "Daily_Return" not in df.columns:
        df = df.copy()
        df["Daily_Return"] = df["Close"].pct_change()
    df = df.dropna(subset=["Daily_Return"])

    returns    = df["Daily_Return"].dropna()
    last_price = float(df["Close"].iloc[-1])
    hist_sigma = float(returns.std())

    # ── 1. GARCH volatility forecast ─────────────────────────────────────────
    log("  GARCH(1,1): fitting model...")
    try:
        garch_vols = _fit_garch_volatility(returns, forecast_days)
    except Exception as exc:
        log(f"  GARCH failed ({exc}) — using constant historical volatility.")
        garch_vols = np.full(forecast_days, hist_sigma)

    # ── 2. ML drift ───────────────────────────────────────────────────────────
    log("  ML ensemble: training Random Forest / XGBoost...")
    ml_drift = _train_ml_drift(df, log=log)

    # ── 3. Monte Carlo with GARCH vol + ML drift ──────────────────────────────
    log(f"  Monte Carlo: {n_simulations:,} paths × {forecast_days} days (dynamic σ)...")
    rand  = np.random.standard_normal((forecast_days, n_simulations))
    paths = np.zeros((forecast_days + 1, n_simulations))
    paths[0] = last_price

    for t in range(1, forecast_days + 1):
        sigma_t = garch_vols[t - 1]
        mu_t    = ml_drift - 0.5 * sigma_t ** 2
        paths[t] = paths[t - 1] * np.exp(mu_t + sigma_t * rand[t - 1])

    fp   = paths[-1]
    pcts = np.percentile(fp, [5, 25, 50, 75, 95])

    summary = {
        "Last Price":              round(last_price, 2),
        "Forecast Horizon (days)": forecast_days,
        "Simulations":             n_simulations,
        "Mean Forecast":           round(float(fp.mean()), 2),
        "Median (P50)":            round(float(pcts[2]), 2),
        "Bear Case (P5)":          round(float(pcts[0]), 2),
        "Low Case (P25)":          round(float(pcts[1]), 2),
        "Bull Case (P75)":         round(float(pcts[3]), 2),
        "Best Case (P95)":         round(float(pcts[4]), 2),
        "Prob. of Gain":           f"{(fp > last_price).mean()*100:.1f}%",
        "Ann. Volatility (GARCH)": f"{garch_vols.mean() * np.sqrt(252) * 100:.2f}%",
        "ML Drift (daily)":        f"{ml_drift*100:.4f}%",
    }

    log(f"   P5 ${summary['Bear Case (P5)']:,.2f}  "
        f"P50 ${summary['Median (P50)']:,.2f}  "
        f"P95 ${summary['Best Case (P95)']:,.2f}")

    return pd.DataFrame(paths), garch_vols, ml_drift, summary


def generate_summary_paragraph(ticker, df, company_details, mc_summary, sharpe, sortino,
                               forecast_method="Monte Carlo"):
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
        mc_str = (f"{forecast_method} ({mc_summary['Simulations']:,} paths, "
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
