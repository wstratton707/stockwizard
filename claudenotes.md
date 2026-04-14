# Claude Notes — StockWizard
Internal reference file for Claude. Tracks known issues, architectural decisions,
conversation history, and onboarding context. Not for users.

Founders: Wyatt Stratton + Nicholas Carriello (IU Bloomington)
- Wyatt LinkedIn: https://www.linkedin.com/in/wyattstratton7/
- Nicholas LinkedIn: https://www.linkedin.com/in/nicholas-carriello-471306254/

---

## FOR ANY CLAUDE READING THIS — START HERE

This is StockWizard, a Streamlit-based stock analysis SaaS being built by two
college co-founders. You may be talking to either Wyatt (the one who set up this
file) or Nicholas. Before doing anything, read this whole file. It will save you
from re-discovering things that were already figured out.

**The single most important thing to know:**
Polygon.io's free tier only returns ~180 rows per API request and 403s for data
older than ~18 months. Every historical metric in the app (Sharpe, volatility,
drawdown, beta, backtest) is based on roughly the last 18 months of data, not
the years the UI originally advertised. We reduced all labels to "2 years" as a
stopgap. The permanent fix is upgrading Polygon to a paid tier. Do not suggest
changes that assume long historical data is available — it isn't yet.

---

## WHAT THE APP DOES

StockWizard is a 4-tab Streamlit app deployed on Railway. Users get a free tier
and a Pro tier ($9.99/month — currently DISABLED while we validate data accuracy).

**Tab 1 — Stock Analysis (Free)**
- User enters any ticker
- Shows price chart with Bollinger Bands, RSI, MACD
- GARCH volatility forecast (30-day ahead)
- Monte Carlo simulation (1,000 paths, 1-year horizon)
- Peer comparison vs sector ETF benchmark
- Intraday candlestick chart
- Downloadable Excel + PowerPoint report
- Data source: Polygon.io via data.py → fetch_stock_data()

**Tab 2 — Portfolio Builder (Pro)**
- User sets: risk tolerance (1-10), starting capital, monthly contribution,
  investment horizon, sectors to include/exclude, tickers to add/exclude
- App selects best 18 stocks from 330+ universe using nightly precompute rankings
- Runs mean-variance optimization (Markowitz) via SLSQP solver
- Shows 3 portfolios: max Sharpe, min volatility, recommended (blended by risk)
- 2-year backtest with quarterly rebalancing and 0.10% transaction cost
- 1,000-path Monte Carlo with milestone probabilities (1/3/5/10yr)
- Stress test against 5 historical crashes (2008, COVID, 2022, dot-com, 2018)
- Diversification score (1-10), correlation heatmap, efficient frontier
- Downloadable Excel + PowerPoint report
- Key files: portfolio_builder.py (UI), portfolio_analysis.py (math),
  portfolio_data.py (data fetching + caching), precompute.py (nightly rankings)

**Tab 3 — Bond Analysis (Pro)**
- 6 bond ETF categories (Government, Corporate, Inflation-Protected, Municipal,
  International, Broad Market)
- Portfolio autopsy: upload a CSV of holdings, see P&L attribution
- Rolling volatility and drawdown charts, benchmark comparison

**Tab 4 — Stress Test (Pro)**
- 5 historical crash scenarios with real Polygon price data
- Shows portfolio return vs S&P 500 per crash
- Dollar impact calculator, correlation culprit detection

---

## FULL FILE MAP

| File | What it does |
|---|---|
| app.py | Main Streamlit app — all 4 tabs, landing page, sidebar |
| portfolio_builder.py | Portfolio Builder tab UI logic (~1400 lines) |
| portfolio_analysis.py | Mean-variance math: optimizer, backtest, Monte Carlo, metrics |
| portfolio_data.py | Price fetching with chunked Polygon requests, Supabase cache, stock universe |
| precompute.py | GitHub Actions cron: ranks ~330 stocks nightly, stores in Supabase |
| data.py | Stock Analysis tab data fetching (single-request, no chunking yet) |
| analysis.py | GARCH model, ML forecast (Random Forest + XGBoost), technical indicators |
| stress_test.py | Stress test tab logic |
| database.py | Supabase REST API wrapper: cache_get/cache_set, save_portfolio/load_portfolios |
| constants.py | get_risk_free_rate() — fetches live 3M T-bill from FRED, fallback 4.5% |
| disclaimers.py | All legal/methodology disclaimer strings + HTML render helpers |
| validate.py | Backtest accuracy test suite — 4 known portfolios vs published benchmarks |
| excel_builder.py | Builds Excel + PowerPoint reports |

---

## INFRASTRUCTURE

- **Deployment:** Railway (auto-deploys from main branch on push)
- **Data:** Polygon.io API — `adjusted=true` means prices include dividends (total return)
- **Database:** Supabase (REST API only, no SDK) — two tables:
  - `api_cache` — key/value store for Polygon responses + precompute rankings
  - `saved_portfolios` — user portfolio saves/loads
- **Nightly job:** GitHub Actions cron at 9am ET weekdays (.github/workflows/precompute.yml)
  - Ranks ~330 stocks, checkpoints every 50 tickers to Supabase
  - Timeout: 90 minutes (serial execution to avoid Polygon rate limits)
- **Payments:** Stripe — wired up but INTENTIONALLY DISABLED via `DEV_MODE_FREE=True` in constants.py
- **Environment variables needed:** POLYGON_API_KEY, SUPABASE_URL, SUPABASE_KEY,
  STRIPE_SECRET_KEY, STRIPE_PRICE_ID (Railway + .env locally)

---

## PRECOMPUTE RANKING SYSTEM

Runs nightly. For each of ~330 stocks:
1. Fetches ~1 year of daily prices (400 calendar days, single Polygon request — within free tier)
2. Computes: Sharpe ratio, 6-month momentum, 3-month momentum
3. Min-max normalizes each factor across the universe
4. Combined score = 50% Sharpe + 30% 6M momentum + 20% 3M momentum
5. Stores full rankings dict in Supabase under key `sharpe_rankings_{YYYY-MM-DD}`
6. Stores `_meta` with timestamp and partial-run info for UI display

Portfolio Builder reads these rankings to select candidates — avoids in-sample bias
(precompute uses independent 1-year data, not the 2-year backtest window).

---

## PORTFOLIO OPTIMIZATION PIPELINE (step by step)

1. Load precompute rankings from Supabase (if available)
2. Filter by user preferences (sectors, excluded tickers, risk tolerance)
3. Select top 2 per sector by precompute score → ~22 candidates
4. Fetch 2-year daily prices for those candidates (chunked Polygon, Supabase cached)
5. Trim to 18 stocks using precompute scores (NOT 2-year Sharpe — avoids in-sample bias)
6. Run SLSQP mean-variance optimizer with constraints:
   - Weights sum to 1
   - Each stock: min 2%, max 30%
   - Each sector: max 40% (Market/Commodities/User sectors exempt)
7. Return 3 portfolios: max Sharpe, min vol, recommended (blend by risk_tolerance)
8. Backtest: buy-and-hold with quarterly rebalance + 0.10% transaction cost
9. Monte Carlo: 1,000 paths, correlated via Cholesky, drift blended 70% historical + 30% 7% long-run

---

## KNOWN ISSUES

### [OPEN — CRITICAL] Polygon free tier data cap
- Free tier: ~180 rows/request max, HTTP 403 for data older than ~18 months
- All historical metrics (Sharpe, vol, drawdown, beta, backtest) are based on
  ~18 months of recent data, not the 2 years shown in the UI
- COVID crash (2020) and 2022 bear market are NOT in any user-facing calculation
- validate.py 5-year tests fail entirely on free tier
- **Mitigation in place:** UI labels changed to "2 years", slider capped at 2Y
- **Fix:** Upgrade Polygon to Starter plan (~$29/mo). Chunked fetching already
  implemented in portfolio_data.py and validate.py — just needs the paid key.

### [OPEN] data.py has no chunked fetching
- Stock Analysis tab (data.py) still uses single non-chunked Polygon requests
- Lower priority since slider is capped at 2Y
- Fix alongside Polygon upgrade

### [OPEN] validate.py requires paid Polygon account to pass
- 4 test cases use 5-year reference windows (2020–2024)
- Tests are correctly written — only data access is the problem
- Run `py validate.py --verbose` after upgrading Polygon

### [OPEN] Waitlist emails lost on Railway redeploy
- Currently writes to local CSV — wiped every deploy
- Fix: move to Supabase

### [OPEN] Terms of Service + Privacy Policy missing
- Required before re-enabling Stripe and charging real users

### [RESOLVED] Thundering herd rate limiting in precompute
- Was: 3 parallel workers all hitting 429, retrying simultaneously, cascading failures
- Fix: max_workers=1 (serial), 4-attempt backoff at 30/60/90s intervals

### [RESOLVED] RISK_FREE_RATE NameError in compute_backtest_metrics
- Was: used hardcoded RISK_FREE_RATE constant that wasn't imported → NameError
- Fix: changed to get_risk_free_rate() call

### [RESOLVED] GOOGL + META duplicated across sectors
- Were listed in both Technology and Communication Services
- Fix: removed from Technology (correct GICS classification is Communication Services)

### [RESOLVED] In-sample selection bias in portfolio final trim
- Was: select_by_sharpe() re-ranked candidates by same 2-year data used for backtest
- Fix: when precompute available, final trim uses precompute scores (independent data)

---

## ARCHITECTURAL DECISIONS (the "why" behind things)

### Why Polygon adjusted=true matters
Polygon's `adjusted=true` includes dividend reinvestment in historical prices.
Our returns are total returns, not price-only. No separate dividend accounting needed.
Documented in disclaimers.py DIVIDENDS string.

### Why bootstrap-once + daily-append cache
First portfolio build for a ticker set: fetches full 2-year history, stores in Supabase
under a stable key (hash of tickers, no date). Subsequent builds: only append missing
trading days. Prevents re-fetching 2 years of data on every user session.
TTL: 400 days. Lives in portfolio_data.py → fetch_portfolio_prices_cached().

### Why max_workers=1 in precompute
Tried 3 parallel workers — all hit 429 simultaneously, all waited 30s, all retried
simultaneously, all failed again. Serial execution (max_workers=1) with exponential
backoff is slower but never cascades. GitHub Actions timeout is 90 min.

### Why sector caps are SLSQP inequality constraints
Mean-variance optimizer without caps would put 80%+ in one sector if it had the
best recent Sharpe. Caps (40% per sector, 30% per stock) are hard constraints in
the SLSQP solver, not post-hoc adjustments. Market/Commodities/User sectors are
exempt because SPY/QQQ are benchmarks, not sector bets.

### Why precompute uses 1 year and portfolio builder uses 2 years
Precompute only needs to RANK stocks relative to each other — 1-year window is
enough for that signal and stays within Polygon free tier.
Portfolio builder needs enough history for a stable covariance matrix and a
meaningful backtest — 2 years is the max available on free tier.

### Why Sharpe for stock ranking is fine at 1 year
The precompute ranking compares all 300+ stocks on the same 1-year window.
Relative ranking is valid even if absolute Sharpe numbers are inflated by the
recent market. It's comparing apples to apples.

---

## MOST RECENT CHANGES (Session 2, ~April 2026)

All changes made in a single extended session. Committed together.

**Reliability fixes:**
- precompute.py: max_workers 3→1 (thundering herd fix)
- precompute.py: 4-attempt backoff with 30/60/90s waits
- precompute.py: checkpoint every 50 tickers to Supabase (partial save on cancellation)
- .github/workflows/precompute.yml: timeout 45→90 min

**Data accuracy fixes:**
- constants.py: added get_risk_free_rate() — live FRED 3M T-bill, 24hr cache, 4.5% fallback
- portfolio_analysis.py: all Sharpe calcs now use get_risk_free_rate()
- portfolio_analysis.py: fixed RISK_FREE_RATE NameError in compute_backtest_metrics
- portfolio_data.py: momentum timing bug fixed (was 6 days stale)
- portfolio_data.py: sector caps added (40% sector, 30% stock) as SLSQP constraints
- portfolio_data.py: GOOGL + META removed from Technology (were duplicated)
- portfolio_data.py: chunked Polygon fetching (180-day windows) in _fetch_ohlcv
- portfolio_data.py: bootstrap-once + daily-append cache in fetch_portfolio_prices_cached
- portfolio_builder.py: final stock trim now uses precompute scores (was in-sample 2yr Sharpe)
- precompute.py: _meta timestamp stored in rankings, shown in UI

**Polygon free tier limitation discovered + mitigated:**
- Free tier caps at ~180 rows/request, 403 for data >18 months old
- All UI labels changed from "7 years" → "2 years"
- Stock Analysis date slider capped at 2Y (removed 5Y)
- All period_years defaults changed from 7→2 in portfolio_data.py + portfolio_builder.py
- validate.py: added warning at top about free tier requirement

**Legal + transparency:**
- disclaimers.py: created — 6 disclosure strings + HTML render helpers
- app.py, portfolio_builder.py, stress_test.py: disclaimers added throughout
- validate.py: created — 4 reference benchmark tests (SPY, 60/40, defensive sectors, SPY+QQQ)

---

## FUTURE PLANS (prioritized)

### Must-do before charging users
1. **Upgrade Polygon** (~$29/mo Starter) → run validate.py → confirm accuracy
   This unblocks everything else: longer backtests, rolling beta, historical drawdowns
2. **Fix waitlist email storage** — write to Supabase, not local CSV
3. **Terms of Service + Privacy Policy** — legal requirement before taking payments
4. **Re-enable Stripe** — flip DEV_MODE_FREE=False in constants.py once above done

### Data quality improvements (after Polygon upgrade)
5. Add data length guard — detect if fewer trading days than expected were fetched,
   show warning in UI rather than silently showing truncated metrics
6. Add chunked fetching to data.py (Stock Analysis tab) — currently single request
7. Extend precompute to 3-5 years once paid tier available — more robust Sharpe rankings
8. Re-run validate.py and confirm all 4 benchmark tests pass within tolerance

### Feature ideas discussed
9. Mid-day precompute refresh trigger on major news events (e.g. Fed announcements)
10. Upgrade from 2-year backtest to 5-year once Polygon paid — captures 2022 bear market
    and COVID crash for more realistic drawdown estimates in user portfolios

### Not doing yet (reasons)
- yfinance: Yahoo Finance ToS prohibits commercial use
- Alpha Vantage for validate.py: 25 req/day limit, decided to wait for Polygon upgrade
- Splitting payments launch from data validation: keeping them linked for safety

---

## PAYMENTS STATUS

Stripe is fully wired up but disabled. To re-enable:
1. Set `DEV_MODE_FREE = False` in constants.py
2. Set `SHOW_PRICING = True` in app.py
3. Ensure STRIPE_SECRET_KEY and STRIPE_PRICE_ID are set in Railway environment

Do NOT re-enable until: data validated (validate.py passes), ToS/PP pages exist.
