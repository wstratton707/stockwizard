import numpy as np
import pandas as pd
from scipy.optimize import minimize
from datetime import datetime, timedelta

# Annualised risk-free rate used in all Sharpe / Sortino calculations.
# Approximate 3-month US Treasury yield. Update periodically.
RISK_FREE_RATE = 0.045  # 4.5%


# ── Individual stock metrics ──────────────────────────────────────────────────

def compute_stock_metrics(returns_df):
    """
    Compute per-stock metrics from a returns DataFrame.
    Returns a dict: {ticker: {metrics}}
    """
    metrics = {}
    for ticker in returns_df.columns:
        r       = returns_df[ticker].dropna()
        ann_ret = r.mean() * 252
        ann_std = r.std() * np.sqrt(252)
        down    = r[r < 0].std() * np.sqrt(252)
        sharpe  = (ann_ret - RISK_FREE_RATE) / ann_std  if ann_std  else 0
        sortino = (ann_ret - RISK_FREE_RATE) / down     if down     else 0

        # Max drawdown
        cumret   = (1 + r).cumprod()
        peak     = cumret.cummax()
        drawdown = (cumret - peak) / peak
        max_dd   = drawdown.min()

        metrics[ticker] = {
            "ann_return":   round(ann_ret * 100, 2),
            "ann_vol":      round(ann_std * 100, 2),
            "sharpe":       round(sharpe, 3),
            "sortino":      round(sortino, 3),
            "max_drawdown": round(max_dd * 100, 2),
            "total_return": round(((1 + r).prod() - 1) * 100, 2),
        }
    return metrics


def compute_correlation_matrix(returns_df):
    return returns_df.corr()


# ── Portfolio optimisation (Mean-Variance) ────────────────────────────────────

def portfolio_metrics(weights, returns_df):
    weights      = np.array(weights)
    port_ret     = returns_df.mean().values @ weights * 252
    port_vol     = np.sqrt(weights @ (returns_df.cov().values * 252) @ weights)
    sharpe       = (port_ret - RISK_FREE_RATE) / port_vol if port_vol > 0 else 0
    return port_ret, port_vol, sharpe


def _neg_sharpe(weights, returns_df):
    _, _, sharpe = portfolio_metrics(weights, returns_df)
    return -sharpe


def _portfolio_vol(weights, returns_df):
    _, vol, _ = portfolio_metrics(weights, returns_df)
    return vol


def optimise_portfolio(returns_df, risk_tolerance=5, target_return=None):
    """
    Run mean-variance optimisation.
    Returns weights dict for max Sharpe, min vol, and target return portfolios.
    """
    n      = len(returns_df.columns)
    # Scale min weight down so n * min_w never exceeds 1.0; keep max at 40%
    min_w  = min(0.02, 0.80 / n)
    bounds = [(min_w, 0.40)] * n
    constraints = [{"type":"eq","fun": lambda w: np.sum(w) - 1}]

    # Add target return constraint if specified
    if target_return is not None:
        constraints.append({
            "type": "ineq",
            "fun": lambda w: portfolio_metrics(w, returns_df)[0] - target_return
        })

    init = np.ones(n) / n

    # 1. Maximum Sharpe ratio
    res_sharpe = minimize(_neg_sharpe, init, args=(returns_df,),
                          method="SLSQP", bounds=bounds, constraints=constraints)

    # 2. Minimum volatility
    res_minvol = minimize(_portfolio_vol, init, args=(returns_df,),
                          method="SLSQP", bounds=bounds,
                          constraints=[{"type":"eq","fun":lambda w: np.sum(w)-1}])

    # 3. Risk-adjusted (blend based on risk tolerance)
    # Low risk → closer to min vol; high risk → closer to max sharpe
    alpha = (risk_tolerance - 1) / 9.0  # 0 to 1
    if res_sharpe.success and res_minvol.success:
        blended_w = alpha * res_sharpe.x + (1 - alpha) * res_minvol.x
        blended_w = blended_w / blended_w.sum()
    else:
        blended_w = init

    def w_to_dict(w, cols):
        raw = {col: max(0, w[i]) for i, col in enumerate(cols)}
        total = sum(raw.values())
        return {k: v/total for k,v in raw.items()}

    cols = list(returns_df.columns)
    result = {
        "max_sharpe":  w_to_dict(res_sharpe.x if res_sharpe.success else init, cols),
        "min_vol":     w_to_dict(res_minvol.x if res_minvol.success else init, cols),
        "recommended": w_to_dict(blended_w, cols),
        "target_met":  True,
    }
    # Check whether the target return constraint was actually satisfied
    if target_return is not None:
        achieved = portfolio_metrics(
            np.array([result["recommended"][c] for c in cols]), returns_df
        )[0]
        if achieved < target_return * 0.95:   # 5% tolerance
            result["target_met"]    = False
            result["target_achieved"] = round(achieved * 100, 1)
            result["target_requested"] = round(target_return * 100, 1)
    return result


def generate_efficient_frontier(returns_df, n_portfolios=8000):
    """
    Generate random portfolios for efficient frontier scatter plot.
    Returns DataFrame with columns: Return, Volatility, Sharpe, Weights
    """
    n      = len(returns_df.columns)
    cols   = list(returns_df.columns)
    mu     = returns_df.mean().values * 252
    cov    = returns_df.cov().values  * 252
    rows   = []

    np.random.seed(42)
    for _ in range(n_portfolios):
        w     = np.random.dirichlet(np.ones(n))
        ret   = w @ mu
        vol   = np.sqrt(w @ cov @ w)
        sr    = (ret - RISK_FREE_RATE) / vol if vol > 0 else 0
        rows.append({"Return": ret*100, "Volatility": vol*100,
                     "Sharpe": sr, "Weights": dict(zip(cols, w))})

    return pd.DataFrame(rows)


# ── Backtesting engine ────────────────────────────────────────────────────────

def backtest_portfolio(close_df, weights, starting_capital, monthly_contribution,
                       rebalance_freq="quarterly"):
    """
    Backtest a weighted portfolio against historical prices.
    Returns daily portfolio values and performance metrics.
    """
    tickers  = list(weights.keys())
    avail    = [t for t in tickers if t in close_df.columns]
    if not avail:
        raise ValueError("No matching tickers in price data.")

    # Renormalise weights to available tickers
    w_arr   = np.array([weights[t] for t in avail])
    w_arr   = w_arr / w_arr.sum()
    w_dict  = dict(zip(avail, w_arr))

    prices  = close_df[avail].copy()
    dates   = prices.index.tolist()

    # Initialise holdings (shares)
    init_prices = prices.iloc[0]
    shares      = {t: (starting_capital * w_dict[t]) / init_prices[t] for t in avail}

    portfolio_values = []
    contributions    = []
    total_contrib    = starting_capital
    last_month       = dates[0].month
    last_rebal_q     = (dates[0].month - 1) // 3

    for i, date in enumerate(dates):
        current_prices = prices.iloc[i]
        port_val       = sum(shares[t] * current_prices[t] for t in avail)

        # Monthly contribution
        if date.month != last_month:
            last_month    = date.month
            total_contrib += monthly_contribution
            contributions.append(total_contrib)
            # Add contribution proportionally
            for t in avail:
                add_val     = monthly_contribution * w_dict[t]
                shares[t]  += add_val / current_prices[t]
            port_val = sum(shares[t] * current_prices[t] for t in avail)
        else:
            contributions.append(total_contrib)

        # Rebalance quarterly (0.10% transaction cost per rebalance event)
        TRANSACTION_COST = 0.001
        current_q = (date.month - 1) // 3
        if rebalance_freq == "quarterly" and current_q != last_rebal_q and i > 0:
            last_rebal_q = current_q
            port_val_after_cost = port_val * (1 - TRANSACTION_COST)
            for t in avail:
                shares[t] = (port_val_after_cost * w_dict[t]) / current_prices[t]

        portfolio_values.append(port_val)

    result_df              = pd.DataFrame(index=dates)
    result_df["Portfolio"] = portfolio_values
    result_df["Contrib"]   = contributions

    # Add benchmark (SPY) if available
    if "SPY" in close_df.columns:
        spy_prices            = close_df["SPY"]
        spy_init              = spy_prices.iloc[0]
        spy_shares            = starting_capital / spy_init
        result_df["SP500"]    = spy_prices * spy_shares
    else:
        result_df["SP500"] = np.nan

    return result_df


def compute_backtest_metrics(backtest_df, starting_capital):
    """Compute performance metrics from backtest results."""
    port   = backtest_df["Portfolio"]
    dates  = backtest_df.index

    n_years   = (dates[-1] - dates[0]).days / 365.25
    daily_ret = port.pct_change().dropna()
    ann_vol   = daily_ret.std() * np.sqrt(252) * 100

    final_val         = port.iloc[-1]
    total_contributed = backtest_df["Contrib"].iloc[-1]
    total_gain        = final_val - total_contributed

    # Fix 1: Total return on total invested capital (not starting capital only)
    total_ret = (total_gain / total_contributed) * 100 if total_contributed > 0 else 0

    # Fix 2: Annualised return from daily returns (handles contributions correctly)
    ann_ret = daily_ret.mean() * 252 * 100

    # Fix 3: Sharpe/Sortino with risk-free rate (excess return basis)
    rf_daily = RISK_FREE_RATE / 252
    excess   = daily_ret - rf_daily
    sharpe   = (excess.mean() * 252) / (excess.std() * np.sqrt(252)) if excess.std() > 0 else 0
    down_ret = excess[excess < 0]
    sortino  = (excess.mean() * 252) / (down_ret.std() * np.sqrt(252)) if down_ret.std() > 0 else 0

    # Drawdown
    peak     = port.cummax()
    drawdown = (port - peak) / peak
    max_dd   = drawdown.min() * 100

    # Monthly returns
    monthly = port.resample("ME").last().pct_change().dropna() * 100
    best_m  = monthly.max()
    worst_m = monthly.min()
    pct_pos = (monthly > 0).mean() * 100

    # Fix 4: vs S&P 500 on same basis — return on invested capital vs SPY price return
    sp500_ret = np.nan
    if "SP500" in backtest_df.columns and not backtest_df["SP500"].isna().all():
        sp = backtest_df["SP500"]
        sp500_ret = (sp.iloc[-1] / sp.iloc[0] - 1) * 100

    alpha = round(total_ret - sp500_ret, 2) if not np.isnan(sp500_ret) else "N/A"

    return {
        "Final Value":        round(final_val, 2),
        "Total Contributed":  round(total_contributed, 2),
        "Total Gain/Loss":    round(total_gain, 2),
        "Total Return":       round(total_ret, 2),
        "Ann. Return":        round(ann_ret, 2),
        "Ann. Volatility":    round(ann_vol, 2),
        "Sharpe Ratio":       round(sharpe, 3),
        "Sortino Ratio":      round(sortino, 3),
        "Max Drawdown":       round(max_dd, 2),
        "Best Month":         round(best_m, 2),
        "Worst Month":        round(worst_m, 2),
        "% Months Positive":  round(pct_pos, 1),
        "vs S&P 500":         alpha,
        "S&P 500 Return":     round(sp500_ret, 2) if not np.isnan(sp500_ret) else "N/A",
    }


def compute_monthly_heatmap(backtest_df):
    """Returns a pivot table of monthly returns for heatmap."""
    port        = backtest_df["Portfolio"]
    monthly_ret = port.resample("ME").last().pct_change().dropna() * 100
    monthly_ret.index = pd.to_datetime(monthly_ret.index)
    heatmap     = monthly_ret.groupby([monthly_ret.index.year, monthly_ret.index.month]).first()
    heatmap.index = pd.MultiIndex.from_tuples(heatmap.index, names=["Year","Month"])
    return heatmap.unstack(level="Month")


# ── Portfolio Monte Carlo ─────────────────────────────────────────────────────

def run_portfolio_monte_carlo(returns_df, weights, starting_capital,
                               monthly_contribution, forecast_years=10,
                               n_simulations=1000, target_value=None, log=print):
    """
    Run Monte Carlo on the full portfolio preserving correlations.
    Uses Cholesky decomposition for correlated returns.
    """
    log(f"Portfolio Monte Carlo: {n_simulations:,} paths × {forecast_years} years...")

    tickers  = [t for t in weights.keys() if t in returns_df.columns]
    w_arr    = np.array([weights[t] for t in tickers])
    w_arr   /= w_arr.sum()

    port_ret = returns_df[tickers] @ w_arr
    hist_mu  = port_ret.mean()
    sigma    = port_ret.std()

    # Blend historical return with long-term market mean.
    # 70% historical, 30% long-term — anchors to actual portfolio performance
    # while slightly moderating extreme recent bull/bear runs.
    LONGTERM_DAILY_MU = 0.07 / 252   # conservative 7% long-run real equity return
    mu = 0.70 * hist_mu + 0.30 * LONGTERM_DAILY_MU

    # Hard cap: annualised mu never exceeds 12% regardless of historical period
    mu = min(mu, 0.12 / 252)

    ann_mu_pct = mu * 252 * 100
    log(f"   Assumed annual return: {ann_mu_pct:.1f}% "
        f"(historical: {hist_mu*252*100:.1f}%, blended with 7% long-term avg, capped at 12%)")

    forecast_days = forecast_years * 252
    np.random.seed(None)

    rand          = np.random.standard_normal((forecast_days, n_simulations))
    daily_factors = np.exp((mu - 0.5 * sigma**2) + sigma * rand)

    paths    = np.zeros((forecast_days + 1, n_simulations))
    paths[0] = starting_capital

    monthly_days = 21
    for t in range(1, forecast_days + 1):
        paths[t] = paths[t-1] * daily_factors[t-1]
        if t % monthly_days == 0:
            paths[t] += monthly_contribution

    def _total_invested(yr):
        """Total capital put in by the end of `yr` years (contributions are monthly)."""
        months = yr * 12
        return starting_capital + monthly_contribution * months

    # Milestone percentiles + per-milestone probabilities
    milestones = {}
    milestone_years = [yr for yr in [1, 3, 5, 10] if yr <= forecast_years]
    if forecast_years not in milestone_years:
        milestone_years.append(forecast_years)
    for yr in milestone_years:
        day  = yr * 252  # guaranteed <= forecast_days since yr <= forecast_years
        vals = paths[day]
        pcts = np.percentile(vals, [5, 25, 50, 75, 95])
        tot_invested = _total_invested(yr)
        milestones[f"{yr}yr"] = {
            "P5":              round(pcts[0], 2),
            "P25":             round(pcts[1], 2),
            "P50":             round(pcts[2], 2),
            "P75":             round(pcts[3], 2),
            "P95":             round(pcts[4], 2),
            "total_invested":  round(tot_invested, 2),
            "prob_gain":       f"{(vals > tot_invested).mean()*100:.1f}%",
            "prob_double":     f"{(vals > tot_invested * 2).mean()*100:.1f}%",
            "prob_loss_20":    f"{(vals < tot_invested * 0.8).mean()*100:.1f}%",
        }
        if target_value:
            milestones[f"{yr}yr"]["prob_goal"] = f"{(vals > target_value).mean()*100:.1f}%"

    fp           = paths[-1]
    pcts         = np.percentile(fp, [5, 25, 50, 75, 95])
    tot_invested = _total_invested(forecast_years)

    # Probabilities compare against total invested (starting capital + all contributions)
    prob_gain    = (fp > tot_invested).mean() * 100
    prob_double  = (fp > tot_invested * 2).mean() * 100
    prob_loss_20 = (fp < tot_invested * 0.8).mean() * 100
    prob_goal    = (fp > target_value).mean() * 100 if target_value else None

    summary = {
        "Starting Capital":        round(starting_capital, 2),
        "Monthly Contribution":    round(monthly_contribution, 2),
        "Total Invested":          round(tot_invested, 2),
        "Forecast Horizon":        f"{forecast_years} years",
        "Simulations":             n_simulations,
        "Bear Case (P5)":          round(pcts[0], 2),
        "Low Case (P25)":          round(pcts[1], 2),
        "Median (P50)":            round(pcts[2], 2),
        "Bull Case (P75)":         round(pcts[3], 2),
        "Best Case (P95)":         round(pcts[4], 2),
        "Prob. of Any Gain":       f"{prob_gain:.1f}%",
        "Prob. of Doubling":       f"{prob_double:.1f}%",
        "Prob. of >20% Loss":      f"{prob_loss_20:.1f}%",
        "Ann. Volatility":         f"{sigma * np.sqrt(252) * 100:.2f}%",
    }
    if prob_goal is not None:
        summary["Prob. of Reaching Goal"] = f"{prob_goal:.1f}%"

    log(f"   P5 ${pcts[0]:,.0f}  P50 ${pcts[2]:,.0f}  P95 ${pcts[4]:,.0f}")
    return pd.DataFrame(paths), summary, milestones


# ── Diversification score ─────────────────────────────────────────────────────

def compute_diversification_score(weights, returns_df):
    """
    Score from 1-10. Higher = more diversified.
    Based on effective number of assets and avg pairwise correlation.
    """
    tickers = [t for t in weights.keys() if t in returns_df.columns]
    w_arr   = np.array([weights[t] for t in tickers])
    w_arr  /= w_arr.sum()

    # Effective N (Herfindahl-Hirschman Index)
    hhi      = np.sum(w_arr ** 2)
    eff_n    = 1 / hhi
    n_score  = min(eff_n / len(tickers), 1.0)

    # Average pairwise correlation
    corr     = returns_df[tickers].corr()
    mask     = np.triu(np.ones(corr.shape), k=1).astype(bool)
    avg_corr = corr.where(mask).stack().mean()
    c_score  = 1 - max(0, avg_corr)

    # Concentration penalty: any single position >25% drags the score down
    max_w        = w_arr.max()
    conc_penalty = max(0.0, (max_w - 0.25) / 0.75)  # 0 at 25%, 1.0 at 100%

    raw = (n_score * 0.5 + c_score * 0.4 + (1 - conc_penalty) * 0.1) * 10
    return round(min(10, max(1, raw)), 1)


# ── Rebalancing recommendations ───────────────────────────────────────────────

def get_rebalancing_recommendations(current_holdings, target_weights, current_prices):
    """
    Given current holdings (shares) and target weights,
    return what to buy/sell to rebalance.
    """
    total_val = sum(current_holdings.get(t, 0) * current_prices.get(t, 0)
                    for t in target_weights)
    recs = []
    for ticker, target_w in target_weights.items():
        target_val   = total_val * target_w
        current_val  = current_holdings.get(ticker, 0) * current_prices.get(ticker, 1)
        diff_val     = target_val - current_val
        diff_pct     = (diff_val / total_val) * 100

        if abs(diff_pct) > 1.0:  # only flag if > 1% off target
            action = "BUY" if diff_val > 0 else "SELL"
            recs.append({
                "Ticker":        ticker,
                "Action":        action,
                "Current Value": round(current_val, 2),
                "Target Value":  round(target_val, 2),
                "Difference":    round(abs(diff_val), 2),
                "Off Target":    f"{abs(diff_pct):.1f}%",
            })

    return sorted(recs, key=lambda x: x["Difference"], reverse=True)
