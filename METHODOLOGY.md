# QuantWizard — Methodology

How every number is calculated. Plain enough to put in front of a user, precise
enough to audit against the code.

## Data sources
Multi-source router (`market_data.py`):
- **Live quotes** — Finnhub (real-time, free tier).
- **Daily price history** — Yahoo Finance (same-day close), Polygon as fallback.
- **Fundamentals** — SEC EDGAR 10-K/10-Q filings.
- **Risk-free rate** — FRED 3-month T-bill, fetched daily (`get_risk_free_rate()`),
  falls back to 4.5%. Used everywhere "Rf" appears below.

All prices are **dividend-adjusted** (Yahoo `auto_adjust`), so returns are total
returns (≈ price appreciation + reinvested dividends).

## Per-stock metrics (`compute_stock_metrics`)
From the daily return series `r` over the lookback window (portfolio builder uses 2 years):
- **Ann. return (historical)** — `mean(r) × 252`. A *trailing* figure (what it did), labeled as such.
- **Ann. volatility** — `std(r) × √252`.
- **Sharpe** — `(ann_return − Rf) / ann_volatility` (excess return per unit of risk).
- **Sortino** — same numerator over *downside* deviation only.
- **Max drawdown** — largest peak-to-trough decline of the cumulative return.
- **Beta** — `cov(r, r_market) / var(r_market)` vs SPY. How much the stock moves with the market.

## Expected returns — CAPM (the forward number)
Expected return is **not** a stock's own past return (that over-weighted recent
winners and produced unreal ~40% figures, and made the optimizer chase them).
Instead it comes from market risk:

```
E(R) = Rf + β × ERP
```
- **ERP (equity risk premium)** = 5% — the one stated assumption (`EQUITY_RISK_PREMIUM`
  in `constants.py`). Historical US ERP ≈ 5–6%; forward estimates ≈ 4–5%.
- A stock's expected return rises only with its **beta** (market risk), so e.g. AMD
  drops from a 78% trailing figure to ~15% expected.

**Portfolio expected return** uses the portfolio beta (beta is linear):
```
β_portfolio = Σ (wᵢ × βᵢ)
E(R_portfolio) = Rf + β_portfolio × ERP
```
This is identical to the weighted average of the holdings' CAPM returns, so the
headline and the holdings table always agree.

## Optimization (`optimise_portfolio`)
Mean-variance optimization (SLSQP) over the candidate universe, long-only, with a
per-position cap (default 30%) and per-sector cap (default 40%). It computes three
anchor portfolios using **CAPM expected returns** as the inputs:
- **Min-vol** — lowest variance.
- **Max-Sharpe** — best return per unit of risk. *Note:* under CAPM this maximizes
  `β/σ`, so it is inherently well-diversified and **defensive** (modest beta).
- **Max-return** — highest expected return = highest beta (subject to the caps).

**Risk tolerance (1–10)** slides along these anchors:
- 1 → min-vol (low beta, defensive)
- 5.5 → max-Sharpe (the efficient middle)
- 10 → max-return (high beta, aggressive)

So aggressive profiles genuinely take more market risk for more expected return
(verified: risk 1→10 moves portfolio beta ≈ 0.3 → 1.9).

## Monte Carlo forecast (`run_portfolio_monte_carlo`)
- Per-asset drift = **CAPM** `Rf + β×ERP` (same as the optimizer, so forecast and
  headline agree), with the Itô −½σ² correction.
- Correlated multi-asset paths via **Cholesky** decomposition of the historical
  daily-return correlation matrix.
- Compounds forward with monthly contributions; probabilities (gain, doubling, >20%
  loss, reaching goal) compare the simulated end value to **total invested**
  (starting capital + all contributions), never to starting capital alone.

## Backtest & forward tracker
Both report performance on a **contribution-free, time-weighted** basis — cash you
add is never counted as investment return.
- **Backtest** (`backtest_portfolio`) — weighted holdings with quarterly rebalancing
  and a 0.1% transaction cost; maintains a NAV index (the return basis) separate
  from account value (which includes contributions). Benchmark = SPY dollar-cost-
  averaged on the same schedule, so a 100%-SPY portfolio shows ~0 alpha.
- **Tracker** (`tracker.py`, "Your Portfolios") — marks dated lots to market from
  inception to today (no rebalancing). Reuses the same metric engine, so its
  return/Sharpe/drawdown/vs-SPY are computed identically.

## Validation
`validate_metrics.py` (run via `predeploy_check.py`) checks all of the above against
independently-computed values and sanity bounds — including a CAPM block (Part I)
that asserts `SPY self-beta == 1`, `E(R) = Rf + β×ERP` for every holding, no absurd
expected returns, and that aggressive risk → higher beta than conservative.
