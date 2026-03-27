import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime
import time

from data import (
    validate_ticker, fetch_stock_data, fetch_company_details,
    fetch_news, fetch_peer_comparison, fetch_sector_data,
    SECTOR_ETF_MAP
)
from analysis import (
    detect_support_resistance, build_correlation_matrix,
    run_monte_carlo, generate_summary_paragraph
)
from excel_builder import build_excel

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="StockWizard",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── API Key ───────────────────────────────────────────────────────────────────
POLYGON_API_KEY = "l0p56or_wphBN0EMtK7LYFFs8nETcMEM"

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
}

/* Hide default streamlit elements */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
.stDeployButton {display: none;}

/* Header */
.main-header {
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
    padding: 2rem 2.5rem;
    border-radius: 16px;
    margin-bottom: 2rem;
    border: 1px solid #334155;
}
.main-header h1 {
    font-family: 'DM Mono', monospace;
    color: #38bdf8;
    font-size: 2rem;
    margin: 0 0 0.25rem;
    font-weight: 500;
    letter-spacing: -0.5px;
}
.main-header p {
    color: #94a3b8;
    font-size: 0.9rem;
    margin: 0;
}

/* Metric cards */
.metric-card {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 1.25rem;
    text-align: center;
}
.metric-label {
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    color: #94a3b8;
    margin-bottom: 0.4rem;
}
.metric-value {
    font-family: 'DM Mono', monospace;
    font-size: 1.4rem;
    font-weight: 500;
    color: #0f172a;
}
.metric-value.positive { color: #16a34a; }
.metric-value.negative { color: #dc2626; }
.metric-value.neutral  { color: #0f172a; }

/* MC result cards */
.mc-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.75rem;
    margin-top: 1rem;
}
.mc-card {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 1rem;
    text-align: center;
}
.mc-card-label {
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: #94a3b8;
    margin-bottom: 0.35rem;
}
.mc-card-value {
    font-family: 'DM Mono', monospace;
    font-size: 1.1rem;
    font-weight: 500;
}

/* Section headers */
.section-header {
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: #64748b;
    border-bottom: 1px solid #e2e8f0;
    padding-bottom: 0.5rem;
    margin-bottom: 1rem;
    margin-top: 1.5rem;
}

/* Disclaimer */
.disclaimer {
    background: #fffbeb;
    border: 1px solid #fcd34d;
    border-radius: 8px;
    padding: 0.75rem 1rem;
    font-size: 0.78rem;
    color: #92400e;
    margin-top: 1.5rem;
}

/* Sidebar */
.sidebar-label {
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: #64748b;
    margin-bottom: 0.25rem;
}
</style>
""", unsafe_allow_html=True)

# ── Landing / Header ──────────────────────────────────────────────────────────
st.markdown("""
<div class="main-header">
    <h1>◈ StockWizard</h1>
    <p>Professional stock analysis · Monte Carlo simulation · Excel report generation · Powered by Polygon.io</p>
</div>
""", unsafe_allow_html=True)

# ── Sidebar — inputs ──────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Configure Analysis")
    st.markdown("---")

    ticker_input = st.text_input(
        "Ticker Symbol",
        placeholder="e.g. AAPL, TSLA, MSFT",
        help="Enter any US stock ticker symbol"
    ).strip().upper()

    period = st.radio(
        "Date Range",
        options=["1y", "2y", "5y", "10y"],
        index=2,
        horizontal=True
    )

    st.markdown("---")
    st.markdown("**Benchmarks**")
    include_spy = st.checkbox("S&P 500 (SPY)", value=True)
    include_qqq = st.checkbox("NASDAQ (QQQ)", value=True)

    st.markdown("---")
    st.markdown("**Peer Comparison**")
    peers_input = st.text_input(
        "Peer Tickers (optional)",
        placeholder="e.g. GOOGL, AMZN, META"
    )

    st.markdown("---")
    st.markdown("**Report Modules**")
    do_mc     = st.checkbox("Monte Carlo Forecast", value=True)
    do_sector = st.checkbox("Sector Comparison", value=True)
    do_corr   = st.checkbox("Correlation Matrix", value=True)
    do_sr     = st.checkbox("Support & Resistance", value=True)
    do_news   = st.checkbox("News Headlines", value=True)
    do_peers  = st.checkbox("Peer Comparison", value=True)

    if do_mc:
        st.markdown("**Monte Carlo Settings**")
        n_sims    = st.slider("Simulations", 100, 5000, 1000, step=100)
        n_horizon = st.slider("Horizon (days)", 21, 504, 252, step=21)

    st.markdown("---")
    run_btn = st.button("▶  Run Analysis", type="primary", use_container_width=True)

# ── Email waitlist (sidebar bottom) ──────────────────────────────────────────
with st.sidebar:
    st.markdown("---")
    st.markdown("### Stay Updated")
    email_input = st.text_input("Get notified about new features", placeholder="your@email.com")
    if st.button("Join waitlist", use_container_width=True):
        if email_input and "@" in email_input:
            st.success("Thanks! We'll be in touch.")
        else:
            st.error("Please enter a valid email.")

# ── Main content ──────────────────────────────────────────────────────────────
if not run_btn:
    # Landing page content
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("""
        <div class="metric-card">
            <div class="metric-label">What you get</div>
            <div style="text-align:left;margin-top:0.75rem;font-size:0.88rem;color:#334155;line-height:1.8">
            ◈ 10-sheet Excel report<br>
            ◈ Monte Carlo simulation<br>
            ◈ Bollinger Bands & RSI<br>
            ◈ Support & resistance<br>
            ◈ Correlation matrix<br>
            ◈ Automated AI summary
            </div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown("""
        <div class="metric-card">
            <div class="metric-label">Data source</div>
            <div style="text-align:left;margin-top:0.75rem;font-size:0.88rem;color:#334155;line-height:1.8">
            ◈ Licensed via Polygon.io<br>
            ◈ Up to 10 years history<br>
            ◈ Daily OHLCV data<br>
            ◈ Company fundamentals<br>
            ◈ News & events<br>
            ◈ Sector ETF benchmarks
            </div>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        st.markdown("""
        <div class="metric-card">
            <div class="metric-label">How to use</div>
            <div style="text-align:left;margin-top:0.75rem;font-size:0.88rem;color:#334155;line-height:1.8">
            1. Enter a ticker symbol<br>
            2. Choose date range<br>
            3. Select modules<br>
            4. Click Run Analysis<br>
            5. Download Excel report<br>
            ◈ Free to use
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("""
    <div class="disclaimer">
        ⚠ This tool is for informational purposes only and does not constitute financial advice.
        Data provided by Polygon.io. Past performance is not indicative of future results.
    </div>
    """, unsafe_allow_html=True)

else:
    # ── Validation ────────────────────────────────────────────────────────────
    if not ticker_input:
        st.error("Please enter a ticker symbol in the sidebar.")
        st.stop()

    with st.spinner(f"Validating {ticker_input}..."):
        valid, info = validate_ticker(ticker_input, POLYGON_API_KEY)

    if not valid:
        st.error(f"❌ {info}")
        st.stop()

    # ── Run analysis ──────────────────────────────────────────────────────────
    benchmarks = []
    if include_spy: benchmarks.append("SPY")
    if include_qqq: benchmarks.append("QQQ")

    peers_list = [p.strip().upper() for p in peers_input.split(",") if p.strip()] if peers_input else []

    progress = st.progress(0, text="Starting analysis...")
    logs     = st.empty()
    log_lines = []

    def log(msg):
        log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}]  {msg}")
        logs.code("\n".join(log_lines[-12:]), language=None)

    try:
        # 1. Stock data
        progress.progress(10, text="Downloading price data...")
        df = fetch_stock_data(ticker_input, period=period,
                              benchmark_tickers=benchmarks,
                              api_key=POLYGON_API_KEY, log=log)
        progress.progress(25, text="Fetching company details...")

        # 2. Company details
        company_details = fetch_company_details(ticker_input, POLYGON_API_KEY, log=log)
        sector = company_details.get("Sector", "Unknown")

        # 3. News
        news_list = []
        if do_news:
            progress.progress(35, text="Fetching news...")
            news_list = fetch_news(ticker_input, POLYGON_API_KEY, log=log)

        # 4. Peers
        peer_df = None
        if do_peers and peers_list:
            progress.progress(45, text="Fetching peer data...")
            peer_df = fetch_peer_comparison(ticker_input, peers_list, POLYGON_API_KEY, log=log)

        # 5. Sector ETF
        sector_df = None
        if do_sector:
            progress.progress(50, text="Fetching sector ETF...")
            sector_df = fetch_sector_data(ticker_input, period, POLYGON_API_KEY, sector, log=log)

        # 6. Correlation
        corr_matrix = None
        if do_corr:
            progress.progress(60, text="Building correlation matrix...")
            log("Building correlation matrix...")
            corr_matrix = build_correlation_matrix(df, benchmarks if benchmarks else None)

        # 7. Support & resistance
        resistance, support = None, None
        if do_sr:
            progress.progress(65, text="Detecting support & resistance...")
            log("Detecting support & resistance...")
            resistance, support = detect_support_resistance(df)
            log(f"   Resistance: {resistance}  Support: {support}")

        # 8. Monte Carlo
        mc_sim_df = mc_summary = None
        if do_mc:
            progress.progress(75, text="Running Monte Carlo simulation...")
            mc_sim_df, mc_summary = run_monte_carlo(
                df, n_simulations=n_sims, forecast_days=n_horizon, log=log)

        # 9. Summary paragraph
        progress.progress(85, text="Generating analysis summary...")
        ret      = df["Daily_Return"].dropna()
        ann_ret  = ret.mean() * 252
        ann_std  = ret.std() * np.sqrt(252)
        downside = ret[ret < 0].std() * np.sqrt(252)
        sharpe   = ann_ret / ann_std  if ann_std  else np.nan
        sortino  = ann_ret / downside if downside else np.nan
        summary_text = generate_summary_paragraph(
            ticker_input, df, company_details, mc_summary, sharpe, sortino)

        # 10. Build Excel
        progress.progress(92, text="Building Excel report...")
        log("Building Excel workbook...")
        excel_buf = build_excel(
            ticker_input, df, period,
            company_details=company_details,
            sector_df=sector_df,
            mc_sim_df=mc_sim_df,
            mc_summary=mc_summary,
            news_list=news_list,
            peer_df=peer_df,
            corr_matrix=corr_matrix,
            resistance_levels=resistance,
            support_levels=support,
            summary_text=summary_text,
        )

        progress.progress(100, text="Complete!")
        log("Complete!")
        time.sleep(0.5)
        progress.empty()
        logs.empty()

    except Exception as e:
        progress.empty()
        st.error(f"❌ Analysis failed: {e}")
        st.exception(e)
        st.stop()

    # ── Results UI ────────────────────────────────────────────────────────────
    latest     = df.iloc[-1]
    first      = df.iloc[0]
    period_ret = (latest["Close"] / first["Close"] - 1) * 100

    # Download button — top of page
    st.download_button(
        label=f"⬇  Download Excel Report — {ticker_input}_{period}_Analysis.xlsx",
        data=excel_buf,
        file_name=f"{ticker_input}_{period}_Analysis.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        type="primary",
        key="download_top",
    )

    st.markdown("---")

    # ── Key metrics row ───────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Key Metrics</div>', unsafe_allow_html=True)

    pos_neg = lambda v: "positive" if v > 0 else ("negative" if v < 0 else "neutral")

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    metrics = [
        (col1, "Current Price",    f"${latest['Close']:,.2f}",         "neutral"),
        (col2, "Period Return",    f"{period_ret:+.1f}%",               pos_neg(period_ret)),
        (col3, "52W High",         f"${latest.get('52W_High', 0):,.2f}","neutral"),
        (col4, "52W Low",          f"${latest.get('52W_Low', 0):,.2f}", "neutral"),
        (col5, "Sharpe Ratio",     f"{sharpe:.2f}" if pd.notna(sharpe) else "N/A",
                                                                         pos_neg(sharpe) if pd.notna(sharpe) else "neutral"),
        (col6, "Ann. Volatility",  f"{df['Volatility_20d'].iloc[-1]*100:.1f}%" if pd.notna(df['Volatility_20d'].iloc[-1]) else "N/A",
                                                                         "neutral"),
    ]
    for col, label, value, cls in metrics:
        with col:
            st.markdown(f"""
            <div class="metric-card">
                <div class="metric-label">{label}</div>
                <div class="metric-value {cls}">{value}</div>
            </div>
            """, unsafe_allow_html=True)

    # ── Price chart ───────────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Price & Moving Averages</div>', unsafe_allow_html=True)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["Date"], y=df["Close"], name="Close",
                             line=dict(color="#0ea5e9", width=2)))
    for ma, color in [(20, "#f59e0b"), (50, "#8b5cf6"), (200, "#ef4444")]:
        col_name = f"MA{ma}"
        if col_name in df.columns:
            fig.add_trace(go.Scatter(x=df["Date"], y=df[col_name], name=f"{ma}-Day MA",
                                     line=dict(color=color, width=1.2, dash="dot")))
    if do_sr and resistance:
        for r in resistance[:3]:
            fig.add_hline(y=r, line_dash="dash", line_color="#ef4444", opacity=0.5,
                          annotation_text=f"R ${r:,.0f}", annotation_position="right")
    if do_sr and support:
        for s in support[:3]:
            fig.add_hline(y=s, line_dash="dash", line_color="#16a34a", opacity=0.5,
                          annotation_text=f"S ${s:,.0f}", annotation_position="right")

    fig.update_layout(
        height=420, template="plotly_white",
        margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor="#f1f5f9"),
        font=dict(family="DM Sans"),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── RSI chart ─────────────────────────────────────────────────────────────
    if "RSI14" in df.columns:
        st.markdown('<div class="section-header">RSI (14)</div>', unsafe_allow_html=True)
        fig_rsi = go.Figure()
        fig_rsi.add_trace(go.Scatter(x=df["Date"], y=df["RSI14"], name="RSI",
                                     line=dict(color="#8b5cf6", width=1.5)))
        fig_rsi.add_hline(y=70, line_dash="dash", line_color="#ef4444", opacity=0.6)
        fig_rsi.add_hline(y=30, line_dash="dash", line_color="#16a34a", opacity=0.6)
        fig_rsi.add_hrect(y0=70, y1=100, fillcolor="#ef4444", opacity=0.05)
        fig_rsi.add_hrect(y0=0,  y1=30,  fillcolor="#16a34a", opacity=0.05)
        fig_rsi.update_layout(height=200, template="plotly_white",
                               margin=dict(l=0, r=0, t=5, b=0),
                               yaxis=dict(range=[0, 100], showgrid=True, gridcolor="#f1f5f9"),
                               xaxis=dict(showgrid=False),
                               font=dict(family="DM Sans"))
        st.plotly_chart(fig_rsi, use_container_width=True)

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    if "BB_Upper" in df.columns:
        st.markdown('<div class="section-header">Bollinger Bands</div>', unsafe_allow_html=True)
        fig_bb = go.Figure()
        fig_bb.add_trace(go.Scatter(x=df["Date"], y=df["BB_Upper"], name="Upper",
                                    line=dict(color="#94a3b8", width=1), showlegend=True))
        fig_bb.add_trace(go.Scatter(x=df["Date"], y=df["BB_Lower"], name="Lower",
                                    line=dict(color="#94a3b8", width=1),
                                    fill="tonexty", fillcolor="rgba(148,163,184,0.1)"))
        fig_bb.add_trace(go.Scatter(x=df["Date"], y=df["Close"], name="Close",
                                    line=dict(color="#0ea5e9", width=1.5)))
        fig_bb.add_trace(go.Scatter(x=df["Date"], y=df["BB_Middle"], name="Middle",
                                    line=dict(color="#f59e0b", width=1, dash="dot")))
        fig_bb.update_layout(height=300, template="plotly_white",
                              margin=dict(l=0, r=0, t=5, b=0),
                              font=dict(family="DM Sans"))
        st.plotly_chart(fig_bb, use_container_width=True)

    # ── Monte Carlo results ───────────────────────────────────────────────────
    if mc_summary:
        st.markdown('<div class="section-header">Monte Carlo Forecast</div>', unsafe_allow_html=True)

        mc_cols = st.columns(6)
        mc_items = [
            (mc_cols[0], "Bear (P5)",   f"${mc_summary['Bear Case (P5)']:,.2f}",  "#dc2626"),
            (mc_cols[1], "Low (P25)",   f"${mc_summary['Low Case (P25)']:,.2f}",  "#f59e0b"),
            (mc_cols[2], "Median",      f"${mc_summary['Median (P50)']:,.2f}",    "#0f172a"),
            (mc_cols[3], "Bull (P75)",  f"${mc_summary['Bull Case (P75)']:,.2f}", "#0ea5e9"),
            (mc_cols[4], "Best (P95)",  f"${mc_summary['Best Case (P95)']:,.2f}", "#16a34a"),
            (mc_cols[5], "Prob. Gain",  mc_summary["Prob. of Gain"],              "#8b5cf6"),
        ]
        for col, label, value, color in mc_items:
            with col:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-label">{label}</div>
                    <div class="metric-value" style="color:{color}">{value}</div>
                </div>
                """, unsafe_allow_html=True)

        # MC fan chart
        sample_paths = mc_sim_df.iloc[:, :200].values
        x_days = list(range(len(sample_paths)))
        pcts   = np.percentile(sample_paths, [5, 25, 50, 75, 95], axis=1)

        fig_mc = go.Figure()
        fig_mc.add_trace(go.Scatter(x=x_days, y=pcts[4], name="P95",
                                    line=dict(color="#16a34a", width=1.5)))
        fig_mc.add_trace(go.Scatter(x=x_days, y=pcts[3], name="P75",
                                    line=dict(color="#0ea5e9", width=1),
                                    fill="tonexty", fillcolor="rgba(14,165,233,0.08)"))
        fig_mc.add_trace(go.Scatter(x=x_days, y=pcts[2], name="Median",
                                    line=dict(color="#0f172a", width=2)))
        fig_mc.add_trace(go.Scatter(x=x_days, y=pcts[1], name="P25",
                                    line=dict(color="#f59e0b", width=1),
                                    fill="tonexty", fillcolor="rgba(245,158,11,0.08)"))
        fig_mc.add_trace(go.Scatter(x=x_days, y=pcts[0], name="P5",
                                    line=dict(color="#dc2626", width=1.5)))
        fig_mc.update_layout(height=350, template="plotly_white",
                              margin=dict(l=0, r=0, t=5, b=0),
                              xaxis_title="Trading Days",
                              yaxis_title="Price ($)",
                              font=dict(family="DM Sans"))
        st.plotly_chart(fig_mc, use_container_width=True)

    # ── Volume chart ──────────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Volume</div>', unsafe_allow_html=True)
    fig_vol = go.Figure()
    colors  = ["#16a34a" if r >= 0 else "#dc2626" for r in df["Daily_Return"].fillna(0)]
    fig_vol.add_trace(go.Bar(x=df["Date"], y=df["Volume"], marker_color=colors,
                             name="Volume", opacity=0.7))
    fig_vol.update_layout(height=200, template="plotly_white",
                          margin=dict(l=0, r=0, t=5, b=0),
                          showlegend=False, font=dict(family="DM Sans"))
    st.plotly_chart(fig_vol, use_container_width=True)

    # ── Correlation matrix ────────────────────────────────────────────────────
    if corr_matrix is not None:
        st.markdown('<div class="section-header">Correlation Matrix</div>', unsafe_allow_html=True)
        fig_corr = px.imshow(
            corr_matrix,
            text_auto=".2f",
            color_continuous_scale=["#dc2626", "#ffffff", "#0ea5e9"],
            zmin=-1, zmax=1,
            aspect="auto",
        )
        fig_corr.update_layout(height=280, margin=dict(l=0, r=0, t=5, b=0),
                                font=dict(family="DM Sans"))
        st.plotly_chart(fig_corr, use_container_width=True)

    # ── News ─────────────────────────────────────────────────────────────────
    if news_list:
        st.markdown('<div class="section-header">Recent News</div>', unsafe_allow_html=True)
        for item in news_list[:8]:
            st.markdown(f"""
            <div style="padding:0.6rem 0;border-bottom:1px solid #f1f5f9">
                <div style="font-size:0.82rem;font-weight:500;color:#0f172a">
                    <a href="{item['URL']}" target="_blank" style="text-decoration:none;color:#0f172a">
                        {item['Headline']}
                    </a>
                </div>
                <div style="font-size:0.72rem;color:#94a3b8;margin-top:2px">
                    {item['Publisher']} &nbsp;·&nbsp; {item['Date']}
                </div>
            </div>
            """, unsafe_allow_html=True)

    # ── Peer comparison ───────────────────────────────────────────────────────
    if peer_df is not None and not peer_df.empty:
        st.markdown('<div class="section-header">Peer Comparison</div>', unsafe_allow_html=True)
        st.dataframe(peer_df, use_container_width=True, hide_index=True)

    # ── Analysis summary ──────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Automated Analysis Summary</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
                padding:1.25rem;font-size:0.88rem;color:#334155;line-height:1.75;font-style:italic">
        {summary_text}
    </div>
    """, unsafe_allow_html=True)

    # ── Download button again at bottom ───────────────────────────────────────
    st.markdown("---")
    excel_buf.seek(0)
    st.download_button(
        label=f"⬇  Download Excel Report — {ticker_input}_{period}_Analysis.xlsx",
        data=excel_buf,
        file_name=f"{ticker_input}_{period}_Analysis.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        type="primary",
        key="download_top",
    )

    st.markdown("""
    <div class="disclaimer">
        ⚠ This report is generated programmatically and does not constitute financial advice.
        Data provided by Polygon.io under their terms of service. Past performance is not
        indicative of future results. Always do your own research before making investment decisions.
    </div>
    """, unsafe_allow_html=True)
