import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime

from portfolio_data import (
    fetch_portfolio_prices, build_candidate_universe,
    get_ticker_info, SECTOR_UNIVERSE, SECTOR_ETFS,
    BOND_UNIVERSE, BOND_ETFS,
)
from portfolio_analysis import (
    compute_stock_metrics, compute_correlation_matrix,
    optimise_portfolio, generate_efficient_frontier,
    backtest_portfolio, compute_backtest_metrics,
    compute_monthly_heatmap, run_portfolio_monte_carlo,
    compute_diversification_score, get_rebalancing_recommendations
)
from portfolio_excel import build_portfolio_excel

DARK   = "#0f172a"
BLUE   = "#38bdf8"
GREEN  = "#16a34a"
RED    = "#dc2626"
AMBER  = "#f59e0b"
PURPLE = "#8b5cf6"
MUTED  = "#94a3b8"

ALL_SECTORS        = list(SECTOR_UNIVERSE.keys())
ALL_BOND_CATEGORIES = list(BOND_UNIVERSE.keys())


def _metric_card(label, value, color=DARK, subtitle=None):
    sub = f"<div style='font-size:0.75rem;color:{MUTED};margin-top:2px'>{subtitle}</div>" if subtitle else ""
    return f"""
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;
                padding:1.1rem;text-align:center">
        <div style="font-size:0.68rem;font-weight:600;letter-spacing:0.5px;
                    text-transform:uppercase;color:{MUTED};margin-bottom:0.35rem">{label}</div>
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

    if not is_pro:
        st.info(
            "🎉 **Free Trial** — You're using Portfolio Builder for free. "
            "Upgrade to Pro for unlimited access.",
            icon="ℹ️"
        )
        # No return — free users continue into the full builder below
    
    # if not is_pro:
    #     st.markdown("""
    #     <div style="background:#0f172a;border:1px solid #334155;border-radius:16px;
    #                 padding:2.5rem;text-align:center;margin:1rem 0">
    #         <div style="font-size:1.75rem;margin-bottom:0.75rem">📊</div>
    #         <div style="color:#fff;font-weight:600;font-size:1.2rem;margin-bottom:0.5rem">
    #             Portfolio Builder is a Pro Feature
    #         </div>
    #         <div style="color:#94a3b8;font-size:0.9rem;margin-bottom:1.5rem;max-width:480px;margin-left:auto;margin-right:auto">
    #             Build custom portfolios with backtesting, efficient frontier optimisation,
    #             Monte Carlo simulation, and full Excel report export.
    #         </div>
    #         <div style="color:#38bdf8;font-size:1.1rem;font-weight:600">$9.99 / month</div>
    #         <div style="color:#64748b;font-size:0.8rem;margin-top:4px">Cancel anytime</div>
    #     </div>
    #     """, unsafe_allow_html=True)
    #     if st.button("Upgrade to Pro", type="primary", key="upgrade_portfolio"):
    #         st.session_state["show_payment"] = True
    #         st.rerun()
    #     return
    
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
        f'{"background:#eff6ff;border:1px solid #93c5fd;color:#1d4ed8;font-weight:500" if i==curr_step else "background:#f8fafc;border:1px solid #e2e8f0;color:#94a3b8" if i>curr_step else "background:#f0fdf4;border:1px solid #86efac;color:#15803d;font-weight:500"}">'
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
                    <span style="color:{MUTED};font-size:0.85rem;margin-left:8px">{desc}</span>
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
                # Build universe
                candidates = build_candidate_universe(prefs, api_key, log=log)
                progress.progress(15, text="Fetching price history...")

                # Fetch prices (3 years)
                price_dict, close_df, returns_df, failed = fetch_portfolio_prices(
                    candidates, period_years=3, api_key=api_key, log=log)
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
                                                risk_tolerance=prefs.get("risk_tolerance",5),
                                                target_return=target_ret)
                progress.progress(80, text="Generating efficient frontier...")

                ef_df = generate_efficient_frontier(returns_df, n_portfolios=2000)

                # Get ticker info
                ticker_info = {}
                for t in list(returns_df.columns)[:10]:
                    ticker_info[t] = get_ticker_info(t, api_key)

                progress.progress(95, text="Computing diversification score...")
                recommended_weights = portfolios["recommended"]
                div_score = compute_diversification_score(recommended_weights, returns_df)

                progress.progress(100, text="Done!")
                log_area.empty()
                progress.empty()

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
                st.error(f"❌ Optimisation failed: {e}")
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

        # Clean weights — remove tiny allocations
        selected_weights = {k: v for k, v in selected_weights.items() if v >= 0.01}
        total = sum(selected_weights.values())
        selected_weights = {k: v/total for k, v in selected_weights.items()}

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
            f"'Doubling' = exceeds 2× initial capital"
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

        if prob_goal_val is not None:
            st.markdown(f"""
            <div style="background:#f8fafc;border:2px solid #8b5cf6;border-radius:12px;
                        padding:1rem;text-align:center;margin-top:0.75rem">
                <div style="font-size:0.68rem;font-weight:600;letter-spacing:0.5px;
                            text-transform:uppercase;color:{MUTED}">Prob. of Reaching Your Goal</div>
                <div style="font-family:'DM Mono',monospace;font-size:2rem;
                            font-weight:500;color:{PURPLE};margin-top:4px">
                    {prob_goal_val}
                </div>
                <div style="font-size:0.78rem;color:{MUTED};margin-top:4px">
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

        # Excel download
        _section_header("Download Full Portfolio Report")
        if st.button("📊 Generate Excel Report", type="primary", key="gen_excel"):
            with st.spinner("Building Excel report..."):
                try:
                    bt_data    = st.session_state.get("port_backtest", {})
                    opt_data   = st.session_state.get("port_optimised", {})
                    stock_mets = opt_data.get("stock_metrics", {})
                    corr_mat   = opt_data.get("corr_matrix")
                    div_sc     = opt_data.get("div_score", 5)
                    t_info     = opt_data.get("ticker_info", {})

                    excel_buf = build_portfolio_excel(
                        preferences         = prefs,
                        final_weights       = weights,
                        stock_metrics       = stock_mets,
                        backtest_df         = bt_data.get("df"),
                        backtest_metrics    = bt_data.get("metrics", {}),
                        heatmap_df          = bt_data.get("heatmap"),
                        mc_sim_df           = mc_sim_df,
                        mc_summary          = mc_summary,
                        milestones          = milestones,
                        corr_matrix         = corr_mat,
                        diversification_score = div_sc,
                        ticker_info         = t_info,
                    )
                    st.session_state["port_excel"] = excel_buf
                except Exception as e:
                    st.error(f"❌ Excel build failed: {e}")
                    st.exception(e)

        if "port_excel" in st.session_state:
            st.download_button(
                label="⬇  Download Portfolio Report (.xlsx)",
                data=st.session_state["port_excel"],
                file_name=f"StockWizard_Portfolio_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                type="primary",
                key="download_portfolio",
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

        st.markdown("""
        <div style="background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;
                    padding:0.75rem 1rem;font-size:0.78rem;color:#92400e;margin-top:1rem">
            ⚠ This analysis is for informational purposes only and does not constitute
            financial advice. Past performance is not indicative of future results.
            Always consult a qualified financial advisor before making investment decisions.
        </div>
        """, unsafe_allow_html=True)
