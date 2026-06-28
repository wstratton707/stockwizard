import numpy as np
import pandas as pd
from scipy.optimize import minimize
from datetime import datetime, timedelta

from constants import get_risk_free_rate


# ── Individual stock metrics ──────────────────────────────────────────────────

def compute_stock_metrics(returns_df, market_returns=None):
    """
    Compute per-stock metrics from a returns DataFrame.
    Returns a dict: {ticker: {metrics}}. When market_returns is provided, also
    includes each stock's beta and CAPM expected return (the forward number);
    ann_return stays as the trailing historical figure.
    """
    betas = compute_betas(returns_df, market_returns) if market_returns is not None else {}
    capm  = capm_expected_returns(betas) if betas else {}
    metrics = {}
    for ticker in returns_df.columns:
        r       = returns_df[ticker].dropna()
        ann_ret = r.mean() * 252
        ann_std = r.std() * np.sqrt(252)
        down    = r[r < 0].std() * np.sqrt(252)
        rfr     = get_risk_free_rate()
        sharpe  = (ann_ret - rfr) / ann_std  if ann_std  else 0
        sortino = (ann_ret - rfr) / down     if down     else 0

        # Max drawdown
        cumret   = (1 + r).cumprod()
        peak     = cumret.cummax()
        drawdown = (cumret - peak) / peak
        max_dd   = drawdown.min()

        metrics[ticker] = {
            "ann_return":   round(ann_ret * 100, 2),   # trailing historical
            "ann_vol":      round(ann_std * 100, 2),
            "sharpe":       round(sharpe, 3),
            "sortino":      round(sortino, 3),
            "max_drawdown": round(max_dd * 100, 2),
            "total_return": round(((1 + r).prod() - 1) * 100, 2),
            "beta":         round(betas[ticker], 3) if ticker in betas else None,
            "capm_return":  round(capm[ticker] * 100, 2) if ticker in capm else None,  # forward (CAPM)
        }
    return metrics


def compute_correlation_matrix(returns_df):
    return returns_df.corr()


# ── CAPM expected returns ───────────────────────────────────────────────────────
# Expected return comes from each asset's market risk (beta), NOT its own past
# return — so a stock that merely ran up recently doesn't get a sky-high forecast.
#   E(R) = Rf + beta * ERP

def compute_betas(returns_df, market_returns):
    """Beta of each column vs the market: cov(r_i, r_m)/var(r_m), aligned on common
    dates. Falls back to 1.0 for any series with <30 overlapping days."""
    if market_returns is None:
        return {c: 1.0 for c in returns_df.columns}
    m = market_returns.copy()
    m.index = pd.to_datetime(m.index)
    betas = {}
    for col in returns_df.columns:
        r = returns_df[col].copy()
        r.index = pd.to_datetime(r.index)
        paired = pd.concat([r.rename("r"), m.rename("m")], axis=1).dropna()
        var_m = paired["m"].var()
        if len(paired) < 30 or not var_m:
            betas[col] = 1.0
        else:
            betas[col] = float(paired["r"].cov(paired["m"]) / var_m)
    return betas


def capm_expected_returns(betas, rf=None, erp=None):
    """CAPM expected ANNUAL return per ticker (decimal): Rf + beta * ERP."""
    from constants import EQUITY_RISK_PREMIUM
    rf  = get_risk_free_rate() if rf is None else rf
    erp = EQUITY_RISK_PREMIUM if erp is None else erp
    return {t: rf + b * erp for t, b in betas.items()}


def portfolio_beta(weights, betas):
    """Weighted-average beta of a portfolio. Beta is linear, so this equals
    regressing the blended portfolio's returns on the market."""
    tot = sum(max(0.0, w) for w in weights.values())
    if tot <= 0:
        return 1.0
    return sum(max(0.0, w) * betas.get(t, 1.0) for t, w in weights.items()) / tot


# ── Portfolio optimisation (Mean-Variance) ────────────────────────────────────

def portfolio_metrics(weights, returns_df):
    weights      = np.array(weights)
    port_ret     = returns_df.mean().values @ weights * 252
    port_vol     = np.sqrt(weights @ (returns_df.cov().values * 252) @ weights)
    sharpe       = (port_ret - get_risk_free_rate()) / port_vol if port_vol > 0 else 0
    return port_ret, port_vol, sharpe


def _neg_sharpe(weights, returns_df):
    _, _, sharpe = portfolio_metrics(weights, returns_df)
    return -sharpe


def _portfolio_vol(weights, returns_df):
    _, vol, _ = portfolio_metrics(weights, returns_df)
    return vol


def optimise_portfolio(returns_df, risk_tolerance=5, target_return=None,
                       sector_map=None, max_sector_weight=0.40, max_weight=0.30,
                       expected_returns=None):
    """
    Run mean-variance optimisation.
    Returns weights dict for max Sharpe, min vol, and target return portfolios.

    sector_map       : {ticker: sector_label} — used to cap sector concentration.
    max_sector_weight: maximum combined weight for any single sector (default 40%).
    max_weight       : maximum weight per individual position (default 30%).
    """
    n      = len(returns_df.columns)
    cols   = list(returns_df.columns)
    # Clamp max_weight so n * max_weight >= 1 (otherwise the budget constraint
    # is infeasible); also enforce a sane floor of 5%.
    max_weight = max(0.05, min(max_weight, 1.0))
    if n * max_weight < 1.0:
        max_weight = 1.0 / n + 1e-3
    # Scale min weight down so n * min_w never exceeds 1.0
    min_w  = min(0.02, 0.80 / n)
    bounds = [(min_w, max_weight)] * n

    # Pre-compute constants once — the optimizer calls the objective O(n²) times
    # per run, so recomputing mean/cov inside the objective is massively redundant.
    # Expected returns are CAPM (Rf + beta*ERP) when provided, so the optimizer
    # tilts on market risk, not on which names merely ran up recently. Falls back
    # to the raw historical mean only if no CAPM vector is supplied.
    if expected_returns is not None:
        _mu = np.array([expected_returns.get(c, get_risk_free_rate()) for c in cols])
    else:
        _mu = returns_df.mean().values * 252
    _cov = returns_df.cov().values * 252
    _rfr = get_risk_free_rate()

    def _obj_sharpe(w):
        vol = float(np.sqrt(w @ _cov @ w))
        return -(_mu @ w - _rfr) / vol if vol > 0 else 0.0

    def _obj_vol(w):
        return float(np.sqrt(w @ _cov @ w))

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]

    # Sector concentration caps — prevent >max_sector_weight in any single sector
    if sector_map:
        from collections import defaultdict
        sector_indices: dict = defaultdict(list)
        for i, ticker in enumerate(cols):
            sector = sector_map.get(ticker, "Unknown")
            # Don't cap Market/benchmark tickers (SPY, QQQ) or single-ticker sectors
            if sector not in ("Market", "Commodities", "User"):
                sector_indices[sector].append(i)
        for sector, indices in sector_indices.items():
            if len(indices) > 1:
                constraints.append({
                    "type": "ineq",
                    "fun": lambda w, idx=indices: max_sector_weight - sum(w[i] for i in idx),
                })

    # Add target return constraint if specified
    if target_return is not None:
        constraints.append({
            "type": "ineq",
            "fun": lambda w: _mu @ np.array(w) - target_return
        })

    init = np.ones(n) / n

    # 1. Maximum Sharpe ratio
    res_sharpe = minimize(_obj_sharpe, init,
                          method="SLSQP", bounds=bounds, constraints=constraints)

    # 2. Minimum volatility
    res_minvol = minimize(_obj_vol, init,
                          method="SLSQP", bounds=bounds,
                          constraints=[{"type":"eq","fun":lambda w: np.sum(w)-1}])

    # 3. Maximum expected return (= maximum beta under CAPM) — the aggressive anchor.
    res_maxret = minimize(lambda w: -float(_mu @ w), init,
                          method="SLSQP", bounds=bounds, constraints=constraints)

    # 4. Risk-adjusted blend — slide along the efficient frontier by risk tolerance:
    #    conservative → min-vol, balanced → max-Sharpe, aggressive → max-return.
    #    Under CAPM, max-Sharpe is inherently defensive (it maximizes beta/sigma),
    #    so the aggressive end must push toward higher beta/return, not just Sharpe —
    #    otherwise every risk level lands on the same defensive portfolio.
    w_minvol = res_minvol.x if res_minvol.success else init
    w_sharpe = res_sharpe.x if res_sharpe.success else init
    w_maxret = res_maxret.x if res_maxret.success else w_sharpe
    MID = 5.5  # risk tolerance at which the blend is pure max-Sharpe
    if risk_tolerance <= MID:
        t = max(0.0, min(1.0, (risk_tolerance - 1) / (MID - 1)))
        blended_w = (1 - t) * w_minvol + t * w_sharpe
    else:
        t = max(0.0, min(1.0, (risk_tolerance - MID) / (10 - MID)))
        blended_w = (1 - t) * w_sharpe + t * w_maxret
    blended_w = np.clip(blended_w, 0, None)
    _bw_sum   = blended_w.sum()
    blended_w = blended_w / _bw_sum if _bw_sum > 0 else init

    def w_to_dict(w, cols):
        raw = {col: max(0, w[i]) for i, col in enumerate(cols)}
        total = sum(raw.values())
        return {k: v/total for k,v in raw.items()}

    cols = list(returns_df.columns)
    result = {
        "max_sharpe":  w_to_dict(res_sharpe.x if res_sharpe.success else init, cols),
        "min_vol":     w_to_dict(res_minvol.x if res_minvol.success else init, cols),
        "max_return":  w_to_dict(w_maxret, cols),
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
    Returns DataFrame with columns: Return, Volatility, Sharpe
    Vectorized — ~60% faster than the per-portfolio loop.
    """
    n    = len(returns_df.columns)
    mu   = returns_df.mean().values * 252
    cov  = returns_df.cov().values  * 252

    rng  = np.random.default_rng(42)   # local RNG — no global-state pollution
    W    = rng.dirichlet(np.ones(n), size=n_portfolios)         # (n_portfolios, n)
    rets = W @ mu                                                # (n_portfolios,)
    vols = np.sqrt(np.einsum("ij,jk,ik->i", W, cov, W))        # (n_portfolios,)
    rfr  = get_risk_free_rate()
    srs  = np.where(vols > 0, (rets - rfr) / vols, 0)

    return pd.DataFrame({
        "Return":     rets * 100,
        "Volatility": vols * 100,
        "Sharpe":     srs,
    })


# ── Backtesting engine ────────────────────────────────────────────────────────

def backtest_portfolio(close_df, weights, starting_capital, monthly_contribution,
                       rebalance_freq="quarterly"):
    """
    Backtest a weighted portfolio against historical prices.

    Returns a daily DataFrame with:
      Portfolio  account value INCLUDING monthly contributions
      Contrib    cumulative capital contributed (starting + monthly adds)
      NAV        contribution-free growth index (starts at 1.0) — the basis for
                 every return/volatility/Sharpe/drawdown metric, so cash inflows
                 are never counted as investment performance
      SP500      a SPY position dollar-cost-averaged on the SAME contribution
                 schedule, so the benchmark is a like-for-like comparison
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

    # SPY benchmark dollar-cost-averaged on the same schedule as the portfolio.
    has_spy    = "SPY" in close_df.columns
    spy_prices = close_df["SPY"] if has_spy else None
    spy_shares = (starting_capital / float(spy_prices.iloc[0])) if has_spy else 0.0

    portfolio_values = []
    contributions    = []
    nav_values       = []
    sp500_values     = []

    TRANSACTION_COST = 0.001
    total_contrib    = starting_capital
    last_month       = dates[0].month
    last_rebal_q     = (dates[0].month - 1) // 3
    prev_value       = starting_capital   # prior day's end-of-day account value
    nav              = 1.0

    for i, date in enumerate(dates):
        current_prices = prices.iloc[i]
        # Market value using the prior day's shares, BEFORE today's contribution.
        value_pre = sum(shares[t] * current_prices[t] for t in avail)

        # Monthly contribution — buys shares but is NOT investment performance.
        if date.month != last_month:
            last_month    = date.month
            total_contrib += monthly_contribution
            for t in avail:
                shares[t] += (monthly_contribution * w_dict[t]) / current_prices[t]
            if has_spy:
                spy_shares += monthly_contribution / float(spy_prices.iloc[i])

        value_post = sum(shares[t] * current_prices[t] for t in avail)

        # Quarterly rebalance — transaction cost charged only on the value that
        # actually changes hands (so a no-op / single-asset rebalance is free).
        current_q = (date.month - 1) // 3
        cost      = 0.0
        if rebalance_freq == "quarterly" and current_q != last_rebal_q and i > 0:
            last_rebal_q = current_q
            traded = sum(abs(value_post * w_dict[t] - shares[t] * current_prices[t])
                         for t in avail)
            cost   = traded * TRANSACTION_COST
            value_post -= cost
            for t in avail:
                shares[t] = (value_post * w_dict[t]) / current_prices[t]

        # Time-weighted daily return: market move minus rebalance cost, with the
        # contribution inflow excluded. This is what NAV compounds.
        day_ret = 0.0 if (i == 0 or prev_value <= 0) else (value_pre - cost) / prev_value - 1
        nav    *= (1 + day_ret)

        portfolio_values.append(value_post)
        contributions.append(total_contrib)
        nav_values.append(nav)
        sp500_values.append(spy_shares * float(spy_prices.iloc[i]) if has_spy else np.nan)
        prev_value = value_post

    result_df              = pd.DataFrame(index=dates)
    result_df["Portfolio"] = portfolio_values
    result_df["Contrib"]   = contributions
    result_df["NAV"]       = nav_values
    result_df["SP500"]     = sp500_values
    return result_df


def compute_backtest_metrics(backtest_df, starting_capital):
    """Compute performance metrics from backtest results."""
    port  = backtest_df["Portfolio"]
    nav   = backtest_df["NAV"]

    # Every return/risk metric uses the contribution-free NAV growth index, so
    # monthly cash inflows are never counted as investment returns (which would
    # otherwise inflate return/volatility/Sharpe and mask drawdowns).
    daily_ret = nav.pct_change().dropna()
    ann_vol   = daily_ret.std() * np.sqrt(252) * 100

    final_val         = port.iloc[-1]
    total_contributed = backtest_df["Contrib"].iloc[-1]
    total_gain        = final_val - total_contributed

    # Total return on total invested capital (not starting capital only)
    total_ret = (total_gain / total_contributed) * 100 if total_contributed > 0 else 0

    # Annualised return from the contribution-free daily returns
    ann_ret = daily_ret.mean() * 252 * 100

    # Sharpe/Sortino with risk-free rate (excess return basis)
    rf_daily = get_risk_free_rate() / 252
    excess   = daily_ret - rf_daily
    sharpe   = (excess.mean() * 252) / (excess.std() * np.sqrt(252)) if excess.std() > 0 else 0
    down_ret = excess[excess < 0]
    # Require at least ~1 month of downside days before computing Sortino —
    # with a tiny sample, std() is noisy and can blow the ratio up.
    if len(down_ret) >= 20 and down_ret.std() > 0:
        sortino = (excess.mean() * 252) / (down_ret.std() * np.sqrt(252))
    else:
        sortino = 0

    # Drawdown on the NAV index, so contribution inflows can't mask drawdowns.
    peak   = nav.cummax()
    max_dd = ((nav - peak) / peak).min() * 100

    # Monthly returns from the NAV index
    monthly = nav.resample("ME").last().pct_change().dropna() * 100
    best_m  = monthly.max() if len(monthly) else 0.0
    worst_m = monthly.min() if len(monthly) else 0.0
    pct_pos = (monthly > 0).mean() * 100 if len(monthly) else 0.0

    # The SPY benchmark is dollar-cost-averaged on the same contribution schedule
    # as the portfolio, so compare it on the same total-return-on-contributed-
    # capital basis (a 100%-SPY portfolio then shows ~0 alpha, as it should).
    sp500_ret_matched = np.nan
    if "SP500" in backtest_df.columns and not backtest_df["SP500"].isna().all():
        sp_final = float(backtest_df["SP500"].iloc[-1])
        if total_contributed > 0:
            sp500_ret_matched = (sp_final - total_contributed) / total_contributed * 100

    alpha = round(total_ret - sp500_ret_matched, 2) if not np.isnan(sp500_ret_matched) else "N/A"

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
        "S&P 500 Return":     round(sp500_ret_matched, 2) if not np.isnan(sp500_ret_matched) else "N/A",
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
                               n_simulations=1000, target_value=None, log=print,
                               market_returns=None):
    """
    Run Monte Carlo on the full portfolio preserving cross-asset correlations.

    Uses Cholesky decomposition of the historical daily return correlation matrix
    to generate correlated per-asset paths. Each simulation draws correlated
    log-returns for all assets, computes the weighted portfolio log-return, and
    compounds it forward with monthly contributions.

    Per-asset drift is blended (70% historical, 30% long-run 7% nominal) and
    capped at 12% annualised. Ito correction (−½σ²) is applied per asset.
    """
    log(f"Portfolio Monte Carlo: {n_simulations:,} paths × {forecast_years} years...")

    tickers = [t for t in weights.keys() if t in returns_df.columns]
    w_arr   = np.array([weights[t] for t in tickers])
    w_arr  /= w_arr.sum()

    # --- Correlated multi-asset Monte Carlo via Cholesky decomposition ---
    returns_matrix = returns_df[tickers]

    # Per-asset drift and volatility
    mu_vec    = returns_matrix.mean().values   # shape (N,)
    sigma_vec = returns_matrix.std().values    # shape (N,)

    # Portfolio return series for historical reference and summary volatility
    port_ret_series = returns_matrix @ w_arr
    hist_mu         = float(port_ret_series.mean())
    sigma           = float(port_ret_series.std())

    # CAPM per-asset drift: Rf + beta*ERP (annual) -> daily. Replaces the old
    # blend-and-cap heuristic so the forecast matches the optimizer's expected
    # returns. Falls back to a soft blend toward a 7% long-run mean only if no
    # market series is supplied (no hard cap).
    if market_returns is not None:
        _betas = compute_betas(returns_matrix, market_returns)
        _capm  = capm_expected_returns(_betas)                 # annual fractions
        mu_vec = np.array([_capm.get(t, get_risk_free_rate()) for t in tickers]) / 252
    else:
        mu_vec = 0.70 * mu_vec + 0.30 * (0.07 / 252)

    ann_mu_pct = float(mu_vec @ w_arr) * 252 * 100
    log(f"   Assumed annual return: {ann_mu_pct:.1f}% "
        f"(CAPM: Rf + beta*ERP; historical was {hist_mu*252*100:.1f}%)")

    # Correlation matrix and Cholesky factor
    corr_matrix = returns_matrix.corr().values
    try:
        L = np.linalg.cholesky(corr_matrix)
    except np.linalg.LinAlgError:
        # Add small diagonal perturbation if matrix is not positive definite
        L = np.linalg.cholesky(corr_matrix + np.eye(len(tickers)) * 1e-6)

    n_assets      = len(tickers)
    forecast_days = forecast_years * 252
    monthly_days  = 21

    # Local RNG — deterministic (seed=42) so the same inputs produce the same
    # output, and doesn't pollute global numpy random state.
    rng = np.random.default_rng(42)

    paths    = np.empty((forecast_days + 1, n_simulations))
    paths[0] = starting_capital

    # Drift + Ito correction term is constant across sims — precompute it once.
    drift_vec = mu_vec - 0.5 * sigma_vec ** 2            # shape (n_assets,)
    contrib_days = np.arange(monthly_days, forecast_days + 1, monthly_days)

    # Batch simulations to cap memory. One batch of B sims holds
    # B * forecast_days * n_assets floats ≈ 200 * 2520 * 18 * 8 bytes ≈ 72 MB.
    BATCH = 200

    for start in range(0, n_simulations, BATCH):
        b = min(BATCH, n_simulations - start)
        # Correlated standard normals in one shot: (b, days, n_assets)
        Z       = rng.standard_normal((b, forecast_days, n_assets))
        Z_corr  = Z @ L.T
        log_ret = drift_vec + sigma_vec * Z_corr           # broadcast
        # Portfolio daily log-return per sim: (b, days)
        port_log_ret  = log_ret @ w_arr
        daily_factors = np.exp(port_log_ret)

        # Cumulative product along time axis, prepend 1.0 to align with day 0
        CP        = np.empty((b, forecast_days + 1))
        CP[:, 0]  = 1.0
        CP[:, 1:] = np.cumprod(daily_factors, axis=1)

        # Contribution accumulator: at each contrib_day t_c, add 1/CP[:, t_c]
        inv_contrib = np.zeros((b, forecast_days + 1))
        inv_contrib[:, contrib_days] = 1.0 / CP[:, contrib_days]
        running_inv = np.cumsum(inv_contrib, axis=1)

        # Final path values: CP scales starting capital + all past contributions
        batch_paths = CP * (starting_capital + monthly_contribution * running_inv)
        paths[:, start:start + b] = batch_paths.T

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
