import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime

from constants import DEV_MODE_FREE
from disclaimers import render_inline, render_section
import disclaimers as _disc

from portfolio_data import (
    fetch_portfolio_prices, fetch_portfolio_prices_cached,
    build_candidate_universe, select_by_sharpe,
    get_ticker_info, get_sharpe_rankings,
    SECTOR_UNIVERSE, SECTOR_ETFS,
    BOND_UNIVERSE, BOND_ETFS,
)
from database import save_portfolio, load_portfolios, delete_portfolio
from portfolio_analysis import (
    compute_stock_metrics, compute_correlation_matrix,
    optimise_portfolio, generate_efficient_frontier,
    backtest_portfolio, compute_backtest_metrics,
    compute_monthly_heatmap, run_portfolio_monte_carlo,
    compute_diversification_score, get_rebalancing_recommendations
)
from portfolio_excel import build_portfolio_excel
from pptx_builder import build_portfolio_pptx, PPTX_AVAILABLE

DARK   = "#0f172a"
BLUE   = "#38bdf8"
GREEN  = "#16a34a"
RED    = "#dc2626"
AMBER  = "#f59e0b"
PURPLE = "#8b5cf6"
MUTED  = "#94a3b8"

ALL_SECTORS        = list(SECTOR_UNIVERSE.keys())
ALL_BOND_CATEGORIES = list(BOND_UNIVERSE.keys())

# Built once at import time — reused in every render
def _build_sector_lookup():
    lkp = {}
    for s, tl in SECTOR_UNIVERSE.items():
        for t in tl:
            lkp[t] = s
    for s, etf in SECTOR_ETFS.items():
        lkp[etf] = f"{s} ETF"
    for cat, tl in BOND_UNIVERSE.items():
        for t in tl:
            lkp[t] = "Bonds"
    bond_set = {t for tl in BOND_UNIVERSE.values() for t in tl}
    return lkp, bond_set

_SECTOR_LOOKUP, _BOND_SET = _build_sector_lookup()


def _metric_card(label, value, color=DARK, subtitle=None):
    sub = f"<div style='font-size:0.75rem;color:#64748b;margin-top:2px'>{subtitle}</div>" if subtitle else ""
    return f"""
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;
                padding:1.1rem;text-align:center">
        <div style="font-size:0.68rem;font-weight:600;letter-spacing:0.5px;
                    text-transform:uppercase;color:#64748b;margin-bottom:0.35rem">{label}</div>
        <div style="font-family:'DM Mono',monospace;font-size:1.25rem;
                    font-weight:500;color:{color}">{value}</div>
        {sub}
    </div>"""


def _section_header(text):
    st.markdown(f"""
    <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;
                color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                margin-bottom:1rem;margin-top:1.75rem">{text}</div>
    """, unsafe_allow_html=True)


def render_portfolio_builder(api_key, is_pro=False):
    """Main entry point — renders the full portfolio builder UI."""

    # DEV_MODE_FREE bypasses the Pro gate entirely.
    # Original paywall UI preserved below — do not delete.
    if not DEV_MODE_FREE and not is_pro:
        st.markdown("""
        <div style="background:#0f172a;border:1px solid #334155;border-radius:16px;
                    padding:2.5rem;text-align:center;margin:1rem 0">
            <div style="font-size:1.75rem;margin-bottom:0.75rem">📊</div>
            <div style="color:#fff;font-weight:600;font-size:1.2rem;margin-bottom:0.5rem">
                Portfolio Builder is a Pro Feature
            </div>
            <div style="color:#94a3b8;font-size:0.9rem;margin-bottom:1.5rem;
                        max-width:480px;margin-left:auto;margin-right:auto">
                Build custom portfolios with backtesting, efficient frontier optimisation,
                Monte Carlo simulation, Sharpe-ranked stock selection, and full Excel report export.
            </div>
            <div style="color:#38bdf8;font-size:1.1rem;font-weight:600">$9.99 / month</div>
            <div style="color:#64748b;font-size:0.8rem;margin-top:4px">Cancel anytime</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Upgrade to Pro", type="primary", key="upgrade_portfolio"):
            st.session_state["show_payment"] = True
            st.rerun()
        return
    
    st.markdown("""
    <div style="background:linear-gradient(135deg,#0f172a,#1e293b);border:1px solid #334155;
                border-radius:14px;padding:1.5rem 2rem;margin-bottom:1.5rem">
        <div style="font-family:'DM Mono',monospace;color:#38bdf8;font-size:1.1rem;
                    font-weight:500;margin-bottom:4px">◈ Portfolio Builder</div>
        <div style="color:#94a3b8;font-size:0.85rem">
            Custom portfolio construction · Backtest · Efficient frontier · Monte Carlo · Excel report
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Step indicator ─────────────────────────────────────────────────────────
    steps      = ["① Preferences", "② Universe", "③ Optimise", "④ Backtest", "⑤ Forecast"]
    curr_step  = st.session_state.get("port_step", 0)
    step_html  = "".join(
        f'<div style="flex:1;padding:8px 4px;border-radius:8px;text-align:center;font-size:11px;'
        f'{"background:#eff6ff;border:1px solid #93c5fd;color:#1d4ed8;font-weight:500" if i==curr_step else "background:#f8fafc;border:1px solid #e2e8f0;color:#64748b" if i>curr_step else "background:#f0fdf4;border:1px solid #86efac;color:#15803d;font-weight:500"}">'
        f'{s}</div>'
        for i, s in enumerate(steps))
    st.markdown(f'<div style="display:flex;gap:5px;margin-bottom:1.5rem">{step_html}</div>',
                unsafe_allow_html=True)

    # ── STEP 0 — Preferences ──────────────────────────────────────────────────
    if curr_step == 0:
        _section_header("Risk Profile")
        risk = st.slider(
            "Risk Tolerance",
            min_value=1, max_value=10, value=5,
            help="1 = Very conservative (capital preservation) · 10 = Very aggressive (maximum growth)"
        )
        risk_labels = {
            (1,3): ("🛡 Conservative","Capital preservation. Heavy bonds and defensive ETFs."),
            (4,6): ("⚖ Moderate",    "Balanced growth. Mix of growth stocks and stability."),
            (7,9): ("🚀 Aggressive",  "Maximum growth. Heavy equities, higher volatility."),
            (10,10):("⚡ Ultra Aggressive","All-in on high-growth equities. Significant risk."),
        }
        for (lo,hi),(label,desc) in risk_labels.items():
            if lo <= risk <= hi:
                st.markdown(f"""
                <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
                            padding:0.85rem 1rem;margin-top:0.5rem">
                    <span style="font-weight:600;color:{DARK}">{label}</span>
                    <span style="color:#64748b;font-size:0.85rem;margin-left:8px">{desc}</span>
                </div>""", unsafe_allow_html=True)

        _section_header("Investment Parameters")
        col1, col2 = st.columns(2)
        with col1:
            horizon = st.selectbox("Investment Horizon",
                ["1 year","3 years","5 years","10 years","20+ years"], index=2)
            starting_capital = st.number_input("Starting Capital ($)",
                min_value=1000, max_value=10_000_000, value=10000, step=1000,
                format="%d")
        with col2:
            monthly_contribution = st.number_input("Monthly Contribution ($)",
                min_value=0, max_value=100_000, value=500, step=100, format="%d")
            target_value = st.number_input("Target Goal ($) — optional",
                min_value=0, max_value=100_000_000, value=0, step=5000, format="%d",
                help="Leave at 0 if you don't have a specific goal")

        _section_header("Sector Preferences")
        st.markdown("<div style='font-size:0.85rem;color:#64748b;margin-bottom:0.75rem'>"
                    "Select sectors to include in your portfolio:</div>",
                    unsafe_allow_html=True)

        sector_grid = st.columns(3)
        included_sectors = []
        for i, sector in enumerate(ALL_SECTORS):
            with sector_grid[i % 3]:
                if st.checkbox(sector, value=True, key=f"sector_{sector}"):
                    included_sectors.append(sector)

        _section_header("Bond Preferences")
        st.markdown("<div style='font-size:0.85rem;color:#64748b;margin-bottom:0.75rem'>"
                    "Select bond categories to include in your portfolio:</div>",
                    unsafe_allow_html=True)

        # Default to including bonds for conservative/moderate risk profiles
        default_bond = risk <= 6
        bond_grid = st.columns(3)
        included_bond_categories = []
        for i, category in enumerate(ALL_BOND_CATEGORIES):
            with bond_grid[i % 3]:
                if st.checkbox(category, value=default_bond, key=f"bond_{category}"):
                    included_bond_categories.append(category)

        _section_header("Exclusions")
        col1, col2 = st.columns(2)
        with col1:
            exclude_tobacco  = st.checkbox("Exclude Tobacco stocks")
            exclude_defense  = st.checkbox("Exclude Defense stocks")
        with col2:
            exclude_fossil   = st.checkbox("Exclude Fossil Fuel stocks")
            exclude_gambling = st.checkbox("Exclude Gambling stocks")

        excluded_sectors = []
        if exclude_fossil:   excluded_sectors.append("Energy")
        if exclude_defense:  excluded_sectors.append("Industrials")

        st.markdown("---")
        if st.button("Next → Build Universe", type="primary", key="step0_next"):
            st.session_state["port_prefs"] = {
                "risk_tolerance":          risk,
                "horizon":                 horizon,
                "starting_capital":        starting_capital,
                "monthly_contribution":    monthly_contribution,
                "target_value":            target_value if target_value > 0 else None,
                "include_sectors":         included_sectors,
                "exclude_sectors":         excluded_sectors,
                "include_bond_categories": included_bond_categories,
                "user_tickers":            [],
                "exclude_tickers":         [],
            }
            st.session_state["port_step"] = 1
            st.rerun()

    # ── STEP 1 — Universe ─────────────────────────────────────────────────────
    elif curr_step == 1:
        prefs = st.session_state.get("port_prefs", {})

        _section_header("Add Your Own Stocks (Optional)")
        st.markdown("<div style='font-size:0.85rem;color:#64748b;margin-bottom:0.5rem'>"
                    "StockWizard will suggest a portfolio automatically. "
                    "You can optionally add tickers you specifically want included.</div>",
                    unsafe_allow_html=True)

        user_tickers_raw = st.text_input(
            "Tickers to INCLUDE (comma separated)",
            placeholder="e.g. AAPL, TSLA, NVDA",
            key="user_tickers_input"
        )
        exclude_tickers_raw = st.text_input(
            "Tickers to EXCLUDE (comma separated)",
            placeholder="e.g. META, AMZN",
            key="exclude_tickers_input"
        )

        _section_header("Portfolio Style")
        col1, col2 = st.columns(2)
        with col1:
            use_etfs = st.checkbox("Include Sector ETFs (XLK, XLV etc.)", value=True,
                                   help="ETFs provide broad sector exposure with lower volatility")
            dividend_pref = st.radio("Dividend Preference",
                ["Growth (no focus on dividends)","Balanced","High dividend yield"], index=0)
        with col2:
            max_per_stock = st.slider("Max weight per stock (%)", 5, 40, 25, step=5)
            min_holdings  = st.slider("Minimum number of holdings", 5, 20, 8, step=1)

        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("← Back", key="step1_back"):
                st.session_state["port_step"] = 0
                st.rerun()
        with col2:
            if st.button("Next → Optimise Portfolio", type="primary", key="step1_next"):
                user_tickers = [t.strip().upper() for t in user_tickers_raw.split(",") if t.strip()]
                excl_tickers = [t.strip().upper() for t in exclude_tickers_raw.split(",") if t.strip()]
                prefs["user_tickers"]    = user_tickers
                prefs["exclude_tickers"] = excl_tickers
                prefs["use_etfs"]        = use_etfs
                prefs["max_per_stock"]   = max_per_stock / 100
                prefs["min_holdings"]    = min_holdings
                st.session_state["port_prefs"] = prefs
                st.session_state["port_step"]  = 2
                st.rerun()

    # ── STEP 2 — Optimise ─────────────────────────────────────────────────────
    elif curr_step == 2:
        prefs = st.session_state.get("port_prefs", {})

        if "port_optimised" not in st.session_state:
            progress  = st.progress(0, text="Building candidate universe...")
            log_area  = st.empty()
            log_lines = []

            def log(msg):
                log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}]  {msg}")
                log_area.code("\n".join(log_lines[-10:]), language=None)

            try:
                from collections import defaultdict

                ALWAYS_KEEP    = {"SPY", "QQQ", "GLD", "TLT"}
                risk_tolerance = prefs.get("risk_tolerance", 5)
                excl_sectors   = set(prefs.get("exclude_sectors", []))
                incl_sectors   = set(prefs.get("include_sectors", list(SECTOR_UNIVERSE.keys())))
                excl_tickers   = set(t.upper() for t in prefs.get("exclude_tickers", []))
                user_tickers   = [t.upper() for t in prefs.get("user_tickers", [])]

                # ── Try pre-computed multi-factor rankings (considers ALL ~330 tickers) ──
                used_precompute = False
                rankings = get_sharpe_rankings(api_key)

                if rankings:
                    used_precompute = True
                    meta        = rankings.pop("_meta", {})
                    computed_at = meta.get("computed_at", "unknown")
                    is_partial  = meta.get("partial", False)
                    n_ranked    = len(rankings)
                    freshness   = f"{'⚠ partial — ' + str(meta.get('tickers_done','?')) + '/' + str(meta.get('tickers_total','?')) + ' tickers · ' if is_partial else ''}{computed_at}"
                    log(f"   ⚡ Pre-computed rankings loaded — {n_ranked} tickers · computed {freshness}")
                    progress.progress(10, text=f"Selecting best stocks from {n_ranked}-ticker universe...")

                    # Group by sector, respecting user preferences
                    sector_groups: dict = defaultdict(list)
                    for ticker, data in rankings.items():
                        if ticker in excl_tickers:
                            continue
                        sector = data.get("sector", "Unknown")
                        if sector in excl_sectors:
                            continue
                        if sector not in incl_sectors and sector not in {"Market", "Commodities"}:
                            continue
                        sector_groups[sector].append((ticker, data.get("score", data.get("sharpe", 0))))

                    # Conservative profile — skip growth sectors
                    GROWTH_SECTORS = {"Technology", "Consumer Discretionary",
                                      "Communication Services", "Financials"}
                    skipped_sectors = []
                    if risk_tolerance <= 3:
                        for gs in GROWTH_SECTORS:
                            if gs in sector_groups:
                                del sector_groups[gs]
                                skipped_sectors.append(gs)

                    if skipped_sectors:
                        st.info(f"Conservative profile: growth sectors excluded — "
                                f"{', '.join(skipped_sectors)}.")

                    # Pin benchmarks
                    pinned = ["SPY"]
                    if risk_tolerance >= 4:
                        pinned.append("QQQ")
                    if risk_tolerance <= 3:
                        pinned += ["GLD", "TLT"]

                    # Top 2 per sector by combined score
                    candidates = list(user_tickers)
                    for t in pinned:
                        if t not in candidates and t not in excl_tickers:
                            candidates.append(t)

                    for sector, ticker_scores in sector_groups.items():
                        ranked = sorted(ticker_scores, key=lambda x: x[1], reverse=True)
                        added  = 0
                        for t, _ in ranked:
                            if t not in candidates and added < 2:
                                candidates.append(t)
                                added += 1

                    # Build sector_map from rankings
                    sector_map = {t: rankings[t]["sector"] for t in candidates if t in rankings}
                    for t in pinned:
                        sector_map.setdefault(t, "Market")
                    for t in user_tickers:
                        sector_map.setdefault(t, "User")

                    log(f"   Selected {len(candidates)} candidates from full universe")
                else:
                    # ── Fallback: original candidate building (top 5 per sector) ──
                    log("   ℹ No pre-computed rankings — using live candidate building")
                    log("   Tip: run precompute.py daily to enable full-universe selection")
                    candidates, sector_map, skipped_sectors = build_candidate_universe(
                        prefs, api_key, log=log)
                    if skipped_sectors:
                        st.info(f"Conservative profile: growth sectors excluded — "
                                f"{', '.join(skipped_sectors)}.")

                progress.progress(15, text=f"Fetching 2-year price history for {len(candidates)} candidates...")

                # Fetch prices — uses Supabase cache if available (instant on repeat runs)
                price_dict, close_df, returns_df, failed = fetch_portfolio_prices_cached(
                    candidates, period_years=2, api_key=api_key, log=log)
                progress.progress(40, text="Finalising stock selection...")

                # Trim to 18 for optimizer.
                # When precompute was used: rank by precompute score (avoids in-sample bias).
                # Fallback: rank by 2-year Sharpe (only option when precompute unavailable).
                if used_precompute:
                    available = [t for t in candidates if t in returns_df.columns]
                    pinned_set = {t for t, s in sector_map.items()
                                  if s in ("Market", "Commodities", "User")}
                    best_tickers = sorted(
                        available,
                        key=lambda t: float('inf') if t in pinned_set
                                      else rankings.get(t, {}).get("score", 0),
                        reverse=True
                    )[:18]
                    log(f"   Final portfolio: {len(best_tickers)} stocks (precompute-ranked) — {', '.join(best_tickers)}")
                else:
                    best_tickers = select_by_sharpe(returns_df, sector_map,
                                                    max_total=18, top_n_per_sector=2)
                    log(f"   Final portfolio: {len(best_tickers)} stocks — {', '.join(best_tickers)}")
                returns_df = returns_df[best_tickers]
                close_df   = close_df[[t for t in best_tickers if t in close_df.columns]]
                price_dict = {t: v for t, v in price_dict.items() if t in best_tickers}
                sector_map = {t: sector_map.get(t, "Unknown") for t in best_tickers}
                progress.progress(50, text="Computing stock metrics...")

                # Metrics
                stock_metrics = compute_stock_metrics(returns_df)
                corr_matrix   = compute_correlation_matrix(returns_df)
                progress.progress(65, text="Running optimisation...")

                # Optimise
                target_ret = None
                if prefs.get("target_value") and prefs.get("starting_capital"):
                    horizon_map = {"1 year":1,"3 years":3,"5 years":5,"10 years":10,"20+ years":20}
                    yrs = horizon_map.get(prefs.get("horizon","5 years"), 5)
                    target_ret = ((prefs["target_value"]/prefs["starting_capital"])**(1/yrs)-1) if yrs > 0 else None

                portfolios = optimise_portfolio(returns_df,
                                                risk_tolerance=prefs.get("risk_tolerance", 5),
                                                target_return=target_ret,
                                                sector_map=sector_map)
                progress.progress(80, text="Generating efficient frontier...")

                ef_df = generate_efficient_frontier(returns_df, n_portfolios=8000)

                # Get ticker info — parallel fetches
                from concurrent.futures import ThreadPoolExecutor, as_completed
                ticker_info = {}
                with ThreadPoolExecutor(max_workers=5) as _exe:
                    _futs = {_exe.submit(get_ticker_info, t, api_key): t for t in returns_df.columns}
                    for _fut in as_completed(_futs):
                        _t = _futs[_fut]
                        try:
                            ticker_info[_t] = _fut.result()
                        except Exception:
                            ticker_info[_t] = {"name": _t, "sector": "Unknown", "exchange": "", "market_cap": 0}

                progress.progress(95, text="Computing diversification score...")
                recommended_weights = portfolios["recommended"]
                div_score = compute_diversification_score(recommended_weights, returns_df)

                progress.progress(100, text="Done!")
                log_area.empty()
                progress.empty()

                if not portfolios.get("target_met", True):
                    st.warning(
                        f"Your target return of **{portfolios['target_requested']}%/yr** "
                        f"could not be achieved with the selected tickers. "
                        f"The best achievable is **{portfolios['target_achieved']}%/yr**. "
                        f"The recommended portfolio has been optimised for the best "
                        f"risk-adjusted return instead."
                    )

                st.session_state["port_optimised"] = {
                    "price_dict":    price_dict,
                    "close_df":      close_df,
                    "returns_df":    returns_df,
                    "stock_metrics": stock_metrics,
                    "corr_matrix":   corr_matrix,
                    "portfolios":    portfolios,
                    "ef_df":         ef_df,
                    "ticker_info":   ticker_info,
                    "div_score":     div_score,
                    "failed":        failed,
                }

            except Exception as e:
                progress.empty()
                log_area.empty()
                err_str = str(e).lower()
                if "rate limit" in err_str or "429" in err_str:
                    st.warning(
                        "⏳ **Market data API is busy right now.** "
                        "Wait 30 seconds and try again — this happens during peak hours."
                    )
                elif "api key" in err_str or "missing" in err_str:
                    st.error("❌ API key error — contact support.")
                elif "no valid price" in err_str or "all" in err_str and "failed" in err_str:
                    st.error(
                        "❌ Could not fetch price data for the selected tickers. "
                        "Check that your tickers are valid US stock symbols."
                    )
                else:
                    st.error(f"❌ Something went wrong: {e}")
                    st.exception(e)
                if st.button("← Back", key="step2_err_back"):
                    st.session_state["port_step"] = 1
                    del st.session_state["port_optimised"]
                    st.rerun()
                return

        opt = st.session_state["port_optimised"]
        portfolios    = opt["portfolios"]
        ef_df         = opt["ef_df"]
        stock_metrics = opt["stock_metrics"]
        corr_matrix   = opt["corr_matrix"]
        div_score     = opt["div_score"]
        ticker_info   = opt["ticker_info"]

        if opt.get("failed"):
            st.warning(f"⚠ Could not load data for: {', '.join(opt['failed'])} — excluded from analysis")

        # Portfolio selector
        _section_header("Choose Your Portfolio")
        port_choice = st.radio("Optimisation strategy:", [
            "Recommended (risk-adjusted for your profile)",
            "Maximum Sharpe Ratio (best risk/return)",
            "Minimum Volatility (lowest risk)",
        ], index=0, key="port_choice")

        choice_map = {
            "Recommended (risk-adjusted for your profile)": "recommended",
            "Maximum Sharpe Ratio (best risk/return)":      "max_sharpe",
            "Minimum Volatility (lowest risk)":             "min_vol",
        }
        selected_key     = choice_map[port_choice]
        selected_weights = portfolios[selected_key]

        # Clean weights — remove tiny allocations (<1%) and warn user
        dropped = [k for k, v in selected_weights.items() if v < 0.01]
        selected_weights = {k: v for k, v in selected_weights.items() if v >= 0.01}
        total = sum(selected_weights.values())
        selected_weights = {k: v/total for k, v in selected_weights.items()}
        if dropped:
            st.info(f"{len(dropped)} position(s) with weight <1% were removed by the optimizer "
                    f"and excluded from the portfolio: {', '.join(dropped)}")

        # Portfolio metrics
        returns_df = opt["returns_df"]
        ann_ret, ann_vol, sharpe = 0, 0, 0
        tickers_in = [t for t in selected_weights if t in returns_df.columns]
        if tickers_in:
            w_arr   = np.array([selected_weights[t] for t in tickers_in])
            w_arr  /= w_arr.sum()
            ann_ret = returns_df[tickers_in].mean().values @ w_arr * 252 * 100
            cov     = returns_df[tickers_in].cov().values * 252
            ann_vol = np.sqrt(w_arr @ cov @ w_arr) * 100
            sharpe  = ann_ret / ann_vol if ann_vol > 0 else 0

        _section_header("Portfolio Overview")
        cols = st.columns(4)
        for col, label, value, color in [
            (cols[0], "Expected Ann. Return", f"{ann_ret:.1f}%",  GREEN if ann_ret > 0 else RED),
            (cols[1], "Expected Volatility",  f"{ann_vol:.1f}%",  DARK),
            (cols[2], "Sharpe Ratio",         f"{sharpe:.2f}",    GREEN if sharpe > 1 else AMBER),
            (cols[3], "Diversification",      f"{div_score}/10",  GREEN if div_score > 6 else AMBER),
        ]:
            with col:
                st.markdown(_metric_card(label, value, color), unsafe_allow_html=True)

        with st.expander("ℹ️ About these numbers — methodology & assumptions"):
            st.markdown("""
**Expected Ann. Return** — Arithmetic mean of daily returns × 252, based on 2 years of historical data.
Past returns do not guarantee future performance.

**Expected Volatility** — Annualised standard deviation of daily returns over the 2-year window.
Higher volatility = wider range of possible outcomes.

**Sharpe Ratio** — Excess return above the risk-free rate (4.5%) divided by volatility.
A Sharpe above 1.0 is generally considered good. Above 2.0 is exceptional.
Calculated using 2 years of historical data — short-term market conditions may differ.

**Diversification Score** — Proprietary 1–10 score combining:
effective number of holdings (Herfindahl index), average pairwise correlation, and
concentration penalty for any single position above 25%.

**Important caveats:**
- All metrics are based on historical data and will not perfectly predict future returns
- Survivorship bias: the universe only includes stocks that still exist today
- Maximum weight per stock is capped at 40% to prevent over-concentration
- Stock selection uses a multi-factor score: Sharpe (50%) + 6-month momentum (30%) + 3-month momentum (20%)
            """)

        # ── Side-by-Side Portfolio Strategy Comparison ───────────────────────
        _section_header("Compare Portfolio Strategies")
        _cmp_rows = []
        for _ck, _clabel in [("recommended","Recommended"),("max_sharpe","Max Sharpe"),("min_vol","Min Volatility")]:
            _cw = portfolios.get(_ck, {})
            if not _cw:
                continue
            _cw = {k: v for k, v in _cw.items() if v >= 0.01}
            _ct = sum(_cw.values())
            _cw = {k: v/_ct for k, v in _cw.items()}
            _ct2 = [t for t in _cw if t in returns_df.columns]
            if not _ct2:
                continue
            _cwa = np.array([_cw[t] for t in _ct2]); _cwa /= _cwa.sum()
            _car  = returns_df[_ct2].mean().values @ _cwa * 252 * 100
            _ccov = returns_df[_ct2].cov().values * 252
            _cvol = np.sqrt(_cwa @ _ccov @ _cwa) * 100
            _csh  = _car / _cvol if _cvol > 0 else 0
            _ccum = (1 + (returns_df[_ct2] @ _cwa)).cumprod()
            _cdd  = ((_ccum - _ccum.cummax()) / _ccum.cummax()).min() * 100
            _top_t = max(_cw, key=_cw.get)
            _cmp_rows.append({"key": _ck, "label": _clabel,
                "ann_ret": _car, "vol": _cvol, "sharpe": _csh,
                "max_dd": _cdd, "holdings": len(_cw),
                "top": f"{_top_t} ({_cw[_top_t]*100:.0f}%)"})

        if _cmp_rows:
            _cmp_cols = st.columns(len(_cmp_rows))
            for _ccol, _crow in zip(_cmp_cols, _cmp_rows):
                _is_sel  = (_crow["key"] == selected_key)
                _cborder = f"2px solid {BLUE}" if _is_sel else "1px solid #e2e8f0"
                _cbg     = "#eff6ff" if _is_sel else "#ffffff"
                _clbl    = ("✓ " if _is_sel else "") + _crow["label"]
                with _ccol:
                    st.markdown(f"""
                    <div style="background:{_cbg};border:{_cborder};border-radius:8px;
                                padding:1rem;text-align:center">
                        <div style="font-size:0.68rem;font-weight:700;letter-spacing:1px;
                                    color:{BLUE if _is_sel else "#64748b"};text-transform:uppercase;
                                    margin-bottom:0.75rem">{_clbl}</div>
                        <div style="font-size:1.5rem;font-weight:700;
                                    color:{GREEN if _crow['ann_ret']>0 else RED}">
                            {_crow['ann_ret']:+.1f}%</div>
                        <div style="font-size:0.68rem;color:#64748b;margin-bottom:0.6rem">Ann. Return</div>
                        <div style="font-size:0.82rem;color:#0f172a;margin-bottom:2px">
                            {_crow['sharpe']:.2f} Sharpe</div>
                        <div style="font-size:0.82rem;color:#0f172a;margin-bottom:2px">
                            {_crow['vol']:.1f}% Volatility</div>
                        <div style="font-size:0.82rem;color:{RED};margin-bottom:0.5rem">
                            {_crow['max_dd']:.1f}% Max DD</div>
                        <div style="font-size:0.7rem;color:#64748b">{_crow['holdings']} holdings</div>
                        <div style="font-size:0.68rem;color:#64748b">{_crow['top']}</div>
                    </div>""", unsafe_allow_html=True)

        # Holdings table
        _section_header("Suggested Holdings")
        holdings_data = []
        for ticker, weight in sorted(selected_weights.items(), key=lambda x: x[1], reverse=True):
            m    = stock_metrics.get(ticker, {})
            info = ticker_info.get(ticker, {})
            holdings_data.append({
                "Ticker":        ticker,
                "Company":       info.get("name", ticker)[:25],
                "Weight":        f"{weight*100:.1f}%",
                "Ann. Return":   f"{m.get('ann_return',0):.1f}%",
                "Volatility":    f"{m.get('ann_vol',0):.1f}%",
                "Sharpe":        f"{m.get('sharpe',0):.2f}",
                "Max Drawdown":  f"{m.get('max_drawdown',0):.1f}%",
            })
        st.dataframe(pd.DataFrame(holdings_data), use_container_width=True, hide_index=True)

        # ── Explainability Panel ──────────────────────────────────────────────
        _section_header("Why These Holdings Were Selected")
        _sec_lookup = _SECTOR_LOOKUP

        for _et, _ew in sorted(selected_weights.items(), key=lambda x: x[1], reverse=True):
            _em  = stock_metrics.get(_et, {})
            _ese = _sec_lookup.get(_et, "Portfolio")
            _esh = _em.get("sharpe", 0)
            _ear = _em.get("ann_return", 0)
            _eav = _em.get("ann_vol", 0)
            _esc = GREEN if _esh >= 1 else AMBER if _esh >= 0.5 else RED
            st.markdown(f"""
            <div style="background:#f8fafc;border:1px solid #e2e8f0;
                        border-left:3px solid {BLUE};border-radius:6px;
                        padding:0.6rem 1rem;margin-bottom:0.4rem;
                        display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem">
                <div>
                    <span style="font-weight:700;color:#0f172a;font-size:0.9rem">{_et}</span>
                    <span style="font-size:0.72rem;color:#64748b;margin-left:8px;
                                background:#f1f5f9;padding:1px 7px;border-radius:3px">{_ese}</span>
                </div>
                <div style="font-size:0.8rem;color:#64748b">
                    <b style="color:{GREEN if _ear>0 else RED}">{_ear:.1f}%</b> return &nbsp;·&nbsp;
                    <b style="color:#0f172a">{_eav:.1f}%</b> vol &nbsp;·&nbsp;
                    <b style="color:{_esc}">Sharpe {_esh:.2f}</b> &nbsp;·&nbsp;
                    <b style="color:{BLUE}">{_ew*100:.1f}% weight</b>
                </div>
                <div style="font-size:0.7rem;color:#64748b;font-style:italic">
                    Top-ranked by Sharpe in {_ese}
                </div>
            </div>""", unsafe_allow_html=True)

        # Allocation pie chart
        col1, col2 = st.columns(2)
        with col1:
            fig_pie = go.Figure(go.Pie(
                labels=list(selected_weights.keys()),
                values=[round(v*100,1) for v in selected_weights.values()],
                hole=0.45,
                textinfo="label+percent",
                marker=dict(colors=px.colors.qualitative.Set3),
            ))
            fig_pie.update_layout(
                title="Portfolio Allocation",
                height=380,
                margin=dict(l=0,r=0,t=40,b=0),
                showlegend=False,
                font=dict(family="DM Sans"),
            )
            st.plotly_chart(fig_pie, use_container_width=True)

        with col2:
            # Efficient frontier
            fig_ef = go.Figure()
            fig_ef.add_trace(go.Scatter(
                x=ef_df["Volatility"], y=ef_df["Return"],
                mode="markers",
                marker=dict(color=ef_df["Sharpe"], colorscale="RdYlGn",
                            size=4, opacity=0.6,
                            colorbar=dict(title="Sharpe")),
                name="Random Portfolios",
                hovertemplate="Vol: %{x:.1f}%<br>Return: %{y:.1f}%<extra></extra>",
            ))
            fig_ef.add_trace(go.Scatter(
                x=[ann_vol], y=[ann_ret],
                mode="markers",
                marker=dict(color=BLUE, size=14, symbol="star",
                            line=dict(color=DARK, width=1.5)),
                name="Your Portfolio",
            ))
            fig_ef.update_layout(
                title="Efficient Frontier",
                xaxis_title="Volatility (%)",
                yaxis_title="Expected Return (%)",
                height=380,
                template="plotly_white",
                margin=dict(l=0,r=0,t=40,b=0),
                font=dict(family="DM Sans"),
            )
            st.plotly_chart(fig_ef, use_container_width=True)

        # Correlation heatmap
        _section_header("Correlation Between Holdings")
        tickers_show = list(selected_weights.keys())
        corr_show    = corr_matrix.loc[
            [t for t in tickers_show if t in corr_matrix.index],
            [t for t in tickers_show if t in corr_matrix.columns]
        ]
        if not corr_show.empty:
            fig_corr = px.imshow(corr_show, text_auto=".2f",
                                 color_continuous_scale=["#dc2626","#ffffff","#0ea5e9"],
                                 zmin=-1, zmax=1, aspect="auto")
            fig_corr.update_layout(height=320, margin=dict(l=0,r=0,t=10,b=0),
                                   font=dict(family="DM Sans"))
            st.plotly_chart(fig_corr, use_container_width=True)

        # ── Sector Exposure vs. S&P 500 Benchmark ────────────────────────────
        _section_header("Sector Exposure vs. S&P 500")
        _SP500_SECTOR_W = {
            "Technology": 29.5, "Health Care": 12.8, "Financials": 13.2,
            "Consumer Discretionary": 10.4, "Communication Services": 8.6,
            "Industrials": 8.9, "Consumer Staples": 6.2, "Energy": 3.9,
            "Utilities": 2.5, "Materials": 2.3, "Real Estate": 2.2,
        }
        _sec_map2 = _SECTOR_LOOKUP
        _bond_set = _BOND_SET
        _port_sectors = {}
        for _t, _w in selected_weights.items():
            _s = _sec_map2.get(_t, "Bonds" if _t in _bond_set else "Other")
            if _s in _SP500_SECTOR_W or _s == "Bonds":
                _port_sectors[_s] = _port_sectors.get(_s, 0) + _w * 100

        _sec_labels = sorted(set(list(_SP500_SECTOR_W.keys()) + list(_port_sectors.keys())))
        _pv = [round(_port_sectors.get(s, 0), 1) for s in _sec_labels]
        _sv = [_SP500_SECTOR_W.get(s, 0) for s in _sec_labels]

        fig_sec = go.Figure()
        fig_sec.add_trace(go.Bar(name="Your Portfolio", x=_sec_labels, y=_pv,
            marker_color=BLUE, text=[f"{v:.1f}%" for v in _pv],
            textposition="outside", textfont=dict(size=9)))
        fig_sec.add_trace(go.Bar(name="S&P 500", x=_sec_labels, y=_sv,
            marker_color=MUTED, opacity=0.55,
            text=[f"{v:.1f}%" for v in _sv],
            textposition="outside", textfont=dict(size=9)))
        fig_sec.update_layout(
            barmode="group", height=360, template="plotly_white",
            margin=dict(l=0, r=0, t=30, b=100),
            yaxis=dict(title="Weight (%)", ticksuffix="%"),
            xaxis=dict(tickangle=-30),
            legend=dict(orientation="h", y=1.06),
            font=dict(family="DM Sans", size=11),
        )
        st.plotly_chart(fig_sec, use_container_width=True)

        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("← Back", key="step2_back"):
                st.session_state["port_step"] = 1
                if "port_optimised" in st.session_state:
                    del st.session_state["port_optimised"]
                st.rerun()
        with col2:
            if st.button("Next → Run Backtest", type="primary", key="step2_next"):
                st.session_state["port_selected_weights"] = selected_weights
                st.session_state["port_step"] = 3
                st.rerun()

    # ── STEP 3 — Backtest ─────────────────────────────────────────────────────
    elif curr_step == 3:
        prefs   = st.session_state.get("port_prefs", {})
        opt     = st.session_state.get("port_optimised", {})
        weights = st.session_state.get("port_selected_weights", {})

        if "port_backtest" not in st.session_state:
            with st.spinner("Running backtest..."):
                try:
                    close_df  = opt["close_df"]
                    start_cap = prefs.get("starting_capital", 10000)
                    monthly   = prefs.get("monthly_contribution", 500)

                    backtest_df      = backtest_portfolio(close_df, weights, start_cap, monthly)
                    backtest_metrics = compute_backtest_metrics(backtest_df, start_cap)
                    heatmap_df       = compute_monthly_heatmap(backtest_df)

                    st.session_state["port_backtest"] = {
                        "df":      backtest_df,
                        "metrics": backtest_metrics,
                        "heatmap": heatmap_df,
                    }
                except Exception as e:
                    st.error(f"❌ Backtest failed: {e}")
                    st.exception(e)
                    return

        bt     = st.session_state["port_backtest"]
        bt_df  = bt["df"]
        bt_met = bt["metrics"]
        hmap   = bt["heatmap"]
        prefs  = st.session_state.get("port_prefs", {})
        start_cap = prefs.get("starting_capital", 10000)

        # Key metrics
        _section_header("Backtest Results")
        cols = st.columns(4)
        gain = bt_met.get("Total Gain/Loss", 0)
        for col, label, value, color in [
            (cols[0], "Final Value",      f"${bt_met.get('Final Value',0):,.0f}",    GREEN),
            (cols[1], "Total Return",     f"{bt_met.get('Total Return',0):.1f}%",    GREEN if bt_met.get("Total Return",0)>0 else RED),
            (cols[2], "vs S&P 500",       f"{bt_met.get('vs S&P 500',0):.1f}%" if isinstance(bt_met.get('vs S&P 500'), float) else "N/A", GREEN if isinstance(bt_met.get('vs S&P 500'), float) and bt_met.get('vs S&P 500',0)>0 else RED),
            (cols[3], "Sharpe Ratio",     f"{bt_met.get('Sharpe Ratio',0):.2f}",     GREEN if bt_met.get("Sharpe Ratio",0)>1 else AMBER),
        ]:
            with col:
                st.markdown(_metric_card(label, value, color), unsafe_allow_html=True)

        st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)
        cols2 = st.columns(4)
        for col, label, value, color in [
            (cols2[0], "Ann. Return",      f"{bt_met.get('Ann. Return',0):.1f}%",     GREEN if bt_met.get("Ann. Return",0)>0 else RED),
            (cols2[1], "Max Drawdown",     f"{bt_met.get('Max Drawdown',0):.1f}%",    AMBER),
            (cols2[2], "Best Month",       f"{bt_met.get('Best Month',0):.1f}%",      GREEN),
            (cols2[3], "% Months Positive",f"{bt_met.get('% Months Positive',0):.0f}%",GREEN if bt_met.get('% Months Positive',0)>50 else RED),
        ]:
            with col:
                st.markdown(_metric_card(label, value, color), unsafe_allow_html=True)

        # Portfolio vs S&P vs Contributions chart
        _section_header("Portfolio Growth vs Benchmark")
        fig_bt = go.Figure()
        fig_bt.add_trace(go.Scatter(
            x=bt_df.index, y=bt_df["Portfolio"],
            name="Your Portfolio", line=dict(color=BLUE, width=2.5)))
        if "SP500" in bt_df.columns and not bt_df["SP500"].isna().all():
            fig_bt.add_trace(go.Scatter(
                x=bt_df.index, y=bt_df["SP500"],
                name="S&P 500 (SPY)", line=dict(color=MUTED, width=1.5, dash="dot")))
        fig_bt.add_trace(go.Scatter(
            x=bt_df.index, y=bt_df["Contrib"],
            name="Total Contributed", line=dict(color=AMBER, width=1.5, dash="dash")))
        fig_bt.update_layout(
            height=380, template="plotly_white",
            margin=dict(l=0,r=0,t=10,b=0),
            legend=dict(orientation="h",yanchor="bottom",y=1.02),
            yaxis=dict(tickprefix="$"),
            font=dict(family="DM Sans"),
        )
        st.plotly_chart(fig_bt, use_container_width=True)

        # Drawdown chart
        _section_header("Drawdown from Peak")
        port     = bt_df["Portfolio"]
        peak     = port.cummax()
        drawdown = (port - peak) / peak * 100
        fig_dd   = go.Figure()
        fig_dd.add_trace(go.Scatter(
            x=bt_df.index, y=drawdown,
            fill="tozeroy", fillcolor="rgba(220,38,38,0.12)",
            line=dict(color=RED, width=1.5), name="Drawdown"))
        fig_dd.update_layout(
            height=220, template="plotly_white",
            margin=dict(l=0,r=0,t=10,b=0),
            yaxis=dict(ticksuffix="%"),
            font=dict(family="DM Sans"),
        )
        st.plotly_chart(fig_dd, use_container_width=True)

        # ── Historical Stress Tests ───────────────────────────────────────────
        _section_header("Historical Stress Tests")
        _STRESS_PERIODS = [
            ("2022 Rate Hike Selloff", "2022-01-03", "2022-10-14", "Fed +425bps; S&P fell 25%",   -24.5),
            ("COVID-19 Crash",          "2020-02-19", "2020-03-23", "Market fell 34% in 33 days", -33.9),
            ("Q4 2018 Selloff",         "2018-09-20", "2018-12-24", "Trade war & rate fears",      -19.8),
        ]
        _stress_rows = []
        for _sn, _ss, _se, _sdesc, _known_sp in _STRESS_PERIODS:
            _ss_dt, _se_dt = pd.Timestamp(_ss), pd.Timestamp(_se)
            _bt_min, _bt_max = bt_df.index.min(), bt_df.index.max()
            if _ss_dt >= _bt_min and _se_dt <= _bt_max:
                _psl = bt_df.loc[_ss_dt:_se_dt, "Portfolio"]
                if len(_psl) > 1:
                    _pr = (_psl.iloc[-1] / _psl.iloc[0] - 1) * 100
                    _sr = None
                    if "SP500" in bt_df.columns:
                        _ssl = bt_df.loc[_ss_dt:_se_dt, "SP500"].dropna()
                        if len(_ssl) > 1:
                            _sr = (_ssl.iloc[-1] / _ssl.iloc[0] - 1) * 100
                    _stress_rows.append({"name": _sn, "desc": _sdesc,
                        "port": round(_pr, 1), "sp": round(_sr, 1) if _sr else None,
                        "vs": round(_pr - _sr, 1) if _sr else None, "est": False})
            else:
                _p_ret = bt_df["Portfolio"].pct_change().dropna()
                _s_ret = bt_df.get("SP500", pd.Series()).pct_change().dropna() if "SP500" in bt_df.columns else pd.Series()
                _al = pd.concat([_p_ret.rename("p"), _s_ret.rename("s")], axis=1).dropna()
                if len(_al) > 20:
                    _beta = np.cov(_al["p"], _al["s"])[0,1] / max(np.var(_al["s"]), 1e-10)
                    _est  = round(_beta * _known_sp, 1)
                    _stress_rows.append({"name": _sn, "desc": _sdesc,
                        "port": _est, "sp": _known_sp,
                        "vs": round(_est - _known_sp, 1), "est": True})

        if _stress_rows:
            _scols = st.columns(len(_stress_rows))
            for _scol, _sr in zip(_scols, _stress_rows):
                _pc = RED if _sr["port"] < 0 else GREEN
                _badge = " ·&nbsp;estimated" if _sr["est"] else " ·&nbsp;actual"
                with _scol:
                    st.markdown(f"""
                    <div style="background:#ffffff;border:1px solid #e2e8f0;
                                border-radius:8px;padding:1rem;text-align:center">
                        <div style="font-size:0.67rem;font-weight:700;color:#64748b;
                                    text-transform:uppercase;letter-spacing:0.8px;
                                    margin-bottom:0.5rem">{_sr['name']}</div>
                        <div style="font-size:1.6rem;font-weight:700;color:{_pc}">
                            {_sr['port']:+.1f}%</div>
                        <div style="font-size:0.67rem;color:#64748b;margin-bottom:0.4rem">
                            Portfolio{_badge}</div>
                        {f'<div style="font-size:0.82rem;color:#0f172a">vs S&amp;P:&nbsp;<b style="color:{GREEN if (_sr["vs"] or 0)>0 else RED}">{_sr["vs"]:+.1f}%</b></div>' if _sr["vs"] is not None else ""}
                        <div style="font-size:0.67rem;color:#64748b;margin-top:0.4rem;
                                    font-style:italic">{_sr['desc']}</div>
                    </div>""", unsafe_allow_html=True)
        else:
            st.info("Not enough backtest history for stress testing.")

        # Monthly heatmap
        _section_header("Monthly Returns Heatmap")
        if hmap is not None and not hmap.empty:
            month_names = ["Jan","Feb","Mar","Apr","May","Jun",
                           "Jul","Aug","Sep","Oct","Nov","Dec"]
            hmap = hmap.copy()
            hmap.columns = [month_names[int(c)-1] if str(c).isdigit() and int(c) <= 12 else str(c) for c in hmap.columns]
            fig_hmap = px.imshow(
                hmap.fillna(0),
                text_auto=".1f",
                color_continuous_scale=["#dc2626","#ffffff","#16a34a"],
                zmin=-15, zmax=15,
                aspect="auto",
            )
            fig_hmap.update_layout(
                height=max(200, len(hmap)*35 + 60),
                margin=dict(l=0,r=0,t=10,b=0),
                coloraxis_showscale=False,
                font=dict(family="DM Sans"),
            )
            fig_hmap.update_traces(textfont_size=9)
            st.plotly_chart(fig_hmap, use_container_width=True)

        # ── Rolling Performance Metrics ───────────────────────────────────────
        _section_header("Rolling Performance Metrics")
        _port_ret_full = bt_df["Portfolio"].pct_change().dropna()
        _roll_w = min(252, max(60, len(_port_ret_full) // 3))

        _rc1, _rc2, _rc3 = st.columns(3)

        with _rc1:
            _roll_vol = (_port_ret_full.rolling(60).std() * np.sqrt(252) * 100).dropna()
            _fig_rvol = go.Figure(go.Scatter(
                x=_roll_vol.index, y=_roll_vol,
                fill="tozeroy", fillcolor="rgba(245,158,11,0.08)",
                line=dict(color=AMBER, width=2),
                hovertemplate="Vol: %{y:.1f}%<extra></extra>",
            ))
            _fig_rvol.update_layout(
                title=dict(text="Rolling 60D Volatility (%)", font=dict(size=12)),
                height=230, template="plotly_white",
                margin=dict(l=10, r=10, t=36, b=30),
                yaxis=dict(ticksuffix="%", gridcolor="#f1f5f9"),
                xaxis=dict(gridcolor="#f1f5f9"),
                font=dict(family="DM Sans", size=10),
                showlegend=False,
            )
            st.plotly_chart(_fig_rvol, use_container_width=True)

        with _rc2:
            _rret = _port_ret_full.rolling(_roll_w).mean() * 252 * 100
            _rvol = _port_ret_full.rolling(_roll_w).std() * np.sqrt(252) * 100
            _rsh  = (_rret / _rvol.replace(0, np.nan)).dropna()
            _sh_colors = [GREEN if v >= 1 else AMBER if v >= 0 else RED for v in _rsh]
            _fig_rsh = go.Figure(go.Scatter(
                x=_rsh.index, y=_rsh,
                line=dict(color=BLUE, width=2),
                hovertemplate="Sharpe: %{y:.2f}<extra></extra>",
            ))
            _fig_rsh.add_hline(y=1.0, line_dash="dot", line_color=GREEN, opacity=0.5,
                               annotation_text="1.0", annotation_font_size=9)
            _fig_rsh.add_hline(y=0.0, line_dash="dot", line_color=RED, opacity=0.4)
            _fig_rsh.update_layout(
                title=dict(text=f"Rolling {_roll_w//21}M Sharpe Ratio", font=dict(size=12)),
                height=230, template="plotly_white",
                margin=dict(l=10, r=10, t=36, b=30),
                yaxis=dict(gridcolor="#f1f5f9"),
                xaxis=dict(gridcolor="#f1f5f9"),
                font=dict(family="DM Sans", size=10),
                showlegend=False,
            )
            st.plotly_chart(_fig_rsh, use_container_width=True)

        with _rc3:
            _sp_col = "SP500"
            if _sp_col in bt_df.columns and not bt_df[_sp_col].isna().all():
                _sp_ret = bt_df[_sp_col].pct_change().dropna()
                _aligned = pd.concat([_port_ret_full.rename("port"),
                                      _sp_ret.rename("sp")], axis=1).dropna()
                if len(_aligned) > _roll_w:
                    _rcorr = _aligned["port"].rolling(_roll_w).corr(_aligned["sp"]).dropna()
                    _fig_rc = go.Figure(go.Scatter(
                        x=_rcorr.index, y=_rcorr,
                        fill="tozeroy", fillcolor="rgba(139,92,246,0.08)",
                        line=dict(color=PURPLE, width=2),
                        hovertemplate="Corr: %{y:.2f}<extra></extra>",
                    ))
                    _fig_rc.add_hline(y=0.7, line_dash="dot", line_color=AMBER, opacity=0.5,
                                      annotation_text="0.7 high", annotation_font_size=9)
                    _fig_rc.update_layout(
                        title=dict(text="Rolling Correlation vs S&P 500", font=dict(size=12)),
                        height=230, template="plotly_white",
                        margin=dict(l=10, r=10, t=36, b=30),
                        yaxis=dict(range=[-1.1, 1.1], gridcolor="#f1f5f9"),
                        xaxis=dict(gridcolor="#f1f5f9"),
                        font=dict(family="DM Sans", size=10),
                        showlegend=False,
                    )
                    st.plotly_chart(_fig_rc, use_container_width=True)
                else:
                    st.info("Not enough data for rolling correlation.")
            else:
                st.info("S&P 500 benchmark data unavailable.")

        # ── Holdings Return Attribution ────────────────────────────────────────
        _section_header("Holdings Return Attribution")
        _weights_for_attr = st.session_state.get("port_selected_weights", weights)
        _sm_attr          = opt.get("stock_metrics", {})
        _attr_rows = []
        for _at, _aw in sorted(_weights_for_attr.items(), key=lambda x: x[1], reverse=True):
            _m = _sm_attr.get(_at, {})
            _ar = _m.get("ann_return", 0)
            _contrib = _aw * _ar
            _attr_rows.append({
                "Ticker":         _at,
                "Weight":         _aw,
                "Ann. Return (%)":  round(_ar, 2),
                "Contribution (%)": round(_contrib, 2),
            })
        if _attr_rows:
            _attr_df = pd.DataFrame(_attr_rows).sort_values("Contribution (%)")
            _attr_colors = [GREEN if v >= 0 else RED for v in _attr_df["Contribution (%)"]]
            _fig_attr = go.Figure(go.Bar(
                x=_attr_df["Contribution (%)"],
                y=_attr_df["Ticker"],
                orientation="h",
                marker_color=_attr_colors,
                text=[f"{v:+.2f}%" for v in _attr_df["Contribution (%)"]],
                textposition="outside",
                customdata=np.stack([_attr_df["Weight"]*100, _attr_df["Ann. Return (%)"]], axis=-1),
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "Weight: %{customdata[0]:.1f}%<br>"
                    "Ann. Return: %{customdata[1]:.2f}%<br>"
                    "Contribution: %{x:+.2f}%<extra></extra>"
                ),
            ))
            _fig_attr.update_layout(
                title=dict(text="Weighted Return Contribution by Holding",
                           font=dict(size=13)),
                height=max(260, len(_attr_rows) * 38 + 60),
                template="plotly_white",
                margin=dict(l=60, r=80, t=44, b=30),
                xaxis=dict(title="Contribution to Portfolio Return (%)",
                           ticksuffix="%", gridcolor="#f1f5f9"),
                yaxis=dict(gridcolor="#f1f5f9"),
                font=dict(family="DM Sans"),
                showlegend=False,
            )
            st.plotly_chart(_fig_attr, use_container_width=True)

        # Rebalancing recommendations
        _section_header("Rebalancing Recommendations")
        weights = st.session_state.get("port_selected_weights", {})
        opt     = st.session_state.get("port_optimised", {})
        close_df_rb = opt.get("close_df")
        if weights and close_df_rb is not None:
            latest_prices = {t: float(close_df_rb[t].iloc[-1])
                             for t in weights if t in close_df_rb.columns}
            # Use actual starting capital from user preferences
            _rb_capital = prefs.get("starting_capital", 10000)
            current_holdings = {t: (_rb_capital * weights[t]) / latest_prices[t]
                                for t in weights if t in latest_prices and latest_prices[t] > 0}
            recs = get_rebalancing_recommendations(current_holdings, weights, latest_prices)
            if recs:
                for r in recs:
                    action_color = GREEN if r["Action"] == "BUY" else RED
                    st.markdown(f"""
                    <div style="display:flex;justify-content:space-between;align-items:center;
                                padding:0.6rem 1rem;border-radius:8px;margin-bottom:0.4rem;
                                background:#f8fafc;border:1px solid #e2e8f0">
                        <span style="font-weight:600;color:#0f172a;font-size:0.9rem">{r['Ticker']}</span>
                        <span style="color:{action_color};font-weight:700;font-size:0.85rem">{r['Action']}</span>
                        <span style="color:#64748b;font-size:0.82rem">{r['Off Target']} off target</span>
                        <span style="font-family:'DM Mono',monospace;font-size:0.82rem;color:#0f172a">
                            ${r['Difference']:,.0f}
                        </span>
                    </div>""", unsafe_allow_html=True)
            else:
                st.success("Portfolio is balanced — no rebalancing needed.")

        # ── Fee Drag Analysis ─────────────────────────────────────────────────
        _section_header("Fee Drag Analysis")
        _ETF_FEES = {
            "SPY":0.0945,"QQQ":0.20,"XLK":0.10,"XLV":0.10,"XLF":0.10,
            "XLY":0.10,"XLP":0.10,"XLI":0.10,"XLE":0.10,"XLB":0.10,
            "XLRE":0.10,"XLU":0.10,"XLC":0.10,"IEF":0.15,"TLT":0.15,
            "LQD":0.14,"HYG":0.48,"AGG":0.03,"BND":0.03,"SHY":0.15,
            "TIP":0.19,"VTIP":0.04,"MUB":0.07,"BNDX":0.07,"EMB":0.39,
        }
        _wt_fee = sum(weights.get(t, 0) * _ETF_FEES.get(t, 0) / 100 for t in weights)
        _fee_yrs = {"1 year":1,"3 years":3,"5 years":5,"10 years":10,"20+ years":20}.get(
            prefs.get("horizon","5 years"), 5)
        _fee_cap = prefs.get("starting_capital", 10000)
        _fee_mo  = prefs.get("monthly_contribution", 500)
        _fee_ret = bt_met.get("Ann. Return", 0) / 100
        _fee_mr_with    = max((1 + max(_fee_ret - _wt_fee, -0.5)) ** (1/12) - 1, -0.5)
        _fee_mr_without = (1 + _fee_ret) ** (1/12) - 1 if _fee_ret > -1 else 0

        _vwf, _vwof = _fee_cap, _fee_cap
        _vals_f, _vals_nf = [_vwf], [_vwof]
        for _ in range(_fee_yrs * 12):
            _vwf  = _vwf  * (1 + _fee_mr_with)    + _fee_mo
            _vwof = _vwof * (1 + _fee_mr_without)  + _fee_mo
            _vals_f.append(_vwf); _vals_nf.append(_vwof)
        _fee_drag = _vwof - _vwf

        _fx = list(range(len(_vals_f)))
        fig_fee = go.Figure()
        fig_fee.add_trace(go.Scatter(x=_fx, y=_vals_nf, name="Without Fees",
            line=dict(color=BLUE, width=2.5)))
        fig_fee.add_trace(go.Scatter(x=_fx, y=_vals_f, name="With Fees",
            line=dict(color=AMBER, width=2, dash="dot"),
            fill="tonexty", fillcolor="rgba(220,38,38,0.06)"))
        fig_fee.update_layout(
            title=dict(
                text=f"Fee Drag Over {_fee_yrs}Y  ·  Weighted Expense: {_wt_fee*100:.3f}%  ·  Cost: ${_fee_drag:,.0f}",
                font=dict(size=11)),
            height=280, template="plotly_white",
            margin=dict(l=0, r=0, t=44, b=0),
            yaxis=dict(tickprefix="$"),
            legend=dict(orientation="h", y=1.12),
            font=dict(family="DM Sans"),
        )
        st.plotly_chart(fig_fee, use_container_width=True)
        _fc1, _fc2, _fc3 = st.columns(3)
        with _fc1:
            st.markdown(_metric_card("Weighted Expense Ratio", f"{_wt_fee*100:.3f}%", DARK), unsafe_allow_html=True)
        with _fc2:
            st.markdown(_metric_card("Total Fee Drag", f"${_fee_drag:,.0f}", RED), unsafe_allow_html=True)
        with _fc3:
            st.markdown(_metric_card("Value After Fees", f"${_vwf:,.0f}", BLUE), unsafe_allow_html=True)

        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("← Back", key="step3_back"):
                st.session_state["port_step"] = 2
                if "port_backtest" in st.session_state:
                    del st.session_state["port_backtest"]
                st.rerun()
        with col2:
            if st.button("Next → Monte Carlo Forecast", type="primary", key="step3_next"):
                st.session_state["port_step"] = 4
                st.rerun()

    # ── STEP 4 — Monte Carlo Forecast ─────────────────────────────────────────
    elif curr_step == 4:
        prefs   = st.session_state.get("port_prefs", {})
        opt     = st.session_state.get("port_optimised", {})
        weights = st.session_state.get("port_selected_weights", {})
        bt      = st.session_state.get("port_backtest", {})

        if "port_mc" not in st.session_state:
            horizon_map = {"1 year":1,"3 years":3,"5 years":5,"10 years":10,"20+ years":20}
            forecast_yr = horizon_map.get(prefs.get("horizon","5 years"), 5)

            with st.spinner(f"Running {forecast_yr}-year Monte Carlo simulation..."):
                try:
                    returns_df = opt["returns_df"]
                    start_cap  = prefs.get("starting_capital", 10000)
                    monthly    = prefs.get("monthly_contribution", 500)
                    target_val = prefs.get("target_value")

                    mc_sim_df, mc_summary, milestones = run_portfolio_monte_carlo(
                        returns_df, weights, start_cap, monthly,
                        forecast_years=forecast_yr,
                        n_simulations=1000,
                        target_value=target_val,
                        log=lambda m: None,
                    )
                    st.session_state["port_mc"] = {
                        "sim_df":     mc_sim_df,
                        "summary":    mc_summary,
                        "milestones": milestones,
                    }
                except Exception as e:
                    st.error(f"❌ Monte Carlo failed: {e}")
                    st.exception(e)
                    return

        mc_data    = st.session_state["port_mc"]
        mc_sim_df  = mc_data["sim_df"]
        mc_summary = mc_data["summary"]
        milestones = mc_data["milestones"]
        prefs      = st.session_state.get("port_prefs", {})
        start_cap  = prefs.get("starting_capital", 10000)

        _section_header("Monte Carlo Forecast Results")

        # ── Timeline selector for probability metrics ──────────────────────────
        horizon_map = {"1 year": "1yr", "3 years": "3yr", "5 years": "5yr", "10 years": "10yr"}
        available_horizons = [lbl for lbl, key in horizon_map.items() if key in milestones]
        # Add the full forecast horizon as an option
        forecast_horizon_label = mc_summary.get("Forecast Horizon", "10 years")
        full_horizon_yr = forecast_horizon_label.replace(" years","yr").replace(" year","yr")
        horizon_options = available_horizons + (
            [f"{forecast_horizon_label} (full)"]
            if full_horizon_yr not in [horizon_map.get(h) for h in available_horizons]
            else []
        )
        if not horizon_options:
            horizon_options = [forecast_horizon_label]

        sel_horizon = st.selectbox(
            "Show probabilities at horizon:",
            options=horizon_options,
            index=min(2, len(horizon_options)-1),  # default to 5yr if available
            key="mc_horizon_select",
            help="Choose the time horizon for the probability metrics below.",
        )

        # Resolve which probability data to use
        sel_key = horizon_map.get(sel_horizon.replace(" (full)",""))
        if sel_key and sel_key in milestones:
            ms_data = milestones[sel_key]
            prob_gain_val    = ms_data.get("prob_gain",    "—")
            prob_double_val  = ms_data.get("prob_double",  "—")
            prob_loss_val    = ms_data.get("prob_loss_20", "—")
            prob_goal_val    = ms_data.get("prob_goal")
            tot_invested_val = ms_data.get("total_invested", mc_summary.get("Total Invested", 0))
        else:
            prob_gain_val    = mc_summary.get("Prob. of Any Gain",   "—")
            prob_double_val  = mc_summary.get("Prob. of Doubling",   "—")
            prob_loss_val    = mc_summary.get("Prob. of >20% Loss",  "—")
            prob_goal_val    = mc_summary.get("Prob. of Reaching Goal")
            tot_invested_val = mc_summary.get("Total Invested", start_cap)

        st.caption(
            f"Probabilities at **{sel_horizon}** · "
            f"Total invested by then: **${tot_invested_val:,.0f}** · "
            f"'Any Gain' = portfolio exceeds total invested · "
            f"'Doubling' = exceeds 2× total invested"
        )

        # Probability gauges
        cols = st.columns(3)
        for col, label, value, color in [
            (cols[0], "Prob. of Any Gain",   prob_gain_val,   GREEN),
            (cols[1], "Prob. of Doubling",    prob_double_val, BLUE),
            (cols[2], "Prob. of >20% Loss",   prob_loss_val,   RED),
        ]:
            with col:
                st.markdown(_metric_card(label, value, color), unsafe_allow_html=True)

        with st.expander("ℹ️ How Monte Carlo probabilities are calculated"):
            st.markdown("""
**What this simulation does:**
Runs 1,000 independent scenarios for your portfolio using correlated daily returns
sampled from historical data. Each scenario compounds daily over the forecast period
with monthly contributions added throughout.

**Return assumption:**
Daily returns are blended — 70% from your portfolio's 2-year historical average,
30% from a long-run 7% annual market mean. Individual stock drift is capped at 12%/year.
This prevents recent bull or bear runs from distorting long-range projections.

**Correlation:**
Cross-asset correlations are preserved using Cholesky decomposition of the
historical covariance matrix — so when tech stocks fall together, the simulation
reflects that.

**What these probabilities mean:**
- **Prob. of Any Gain** — % of simulations where final value exceeds total amount invested
- **Prob. of Doubling** — % of simulations where final value exceeds 2× total invested
- **Prob. of >20% Loss** — % of simulations where final value is more than 20% below total invested

**Limitations:**
- Assumes returns are log-normally distributed (fat tails in real markets are larger)
- Does not model tax drag, fund fees, or trading costs
- Black swan events (2008, COVID) are underweighted relative to their real-world impact
- Probabilities are illustrative — not a guarantee of any outcome
            """)

        st.caption(
            "⚠️ Monte Carlo assumes log-normally distributed returns and stationary volatility. "
            "It does not model recessions, black-swan events, or regime changes. "
            "Probabilities are illustrative, not guaranteed. Not investment advice."
        )

        if prob_goal_val is not None:
            st.markdown(f"""
            <div style="background:#f8fafc;border:2px solid #8b5cf6;border-radius:12px;
                        padding:1rem;text-align:center;margin-top:0.75rem">
                <div style="font-size:0.68rem;font-weight:600;letter-spacing:0.5px;
                            text-transform:uppercase;color:#64748b">Prob. of Reaching Your Goal</div>
                <div style="font-family:'DM Mono',monospace;font-size:2rem;
                            font-weight:500;color:{PURPLE};margin-top:4px">
                    {prob_goal_val}
                </div>
                <div style="font-size:0.78rem;color:#64748b;margin-top:4px">
                    Target: ${prefs.get('target_value',0):,.0f} by {sel_horizon}
                </div>
            </div>""", unsafe_allow_html=True)

        # P50 / bear / bull
        _section_header("Outcome Range")
        cols2 = st.columns(5)
        for col, label, key, color in [
            (cols2[0], "Bear (P5)",  "Bear Case (P5)",  RED),
            (cols2[1], "Low (P25)",  "Low Case (P25)",  AMBER),
            (cols2[2], "Median",     "Median (P50)",    DARK),
            (cols2[3], "Bull (P75)", "Bull Case (P75)", BLUE),
            (cols2[4], "Best (P95)", "Best Case (P95)", GREEN),
        ]:
            val = mc_summary.get(key, 0)
            with col:
                st.markdown(_metric_card(label, f"${val:,.0f}" if isinstance(val,(int,float)) else val, color), unsafe_allow_html=True)

        # Fan chart
        _section_header("Simulation Fan Chart")
        sample_paths = mc_sim_df.iloc[:, :300].values
        x_days       = list(range(len(sample_paths)))
        pcts         = np.percentile(sample_paths, [5,25,50,75,95], axis=1)

        fig_mc = go.Figure()
        fig_mc.add_trace(go.Scatter(x=x_days,y=pcts[4],name="P95 (Best)",
                                    line=dict(color=GREEN,width=1.5)))
        fig_mc.add_trace(go.Scatter(x=x_days,y=pcts[3],name="P75 (Bull)",
                                    line=dict(color=BLUE,width=1),
                                    fill="tonexty",fillcolor="rgba(14,165,233,0.07)"))
        fig_mc.add_trace(go.Scatter(x=x_days,y=pcts[2],name="Median",
                                    line=dict(color=DARK,width=2.5)))
        fig_mc.add_trace(go.Scatter(x=x_days,y=pcts[1],name="P25 (Low)",
                                    line=dict(color=AMBER,width=1),
                                    fill="tonexty",fillcolor="rgba(245,158,11,0.07)"))
        fig_mc.add_trace(go.Scatter(x=x_days,y=pcts[0],name="P5 (Bear)",
                                    line=dict(color=RED,width=1.5)))
        fig_mc.add_hline(y=start_cap, line_dash="dot", line_color=MUTED, opacity=0.5,
                         annotation_text="Starting capital", annotation_position="right")
        if prefs.get("target_value"):
            fig_mc.add_hline(y=prefs["target_value"], line_dash="dash", line_color=PURPLE,
                             opacity=0.7, annotation_text="Your goal", annotation_position="right")
        fig_mc.update_layout(
            height=400, template="plotly_white",
            margin=dict(l=0,r=0,t=10,b=0),
            xaxis_title="Trading Days",
            yaxis=dict(title="Portfolio Value ($)", tickprefix="$"),
            legend=dict(orientation="h",yanchor="bottom",y=1.02),
            font=dict(family="DM Sans"),
        )
        st.plotly_chart(fig_mc, use_container_width=True)

        # Milestone table
        _section_header("Projected Value at Key Milestones")
        ms_rows = []
        for horizon, pct_data in milestones.items():
            ms_rows.append({
                "Horizon":    horizon,
                "Bear (P5)":  f"${pct_data['P5']:,.0f}",
                "Low (P25)":  f"${pct_data['P25']:,.0f}",
                "Median":     f"${pct_data['P50']:,.0f}",
                "Bull (P75)": f"${pct_data['P75']:,.0f}",
                "Best (P95)": f"${pct_data['P95']:,.0f}",
            })
        st.dataframe(pd.DataFrame(ms_rows), use_container_width=True, hide_index=True)

        # Export downloads
        _section_header("Download Full Portfolio Report")

        _gen_col1, _gen_col2 = st.columns(2)
        with _gen_col1:
            if st.button("📊 Generate Excel Report", type="primary",
                         use_container_width=True, key="gen_excel"):
                with st.spinner("Building Excel report..."):
                    try:
                        bt_data    = st.session_state.get("port_backtest", {})
                        opt_data   = st.session_state.get("port_optimised", {})
                        stock_mets = opt_data.get("stock_metrics", {})
                        corr_mat   = opt_data.get("corr_matrix")
                        div_sc     = opt_data.get("div_score", 5)
                        t_info     = opt_data.get("ticker_info", {})
                        excel_buf  = build_portfolio_excel(
                            preferences           = prefs,
                            final_weights         = weights,
                            stock_metrics         = stock_mets,
                            backtest_df           = bt_data.get("df"),
                            backtest_metrics      = bt_data.get("metrics", {}),
                            heatmap_df            = bt_data.get("heatmap"),
                            mc_sim_df             = mc_sim_df,
                            mc_summary            = mc_summary,
                            milestones            = milestones,
                            corr_matrix           = corr_mat,
                            diversification_score = div_sc,
                            ticker_info           = t_info,
                        )
                        st.session_state["port_excel"] = excel_buf
                    except Exception as e:
                        st.error(f"❌ Excel build failed: {e}")
                        st.exception(e)

        with _gen_col2:
            if PPTX_AVAILABLE and st.button("📑 Generate PowerPoint Report", type="primary",
                                             use_container_width=True, key="gen_pptx"):
                with st.spinner("Building PowerPoint report..."):
                    try:
                        bt_data    = st.session_state.get("port_backtest", {})
                        opt_data   = st.session_state.get("port_optimised", {})
                        stock_mets = opt_data.get("stock_metrics", {})
                        corr_mat   = opt_data.get("corr_matrix")
                        div_sc     = opt_data.get("div_score", 5)
                        t_info     = opt_data.get("ticker_info", {})
                        pptx_buf   = build_portfolio_pptx(
                            preferences           = prefs,
                            final_weights         = weights,
                            stock_metrics         = stock_mets,
                            backtest_df           = bt_data.get("df"),
                            backtest_metrics      = bt_data.get("metrics", {}),
                            mc_sim_df             = mc_sim_df,
                            mc_summary            = mc_summary,
                            milestones            = milestones,
                            corr_matrix           = corr_mat,
                            diversification_score = div_sc,
                            ticker_info           = t_info,
                        )
                        st.session_state["port_pptx"] = pptx_buf
                    except Exception as e:
                        st.error(f"❌ PowerPoint build failed: {e}")
                        st.exception(e)

        _dl_col1, _dl_col2 = st.columns(2)
        with _dl_col1:
            if "port_excel" in st.session_state:
                st.download_button(
                    label="⬇  Download Portfolio Report (.xlsx)",
                    data=st.session_state["port_excel"],
                    file_name=f"StockWizard_Portfolio_{datetime.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True, type="primary", key="download_portfolio",
                )
        with _dl_col2:
            if "port_pptx" in st.session_state:
                st.session_state["port_pptx"].seek(0)
                st.download_button(
                    label="⬇  Download Portfolio Report (.pptx)",
                    data=st.session_state["port_pptx"],
                    file_name=f"StockWizard_Portfolio_{datetime.now().strftime('%Y%m%d')}.pptx",
                    mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    use_container_width=True, type="primary", key="download_portfolio_pptx",
                )

        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("← Back to Backtest", key="step4_back"):
                st.session_state["port_step"] = 3
                if "port_mc" in st.session_state:
                    del st.session_state["port_mc"]
                st.rerun()
        with col2:
            if st.button("🔄 Start New Portfolio", key="step4_restart"):
                for k in ["port_step","port_prefs","port_optimised",
                          "port_selected_weights","port_backtest","port_mc","port_excel"]:
                    st.session_state.pop(k, None)
                st.rerun()

        # ── Save / Load portfolio ──────────────────────────────────────────────
        st.markdown("---")
        _section_header("Save & Load Portfolios")

        _sv_col, _ld_col = st.columns(2)

        with _sv_col:
            st.markdown("**Save this portfolio**")
            _save_email = st.text_input("Your email", placeholder="you@example.com",
                                        key="save_port_email")
            _save_name  = st.text_input("Portfolio name", placeholder="My Growth Portfolio",
                                        key="save_port_name")
            if st.button("💾  Save Portfolio", key="save_port_btn"):
                if _save_email.strip() and _save_name.strip():
                    _weights  = st.session_state.get("port_selected_weights", {})
                    _prefs    = st.session_state.get("port_prefs", {})
                    _bt       = st.session_state.get("port_backtest", {})
                    _metrics  = _bt.get("metrics", {}) if isinstance(_bt, dict) else {}
                    _ok = save_portfolio(
                        user_email=_save_email,
                        name=_save_name,
                        weights=_weights,
                        preferences=_prefs,
                        metrics=_metrics,
                    )
                    if _ok:
                        st.success("Portfolio saved!")
                    else:
                        st.error("Save failed — check your connection.")
                else:
                    st.warning("Enter both an email and a name.")

        with _ld_col:
            st.markdown("**Load a saved portfolio**")
            _load_email = st.text_input("Your email", placeholder="you@example.com",
                                        key="load_port_email")
            if st.button("🔍  Find My Portfolios", key="load_port_btn"):
                if _load_email.strip():
                    _saved = load_portfolios(_load_email)
                    if _saved:
                        st.session_state["found_portfolios"] = _saved
                    else:
                        st.info("No saved portfolios found for that email.")

            if "found_portfolios" in st.session_state:
                _saved = st.session_state["found_portfolios"]
                for _p in _saved:
                    _pcols = st.columns([3, 1, 1])
                    with _pcols[0]:
                        _date = _p.get("created_at", "")[:10]
                        st.markdown(f"**{_p['name']}** <span style='color:#94a3b8;font-size:0.78rem'>"
                                    f"saved {_date}</span>", unsafe_allow_html=True)
                        _tickers = ", ".join(list(_p.get("weights", {}).keys())[:6])
                        st.caption(_tickers)
                    with _pcols[1]:
                        if st.button("Load", key=f"load_{_p['id']}"):
                            st.session_state["port_selected_weights"] = _p["weights"]
                            st.success(f"Loaded: {_p['name']}")
                    with _pcols[2]:
                        if st.button("Delete", key=f"del_{_p['id']}"):
                            delete_portfolio(_p["id"])
                            del st.session_state["found_portfolios"]
                            st.rerun()

        st.markdown(render_section("Backtesting Methodology", _disc.BACKTEST),
                    unsafe_allow_html=True)
        st.markdown(render_section("Portfolio Optimisation", _disc.OPTIMISATION),
                    unsafe_allow_html=True)
        st.markdown(render_section("Monte Carlo Projections", _disc.MONTE_CARLO),
                    unsafe_allow_html=True)
        st.markdown(render_inline(_disc.FULL_FOOTER), unsafe_allow_html=True)
