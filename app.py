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
)
from analysis import (
    detect_support_resistance, build_correlation_matrix,
    run_monte_carlo, generate_summary_paragraph
)
from excel_builder import build_excel
from live_data import get_live_price, get_intraday_data, get_top_movers
from payments import render_pricing_section, create_checkout_session, verify_session, check_subscription

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="StockWizard",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

POLYGON_API_KEY = "l0p56or_wphBN0EMtK7LYFFs8nETcMEM"

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap');
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
#MainMenu {visibility:hidden;} footer {visibility:hidden;} .stDeployButton {display:none;}

.main-header {
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
    padding: 2rem 2.5rem; border-radius: 16px; margin-bottom: 2rem;
    border: 1px solid #334155;
}
.main-header h1 {
    font-family: 'DM Mono', monospace; color: #38bdf8;
    font-size: 2rem; margin: 0 0 0.25rem; font-weight: 500; letter-spacing: -0.5px;
}
.main-header p { color: #94a3b8; font-size: 0.9rem; margin: 0; }

.live-ticker {
    background: #0f172a; border: 1px solid #334155; border-radius: 12px;
    padding: 1rem 1.5rem; display: flex; align-items: center;
    justify-content: space-between; margin-bottom: 1rem;
}
.live-price { font-family: 'DM Mono', monospace; font-size: 2rem; font-weight: 500; color: #fff; }
.live-change-pos { font-family: 'DM Mono', monospace; font-size: 1rem; color: #22c55e; }
.live-change-neg { font-family: 'DM Mono', monospace; font-size: 1rem; color: #ef4444; }
.live-dot { width: 8px; height: 8px; background: #22c55e; border-radius: 50%;
            display: inline-block; margin-right: 6px; animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

.metric-card {
    background: #f8fafc; border: 1px solid #e2e8f0;
    border-radius: 12px; padding: 1.25rem; text-align: center;
}
.metric-label {
    font-size: 0.72rem; font-weight: 600; letter-spacing: 0.6px;
    text-transform: uppercase; color: #94a3b8; margin-bottom: 0.4rem;
}
.metric-value { font-family: 'DM Mono', monospace; font-size: 1.4rem; font-weight: 500; color: #0f172a; }
.metric-value.positive { color: #16a34a; }
.metric-value.negative { color: #dc2626; }

.section-header {
    font-size: 0.7rem; font-weight: 600; letter-spacing: 0.8px;
    text-transform: uppercase; color: #64748b;
    border-bottom: 1px solid #e2e8f0; padding-bottom: 0.5rem;
    margin-bottom: 1rem; margin-top: 1.5rem;
}

.pro-badge {
    background: #38bdf8; color: #0f172a; font-size: 0.65rem;
    font-weight: 700; padding: 2px 8px; border-radius: 20px;
    letter-spacing: 0.5px; margin-left: 8px; vertical-align: middle;
}

.founder-card {
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 16px;
    padding: 1.75rem; height: 100%;
}
.founder-quote {
    font-size: 0.95rem; color: #334155; line-height: 1.7;
    font-style: italic; margin-bottom: 1.25rem;
}
.founder-name { font-weight: 600; color: #0f172a; font-size: 0.95rem; }
.founder-school { font-size: 0.82rem; color: #94a3b8; margin-top: 2px; }
.founder-role { font-size: 0.78rem; color: #38bdf8; margin-top: 2px; font-weight: 500; }

.disclaimer {
    background: #fffbeb; border: 1px solid #fcd34d; border-radius: 8px;
    padding: 0.75rem 1rem; font-size: 0.78rem; color: #92400e; margin-top: 1.5rem;
}

.pro-locked {
    background: #0f172a; border: 1px solid #334155; border-radius: 12px;
    padding: 2rem; text-align: center; margin: 1rem 0;
}
.mover-card {
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px;
    padding: 0.75rem 1rem; margin-bottom: 0.5rem;
    display: flex; justify-content: space-between; align-items: center;
}
</style>
""", unsafe_allow_html=True)

# ── Session state init ────────────────────────────────────────────────────────
if "is_pro"        not in st.session_state: st.session_state["is_pro"]        = True
if "user_email"    not in st.session_state: st.session_state["user_email"]    = ""
if "show_payment"  not in st.session_state: st.session_state["show_payment"]  = False
if "live_ticker"   not in st.session_state: st.session_state["live_ticker"]   = ""
if "candle_tf"     not in st.session_state: st.session_state["candle_tf"]     = "5min"

# ── Check if returning from Stripe ───────────────────────────────────────────
params = st.query_params
if "session_id" in params:
    ok, email = verify_session(params["session_id"])
    if ok:
        st.session_state["is_pro"]     = True
        st.session_state["user_email"] = email or ""
        st.query_params.clear()
        st.success("Welcome to StockWizard Pro!")

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="main-header">
    <h1>◈ StockWizard</h1>
    <p>Professional stock analysis · Monte Carlo simulation · Excel reports · Day trader mode · Powered by Polygon.io</p>
</div>
""", unsafe_allow_html=True)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    # Pro status
    if st.session_state["is_pro"]:
        st.markdown("""
        <div style="background:#0f172a;border:1px solid #38bdf8;border-radius:10px;
                    padding:0.75rem 1rem;margin-bottom:1rem;text-align:center">
            <span style="color:#38bdf8;font-weight:600;font-size:0.85rem">◈ Pro Member</span>
        </div>
        """, unsafe_allow_html=True)
    else:
        if st.button("⚡ Upgrade to Pro — $9.99/mo", use_container_width=True):
            st.session_state["show_payment"] = True
            st.rerun()

    st.markdown("### Configure Analysis")
    st.markdown("---")

    # Mode selector
    if st.session_state["is_pro"]:
        mode = st.radio("Mode", ["Investor Mode", "Day Trader Mode"], horizontal=True)
    else:
        mode = "Investor Mode"

    ticker_input = st.text_input(
        "Ticker Symbol", placeholder="e.g. AAPL, TSLA, MSFT",
    ).strip().upper()

    if mode == "Investor Mode":
        period = st.radio("Date Range", ["1y","2y","5y","10y"], index=2, horizontal=True)
    else:
        period = "1y"
        tf_options = {"1 Min":"1min","5 Min":"5min","15 Min":"15min","1 Hour":"1hour"}
        tf_label   = st.radio("Candle Size", list(tf_options.keys()), index=1, horizontal=True)
        st.session_state["candle_tf"] = tf_options[tf_label]

    st.markdown("---")
    st.markdown("**Benchmarks**")
    include_spy = st.checkbox("S&P 500 (SPY)", value=True)
    include_qqq = st.checkbox("NASDAQ (QQQ)", value=True)

    if mode == "Investor Mode":
        st.markdown("---")
        st.markdown("**Peer Comparison**")
        peers_input = st.text_input("Peer Tickers (optional)", placeholder="e.g. GOOGL, AMZN")

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
    else:
        peers_input = ""
        do_mc = do_sector = do_corr = do_sr = do_news = do_peers = False
        n_sims = 1000
        n_horizon = 252

    st.markdown("---")
    run_btn = st.button("▶  Run Analysis", type="primary", use_container_width=True)

    st.markdown("---")
    st.markdown("### Stay Updated")
    email_input = st.text_input("Get notified about new features", placeholder="your@email.com")
    if st.button("Join waitlist", use_container_width=True):
        if email_input and "@" in email_input:
            st.success("Thanks! We'll be in touch.")
        else:
            st.error("Please enter a valid email.")

# ── Payment modal ─────────────────────────────────────────────────────────────
if st.session_state["show_payment"] and not st.session_state["is_pro"]:
    st.markdown("---")
    st.markdown("### Upgrade to StockWizard Pro")
    col1, col2 = st.columns([2, 1])
    with col1:
        email_for_payment = st.text_input("Your email address", placeholder="you@email.com", key="pay_email")
    with col2:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("Continue to Payment →", type="primary"):
            if email_for_payment and "@" in email_for_payment:
                base_url = "https://stockwizard-production.up.railway.app"
                session  = create_checkout_session(base_url, base_url)
                if session:
                    st.markdown(f"""
                    <meta http-equiv="refresh" content="0; url={session.url}">
                    <a href="{session.url}">Click here if not redirected</a>
                    """, unsafe_allow_html=True)
            else:
                st.error("Please enter a valid email.")
    if st.button("Cancel", key="cancel_payment"):
        st.session_state["show_payment"] = False
        st.rerun()
    st.markdown("---")

# ── Landing page ──────────────────────────────────────────────────────────────
if not run_btn and not ticker_input:

    # Market movers
    st.markdown('<div class="section-header">Market Movers Today</div>', unsafe_allow_html=True)
    with st.spinner("Loading market data..."):
        gainers, losers = get_top_movers(POLYGON_API_KEY)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Top Gainers**")
        if gainers:
            for g in gainers:
                st.markdown(f"""
                <div class="mover-card">
                    <span style="font-weight:600;color:#0f172a">{g['Ticker']}</span>
                    <span style="font-family:'DM Mono',monospace;font-size:0.9rem">{g['Price']}</span>
                    <span style="color:#16a34a;font-family:'DM Mono',monospace;font-weight:600">{g['Change']}</span>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.caption("Market data unavailable")

    with col2:
        st.markdown("**Top Losers**")
        if losers:
            for l in losers:
                st.markdown(f"""
                <div class="mover-card">
                    <span style="font-weight:600;color:#0f172a">{l['Ticker']}</span>
                    <span style="font-family:'DM Mono',monospace;font-size:0.9rem">{l['Price']}</span>
                    <span style="color:#dc2626;font-family:'DM Mono',monospace;font-weight:600">{l['Change']}</span>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.caption("Market data unavailable")

    # Feature cards
    st.markdown('<div class="section-header">What You Get</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("""
        <div class="metric-card">
            <div class="metric-label">Free — Investor Mode</div>
            <div style="text-align:left;margin-top:0.75rem;font-size:0.88rem;color:#334155;line-height:1.9">
            ◈ 10-sheet Excel report<br>◈ Monte Carlo simulation<br>
            ◈ RSI, MACD, Bollinger Bands<br>◈ Support & resistance<br>
            ◈ Correlation matrix<br>◈ News headlines<br>◈ Up to 5 year history
            </div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown("""
        <div class="metric-card" style="border-color:#38bdf8">
            <div class="metric-label" style="color:#38bdf8">Pro — Day Trader Mode</div>
            <div style="text-align:left;margin-top:0.75rem;font-size:0.88rem;color:#334155;line-height:1.9">
            ◈ Live intraday candlestick charts<br>◈ Real-time price (30s refresh)<br>
            ◈ 1min 5min 15min 1hr candles<br>◈ Volume spike detection<br>
            ◈ Pre-market & after-hours<br>◈ Live RSI & MACD<br>◈ 10 year history
            </div>
        </div>""", unsafe_allow_html=True)
    with c3:
        st.markdown("""
        <div class="metric-card">
            <div class="metric-label">Data Source</div>
            <div style="text-align:left;margin-top:0.75rem;font-size:0.88rem;color:#334155;line-height:1.9">
            ◈ Licensed via Polygon.io<br>◈ Not Yahoo Finance<br>
            ◈ Commercial use approved<br>◈ Daily & intraday data<br>
            ◈ Company fundamentals<br>◈ News & events<br>◈ Sector ETF benchmarks
            </div>
        </div>""", unsafe_allow_html=True)

    # Pricing
    render_pricing_section()

    # Founders section
    st.markdown('<div class="section-header">Meet the Team</div>', unsafe_allow_html=True)
    fc1, fc2 = st.columns(2)
    with fc1:
        st.markdown("""
        <div class="founder-card">
            <div class="founder-quote">
                "I built StockWizard because I was tired of spending hours pulling financial
                data into spreadsheets manually. I wanted a tool that gives any trader —
                beginner or pro — a professional report in seconds."
            </div>
            <div class="founder-name">Wyatt Stratton</div>
            <div class="founder-role">Founder</div>
            <div class="founder-school">Indiana University Bloomington</div>
        </div>
        """, unsafe_allow_html=True)
    with fc2:
        st.markdown("""
        <div class="founder-card">
            <div class="founder-quote">
                "My role was making sure the analysis was rigorous and the experience was seamless.
                From Monte Carlo simulation to the overall architecture, I wanted every number
                StockWizard produces to be something a professional quant would stand behind."
            </div>
            <div class="founder-name">Nicholas Carriello</div>
            <div class="founder-role">Co-Founder & Quant Lead</div>
            <div class="founder-school">Bucknell University</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("""
    <div class="disclaimer">
        ⚠ This tool is for informational purposes only and does not constitute financial advice.
        Data provided by Polygon.io. Past performance is not indicative of future results.
    </div>
    """, unsafe_allow_html=True)

# ── Analysis ──────────────────────────────────────────────────────────────────
elif run_btn or ticker_input:

    if not ticker_input:
        st.error("Please enter a ticker symbol.")
        st.stop()

    with st.spinner(f"Validating {ticker_input}..."):
        valid, info = validate_ticker(ticker_input, POLYGON_API_KEY)

    if not valid:
        live_check = get_live_price(ticker_input, POLYGON_API_KEY)
        if not live_check or not live_check.get("price"):
            st.error(f"❌ Ticker '{ticker_input}' not found. Check the symbol and try again.")
            st.stop()

    # ── LIVE PRICE TICKER
    live = get_live_price(ticker_input, POLYGON_API_KEY)
    if live:
        color    = "#22c55e" if live["change"] >= 0 else "#ef4444"
        sign     = "+" if live["change"] >= 0 else ""
        change_cls = "live-change-pos" if live["change"] >= 0 else "live-change-neg"
        st.markdown(f"""
        <div class="live-ticker">
            <div>
                <span style="color:#94a3b8;font-size:0.8rem;font-weight:600;
                             letter-spacing:0.5px;text-transform:uppercase">{ticker_input}</span>
                <div class="live-price">${live['price']:,.2f}</div>
                <span class="{change_cls}">{sign}{live['change']:,.2f} ({sign}{live['pct']:.2f}%)</span>
            </div>
            <div style="text-align:right">
                <div><span class="live-dot"></span>
                     <span style="color:#22c55e;font-size:0.78rem">Live</span></div>
                <div style="color:#475569;font-size:0.75rem;margin-top:4px">
                     Updated {live['time']}</div>
                <div style="color:#475569;font-size:0.72rem">Refreshes every 30s</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    # ── DAY TRADER MODE ───────────────────────────────────────────────────────
    if mode == "Day Trader Mode" and st.session_state["is_pro"]:

        st.markdown(f'<div class="section-header">Day Trader Mode <span class="pro-badge">PRO</span></div>',
                    unsafe_allow_html=True)

        tf = st.session_state["candle_tf"]
        tf_map = {"1min":(1,"minute"),"5min":(5,"minute"),
                  "15min":(15,"minute"),"1hour":(1,"hour")}
        mult, span = tf_map.get(tf, (5,"minute"))

        with st.spinner("Loading intraday data..."):
            intraday_df = get_intraday_data(ticker_input, POLYGON_API_KEY, mult, span)

        if intraday_df is not None and not intraday_df.empty:

            # Candlestick chart
            fig_candle = go.Figure(data=[go.Candlestick(
                x=intraday_df["Time"],
                open=intraday_df["Open"],
                high=intraday_df["High"],
                low=intraday_df["Low"],
                close=intraday_df["Close"],
                increasing_line_color="#22c55e",
                decreasing_line_color="#ef4444",
                name=ticker_input,
            )])

            # Add volume as bar chart below
            fig_candle.add_trace(go.Bar(
                x=intraday_df["Time"],
                y=intraday_df["Volume"],
                name="Volume",
                marker_color=["#22c55e" if c >= o else "#ef4444"
                              for c, o in zip(intraday_df["Close"], intraday_df["Open"])],
                opacity=0.4,
                yaxis="y2",
            ))

            fig_candle.update_layout(
                height=500,
                template="plotly_white",
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis_rangeslider_visible=False,
                yaxis=dict(title="Price ($)", showgrid=True, gridcolor="#f1f5f9", side="right"),
                yaxis2=dict(title="Volume", overlaying="y", side="left",
                            showgrid=False, range=[0, intraday_df["Volume"].max() * 5]),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
                font=dict(family="DM Sans"),
            )
            st.plotly_chart(fig_candle, use_container_width=True)

            # Live indicators
            try:
                import ta
                closes = intraday_df["Close"]
                if len(closes) >= 14:
                    intraday_df["RSI"] = ta.momentum.RSIIndicator(closes, window=14).rsi()
                    macd_ind = ta.trend.MACD(closes)
                    intraday_df["MACD"]        = macd_ind.macd()
                    intraday_df["MACD_Signal"] = macd_ind.macd_signal()
                    intraday_df["MACD_Hist"]   = intraday_df["MACD"] - intraday_df["MACD_Signal"]

                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown('<div class="section-header">RSI (14)</div>', unsafe_allow_html=True)
                        fig_rsi = go.Figure()
                        fig_rsi.add_trace(go.Scatter(x=intraday_df["Time"], y=intraday_df["RSI"],
                                                     line=dict(color="#8b5cf6", width=1.5), name="RSI"))
                        fig_rsi.add_hline(y=70, line_dash="dash", line_color="#ef4444", opacity=0.6)
                        fig_rsi.add_hline(y=30, line_dash="dash", line_color="#22c55e", opacity=0.6)
                        fig_rsi.update_layout(height=200, template="plotly_white",
                                              margin=dict(l=0,r=0,t=5,b=0),
                                              yaxis=dict(range=[0,100]),
                                              font=dict(family="DM Sans"))
                        st.plotly_chart(fig_rsi, use_container_width=True)

                    with col2:
                        st.markdown('<div class="section-header">MACD</div>', unsafe_allow_html=True)
                        fig_macd = go.Figure()
                        fig_macd.add_trace(go.Scatter(x=intraday_df["Time"], y=intraday_df["MACD"],
                                                      line=dict(color="#0ea5e9", width=1.5), name="MACD"))
                        fig_macd.add_trace(go.Scatter(x=intraday_df["Time"], y=intraday_df["MACD_Signal"],
                                                      line=dict(color="#f59e0b", width=1.5), name="Signal"))
                        colors = ["#22c55e" if v >= 0 else "#ef4444" for v in intraday_df["MACD_Hist"]]
                        fig_macd.add_trace(go.Bar(x=intraday_df["Time"], y=intraday_df["MACD_Hist"],
                                                  marker_color=colors, name="Histogram", opacity=0.6))
                        fig_macd.update_layout(height=200, template="plotly_white",
                                               margin=dict(l=0,r=0,t=5,b=0),
                                               font=dict(family="DM Sans"))
                        st.plotly_chart(fig_macd, use_container_width=True)
            except Exception:
                pass

            # Key intraday stats
            st.markdown('<div class="section-header">Intraday Stats</div>', unsafe_allow_html=True)
            ic1,ic2,ic3,ic4 = st.columns(4)
            day_open  = intraday_df["Open"].iloc[0]
            day_high  = intraday_df["High"].max()
            day_low   = intraday_df["Low"].min()
            day_vol   = intraday_df["Volume"].sum()
            day_close = intraday_df["Close"].iloc[-1]
            day_chg   = (day_close - day_open) / day_open * 100

            for col, label, value in [
                (ic1, "Day Open",   f"${day_open:,.2f}"),
                (ic2, "Day High",   f"${day_high:,.2f}"),
                (ic3, "Day Low",    f"${day_low:,.2f}"),
                (ic4, "Volume",     f"{day_vol:,.0f}"),
            ]:
                with col:
                    st.markdown(f"""
                    <div class="metric-card">
                        <div class="metric-label">{label}</div>
                        <div class="metric-value">{value}</div>
                    </div>""", unsafe_allow_html=True)

        else:
            st.warning("No intraday data available. Market may be closed.")

        # Auto-refresh every 30 seconds
        time.sleep(0.1)
        st.markdown("""
        <script>
        setTimeout(function() { window.location.reload(); }, 30000);
        </script>
        """, unsafe_allow_html=True)

    elif mode == "Day Trader Mode" and not st.session_state["is_pro"]:
        st.markdown("""
        <div class="pro-locked">
            <div style="font-size:1.5rem;margin-bottom:0.5rem">🔒</div>
            <div style="color:#fff;font-weight:600;font-size:1.1rem;margin-bottom:0.5rem">
                Day Trader Mode is Pro Only
            </div>
            <div style="color:#94a3b8;font-size:0.88rem;margin-bottom:1.25rem">
                Get live intraday charts, real-time updates, and full day trading tools for $9.99/month
            </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Upgrade to Pro — $9.99/month", type="primary", use_container_width=False, key="upgrade_locked"):
            st.session_state["show_payment"] = True
            st.rerun()

    # ── INVESTOR MODE ─────────────────────────────────────────────────────────
    if mode == "Investor Mode":

        benchmarks  = []
        if include_spy: benchmarks.append("SPY")
        if include_qqq: benchmarks.append("QQQ")
        peers_list  = [p.strip().upper() for p in peers_input.split(",") if p.strip()] if peers_input else []

        progress  = st.progress(0, text="Starting analysis...")
        logs      = st.empty()
        log_lines = []

        def log(msg):
            log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}]  {msg}")
            logs.code("\n".join(log_lines[-12:]), language=None)

        try:
            progress.progress(10, text="Downloading price data...")
            df = fetch_stock_data(ticker_input, period=period,
                                  benchmark_tickers=benchmarks,
                                  api_key=POLYGON_API_KEY, log=log)
            progress.progress(25, text="Fetching company details...")
            company_details = fetch_company_details(ticker_input, POLYGON_API_KEY, log=log)
            sector = company_details.get("Sector", "Unknown")

            news_list = []
            if do_news:
                progress.progress(35, text="Fetching news...")
                news_list = fetch_news(ticker_input, POLYGON_API_KEY, log=log)

            peer_df = None
            if do_peers and peers_list:
                progress.progress(45, text="Fetching peer data...")
                peer_df = fetch_peer_comparison(ticker_input, peers_list, POLYGON_API_KEY, log=log)

            sector_df = None
            if do_sector:
                progress.progress(50, text="Fetching sector ETF...")
                sector_df = fetch_sector_data(ticker_input, period, POLYGON_API_KEY, sector, log=log)

            corr_matrix = None
            if do_corr:
                progress.progress(60, text="Building correlation matrix...")
                corr_matrix = build_correlation_matrix(df, benchmarks if benchmarks else None)

            resistance = support = None
            if do_sr:
                progress.progress(65, text="Detecting support & resistance...")
                resistance, support = detect_support_resistance(df)

            mc_sim_df = mc_summary = None
            if do_mc:
                progress.progress(75, text="Running Monte Carlo simulation...")
                mc_sim_df, mc_summary = run_monte_carlo(
                    df, n_simulations=n_sims, forecast_days=n_horizon, log=log)

            progress.progress(85, text="Generating summary...")
            ret      = df["Daily_Return"].dropna()
            ann_ret  = ret.mean() * 252
            ann_std  = ret.std() * np.sqrt(252)
            downside = ret[ret < 0].std() * np.sqrt(252)
            sharpe   = ann_ret / ann_std  if ann_std  else np.nan
            sortino  = ann_ret / downside if downside else np.nan
            summary_text = generate_summary_paragraph(
                ticker_input, df, company_details, mc_summary, sharpe, sortino)

            progress.progress(92, text="Building Excel report...")
            excel_buf = build_excel(
                ticker_input, df, period,
                company_details=company_details, sector_df=sector_df,
                mc_sim_df=mc_sim_df, mc_summary=mc_summary,
                news_list=news_list, peer_df=peer_df,
                corr_matrix=corr_matrix,
                resistance_levels=resistance, support_levels=support,
                summary_text=summary_text,
            )

            progress.progress(100, text="Complete!")
            time.sleep(0.3)
            progress.empty()
            logs.empty()

        except Exception as e:
            progress.empty()
            st.error(f"❌ Analysis failed: {e}")
            st.exception(e)
            st.stop()

        # ── Results ───────────────────────────────────────────────────────────
        latest     = df.iloc[-1]
        first      = df.iloc[0]
        period_ret = (latest["Close"] / first["Close"] - 1) * 100
        pos_neg    = lambda v: "positive" if v > 0 else ("negative" if v < 0 else "neutral")

        st.download_button(
            label=f"⬇  Download Excel Report — {ticker_input}_{period}_Analysis.xlsx",
            data=excel_buf,
            file_name=f"{ticker_input}_{period}_Analysis.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True, type="primary", key="download_top",
        )
        st.markdown("---")

        # Key metrics
        st.markdown('<div class="section-header">Key Metrics</div>', unsafe_allow_html=True)
        col1,col2,col3,col4,col5,col6 = st.columns(6)
        vol_val = df["Volatility_20d"].iloc[-1]
        for col, label, value, cls in [
            (col1,"Current Price",   f"${latest['Close']:,.2f}",                          "neutral"),
            (col2,"Period Return",   f"{period_ret:+.1f}%",                               pos_neg(period_ret)),
            (col3,"52W High",        f"${latest.get('52W_High',0):,.2f}",                 "neutral"),
            (col4,"52W Low",         f"${latest.get('52W_Low',0):,.2f}",                  "neutral"),
            (col5,"Sharpe Ratio",    f"{sharpe:.2f}" if pd.notna(sharpe) else "N/A",      pos_neg(sharpe) if pd.notna(sharpe) else "neutral"),
            (col6,"Ann. Volatility", f"{vol_val*100:.1f}%" if pd.notna(vol_val) else "N/A","neutral"),
        ]:
            with col:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-label">{label}</div>
                    <div class="metric-value {cls}">{value}</div>
                </div>""", unsafe_allow_html=True)

        # Price chart
        st.markdown('<div class="section-header">Price & Moving Averages</div>', unsafe_allow_html=True)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df["Date"], y=df["Close"], name="Close",
                                 line=dict(color="#0ea5e9", width=2)))
        for ma, color in [(20,"#f59e0b"),(50,"#8b5cf6"),(200,"#ef4444")]:
            if f"MA{ma}" in df.columns:
                fig.add_trace(go.Scatter(x=df["Date"], y=df[f"MA{ma}"],
                                         name=f"{ma}-Day MA",
                                         line=dict(color=color, width=1.2, dash="dot")))
        if do_sr and resistance:
            for r in resistance[:3]:
                fig.add_hline(y=r, line_dash="dash", line_color="#ef4444", opacity=0.5,
                              annotation_text=f"R ${r:,.0f}", annotation_position="right")
        if do_sr and support:
            for s in support[:3]:
                fig.add_hline(y=s, line_dash="dash", line_color="#16a34a", opacity=0.5,
                              annotation_text=f"S ${s:,.0f}", annotation_position="right")
        fig.update_layout(height=420, template="plotly_white",
                          margin=dict(l=0,r=0,t=10,b=0),
                          legend=dict(orientation="h",yanchor="bottom",y=1.02),
                          font=dict(family="DM Sans"))
        st.plotly_chart(fig, use_container_width=True)

        # RSI
        if "RSI14" in df.columns:
            st.markdown('<div class="section-header">RSI (14)</div>', unsafe_allow_html=True)
            fig_rsi = go.Figure()
            fig_rsi.add_trace(go.Scatter(x=df["Date"], y=df["RSI14"],
                                         line=dict(color="#8b5cf6",width=1.5), name="RSI"))
            fig_rsi.add_hline(y=70, line_dash="dash", line_color="#ef4444", opacity=0.6)
            fig_rsi.add_hline(y=30, line_dash="dash", line_color="#16a34a", opacity=0.6)
            fig_rsi.add_hrect(y0=70,y1=100,fillcolor="#ef4444",opacity=0.05)
            fig_rsi.add_hrect(y0=0, y1=30, fillcolor="#16a34a",opacity=0.05)
            fig_rsi.update_layout(height=200, template="plotly_white",
                                  margin=dict(l=0,r=0,t=5,b=0),
                                  yaxis=dict(range=[0,100]),
                                  font=dict(family="DM Sans"))
            st.plotly_chart(fig_rsi, use_container_width=True)

        # Bollinger Bands
        if "BB_Upper" in df.columns:
            st.markdown('<div class="section-header">Bollinger Bands</div>', unsafe_allow_html=True)
            fig_bb = go.Figure()
            fig_bb.add_trace(go.Scatter(x=df["Date"],y=df["BB_Upper"],
                                        line=dict(color="#94a3b8",width=1),name="Upper"))
            fig_bb.add_trace(go.Scatter(x=df["Date"],y=df["BB_Lower"],
                                        line=dict(color="#94a3b8",width=1),name="Lower",
                                        fill="tonexty",fillcolor="rgba(148,163,184,0.1)"))
            fig_bb.add_trace(go.Scatter(x=df["Date"],y=df["Close"],
                                        line=dict(color="#0ea5e9",width=1.5),name="Close"))
            fig_bb.add_trace(go.Scatter(x=df["Date"],y=df["BB_Middle"],
                                        line=dict(color="#f59e0b",width=1,dash="dot"),name="Middle"))
            fig_bb.update_layout(height=300,template="plotly_white",
                                 margin=dict(l=0,r=0,t=5,b=0),
                                 font=dict(family="DM Sans"))
            st.plotly_chart(fig_bb, use_container_width=True)

        # Monte Carlo
        if mc_summary:
            st.markdown('<div class="section-header">Monte Carlo Forecast</div>', unsafe_allow_html=True)
            mc_cols = st.columns(6)
            for col, label, value, color in [
                (mc_cols[0],"Bear (P5)",  f"${mc_summary['Bear Case (P5)']:,.2f}","#dc2626"),
                (mc_cols[1],"Low (P25)",  f"${mc_summary['Low Case (P25)']:,.2f}","#f59e0b"),
                (mc_cols[2],"Median",     f"${mc_summary['Median (P50)']:,.2f}",  "#0f172a"),
                (mc_cols[3],"Bull (P75)", f"${mc_summary['Bull Case (P75)']:,.2f}","#0ea5e9"),
                (mc_cols[4],"Best (P95)", f"${mc_summary['Best Case (P95)']:,.2f}","#16a34a"),
                (mc_cols[5],"Prob. Gain", mc_summary["Prob. of Gain"],             "#8b5cf6"),
            ]:
                with col:
                    st.markdown(f"""
                    <div class="metric-card">
                        <div class="metric-label">{label}</div>
                        <div class="metric-value" style="color:{color}">{value}</div>
                    </div>""", unsafe_allow_html=True)

            pcts = np.percentile(mc_sim_df.iloc[:,:200].values,[5,25,50,75,95],axis=1)
            x    = list(range(len(pcts[0])))
            fig_mc = go.Figure()
            fig_mc.add_trace(go.Scatter(x=x,y=pcts[4],name="P95",line=dict(color="#16a34a",width=1.5)))
            fig_mc.add_trace(go.Scatter(x=x,y=pcts[3],name="P75",line=dict(color="#0ea5e9",width=1),
                                        fill="tonexty",fillcolor="rgba(14,165,233,0.08)"))
            fig_mc.add_trace(go.Scatter(x=x,y=pcts[2],name="Median",line=dict(color="#0f172a",width=2)))
            fig_mc.add_trace(go.Scatter(x=x,y=pcts[1],name="P25",line=dict(color="#f59e0b",width=1),
                                        fill="tonexty",fillcolor="rgba(245,158,11,0.08)"))
            fig_mc.add_trace(go.Scatter(x=x,y=pcts[0],name="P5",line=dict(color="#dc2626",width=1.5)))
            fig_mc.update_layout(height=350,template="plotly_white",
                                 margin=dict(l=0,r=0,t=5,b=0),
                                 xaxis_title="Trading Days",yaxis_title="Price ($)",
                                 font=dict(family="DM Sans"))
            st.plotly_chart(fig_mc, use_container_width=True)

        # Volume
        st.markdown('<div class="section-header">Volume</div>', unsafe_allow_html=True)
        colors = ["#16a34a" if r>=0 else "#dc2626" for r in df["Daily_Return"].fillna(0)]
        fig_vol = go.Figure()
        fig_vol.add_trace(go.Bar(x=df["Date"],y=df["Volume"],marker_color=colors,opacity=0.7))
        fig_vol.update_layout(height=200,template="plotly_white",
                              margin=dict(l=0,r=0,t=5,b=0),showlegend=False,
                              font=dict(family="DM Sans"))
        st.plotly_chart(fig_vol, use_container_width=True)

        # Correlation
        if corr_matrix is not None:
            st.markdown('<div class="section-header">Correlation Matrix</div>', unsafe_allow_html=True)
            fig_corr = px.imshow(corr_matrix, text_auto=".2f",
                                 color_continuous_scale=["#dc2626","#ffffff","#0ea5e9"],
                                 zmin=-1, zmax=1, aspect="auto")
            fig_corr.update_layout(height=280,margin=dict(l=0,r=0,t=5,b=0),
                                   font=dict(family="DM Sans"))
            st.plotly_chart(fig_corr, use_container_width=True)

        # News
        if news_list:
            st.markdown('<div class="section-header">Recent News</div>', unsafe_allow_html=True)
            for item in news_list[:8]:
                st.markdown(f"""
                <div style="padding:0.6rem 0;border-bottom:1px solid #f1f5f9">
                    <div style="font-size:0.82rem;font-weight:500">
                        <a href="{item['URL']}" target="_blank"
                           style="text-decoration:none;color:#0f172a">{item['Headline']}</a>
                    </div>
                    <div style="font-size:0.72rem;color:#94a3b8;margin-top:2px">
                        {item['Publisher']} &nbsp;·&nbsp; {item['Date']}
                    </div>
                </div>""", unsafe_allow_html=True)

        # Peers
        if peer_df is not None and not peer_df.empty:
            st.markdown('<div class="section-header">Peer Comparison</div>', unsafe_allow_html=True)
            st.dataframe(peer_df, use_container_width=True, hide_index=True)

        # Summary
        st.markdown('<div class="section-header">Automated Analysis Summary</div>', unsafe_allow_html=True)
        st.markdown(f"""
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
                    padding:1.25rem;font-size:0.88rem;color:#334155;
                    line-height:1.75;font-style:italic">
            {summary_text}
        </div>""", unsafe_allow_html=True)

        # Bottom download
        st.markdown("---")
        excel_buf.seek(0)
        st.download_button(
            label=f"⬇  Download Excel Report — {ticker_input}_{period}_Analysis.xlsx",
            data=excel_buf,
            file_name=f"{ticker_input}_{period}_Analysis.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True, type="primary", key="download_bottom",
        )

    st.markdown("""
    <div class="disclaimer">
        ⚠ This report is generated programmatically and does not constitute financial advice.
        Data provided by Polygon.io. Past performance is not indicative of future results.
        Always do your own research before making investment decisions.
    </div>
    """, unsafe_allow_html=True)
