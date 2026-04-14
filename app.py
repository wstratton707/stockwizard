import os
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from data import (
    validate_ticker, fetch_stock_data, fetch_ohlcv, fetch_company_details,
    fetch_news, fetch_peer_comparison, fetch_sector_data, fetch_bond_data,
    fetch_next_earnings, detect_asset_type,
    fetch_crypto_data, fetch_crypto_details, fetch_etf_details,
    CRYPTO_TICKERS,
)
try:
    from streamlit_autorefresh import st_autorefresh
    _HAS_AUTOREFRESH = True
except ImportError:
    _HAS_AUTOREFRESH = False
from portfolio_data import BOND_UNIVERSE, BOND_DURATION_MAP
from analysis import (
    detect_support_resistance, build_correlation_matrix,
    run_monte_carlo, run_custom_forecast, generate_summary_paragraph
)
from excel_builder import build_excel
from pptx_builder import build_stock_pptx, build_portfolio_pptx, PPTX_AVAILABLE
from live_data import get_live_price, get_intraday_data, get_top_movers, get_tape_prices
from payments import render_pricing_section, create_checkout_session, verify_session, check_subscription
from portfolio_builder import render_portfolio_builder
from stress_test import render_stress_test
from constants import DEV_MODE_FREE
from disclaimers import render_inline, render_section, render_footer
import disclaimers as _disc

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="StockWizard",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

POLYGON_API_KEY  = os.environ.get("POLYGON_API_KEY", "").strip()
FMP_API_KEY      = os.environ.get("FMP_API_KEY", "").strip()
SHOW_PRICING     = False  # Set True when ready to accept payments

if not POLYGON_API_KEY:
    st.error("❌ POLYGON_API_KEY is not configured. Contact support or check your environment variables.")
    st.stop()

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
  --bg:       #f1f5f9;
  --surface:  #ffffff;
  --surface2: #f8fafc;
  --border:   #e2e8f0;
  --border2:  #cbd5e1;
  --accent:   #1d4ed8;
  --accent2:  #059669;
  --accent3:  #3b82f6;
  --text:     #0f172a;
  --muted:    #64748b;
  --dim:      #94a3b8;
  --red:      #dc2626;
  --green:    #059669;
  --shadow-sm: 0 1px 3px rgba(15,23,42,0.08), 0 1px 2px rgba(15,23,42,0.04);
  --shadow-md: 0 4px 12px rgba(15,23,42,0.08), 0 2px 4px rgba(15,23,42,0.04);
}

html, body, [class*="css"] {
  font-family: 'Inter', sans-serif;
  color: #0f172a;
  -webkit-font-smoothing: antialiased;
}
#MainMenu {visibility:hidden;} footer {visibility:hidden;} .stDeployButton {display:none;}
.block-container { padding-top: 1.75rem !important; max-width: 1200px !important; }

[data-testid="stAppViewContainer"] { background: #f1f5f9 !important; }
[data-testid="stHeader"] { background: #f1f5f9 !important; border-bottom: 1px solid #e2e8f0 !important; }
.main { background: #f1f5f9 !important; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
  background: #0c1e35 !important;
  border-right: 1px solid rgba(255,255,255,0.06);
  box-shadow: 2px 0 12px rgba(0,0,0,0.12);
}
[data-testid="stSidebar"] * { color: #cbd5e1 !important; }
[data-testid="stSidebar"] .stMarkdown h3 {
  color: #7dd3fc !important; font-size: 0.7rem !important; font-weight: 600 !important;
  letter-spacing: 1.2px !important; text-transform: uppercase !important;
  font-family: 'Inter', sans-serif !important;
}
[data-testid="stSidebar"] label {
  color: #94a3b8 !important; font-size: 0.8rem !important;
  font-family: 'Inter', sans-serif !important; font-weight: 500 !important;
}
[data-testid="stSidebar"] .stCheckbox label { color: #cbd5e1 !important; }
[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.08) !important; }
[data-testid="stSidebar"] .stButton button {
  background: #1d4ed8 !important; color: #ffffff !important;
  border: none !important; border-radius: 8px !important;
  font-weight: 600 !important; font-size: 0.82rem !important;
  font-family: 'Inter', sans-serif !important; letter-spacing: 0.3px !important;
  box-shadow: 0 1px 3px rgba(29,78,216,0.4) !important;
  transition: all 0.2s !important;
}
[data-testid="stSidebar"] .stButton button:hover {
  background: #1e40af !important; box-shadow: 0 4px 12px rgba(29,78,216,0.4) !important;
  transform: translateY(-1px) !important;
}
[data-testid="stSidebar"] [data-testid="stTextInput"] input,
[data-testid="stSidebar"] [data-testid="stNumberInput"] input {
  background: #ffffff !important; border: 1px solid #bfdbfe !important;
  color: #1d4ed8 !important; border-radius: 8px !important; font-size: 0.9rem !important;
  font-family: 'Inter', sans-serif !important;
}
[data-testid="stSidebar"] [data-testid="stTextInput"] input::placeholder,
[data-testid="stSidebar"] [data-testid="stNumberInput"] input::placeholder { color: #93c5fd !important; }
[data-testid="stSidebar"] [data-testid="stTextInput"] input:focus,
[data-testid="stSidebar"] [data-testid="stNumberInput"] input:focus {
  border-color: #3b82f6 !important; box-shadow: 0 0 0 3px rgba(59,130,246,0.2) !important;
}
[data-testid="stSidebar"] .stSelectbox > div > div {
  background: #ffffff !important; border: 1px solid #bfdbfe !important;
  color: #1d4ed8 !important; border-radius: 8px !important; font-family: 'Inter', sans-serif !important;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
  background: #ffffff; border-radius: 12px; padding: 5px; gap: 2px;
  border: 1px solid #e2e8f0; box-shadow: var(--shadow-sm);
}
.stTabs [data-baseweb="tab"] {
  border-radius: 8px; font-size: 0.82rem; font-weight: 500;
  font-family: 'Inter', sans-serif;
  color: #64748b; padding: 0.45rem 1.25rem; border: none !important;
  background: transparent !important; letter-spacing: 0.1px;
  transition: color 0.15s;
}
.stTabs [aria-selected="true"] {
  background: #1d4ed8 !important; color: #ffffff !important;
  font-weight: 600 !important;
  box-shadow: 0 1px 4px rgba(29,78,216,0.35) !important;
}
.stTabs [data-baseweb="tab-panel"] { padding-top: 1.75rem !important; }

/* ── Main header ── */
.main-header {
  background: linear-gradient(135deg, #0c1e35 0%, #1a3557 55%, #0f2a4a 100%);
  padding: 2.5rem 3rem; border-radius: 16px; margin-bottom: 2rem;
  border: 1px solid rgba(255,255,255,0.06);
  box-shadow: 0 8px 32px rgba(12,30,53,0.25), 0 2px 8px rgba(12,30,53,0.15);
  position: relative; overflow: hidden;
}
.main-header::before {
  content: ''; position: absolute; top: -30%; right: -5%;
  width: 500px; height: 500px; border-radius: 50%;
  background: radial-gradient(circle, rgba(59,130,246,0.12) 0%, transparent 65%);
  pointer-events: none;
}
.main-header::after {
  content: ''; position: absolute; bottom: -40%; left: 20%;
  width: 300px; height: 300px; border-radius: 50%;
  background: radial-gradient(circle, rgba(29,78,216,0.08) 0%, transparent 70%);
  pointer-events: none;
}
.main-header-logo { display: flex; align-items: center; gap: 0.85rem; margin-bottom: 0.7rem; }
.main-header-logo-icon {
  width: 44px; height: 44px;
  background: linear-gradient(135deg, #3b82f6, #1d4ed8);
  border-radius: 10px; display: flex; align-items: center; justify-content: center;
  font-family: 'JetBrains Mono', monospace; font-size: 1.1rem; font-weight: 600;
  color: #ffffff; flex-shrink: 0;
  box-shadow: 0 4px 12px rgba(59,130,246,0.4);
}
.main-header h1 {
  font-family: 'Inter', sans-serif; color: #f8fafc;
  font-size: 1.85rem; margin: 0; font-weight: 700;
  letter-spacing: -0.5px;
}
.main-header h1 span { color: #7dd3fc; }
.main-header-sub {
  color: #94a3b8; font-size: 0.8rem; font-family: 'Inter', sans-serif;
  margin: 0.45rem 0 0; display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap;
}
.main-header-sub .badge {
  background: rgba(59,130,246,0.18); color: #93c5fd;
  border: 1px solid rgba(59,130,246,0.3);
  border-radius: 20px; padding: 2px 10px;
  font-size: 0.7rem; font-weight: 600; font-family: 'Inter', sans-serif;
  letter-spacing: 0.2px;
}
.main-header-stats {
  display: flex; gap: 2.5rem; margin-top: 1.75rem; padding-top: 1.5rem;
  border-top: 1px solid rgba(255,255,255,0.08);
}
.main-header-stat { text-align: left; }
.main-header-stat-val {
  font-family: 'JetBrains Mono', monospace; font-size: 1.5rem;
  font-weight: 600; color: #7dd3fc; line-height: 1;
}
.main-header-stat-lbl {
  font-family: 'Inter', sans-serif; font-size: 0.65rem; color: #4a6a8e;
  text-transform: uppercase; letter-spacing: 0.8px; margin-top: 5px; font-weight: 500;
}

/* ── Live ticker ── */
.live-ticker {
  background: #ffffff; border: 1px solid #e2e8f0;
  border-radius: 14px; padding: 1.25rem 1.75rem; margin-bottom: 1.25rem;
  display: flex; justify-content: space-between; align-items: center;
  box-shadow: var(--shadow-sm);
}
.live-price {
  font-family: 'JetBrains Mono', monospace; font-size: 2.2rem;
  font-weight: 500; color: #0f172a; line-height: 1;
}
.live-change-pos { font-family: 'JetBrains Mono', monospace; font-size: 1rem; color: #059669; font-weight: 500; }
.live-change-neg { font-family: 'JetBrains Mono', monospace; font-size: 1rem; color: #dc2626; font-weight: 500; }
.live-dot { width: 7px; height: 7px; background: #059669; border-radius: 50%; display: inline-block; margin-right: 6px; animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

/* ── Metric cards ── */
.metric-card {
  background: #ffffff; border: 1px solid #e8eef6;
  border-radius: 12px; padding: 1.1rem 0.6rem; text-align: center;
  position: relative; overflow: visible;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.2s, transform 0.2s;
  min-width: 0;
}
.metric-card::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, #1d4ed8, #3b82f6);
  border-radius: 12px 12px 0 0;
}
.metric-card:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}
.metric-label {
  font-family: 'Inter', sans-serif; font-size: 0.63rem; font-weight: 600;
  letter-spacing: 0.4px; text-transform: uppercase; color: #94a3b8;
  margin-bottom: 0.45rem; white-space: nowrap;
}
.metric-value {
  font-family: 'JetBrains Mono', monospace;
  font-size: clamp(0.82rem, 1.05vw, 1.05rem);
  font-weight: 600; color: #0f172a;
  white-space: nowrap; line-height: 1.3;
}
.metric-value.positive { color: #059669; }
.metric-value.negative { color: #dc2626; }
.metric-value.neutral  { color: #1d4ed8; }

/* ── Section headers ── */
.section-header {
  font-family: 'Inter', sans-serif; font-size: 0.7rem; font-weight: 700;
  letter-spacing: 0.8px; text-transform: uppercase; color: #475569;
  padding: 0 0 0.6rem 0.75rem; border-bottom: 1px solid #e2e8f0;
  margin-bottom: 1.1rem; margin-top: 2rem;
  display: flex; align-items: center; gap: 0.6rem;
  border-left: 3px solid #1d4ed8;
}
.section-header::before { content: none; }

/* ── Feature cards ── */
.feature-card {
  background: #ffffff; border: 1px solid #e8eef6;
  border-radius: 14px; padding: 1.6rem; height: 100%;
  box-shadow: var(--shadow-sm);
  transition: box-shadow 0.2s, transform 0.2s;
}
.feature-card:hover {
  box-shadow: var(--shadow-md); transform: translateY(-2px);
  border-color: #bfdbfe;
}
.feature-card-icon { font-size: 1.6rem; margin-bottom: 0.85rem; display: block; }
.feature-card-title {
  font-family: 'Inter', sans-serif; font-size: 0.95rem; font-weight: 700;
  color: #0f172a; margin-bottom: 0.3rem; letter-spacing: -0.1px;
}
.feature-card-subtitle { font-family: 'Inter', sans-serif; font-size: 0.75rem; color: #64748b; margin-bottom: 1rem; }
.feature-card-list { font-size: 0.82rem; color: #64748b; line-height: 2.1; list-style: none; padding: 0; margin: 0; }
.feature-card-list li::before { content: "→ "; color: #3b82f6; font-weight: 600; }

/* ── Quick-start chips ── */
.quickstart-bar { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 1rem; }
.quickstart-chip {
  background: #ffffff; border: 1px solid #e2e8f0; border-radius: 20px;
  padding: 5px 16px; font-size: 0.78rem; font-weight: 600;
  font-family: 'Inter', sans-serif; color: #64748b; cursor: pointer;
  letter-spacing: 0.2px; transition: all 0.15s;
  box-shadow: var(--shadow-sm);
}
.quickstart-chip:hover {
  background: #eff6ff; border-color: #93c5fd; color: #1d4ed8;
  box-shadow: 0 2px 8px rgba(29,78,216,0.12);
}

/* ── Mover cards ── */
.mover-card {
  background: #ffffff; border: 1px solid #e8eef6; border-radius: 10px;
  padding: 0.75rem 1rem; margin-bottom: 0.5rem;
  display: flex; justify-content: space-between; align-items: center;
  box-shadow: var(--shadow-sm); transition: box-shadow 0.15s;
}
.mover-card:hover { box-shadow: var(--shadow-md); }

/* ── Stats bar ── */
.stats-bar {
  display: flex; gap: 1px; background: #e2e8f0;
  border-radius: 14px; overflow: hidden; margin-bottom: 1.75rem;
  border: 1px solid #e2e8f0; box-shadow: var(--shadow-sm);
}
.stats-bar-item { flex: 1; background: #ffffff; padding: 1rem 1rem; text-align: center; }
.stats-bar-val {
  font-family: 'JetBrains Mono', monospace; font-size: 1.15rem;
  font-weight: 600; color: #1d4ed8;
}
.stats-bar-lbl {
  font-family: 'Inter', sans-serif; font-size: 0.65rem; color: #64748b;
  text-transform: uppercase; letter-spacing: 0.6px; margin-top: 4px; font-weight: 500;
}

/* ── Pro badge ── */
.pro-badge {
  background: linear-gradient(135deg, #1d4ed8, #3b82f6); color: #ffffff;
  font-family: 'Inter', sans-serif; font-size: 0.58rem; font-weight: 700;
  padding: 3px 9px; border-radius: 20px; letter-spacing: 0.5px;
  margin-left: 8px; vertical-align: middle;
  box-shadow: 0 1px 4px rgba(29,78,216,0.35);
}

/* ── Founder cards ── */
.founder-card {
  background: #ffffff; border: 1px solid #e8eef6;
  border-radius: 14px; padding: 1.75rem;
  box-shadow: var(--shadow-sm);
}
.founder-quote {
  font-family: 'Inter', sans-serif; font-size: 0.88rem; color: #64748b;
  line-height: 1.85; font-style: italic; margin-bottom: 1.25rem;
  border-left: 3px solid #3b82f6; padding-left: 1rem;
}
.founder-name  { font-family: 'Inter', sans-serif; font-weight: 700; color: #0f172a; font-size: 0.92rem; }
.founder-school{ font-family: 'Inter', sans-serif; font-size: 0.75rem; color: #64748b; margin-top: 3px; }
.founder-role  { font-family: 'Inter', sans-serif; font-size: 0.72rem; color: #1d4ed8; margin-top: 3px; font-weight: 600; }

/* ── Disclaimer ── */
.disclaimer {
  background: #f0f7ff; border: 1px solid #bfdbfe;
  border-radius: 10px; padding: 0.85rem 1.1rem;
  font-family: 'Inter', sans-serif; font-size: 0.72rem;
  color: #475569; margin-top: 1.5rem; line-height: 1.6;
}

/* ── Pro locked ── */
.pro-locked {
  background: #ffffff; border: 1px solid #e8eef6;
  border-radius: 16px; padding: 2.5rem; text-align: center;
  margin: 1rem 0; box-shadow: var(--shadow-sm);
}

/* ── Tooltip ── */
.tooltip-wrap { position: relative; display: inline-block; cursor: help; }
.tooltip-wrap .tooltip-text {
  visibility: hidden; opacity: 0; background: #0f172a; color: #f8fafc;
  font-family: 'Inter', sans-serif; font-size: 0.72rem;
  border-radius: 8px; padding: 0.55rem 0.85rem;
  position: absolute; z-index: 999; bottom: 130%; left: 50%; transform: translateX(-50%);
  width: 210px; text-align: left; line-height: 1.55;
  box-shadow: 0 8px 24px rgba(0,0,0,0.18); border: 1px solid rgba(255,255,255,0.08);
  transition: opacity 0.15s; pointer-events: none;
}
.tooltip-wrap:hover .tooltip-text { visibility: visible; opacity: 1; }

/* ── Analysis summary ── */
.summary-box {
  background: #ffffff; border: 1px solid #e8eef6;
  border-left: 4px solid #1d4ed8; border-radius: 12px;
  padding: 1.5rem 1.75rem; font-size: 0.88rem; color: #475569;
  line-height: 1.9; font-family: 'Inter', sans-serif;
  box-shadow: var(--shadow-sm);
}

/* ── Download buttons ── */
.stDownloadButton button {
  border-radius: 8px !important; font-family: 'Inter', sans-serif !important;
  font-weight: 600 !important; font-size: 0.82rem !important;
  letter-spacing: 0.2px !important;
  padding: 0.6rem 1.4rem !important;
  transition: all 0.2s !important;
}
.stDownloadButton button:hover {
  transform: translateY(-1px) !important;
  box-shadow: 0 4px 12px rgba(0,0,0,0.12) !important;
}

/* ── Run button ── */
[data-testid="stSidebar"] .stButton [kind="primary"] {
  background: #1d4ed8 !important; color: #ffffff !important;
  border: none !important; font-weight: 600 !important;
  font-family: 'Inter', sans-serif !important;
  letter-spacing: 0.3px !important; font-size: 0.85rem !important;
}

/* ── Sidebar group label ── */
.sidebar-group {
  font-family: 'Inter', sans-serif; font-size: 0.65rem; font-weight: 700;
  letter-spacing: 1px; text-transform: uppercase; color: #7dd3fc;
  padding: 0.5rem 0 0.35rem; border-bottom: 1px solid rgba(255,255,255,0.07);
  margin-bottom: 0.6rem;
}

/* ── Page footer ── */
.page-footer {
  text-align: center; padding: 2rem 0 1rem; border-top: 1px solid #e2e8f0;
  margin-top: 3rem; font-family: 'Inter', sans-serif; color: #64748b;
  font-size: 0.72rem; font-weight: 400;
}
.page-footer a { color: #3b82f6; text-decoration: none; font-weight: 500; }
.page-footer a:hover { color: #1d4ed8; }

/* ── Dataframes ── */
[data-testid="stDataFrame"] {
  background: #ffffff !important; border-radius: 10px !important;
  box-shadow: var(--shadow-sm) !important;
}

/* ── Input focus rings ── */
[data-testid="stTextInput"] input:focus,
[data-testid="stNumberInput"] input:focus {
  border-color: #3b82f6 !important;
  box-shadow: 0 0 0 3px rgba(59,130,246,0.15) !important;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: #f1f5f9; border-radius: 10px; }
::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 10px; }
::-webkit-scrollbar-thumb:hover { background: #94a3b8; }

/* ── Ticker tape ── */
.ticker-tape-wrap {
  background: #091929; overflow: hidden;
  border-radius: 10px; margin-bottom: 1.5rem;
  padding: 0.6rem 0; border: 1px solid rgba(255,255,255,0.06);
  box-shadow: 0 2px 8px rgba(9,25,41,0.18);
}
.ticker-tape {
  display: inline-flex; white-space: nowrap;
  animation: scroll-left 40s linear infinite;
}
.ticker-tape:hover { animation-play-state: paused; cursor: default; }
@keyframes scroll-left {
  0%   { transform: translateX(0); }
  100% { transform: translateX(-50%); }
}
.t-item { display: inline-flex; align-items: center; gap: 0.5rem; padding: 0 1.5rem; }
.t-sym  { font-family:'JetBrains Mono',monospace; font-size:0.78rem; font-weight:700; color:#f1f5f9; }
.t-px   { font-family:'JetBrains Mono',monospace; font-size:0.78rem; color:#94a3b8; }
.t-up   { font-family:'JetBrains Mono',monospace; font-size:0.75rem; font-weight:600; color:#34d399; }
.t-dn   { font-family:'JetBrains Mono',monospace; font-size:0.75rem; font-weight:600; color:#f87171; }
.t-div  { color:#2d4a6e; font-size:0.55rem; padding: 0 0.25rem; }

/* ── Feature grid (icon chips) ── */
.feature-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 0.55rem; margin-top: 1rem;
}
.feature-chip {
  background: #f8fafc; border: 1px solid #e8eef6;
  border-radius: 8px; padding: 0.5rem 0.65rem;
  font-family: 'Inter', sans-serif; font-size: 0.76rem;
  color: #475569; font-weight: 500; line-height: 1.3;
  display: flex; align-items: center; gap: 0.4rem;
}
.feature-chip-icon { font-size: 0.85rem; flex-shrink: 0; }

/* ── Testimonials ── */
.testimonial-wrap { margin: 2rem 0; }
.testimonial-card {
  background: #ffffff; border: 1px solid #e8eef6;
  border-radius: 14px; padding: 1.5rem 1.75rem;
  box-shadow: var(--shadow-sm); height: 100%;
  position: relative;
}
.testimonial-quote-mark {
  font-size: 3.5rem; color: #dbeafe; font-family: Georgia, serif;
  line-height: 0.8; display: block; margin-bottom: 0.5rem;
}
.testimonial-text {
  font-family: 'Inter', sans-serif; font-size: 0.84rem;
  color: #475569; line-height: 1.75; margin-bottom: 1rem;
}
.testimonial-stars { color: #f59e0b; font-size: 0.82rem; margin-bottom: 0.75rem; letter-spacing: 1px; }
.testimonial-author { font-family: 'Inter', sans-serif; font-weight: 700; color: #0f172a; font-size: 0.85rem; }
.testimonial-role   { font-family: 'Inter', sans-serif; font-size: 0.73rem; color: #94a3b8; margin-top: 2px; }
.social-proof-bar {
  text-align: center; padding: 1.5rem;
  background: linear-gradient(135deg, #eff6ff, #f0fdf4);
  border-radius: 14px; border: 1px solid #dbeafe;
  margin-bottom: 1.75rem;
}
.social-proof-number {
  font-family: 'JetBrains Mono', monospace; font-size: 2rem;
  font-weight: 700; color: #1d4ed8; line-height: 1;
}
.social-proof-label {
  font-family: 'Inter', sans-serif; font-size: 0.78rem;
  color: #64748b; margin-top: 0.3rem;
}

/* ── Metric icon ── */
.metric-icon {
  font-size: 1.1rem; display: block; margin-bottom: 0.3rem; line-height: 1;
}

/* ── Rangeselector styling ── */
.rangeselector { font-family: 'Inter', sans-serif !important; }
</style>
""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────────
# DEV_MODE_FREE: is_pro starts True so every feature gate in the app unlocks.
# When DEV_MODE_FREE = False this reverts to False and Stripe handles elevation.
if "is_pro"       not in st.session_state: st.session_state["is_pro"]       = DEV_MODE_FREE
if "user_email"   not in st.session_state: st.session_state["user_email"]   = ""
if "show_payment" not in st.session_state: st.session_state["show_payment"] = False
if "candle_tf"    not in st.session_state: st.session_state["candle_tf"]    = "5min"

# ── Check returning from Stripe ───────────────────────────────────────────────
# DEV_MODE_FREE: skip all Stripe session verification — preserved, not deleted.
params = st.query_params
if not DEV_MODE_FREE and "session_id" in params:
    ok, email = verify_session(params["session_id"])
    if ok:
        st.session_state["is_pro"]     = True
        st.session_state["user_email"] = email or ""
        # Persist email in URL so Pro status survives page refreshes
        st.query_params.clear()
        if email:
            st.query_params["email"] = email
        st.success("Welcome to StockWizard Pro!")

# ── Re-verify Pro status on page refresh via saved email ──────────────────────
# DEV_MODE_FREE: skip subscription lookup — preserved, not deleted.
elif not DEV_MODE_FREE and not st.session_state.get("is_pro"):
    saved_email = params.get("email", "")
    if saved_email and not st.session_state.get("_sub_checked"):
        st.session_state["_sub_checked"] = True
        if check_subscription(saved_email):
            st.session_state["is_pro"]     = True
            st.session_state["user_email"] = saved_email

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="main-header">
    <div class="main-header-logo">
        <div class="main-header-logo-icon">W</div>
        <h1>Stock<span>Wizard</span></h1>
    </div>
    <div class="main-header-sub">
        <span class="badge">Stocks</span>
        <span class="badge">ETFs</span>
        <span class="badge">Portfolio Builder</span>
        <span class="badge">Stress Test</span>
        <span class="badge">Monte Carlo</span>
        <span style="color:#6b7a8d;margin-left:0.5rem">· Powered by Polygon.io</span>
    </div>
</div>
""", unsafe_allow_html=True)

# ── Ticker tape ──────────────────────────────────────────────────────────────
def _tape_html(items):
    items_html = ""
    for sym, px, chg, up in items:
        chg_class = "t-up" if up else "t-dn"
        arrow     = "▲" if up else "▼"
        chg_part  = f'<span class="{chg_class}">{arrow} {chg}</span>' if chg else ""
        items_html += (f'<span class="t-item"><span class="t-sym">{sym}</span>'
                       f'<span class="t-px">{px}</span>{chg_part}</span>'
                       f'<span class="t-div">●</span>')
    doubled = items_html * 2  # seamless loop
    return f'<div class="ticker-tape-wrap"><div class="ticker-tape">{doubled}</div></div>'

_tape_items = get_tape_prices(POLYGON_API_KEY)
if not _tape_items:
    # Static fallback if market is closed or API is unavailable
    _tape_items = [
        ("AAPL","$—","",True),("TSLA","$—","",True),("NVDA","$—","",True),
        ("SPY","$—","",True),("MSFT","$—","",True),("QQQ","$—","",True),
    ]
st.markdown(_tape_html(_tape_items), unsafe_allow_html=True)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    # ── Pro status / upgrade ──────────────────────────────────────────────────
    if DEV_MODE_FREE:
        # Development phase banner — replace with payment UI when DEV_MODE_FREE = False
        st.markdown("""
        <div style="background:rgba(245,166,35,0.08);border:1px solid rgba(245,166,35,0.35);
                    border-radius:4px;padding:0.6rem 0.9rem;margin-bottom:1.25rem">
            <div style="font-size:0.62rem;font-weight:700;letter-spacing:1.5px;
                        text-transform:uppercase;color:#f5a623;margin-bottom:3px">
                Dev Mode
            </div>
            <div style="font-size:0.75rem;color:#94a3b8;line-height:1.4">
                All features unlocked.<br>Payments disabled.
            </div>
        </div>
        """, unsafe_allow_html=True)
    elif SHOW_PRICING:
        # ── Original payment UI preserved below — do not delete ──────────────
        if st.session_state["is_pro"]:
            st.markdown("""
            <div style="background:linear-gradient(135deg,#0c1e35,#1e3a5f);
                        border:1px solid rgba(59,130,246,0.3);border-radius:2px;
                        padding:0.75rem 1rem;margin-bottom:1.25rem;text-align:center">
                <span style="color:#1d4ed8;font-weight:700;font-size:0.82rem;
                             letter-spacing:0.5px">⚡ PRO MEMBER</span>
            </div>
            """, unsafe_allow_html=True)
        else:
            if st.button("⚡ Upgrade to Pro — $9.99/mo", use_container_width=True):
                st.session_state["show_payment"] = True
                st.rerun()
    else:
        st.markdown("""
        <div style="background:linear-gradient(135deg,#0c1e35,#1e3a5f);
                    border:1px solid rgba(59,130,246,0.3);border-radius:2px;
                    padding:0.7rem 1rem;margin-bottom:1.25rem;text-align:center">
            <span style="color:#1d4ed8;font-weight:700;font-size:0.8rem;letter-spacing:0.5px">
                ⚡ STOCKWIZARD
            </span>
        </div>
        """, unsafe_allow_html=True)

    # ── Mode ──────────────────────────────────────────────────────────────────
    st.markdown('<div class="sidebar-group">Mode</div>', unsafe_allow_html=True)
    if st.session_state["is_pro"]:
        mode = st.radio("", ["Investor Mode", "Day Trader Mode"],
                        horizontal=True, label_visibility="collapsed")
    else:
        mode = "Investor Mode"

    # ── Ticker ────────────────────────────────────────────────────────────────
    st.markdown('<div class="sidebar-group" style="margin-top:1rem">Ticker</div>',
                unsafe_allow_html=True)
    ticker_input = st.text_input(
        "", placeholder="e.g. AAPL, SPY, BTC, ETH",
        label_visibility="collapsed"
    ).strip().upper()

    # ── Date range ────────────────────────────────────────────────────────────
    if mode == "Investor Mode":
        st.markdown('<div class="sidebar-group" style="margin-top:1rem">Date Range</div>',
                    unsafe_allow_html=True)
        _SLIDER_OPTIONS = ["1M","3M","6M","1Y","2Y"]
        _SLIDER_DAYS    = {"1M":30,"3M":90,"6M":180,"1Y":365,"2Y":730}
        period_key = st.select_slider("", options=_SLIDER_OPTIONS, value="1Y",
                                      label_visibility="collapsed")
        _today      = datetime.today().date()
        _days       = _SLIDER_DAYS[period_key]
        date_end    = _today.strftime("%Y-%m-%d")
        date_start  = (_today - timedelta(days=_days)).strftime("%Y-%m-%d")
        bar_size    = "day"
        period_label = period_key
    else:
        date_start   = (datetime.today() - timedelta(days=365)).strftime("%Y-%m-%d")
        date_end     = datetime.today().strftime("%Y-%m-%d")
        bar_size     = "day"
        period_label = "1Y"
        st.markdown('<div class="sidebar-group" style="margin-top:1rem">Candle Size</div>',
                    unsafe_allow_html=True)
        tf_options = {"1 Min":"1min","5 Min":"5min","15 Min":"15min","1 Hour":"1hour"}
        tf_label   = st.radio("", list(tf_options.keys()), index=1,
                              horizontal=True, label_visibility="collapsed")
        st.session_state["candle_tf"] = tf_options[tf_label]

    # ── Benchmarks ────────────────────────────────────────────────────────────
    st.markdown('<div class="sidebar-group" style="margin-top:1rem">Benchmarks</div>',
                unsafe_allow_html=True)
    include_spy = st.checkbox("S&P 500 (SPY)", value=True)
    include_qqq = st.checkbox("NASDAQ (QQQ)", value=True)

    if mode == "Investor Mode":
        # ── Peer comparison ───────────────────────────────────────────────────
        st.markdown('<div class="sidebar-group" style="margin-top:1rem">Peer Comparison</div>',
                    unsafe_allow_html=True)
        peers_input = st.text_input("", placeholder="e.g. GOOGL, AMZN",
                                    label_visibility="collapsed")

        # ── Report modules ────────────────────────────────────────────────────
        st.markdown('<div class="sidebar-group" style="margin-top:1rem">Report Modules</div>',
                    unsafe_allow_html=True)
        do_mc     = st.checkbox("Price Forecast", value=True)
        do_sector = st.checkbox("Sector Comparison",    value=True)
        do_corr   = st.checkbox("Correlation Matrix",   value=True)
        do_sr     = st.checkbox("Support & Resistance", value=True)
        do_news   = st.checkbox("News Headlines",       value=True)
        do_peers  = st.checkbox("Peer Comparison",      value=True)

        if do_mc:
            st.markdown('<div class="sidebar-group" style="margin-top:1rem">Forecast Settings</div>',
                        unsafe_allow_html=True)
            forecast_method = st.selectbox(
                "Method",
                ["Monte Carlo", "Custom Forecast"],
                label_visibility="collapsed",
            )
            if forecast_method == "Custom Forecast":
                st.markdown(
                    '<div style="font-size:0.73rem;line-height:1.6;'
                    'padding:0.65rem 0.75rem;'
                    'background:rgba(29,78,216,0.04);'
                    'border-radius:2px;'
                    'border:1px solid #e2e8f0;'
                    'border-left:3px solid #1d4ed8;'
                    'margin-bottom:0.5rem;'
                    'font-family:IBM Plex Sans,sans-serif">'
                    'Our <span style="color:#1d4ed8 !important;font-weight:600">Custom Forecast</span> '
                    'combines three models — '
                    '<span style="color:#1d4ed8 !important;font-weight:500">GARCH</span> volatility modeling, '
                    '<span style="color:#1d4ed8 !important;font-weight:500">Monte Carlo</span> simulation, '
                    'and a <span style="color:#1d4ed8 !important;font-weight:500">ML ensemble</span> '
                    '(Random Forest / XGBoost) — for smarter, more adaptive price projections. '
                    'GARCH captures volatility clustering, Monte Carlo simulates thousands of '
                    'price paths, and the ML model adds a data-driven drift signal — '
                    'all powered by real-time market data via Polygon.io.</div>',
                    unsafe_allow_html=True,
                )
            n_sims    = st.slider("Simulations",    100, 5000, 1000, step=100)
            n_horizon = st.slider("Horizon (days)",  21,  504,  252, step=21)
        else:
            forecast_method = "Monte Carlo"
            n_sims = 1000; n_horizon = 252
    else:
        peers_input = ""
        do_mc = do_sector = do_corr = do_sr = do_news = do_peers = False
        forecast_method = "Monte Carlo"
        n_sims = 1000; n_horizon = 252

    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)
    run_btn = st.button("▶  Run Analysis", type="primary", use_container_width=True)

    # ── Waitlist ──────────────────────────────────────────────────────────────
    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
    st.markdown('<div class="sidebar-group">Stay Updated</div>', unsafe_allow_html=True)
    email_input = st.text_input("", placeholder="your@email.com",
                                key="waitlist_email", label_visibility="collapsed")
    if st.button("Join Waitlist", use_container_width=True):
        if email_input and "@" in email_input:
            import csv
            csv_path = os.path.join(os.path.dirname(__file__), "waitlist.csv")
            already_exists = os.path.exists(csv_path)
            with open(csv_path, "a", newline="") as f:
                writer = csv.writer(f)
                if not already_exists:
                    writer.writerow(["email", "timestamp"])
                writer.writerow([email_input, datetime.now().isoformat()])
            st.success("Thanks! We'll be in touch.")
        else:
            st.error("Please enter a valid email.")

# ── Payment modal ─────────────────────────────────────────────────────────────
# DEV_MODE_FREE: modal never shown — Stripe checkout logic preserved, not deleted.
if not DEV_MODE_FREE and SHOW_PRICING and st.session_state["show_payment"] and not st.session_state["is_pro"]:
    st.markdown("---")
    st.markdown("### Upgrade to StockWizard Pro")
    col1, col2 = st.columns([2, 1])
    with col1:
        email_for_payment = st.text_input("Your email address", placeholder="you@email.com", key="pay_email")
    with col2:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("Continue to Payment →", type="primary"):
            if email_for_payment and "@" in email_for_payment:
                base_url = os.environ.get("BASE_URL", "https://stockwizard-production.up.railway.app")
                session  = create_checkout_session(base_url, base_url, email=email_for_payment)
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

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["📈  Stock Analysis", "💼  Portfolio Builder", "🏦  Bond Analysis", "🔥  Stress Test"])

# ═════════════════════════════════════════════════════════════════════════════
# TAB 1 — STOCK ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════
with tab1:

    # ── Landing page (no ticker entered) ─────────────────────────────────────
    if not run_btn and not ticker_input:

        # ── Hero ──────────────────────────────────────────────────────────────
        st.markdown("""
        <div style="background:linear-gradient(135deg,#0c1e35 0%,#1e3a5f 100%);
                    border-radius:12px;padding:3rem 2.5rem 2.5rem;margin-bottom:2rem;
                    border:1px solid rgba(255,255,255,0.08)">
            <div style="display:inline-block;background:rgba(29,78,216,0.2);
                        border:1px solid rgba(29,78,216,0.4);border-radius:20px;
                        padding:0.3rem 0.9rem;margin-bottom:1.25rem">
                <span style="color:#60a5fa;font-size:0.75rem;font-weight:600;
                              letter-spacing:0.5px;text-transform:uppercase">
                    Institutional Tools · Retail Price
                </span>
            </div>
            <h2 style="color:#ffffff;font-size:2rem;font-weight:700;
                       line-height:1.2;margin:0 0 1rem;font-family:'DM Sans',sans-serif">
                Portfolio Analysis That Actually<br>Tells You What to Do
            </h2>
            <p style="color:#94a3b8;font-size:1rem;max-width:560px;
                      line-height:1.7;margin:0 0 2rem">
                Build optimized portfolios from 330+ ranked stocks, backtest 2 years of history,
                stress test through real crashes, and get a complete Excel report —
                all for <strong style="color:#60a5fa">&#36;9.99/month</strong>.
            </p>
            <div style="display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:2.5rem">
                <div style="background:#1d4ed8;color:#fff;padding:0.75rem 1.75rem;
                            border-radius:6px;font-weight:600;font-size:0.9rem;
                            cursor:default">
                    &#8593; Enter a ticker in the sidebar to start free
                </div>
            </div>
            <div style="display:flex;gap:3rem;flex-wrap:wrap;border-top:1px solid rgba(255,255,255,0.08);
                        padding-top:1.5rem">
                <div>
                    <div style="color:#ffffff;font-size:1.5rem;font-weight:700;
                                font-family:'IBM Plex Mono',monospace">330+</div>
                    <div style="color:#64748b;font-size:0.75rem;text-transform:uppercase;
                                letter-spacing:0.5px">Stocks Ranked Daily</div>
                </div>
                <div>
                    <div style="color:#ffffff;font-size:1.5rem;font-weight:700;
                                font-family:'IBM Plex Mono',monospace">2 Years</div>
                    <div style="color:#64748b;font-size:0.75rem;text-transform:uppercase;
                                letter-spacing:0.5px">Price History</div>
                </div>
                <div>
                    <div style="color:#ffffff;font-size:1.5rem;font-weight:700;
                                font-family:'IBM Plex Mono',monospace">1,000</div>
                    <div style="color:#64748b;font-size:0.75rem;text-transform:uppercase;
                                letter-spacing:0.5px">Monte Carlo Paths</div>
                </div>
                <div>
                    <div style="color:#ffffff;font-size:1.5rem;font-weight:700;
                                font-family:'IBM Plex Mono',monospace">5</div>
                    <div style="color:#64748b;font-size:0.75rem;text-transform:uppercase;
                                letter-spacing:0.5px">Crash Scenarios</div>
                </div>
                <div>
                    <div style="color:#ffffff;font-size:1.5rem;font-weight:700;
                                font-family:'IBM Plex Mono',monospace">Live</div>
                    <div style="color:#64748b;font-size:0.75rem;text-transform:uppercase;
                                letter-spacing:0.5px">Market Data</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ── Market movers ─────────────────────────────────────────────────────
        st.markdown("""
        <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;
                    color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                    margin-bottom:1rem">Market Movers Today</div>
        """, unsafe_allow_html=True)

        with st.spinner("Loading market data..."):
            gainers, losers = get_top_movers(POLYGON_API_KEY)

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("""<div style="font-size:0.72rem;font-weight:700;color:#059669;
                        letter-spacing:0.5px;text-transform:uppercase;margin-bottom:0.6rem">
                ▲ Top Gainers</div>""", unsafe_allow_html=True)
            if gainers:
                for g in gainers:
                    st.markdown(f"""
                    <div class="mover-card">
                        <span style="font-weight:700;color:#0f172a;font-size:0.88rem">{g['Ticker']}</span>
                        <span style="font-family:'IBM Plex Mono',monospace;font-size:0.83rem;color:#6b7a8d">{g['Price']}</span>
                        <span style="color:#059669;font-family:'IBM Plex Mono',monospace;font-weight:700;font-size:0.88rem">{g['Change']}</span>
                    </div>""", unsafe_allow_html=True)
            else:
                st.markdown('<span style="color:#38bdf8;font-size:0.85rem">Market data unavailable right now.</span>', unsafe_allow_html=True)

        with col2:
            st.markdown("""<div style="font-size:0.72rem;font-weight:700;color:#dc2626;
                        letter-spacing:0.5px;text-transform:uppercase;margin-bottom:0.6rem">
                ▼ Top Losers</div>""", unsafe_allow_html=True)
            if losers:
                for l in losers:
                    st.markdown(f"""
                    <div class="mover-card">
                        <span style="font-weight:700;color:#0f172a;font-size:0.88rem">{l['Ticker']}</span>
                        <span style="font-family:'IBM Plex Mono',monospace;font-size:0.83rem;color:#6b7a8d">{l['Price']}</span>
                        <span style="color:#dc2626;font-family:'IBM Plex Mono',monospace;font-weight:700;font-size:0.88rem">{l['Change']}</span>
                    </div>""", unsafe_allow_html=True)
            else:
                st.markdown('<span style="color:#38bdf8;font-size:0.85rem">Market data unavailable right now.</span>', unsafe_allow_html=True)

        # ── Problem section ───────────────────────────────────────────────────
        st.markdown("""
        <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;
                    color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                    margin-bottom:1rem;margin-top:2rem">Why StockWizard</div>
        """, unsafe_allow_html=True)

        p1, p2, p3 = st.columns(3)
        for col, icon, problem, solution in [
            (p1, "📉", "Your brokerage shows you a chart.",
             "It doesn't tell you if your portfolio would survive a crash. We run your holdings through 2008, COVID, and 2022 with real historical prices."),
            (p2, "🔍", "Stock screeners give you data.",
             "They don't build your portfolio. We rank 330+ stocks daily by Sharpe ratio and momentum, then optimize weights for your risk tolerance."),
            (p3, "💸", "Financial advisors charge thousands.",
             "You shouldn't need one for quantitative analysis. Everything a quant analyst would run — Monte Carlo, efficient frontier, GARCH — for $9.99/month."),
        ]:
            with col:
                st.markdown(f"""
                <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
                            padding:1.25rem;height:100%">
                    <div style="font-size:1.5rem;margin-bottom:0.75rem">{icon}</div>
                    <div style="font-weight:600;color:#0f172a;font-size:0.88rem;
                                margin-bottom:0.5rem">{problem}</div>
                    <div style="color:#64748b;font-size:0.82rem;line-height:1.6">{solution}</div>
                </div>
                """, unsafe_allow_html=True)

        # ── Feature cards ─────────────────────────────────────────────────────
        st.markdown("""
        <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;
                    color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                    margin-bottom:1rem;margin-top:2rem">What's Included</div>
        """, unsafe_allow_html=True)

        fc1, fc2 = st.columns(2)
        fc3, fc4 = st.columns(2)

        for col, icon, title, tier, items in [
            (fc1, "📈", "Stock Analysis", "Free",
             ["Bollinger Bands, RSI, GARCH volatility", "Monte Carlo simulation (1,000 paths)",
              "Peer comparison vs sector", "Intraday candlestick chart", "Excel + PowerPoint export"]),
            (fc2, "💼", "Portfolio Builder", "Pro",
             ["330+ stocks ranked by multi-factor score", "2-year backtest with quarterly rebalancing",
              "Mean-variance optimization", "Portfolio Monte Carlo with milestone projections",
              "Diversification score + correlation heatmap"]),
            (fc3, "🔥", "Stress Test", "Pro",
             ["5 real historical crashes: 2008, COVID, 2022, dot-com, 2018", "Real Polygon price data for each scenario",
              "Portfolio return vs S&P 500 per crash", "Dollar impact calculator",
              "Correlation culprit detection"]),
            (fc4, "🏦", "Bond & Portfolio Autopsy", "Pro",
             ["Bond ETF analysis across 6 categories", "Upload your holdings CSV — see what broke",
              "P&L attribution per position", "Rolling volatility + drawdown charts",
              "Benchmark comparison"]),
        ]:
            with col:
                tier_color = "#1d4ed8" if tier == "Pro" else "#059669"
                tier_bg    = "rgba(29,78,216,0.08)" if tier == "Pro" else "rgba(5,150,105,0.08)"
                items_html = "".join(f"<li style='margin-bottom:0.3rem'>{i}</li>" for i in items)
                st.markdown(f"""
                <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;
                            padding:1.25rem;margin-bottom:1rem;
                            border-top:3px solid {tier_color}">
                    <div style="display:flex;align-items:center;justify-content:space-between;
                                margin-bottom:0.75rem">
                        <div style="font-size:1.3rem">{icon}
                            <span style="font-weight:700;color:#0f172a;font-size:0.95rem;
                                         margin-left:0.5rem">{title}</span>
                        </div>
                        <div style="background:{tier_bg};color:{tier_color};font-size:0.68rem;
                                    font-weight:700;letter-spacing:0.5px;text-transform:uppercase;
                                    padding:0.2rem 0.6rem;border-radius:20px">{tier}</div>
                    </div>
                    <ul style="color:#64748b;font-size:0.82rem;line-height:1.6;
                               padding-left:1.1rem;margin:0">
                        {items_html}
                    </ul>
                </div>
                """, unsafe_allow_html=True)

        # ── How it works ──────────────────────────────────────────────────────
        st.markdown("""
        <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;
                    color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                    margin-bottom:1.5rem;margin-top:2rem">How It Works</div>
        """, unsafe_allow_html=True)

        h1, h2, h3 = st.columns(3)
        for col, num, title, desc in [
            (h1, "1", "Enter Your Preferences",
             "Set your risk tolerance, starting capital, sectors to include, and investment horizon. Takes 2 minutes."),
            (h2, "2", "We Rank & Optimize",
             "StockWizard scores 330+ stocks daily using Sharpe ratio and momentum, selects the best per sector, then runs mean-variance optimization."),
            (h3, "3", "Get Your Full Report",
             "2-year backtest, Monte Carlo forecast with milestone probabilities, stress test results, and a downloadable Excel + PowerPoint report."),
        ]:
            with col:
                st.markdown(f"""
                <div style="text-align:center;padding:1.25rem 0.75rem;
                            background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px">
                    <div style="width:40px;height:40px;background:#1d4ed8;border-radius:50%;
                                display:flex;align-items:center;justify-content:center;
                                margin:0 auto 0.85rem;font-size:1rem;font-weight:700;
                                color:#ffffff">{num}</div>
                    <div style="font-weight:700;color:#0f172a;font-size:0.9rem;
                                margin-bottom:0.4rem">{title}</div>
                    <div style="color:#64748b;font-size:0.81rem;line-height:1.6">{desc}</div>
                </div>
                """, unsafe_allow_html=True)

        # ── Pricing ───────────────────────────────────────────────────────────
        st.markdown("""
        <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;
                    color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                    margin-bottom:1.5rem;margin-top:2rem">Pricing</div>
        """, unsafe_allow_html=True)

        pr1, pr2 = st.columns(2)
        with pr1:
            st.markdown("""
            <div style="border:1px solid #e2e8f0;border-radius:12px;padding:1.75rem;
                        background:#ffffff">
                <div style="font-size:0.72rem;font-weight:700;letter-spacing:0.5px;
                            text-transform:uppercase;color:#64748b;margin-bottom:0.5rem">Free</div>
                <div style="font-size:2rem;font-weight:700;color:#0f172a;
                            font-family:'IBM Plex Mono',monospace;margin-bottom:0.25rem">$0</div>
                <div style="color:#64748b;font-size:0.82rem;margin-bottom:1.25rem">No credit card required</div>
                <ul style="color:#64748b;font-size:0.83rem;line-height:1.8;padding-left:1.1rem;margin:0">
                    <li>Full stock analysis on any ticker</li>
                    <li>Bollinger Bands, RSI, Monte Carlo</li>
                    <li>Peer comparison charts</li>
                    <li>Excel + PowerPoint export</li>
                </ul>
            </div>
            """, unsafe_allow_html=True)
        with pr2:
            st.markdown("""
            <div style="border:2px solid #1d4ed8;border-radius:12px;padding:1.75rem;
                        background:linear-gradient(135deg,#eff6ff,#ffffff)">
                <div style="font-size:0.72rem;font-weight:700;letter-spacing:0.5px;
                            text-transform:uppercase;color:#1d4ed8;margin-bottom:0.5rem">Pro</div>
                <div style="font-size:2rem;font-weight:700;color:#0f172a;
                            font-family:'IBM Plex Mono',monospace;margin-bottom:0.25rem">$9.99
                    <span style="font-size:0.9rem;font-weight:400;color:#64748b">/month</span>
                </div>
                <div style="color:#64748b;font-size:0.82rem;margin-bottom:1.25rem">Cancel anytime</div>
                <ul style="color:#0f172a;font-size:0.83rem;line-height:1.8;padding-left:1.1rem;margin:0 0 1.25rem">
                    <li>Everything in Free</li>
                    <li><strong>Portfolio Builder</strong> — 330+ stocks, 2-year backtest</li>
                    <li><strong>Stress Test</strong> — 5 historical crash scenarios</li>
                    <li><strong>Bond Analysis</strong> — 60+ ETFs</li>
                    <li><strong>Portfolio Autopsy</strong> — CSV upload + P&L attribution</li>
                    <li>Save &amp; load portfolios</li>
                </ul>
            </div>
            """, unsafe_allow_html=True)

        if SHOW_PRICING:
            render_pricing_section()

        # ── Team ──────────────────────────────────────────────────────────────
        st.markdown("""
        <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;
                    color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                    margin-bottom:1.5rem;margin-top:2rem">Built By</div>
        """, unsafe_allow_html=True)

        tc1, tc2 = st.columns(2)
        with tc1:
            st.markdown("""
            <div class="founder-card" style="display:flex;align-items:flex-start;gap:1.25rem">
                <img src="https://raw.githubusercontent.com/wstratton707/stockwizard/main/assets/IMG_0434.jpeg"
                     style="width:64px;height:64px;border-radius:50%;object-fit:cover;
                            flex-shrink:0;border:2px solid #1d4ed8">
                <div>
                    <div style="font-weight:700;color:#0f172a;font-size:0.92rem">Wyatt Stratton</div>
                    <div style="color:#1d4ed8;font-size:0.78rem;margin-bottom:0.5rem">
                        Founder · Indiana University Bloomington</div>
                    <div style="color:#64748b;font-size:0.82rem;line-height:1.6;font-style:italic">
                        "I built StockWizard because I was tired of spending hours pulling financial
                        data manually. Any investor deserves a professional report in seconds."
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
        with tc2:
            st.markdown("""
            <div class="founder-card" style="display:flex;align-items:flex-start;gap:1.25rem">
                <img src="https://raw.githubusercontent.com/wstratton707/stockwizard/main/assets/IMG_0433.jpeg"
                     style="width:64px;height:64px;border-radius:50%;object-fit:cover;
                            flex-shrink:0;border:2px solid #1d4ed8">
                <div>
                    <div style="font-weight:700;color:#0f172a;font-size:0.92rem">Nicholas Carriello</div>
                    <div style="color:#1d4ed8;font-size:0.78rem;margin-bottom:0.5rem">
                        Co-Founder &amp; Quantitative Lead · Bucknell University</div>
                    <div style="color:#64748b;font-size:0.82rem;line-height:1.6;font-style:italic">
                        "Every number StockWizard produces is something a professional quant
                        would stand behind. Rigorous analysis, clean interface."
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        # ── Footer ────────────────────────────────────────────────────────────
        st.markdown(render_footer(), unsafe_allow_html=True)

    # ── Analysis (ticker entered or run clicked) ──────────────────────────────
    elif run_btn or ticker_input:

        if not ticker_input:
            st.error("Please enter a ticker symbol in the sidebar.")
            st.stop()

        with st.spinner(f"Validating {ticker_input}..."):
            valid, info = validate_ticker(ticker_input, POLYGON_API_KEY)

        if not valid:
            # Reference API may be rate-limited — try fetching price data directly
            # Only hard-stop if the ticker looks obviously wrong (non-alphanumeric)
            import re
            if not re.match(r'^[A-Z0-9.\-]{1,10}$', ticker_input):
                st.error(f"❌ Ticker '{ticker_input}' not found. Check the symbol and try again.")
                st.stop()
            # Otherwise continue — fetch_stock_data will raise if the ticker is truly invalid

        # Detect asset type (stock / etf / crypto)
        asset_type = detect_asset_type(ticker_input, POLYGON_API_KEY)
        is_crypto  = asset_type == "crypto"
        is_etf     = asset_type == "etf"
        # For Polygon API calls, crypto needs the X: prefix
        _poly_ticker = CRYPTO_TICKERS.get(ticker_input, (f"X:{ticker_input}USD", None))[0] \
                       if is_crypto else ticker_input

        # Asset type badge in UI
        if is_crypto:
            st.markdown(f'<span style="background:#1d4ed8;color:#0f172a;font-size:0.7rem;font-weight:700;'
                        f'padding:3px 10px;border-radius:2px;letter-spacing:0.5px">CRYPTO</span>',
                        unsafe_allow_html=True)
        elif is_etf:
            st.markdown(f'<span style="background:#4a9eff;color:#0f172a;font-size:0.7rem;font-weight:700;'
                        f'padding:3px 10px;border-radius:2px;letter-spacing:0.5px">ETF</span>',
                        unsafe_allow_html=True)

        # Live price ticker
        live = get_live_price(_poly_ticker, POLYGON_API_KEY)
        if live:
            sign       = "+" if live["change"] >= 0 else ""
            change_cls = "live-change-pos" if live["change"] >= 0 else "live-change-neg"
            st.markdown(f"""
            <div class="live-ticker">
                <div>
                    <span style="color:#6b7a8d;font-size:0.8rem;font-weight:600;
                                 letter-spacing:0.5px;text-transform:uppercase">{ticker_input}</span>
                    <div class="live-price">${live['price']:,.2f}</div>
                    <span class="{change_cls}">{sign}{live['change']:,.2f} ({sign}{live['pct']:.2f}%)</span>
                </div>
                <div style="text-align:right">
                    <div><span class="live-dot"></span>
                         <span style="color:#059669;font-size:0.78rem">Live</span></div>
                    <div style="color:#6b7a8d;font-size:0.75rem;margin-top:4px">Updated {live['time']}</div>
                    <div style="color:#6b7a8d;font-size:0.72rem">Refreshes every 30s</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        # ── Day Trader Mode ───────────────────────────────────────────────────
        if mode == "Day Trader Mode" and st.session_state["is_pro"]:

            st.markdown('<div class="section-header">Day Trader Mode <span class="pro-badge">PRO</span></div>',
                        unsafe_allow_html=True)

            tf         = st.session_state["candle_tf"]
            tf_map     = {"1min":(1,"minute"),"5min":(5,"minute"),"15min":(15,"minute"),"1hour":(1,"hour")}
            mult, span = tf_map.get(tf, (5,"minute"))

            with st.spinner("Loading intraday data..."):
                intraday_df = get_intraday_data(ticker_input, POLYGON_API_KEY, mult, span)

            if intraday_df is not None and not intraday_df.empty:

                fig_candle = go.Figure(data=[go.Candlestick(
                    x=intraday_df["Time"],
                    open=intraday_df["Open"], high=intraday_df["High"],
                    low=intraday_df["Low"],   close=intraday_df["Close"],
                    increasing_line_color="#059669", decreasing_line_color="#dc2626",
                    name=ticker_input,
                )])
                fig_candle.add_trace(go.Bar(
                    x=intraday_df["Time"], y=intraday_df["Volume"], name="Volume",
                    marker_color=["#059669" if c >= o else "#dc2626"
                                  for c, o in zip(intraday_df["Close"], intraday_df["Open"])],
                    opacity=0.4, yaxis="y2",
                ))
                fig_candle.update_layout(
                    title=dict(text=f"{ticker_input} — Intraday Candlestick",
                               font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
                    height=500, template=None,
                    plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=60, r=90, t=50, b=50),
                    xaxis_rangeslider_visible=False,
                    hovermode="x unified",
                    font=dict(family="DM Sans, system-ui, sans-serif"),
                    hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                    font=dict(color="white", size=12, family="DM Sans")),
                    yaxis=dict(title="Price ($)", showgrid=True, gridcolor="#e2e8f0",
                               showline=True, linecolor="#e2e8f0", linewidth=1,
                               tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                               title_font=dict(size=12, color="#64748b", family="DM Sans"),
                               tickprefix="$", tickformat=",.2f",
                               side="right"),
                    yaxis2=dict(title="Volume", overlaying="y", side="left",
                                showgrid=False, range=[0, intraday_df["Volume"].max() * 5]),
                    legend=dict(
                        orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                        font=dict(size=11, family="DM Sans", color="#374151"),
                        bgcolor="rgba(255,255,255,0.9)", bordercolor="#e2e8f0", borderwidth=1,
                    ),
                )
                st.plotly_chart(fig_candle, use_container_width=True)

                try:
                    import ta
                    closes = intraday_df["Close"]
                    if len(closes) >= 14:
                        intraday_df["RSI"]        = ta.momentum.RSIIndicator(closes, window=14).rsi()
                        macd_ind                  = ta.trend.MACD(closes)
                        intraday_df["MACD"]        = macd_ind.macd()
                        intraday_df["MACD_Signal"] = macd_ind.macd_signal()
                        intraday_df["MACD_Hist"]   = intraday_df["MACD"] - intraday_df["MACD_Signal"]

                        r1, r2 = st.columns(2)
                        with r1:
                            st.markdown('<div class="section-header">RSI (14)</div>', unsafe_allow_html=True)
                            fig_rsi = go.Figure()
                            fig_rsi.add_trace(go.Scatter(x=intraday_df["Time"], y=intraday_df["RSI"],
                                                         line=dict(color="#4a9eff", width=1.5), name="RSI"))
                            fig_rsi.add_hline(y=70, line_dash="dash", line_color="#dc2626", opacity=0.6)
                            fig_rsi.add_hline(y=30, line_dash="dash", line_color="#059669", opacity=0.6)
                            fig_rsi.add_hrect(y0=70, y1=100, fillcolor="rgba(220,38,38,0.06)", line_width=0)
                            fig_rsi.add_hrect(y0=0,  y1=30,  fillcolor="rgba(5,150,105,0.06)",  line_width=0)
                            fig_rsi.add_annotation(x=intraday_df["Time"].iloc[-1], y=73, text="Overbought",
                                showarrow=False, font=dict(size=10, color="#dc2626"), xanchor="right")
                            fig_rsi.add_annotation(x=intraday_df["Time"].iloc[-1], y=27, text="Oversold",
                                showarrow=False, font=dict(size=10, color="#059669"), xanchor="right")
                            fig_rsi.update_layout(
                                title=dict(text="RSI (14)", font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
                                height=200, template=None,
                                plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                                margin=dict(l=60, r=90, t=50, b=50),
                                hovermode="x unified",
                                font=dict(family="DM Sans, system-ui, sans-serif"),
                                hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                                font=dict(color="white", size=12, family="DM Sans")),
                                xaxis=dict(title=None, gridcolor="#e2e8f0", showline=True,
                                           linecolor="#e2e8f0", linewidth=1,
                                           tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                           title_font=dict(size=12, color="#64748b", family="DM Sans")),
                                yaxis=dict(range=[0, 100], title="RSI (0–100)",
                                           tickvals=[0, 30, 50, 70, 100],
                                           gridcolor="#e2e8f0", showline=True,
                                           linecolor="#e2e8f0", linewidth=1,
                                           tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                           title_font=dict(size=12, color="#64748b", family="DM Sans")),
                            )
                            st.plotly_chart(fig_rsi, use_container_width=True)

                        with r2:
                            st.markdown('<div class="section-header">MACD</div>', unsafe_allow_html=True)
                            fig_macd = go.Figure()
                            fig_macd.add_trace(go.Scatter(x=intraday_df["Time"], y=intraday_df["MACD"],
                                                          line=dict(color="#4a9eff", width=1.5), name="MACD"))
                            fig_macd.add_trace(go.Scatter(x=intraday_df["Time"], y=intraday_df["MACD_Signal"],
                                                          line=dict(color="#1d4ed8", width=1.5), name="Signal"))
                            hist_colors = ["#059669" if v >= 0 else "#dc2626" for v in intraday_df["MACD_Hist"]]
                            fig_macd.add_trace(go.Bar(x=intraday_df["Time"], y=intraday_df["MACD_Hist"],
                                                      marker_color=hist_colors, name="Histogram", opacity=0.6))
                            fig_macd.update_layout(
                                title=dict(text="MACD", font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
                                height=200, template=None,
                                plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                                margin=dict(l=60, r=90, t=50, b=50),
                                hovermode="x unified",
                                font=dict(family="DM Sans, system-ui, sans-serif"),
                                hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                                font=dict(color="white", size=12, family="DM Sans")),
                                legend=dict(
                                    orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                                    font=dict(size=11, family="DM Sans", color="#374151"),
                                    bgcolor="rgba(255,255,255,0.9)", bordercolor="#e2e8f0", borderwidth=1,
                                ),
                                xaxis=dict(title=None, gridcolor="#e2e8f0", showline=True,
                                           linecolor="#e2e8f0", linewidth=1,
                                           tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                           title_font=dict(size=12, color="#64748b", family="DM Sans")),
                                yaxis=dict(title="MACD Value", tickformat=".4f",
                                           gridcolor="#e2e8f0", showline=True,
                                           linecolor="#e2e8f0", linewidth=1,
                                           tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                           title_font=dict(size=12, color="#64748b", family="DM Sans")),
                            )
                            st.plotly_chart(fig_macd, use_container_width=True)
                except Exception:
                    pass

                st.markdown('<div class="section-header">Intraday Stats</div>', unsafe_allow_html=True)
                if not intraday_df.empty:
                    ic1, ic2, ic3, ic4 = st.columns(4)
                    day_open  = intraday_df["Open"].iloc[0]
                    day_high  = intraday_df["High"].max()
                    day_low   = intraday_df["Low"].min()
                    day_vol   = intraday_df["Volume"].sum()
                    for col, label, value in [
                        (ic1, "Day Open",  f"${day_open:,.2f}"),
                        (ic2, "Day High",  f"${day_high:,.2f}"),
                        (ic3, "Day Low",   f"${day_low:,.2f}"),
                        (ic4, "Volume",    f"{day_vol:,.0f}"),
                    ]:
                        with col:
                            st.markdown(f"""
                            <div class="metric-card">
                                <div class="metric-label">{label}</div>
                                <div class="metric-value">{value}</div>
                            </div>""", unsafe_allow_html=True)

            else:
                st.warning("No intraday data available. Market may be closed — showing previous session.")

            if _HAS_AUTOREFRESH:
                st_autorefresh(interval=30_000, key="day_trader_refresh")

        elif mode == "Day Trader Mode" and not st.session_state["is_pro"]:
            # DEV_MODE_FREE: is_pro is True so this branch is never reached in dev mode.
            # Original locked-screen UI preserved below — do not delete.
            st.markdown("""
            <div class="pro-locked">
                <div style="font-size:1.5rem;margin-bottom:0.5rem">🔒</div>
                <div style="color:#fff;font-weight:600;font-size:1.1rem;margin-bottom:0.5rem">
                    Day Trader Mode is Pro Only
                </div>
                <div style="color:#6b7a8d;font-size:0.88rem;margin-bottom:1.25rem">
                    Get live intraday charts, real-time updates, and full day trading tools for $9.99/month
                </div>
            </div>
            """, unsafe_allow_html=True)
            if not DEV_MODE_FREE and SHOW_PRICING:
                if st.button("Upgrade to Pro — $9.99/month", type="primary", key="upgrade_locked"):
                    st.session_state["show_payment"] = True
                    st.rerun()

        # ── Investor Mode ─────────────────────────────────────────────────────
        if mode == "Investor Mode":

            benchmarks = []
            if include_spy: benchmarks.append("SPY")
            if include_qqq: benchmarks.append("QQQ")
            peers_list = [p.strip().upper() for p in peers_input.split(",") if p.strip()] if peers_input else []

            progress  = st.progress(0, text="Starting analysis...")
            logs      = st.empty()
            log_lines = []

            def log(msg):
                log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}]  {msg}")
                logs.code("\n".join(log_lines[-12:]), language=None)

            try:
                progress.progress(10, text="Downloading price data...")
                if is_crypto:
                    df = fetch_crypto_data(ticker_input, api_key=POLYGON_API_KEY, log=log,
                                           start_override=date_start, end_override=date_end,
                                           bar_size=bar_size)
                else:
                    df = fetch_stock_data(ticker_input, benchmark_tickers=benchmarks,
                                          api_key=POLYGON_API_KEY, log=log,
                                          start_override=date_start, end_override=date_end,
                                          bar_size=bar_size)

                progress.progress(25, text="Fetching details...")
                if is_crypto:
                    company_details = {}
                    crypto_details  = fetch_crypto_details(ticker_input)
                    sector          = "Cryptocurrency"
                else:
                    company_details = fetch_company_details(ticker_input, POLYGON_API_KEY, log=log)
                    crypto_details  = {}
                    sector          = company_details.get("Sector", "Unknown")

                etf_details = fetch_etf_details(ticker_input, FMP_API_KEY) if is_etf else {}

                news_list = []
                if do_news:
                    progress.progress(35, text="Fetching news...")
                    news_list = fetch_news(ticker_input, POLYGON_API_KEY, log=log)

                peer_df       = None
                peer_price_dfs = {}   # {ticker: df} with Cumulative_Index + Close
                if do_peers and peers_list and not is_crypto:
                    progress.progress(45, text="Fetching peer data...")
                    peer_df = fetch_peer_comparison(ticker_input, peers_list, POLYGON_API_KEY, log=log)
                    for _pt in [ticker_input] + peers_list[:4]:
                        try:
                            _pdf = fetch_ohlcv(_pt, "5y", POLYGON_API_KEY,
                                               log=lambda m: None,
                                               start_override=date_start,
                                               end_override=date_end,
                                               bar_size=bar_size)
                            _pdf["Daily_Return"]     = _pdf["Close"].pct_change()
                            _pdf["Cumulative_Index"] = (1 + _pdf["Daily_Return"].fillna(0)).cumprod() * 100
                            peer_price_dfs[_pt] = _pdf
                        except Exception:
                            pass

                sector_df = None
                if do_sector and not is_crypto:
                    progress.progress(50, text="Fetching sector ETF...")
                    sector_df = fetch_sector_data(ticker_input, POLYGON_API_KEY, sector, log=log,
                                                  start_override=date_start, end_override=date_end,
                                                  bar_size=bar_size)

                corr_matrix = None
                if do_corr:
                    progress.progress(60, text="Building correlation matrix...")
                    corr_matrix = build_correlation_matrix(df, benchmarks if benchmarks else None)

                resistance = support = None
                if do_sr:
                    progress.progress(65, text="Detecting support & resistance...")
                    resistance, support = detect_support_resistance(df)

                mc_sim_df = mc_summary = None
                custom_garch_vols = custom_ml_drift = None
                if do_mc:
                    if forecast_method == "Custom Forecast":
                        progress.progress(75, text="Running Custom Forecast (GARCH + ML + Monte Carlo)...")
                        mc_sim_df, custom_garch_vols, custom_ml_drift, mc_summary = run_custom_forecast(
                            df,
                            n_simulations=n_sims, forecast_days=n_horizon, log=log)
                    else:
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
                    ticker_input, df, company_details, mc_summary, sharpe, sortino,
                    forecast_method=forecast_method)

                progress.progress(90, text="Building Excel report...")
                excel_buf = build_excel(
                    ticker_input, df, period_label,
                    company_details=company_details, sector_df=sector_df,
                    mc_sim_df=mc_sim_df, mc_summary=mc_summary,
                    news_list=news_list, peer_df=peer_df,
                    corr_matrix=corr_matrix,
                    resistance_levels=resistance, support_levels=support,
                    summary_text=summary_text,
                    bar_size=bar_size,
                )

                pptx_buf = None
                if PPTX_AVAILABLE:
                    progress.progress(96, text="Building PowerPoint report...")
                    try:
                        pptx_buf = build_stock_pptx(
                            ticker_input, df, period_label,
                            company_details=company_details,
                            mc_sim_df=mc_sim_df, mc_summary=mc_summary,
                            news_list=news_list,
                            summary_text=summary_text,
                        )
                    except Exception:
                        pptx_buf = None

                progress.progress(100, text="Complete!")
                time.sleep(0.3)
                progress.empty()
                logs.empty()

            except Exception as e:
                progress.empty()
                st.error(f"❌ Analysis failed: {e}")
                st.exception(e)
                st.stop()

            # Results
            latest     = df.iloc[-1]
            first      = df.iloc[0]
            period_ret = (latest["Close"] / first["Close"] - 1) * 100
            pos_neg    = lambda v: "positive" if v > 0 else ("negative" if v < 0 else "neutral")

            _dl_col1, _dl_col2 = st.columns(2)
            with _dl_col1:
                st.download_button(
                    label="⬇  Export to Excel",
                    data=excel_buf,
                    file_name=f"{ticker_input}_{period_label}_Analysis.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True, type="primary", key="download_top",
                )
            with _dl_col2:
                if pptx_buf:
                    st.download_button(
                        label="⬇  Export to PowerPoint",
                        data=pptx_buf,
                        file_name=f"{ticker_input}_{period_label}_Analysis.pptx",
                        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                        use_container_width=True, type="primary", key="download_pptx_top",
                    )
            st.markdown("---")

            st.markdown('<div class="section-header">Key Metrics</div>', unsafe_allow_html=True)
            # Last metric varies by asset type
            if is_crypto:
                mc_usd = crypto_details.get("market_cap_usd", 0)
                if mc_usd > 1e9:
                    extra_value = f"${mc_usd/1e9:.1f}B"
                elif mc_usd:
                    extra_value = f"${mc_usd/1e6:.0f}M"
                else:
                    extra_value = "N/A"
                extra_label = "Market Cap"
            elif is_etf:
                exp = (etf_details.get("meta") or {}).get("expense", 0)
                extra_label = "Expense Ratio"
                extra_value = f"{exp:.2f}%" if exp else "N/A"
            else:
                earnings_date = fetch_next_earnings(ticker_input, POLYGON_API_KEY)
                extra_label = "Last Earnings"
                extra_value = earnings_date[:10] if earnings_date and earnings_date != "N/A" else "N/A"

            vol_val = df["Volatility_20d"].iloc[-1]
            _TOOLTIPS = {
                "Sharpe Ratio":    "Risk-adjusted return. Above 1.0 is good, above 2.0 is excellent. Higher = better return per unit of risk.",
                "Ann. Volatility": "Annualized standard deviation of daily returns. Higher = more price swings. S&P 500 averages ~15%.",
                "Period Return":   "Total price return over the selected date range.",
                "52W High":        "Highest closing price in the last 52 weeks.",
                "52W Low":         "Lowest closing price in the last 52 weeks.",
                "Current Price":   "Most recent closing price from Polygon.io.",
            }
            _metric_rows = [
                [
                    ("Current Price",   f"${latest['Close']:,.2f}",                            "neutral"),
                    ("Period Return",   f"{period_ret:+.1f}%",                                 pos_neg(period_ret)),
                    ("52W High",        f"${latest['52W_High']:,.2f}" if pd.notna(latest.get('52W_High')) else "N/A", "neutral"),
                    ("52W Low",         f"${latest['52W_Low']:,.2f}"  if pd.notna(latest.get('52W_Low'))  else "N/A", "neutral"),
                ],
                [
                    ("Sharpe Ratio",    f"{sharpe:.2f}" if pd.notna(sharpe) else "N/A",        pos_neg(sharpe) if pd.notna(sharpe) else "neutral"),
                    ("Ann. Volatility", f"{vol_val*100:.1f}%" if pd.notna(vol_val) else "N/A", "neutral"),
                    (extra_label,       extra_value,                                            "neutral"),
                ],
            ]
            for row_items in _metric_rows:
                row_cols = st.columns(len(row_items))
                for col, (label, value, cls) in zip(row_cols, row_items):
                    tip = _TOOLTIPS.get(label, "")
                    tip_html = f'<span class="tooltip-wrap"> ⓘ<span class="tooltip-text">{tip}</span></span>' if tip else ""
                    with col:
                        st.markdown(f"""
                        <div class="metric-card">
                            <div class="metric-label">{label}{tip_html}</div>
                            <div class="metric-value {cls}">{value}</div>
                        </div>""", unsafe_allow_html=True)

            # ── ETF Profile Panel ─────────────────────────────────────────────
            if is_etf:
                meta     = etf_details.get("meta", {})
                holdings = etf_details.get("holdings", [])
                if meta or holdings:
                    st.markdown('<div class="section-header">ETF Profile</div>', unsafe_allow_html=True)
                    if meta:
                        mc1, mc2, mc3, mc4 = st.columns(4)
                        for col, lbl, val in [
                            (mc1, "Full Name",      meta.get("name", ticker_input)),
                            (mc2, "Index Tracked",  meta.get("index", "N/A")),
                            (mc3, "Category",       meta.get("category", "N/A")),
                            (mc4, "No. of Holdings",str(meta.get("holdings", "N/A"))),
                        ]:
                            with col:
                                st.markdown(f"""
                                <div class="metric-card">
                                    <div class="metric-label">{lbl}</div>
                                    <div style="font-size:0.88rem;font-weight:500;color:#0f172a;
                                                margin-top:0.25rem;line-height:1.4">{val}</div>
                                </div>""", unsafe_allow_html=True)
                        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
                        aum = meta.get("aum_b", 0)
                        exp = meta.get("expense", 0)
                        st.markdown(f"""
                        <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:2px;
                                    padding:0.6rem 1rem;font-size:0.85rem;color:#4a9eff">
                            <strong>AUM:</strong> ${aum:,.0f}B &nbsp;·&nbsp;
                            <strong>Expense Ratio:</strong> {exp:.2f}% annually &nbsp;·&nbsp;
                            <strong>Cost on $10,000:</strong> ${exp*100:.0f}/yr
                        </div>""", unsafe_allow_html=True)

                    if holdings:
                        st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)
                        h_col1, h_col2 = st.columns([1, 1])
                        with h_col1:
                            st.markdown("**Top Holdings**")
                            h_rows = [{"Ticker": t, "Weight (%)": f"{w:.2f}%"} for t, w in holdings]
                            st.dataframe(pd.DataFrame(h_rows), use_container_width=True, hide_index=True)
                        with h_col2:
                            fig_h = go.Figure(go.Bar(
                                x=[w for _, w in holdings],
                                y=[t for t, _ in holdings],
                                orientation="h",
                                marker_color="#4a9eff",
                                text=[f"{w:.1f}%" for _, w in holdings],
                                textposition="outside",
                            ))
                            fig_h.update_layout(
                                title=dict(text="Top Holdings by Weight",
                                           font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
                                height=300, template=None,
                                plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                                margin=dict(l=60, r=90, t=50, b=50),
                                font=dict(family="DM Sans, system-ui, sans-serif"),
                                hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                                font=dict(color="white", size=12, family="DM Sans")),
                                xaxis=dict(title="Weight (%)", ticksuffix="%", tickformat=".1f",
                                           gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                                           tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                           title_font=dict(size=12, color="#64748b", family="DM Sans")),
                                yaxis=dict(autorange="reversed",
                                           gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                                           tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                           title_font=dict(size=12, color="#64748b", family="DM Sans")),
                            )
                            st.plotly_chart(fig_h, use_container_width=True)

            # ── Crypto Market Data Panel ──────────────────────────────────────
            if is_crypto and crypto_details:
                st.markdown('<div class="section-header">Market Data</div>', unsafe_allow_html=True)
                cc1, cc2, cc3, cc4, cc5, cc6 = st.columns(6)
                mc_usd   = crypto_details.get("market_cap_usd", 0)
                vol_24h  = crypto_details.get("volume_24h", 0)
                ath_val  = crypto_details.get("ath", 0)
                ath_pct  = crypto_details.get("ath_pct", 0)
                p7d      = crypto_details.get("price_change_7d", 0)
                p30d     = crypto_details.get("price_change_30d", 0)
                circ     = crypto_details.get("circulating_supply", 0)
                max_sup  = crypto_details.get("max_supply", 0)

                def fmt_large(n):
                    if not n: return "N/A"
                    if n > 1e12: return f"${n/1e12:.2f}T"
                    if n > 1e9:  return f"${n/1e9:.2f}B"
                    if n > 1e6:  return f"${n/1e6:.1f}M"
                    return f"${n:,.0f}"

                for col, lbl, val, color in [
                    (cc1, "Market Cap",     fmt_large(mc_usd),                            "#0f172a"),
                    (cc2, "24h Volume",     fmt_large(vol_24h),                           "#0f172a"),
                    (cc3, "All-Time High",  f"${ath_val:,.2f}" if ath_val else "N/A",    "#0f172a"),
                    (cc4, "vs ATH",         f"{ath_pct:+.1f}%" if ath_pct else "N/A",   "#dc2626" if ath_pct and ath_pct < 0 else "#059669"),
                    (cc5, "7d Change",      f"{p7d:+.1f}%"  if p7d  else "N/A",         "#059669" if p7d  and p7d  > 0 else "#dc2626"),
                    (cc6, "30d Change",     f"{p30d:+.1f}%" if p30d else "N/A",         "#059669" if p30d and p30d > 0 else "#dc2626"),
                ]:
                    with col:
                        st.markdown(f"""
                        <div class="metric-card">
                            <div class="metric-label">{lbl}</div>
                            <div class="metric-value" style="color:{color}">{val}</div>
                        </div>""", unsafe_allow_html=True)

                if circ:
                    sup_pct = f" ({circ/max_sup*100:.1f}% of max supply)" if max_sup else ""
                    ath_date = crypto_details.get("ath_date", "")
                    st.markdown(f"""
                    <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:2px;
                                padding:0.6rem 1rem;margin-top:0.75rem;font-size:0.85rem;color:#6b7a8d">
                        <strong>Circulating Supply:</strong> {circ:,.0f} {ticker_input}{sup_pct}
                        {"&nbsp;·&nbsp;<strong>ATH Date:</strong> " + ath_date if ath_date else ""}
                    </div>""", unsafe_allow_html=True)

            fig = go.Figure()

            # ── Price — dominant, strong blue ─────────────────────────────────
            # Invisible base trace at the min price — used for "tonexty" fill
            _close_min = float(df["Close"].min())
            fig.add_trace(go.Scatter(
                x=df["Date"], y=[_close_min] * len(df),
                line=dict(color="rgba(0,0,0,0)", width=0),
                showlegend=False, hoverinfo="skip",
            ))
            fig.add_trace(go.Scatter(
                x=df["Date"], y=df["Close"],
                name="Price",
                line=dict(color="#1d4ed8", width=2.5),
                fill="tonexty",
                fillcolor="rgba(37,99,235,0.05)",
                hovertemplate="$%{y:,.2f}<extra>Price</extra>",
            ))

            # ── Moving averages — thin, visually distinct ─────────────────────
            _ma_cfg = [
                (20,  "#f59e0b", 1.0,  "dot",      "MA 20"),
                (50,  "#8b5cf6", 1.2,  "dash",     "MA 50"),
                (200, "#f97316", 1.5,  "longdash", "MA 200"),
            ]
            for ma, color, width, dash, label in _ma_cfg:
                if f"MA{ma}" in df.columns:
                    fig.add_trace(go.Scatter(
                        x=df["Date"], y=df[f"MA{ma}"],
                        name=label,
                        line=dict(color=color, width=width, dash=dash),
                        opacity=0.9,
                        hovertemplate=f"$%{{y:,.2f}}<extra>MA {ma}</extra>",
                    ))

            # ── S/R lines — top 2 only, very subtle ──────────────────────────
            _y_min = df["Close"].min()
            _y_max = df["Close"].max()
            _price_range = _y_max - _y_min

            if do_sr and resistance:
                # Only show resistance levels above current price, top 2
                _res_above = sorted([r for r in resistance if r > _y_min], reverse=True)[:2]
                for _i, r in enumerate(_res_above):
                    fig.add_shape(type="line", x0=0, x1=1, xref="paper",
                                  y0=r, y1=r,
                                  line=dict(color="#ef4444", width=1, dash="dash"),
                                  opacity=0.4, layer="below")
                    fig.add_annotation(
                        x=1.01, xref="paper", y=r, yref="y",
                        text=f"R ${r:,.0f}",
                        showarrow=False, xanchor="left",
                        font=dict(color="#ef4444", size=12, family="DM Sans", weight="bold"),
                        bgcolor="rgba(255,255,255,0.9)",
                        borderpad=3,
                    )

            if do_sr and support:
                # Only show support levels below current price, bottom 2
                _sup_below = sorted([s for s in support if s < _y_max])[:2]
                for _i, s in enumerate(_sup_below):
                    fig.add_shape(type="line", x0=0, x1=1, xref="paper",
                                  y0=s, y1=s,
                                  line=dict(color="#16a34a", width=1, dash="dash"),
                                  opacity=0.4, layer="below")
                    fig.add_annotation(
                        x=1.01, xref="paper", y=s, yref="y",
                        text=f"S ${s:,.0f}",
                        showarrow=False, xanchor="left",
                        font=dict(color="#16a34a", size=12, family="DM Sans", weight="bold"),
                        bgcolor="rgba(255,255,255,0.9)",
                        borderpad=3,
                    )

            # ── Current price tag ─────────────────────────────────────────────
            _last = df["Close"].iloc[-1]
            fig.add_shape(type="line", x0=0, x1=1, xref="paper",
                          y0=_last, y1=_last,
                          line=dict(color="#94a3b8", width=1, dash="dot"),
                          opacity=0.7, layer="above")
            fig.add_annotation(
                x=1.01, xref="paper", y=_last, yref="y",
                text=f"<b>${_last:,.2f}</b>",
                showarrow=False, xanchor="left",
                font=dict(color="white", size=11, family="DM Sans"),
                bgcolor="#2563eb",
                borderpad=4,
            )

            fig.update_layout(
                height=490, template=None,
                plot_bgcolor="#ffffff", paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10, r=110, t=60, b=30),
                hovermode="x unified",
                font=dict(family="DM Sans, system-ui, sans-serif"),
                hoverlabel=dict(
                    bgcolor="#0f172a", bordercolor="#334155",
                    font=dict(color="white", size=12, family="DM Sans"),
                    namelength=-1,
                ),
                # Legend inside top-left of plot — below rangeselector
                legend=dict(
                    orientation="h", yanchor="top", y=0.99, xanchor="left", x=0.01,
                    font=dict(size=11, family="DM Sans", color="#374151"),
                    bgcolor="rgba(255,255,255,0.85)",
                    bordercolor="#e2e8f0", borderwidth=1,
                    itemsizing="constant",
                ),
                xaxis=dict(
                    title=None,
                    type="date",
                    tickformat="%b '%y",
                    tickfont=dict(size=11, color="#94a3b8", family="DM Sans"),
                    gridcolor="#f1f5f9",
                    showline=True, linecolor="#e2e8f0", linewidth=1,
                    zeroline=False,
                    rangeslider=dict(visible=False),
                    rangeselector=dict(
                        buttons=[
                            dict(count=1,  label="1M", step="month", stepmode="backward"),
                            dict(count=3,  label="3M", step="month", stepmode="backward"),
                            dict(count=6,  label="6M", step="month", stepmode="backward"),
                            dict(count=1,  label="1Y", step="year",  stepmode="backward"),
                            dict(count=3,  label="3Y", step="year",  stepmode="backward"),
                            dict(step="all", label="All"),
                        ],
                        bgcolor="#f8fafc", bordercolor="#e2e8f0", borderwidth=1,
                        font=dict(family="DM Sans", size=11, color="#475569"),
                        activecolor="#2563eb",
                        x=0.0, xanchor="left", y=1.08, yanchor="bottom",
                    ),
                ),
                yaxis=dict(
                    title=None,
                    side="right",
                    tickprefix="$",
                    tickformat=",.0f",
                    tickfont=dict(size=11, color="#94a3b8", family="DM Sans"),
                    gridcolor="#f1f5f9",
                    showline=False,
                    zeroline=False,
                    autorange=True,
                    rangemode="normal",
                    nticks=7,
                ),
            )
            st.plotly_chart(fig, use_container_width=True)

            if "RSI14" in df.columns:
                st.markdown('<div class="section-header">RSI (14)</div>', unsafe_allow_html=True)
                fig_rsi = go.Figure()
                fig_rsi.add_trace(go.Scatter(x=df["Date"], y=df["RSI14"],
                                             line=dict(color="#4a9eff", width=1.5), name="RSI",
                                             hovertemplate="RSI: %{y:.1f}<extra></extra>"))
                fig_rsi.add_hrect(y0=70, y1=100, fillcolor="rgba(239,68,68,0.08)", line_width=0)
                fig_rsi.add_hrect(y0=0, y1=30, fillcolor="rgba(22,163,74,0.08)", line_width=0)
                fig_rsi.add_hline(y=70, line_dash="dash", line_color="#ef4444", line_width=1, opacity=0.6)
                fig_rsi.add_hline(y=50, line_dash="dot",  line_color="#94a3b8", line_width=1, opacity=0.5)
                fig_rsi.add_hline(y=30, line_dash="dash", line_color="#16a34a", line_width=1, opacity=0.6)
                fig_rsi.add_annotation(x=df["Date"].iloc[-1], y=72, text="Overbought",
                    showarrow=False, xanchor="right",
                    font=dict(size=11, color="#ef4444", family="DM Sans"))
                fig_rsi.add_annotation(x=df["Date"].iloc[-1], y=28, text="Oversold",
                    showarrow=False, xanchor="right",
                    font=dict(size=11, color="#16a34a", family="DM Sans"))
                fig_rsi.update_layout(
                    title=dict(text="RSI (14-Day)", font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
                    height=220, template=None,
                    plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=60, r=90, t=50, b=50),
                    hovermode="x unified",
                    font=dict(family="DM Sans, system-ui, sans-serif"),
                    hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                    font=dict(color="white", size=12, family="DM Sans")),
                    xaxis=dict(
                        type="date", tickformat="%b '%y", title=None,
                        tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                        title_font=dict(size=12, color="#64748b", family="DM Sans"),
                        gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0",
                    ),
                    yaxis=dict(
                        range=[0, 100],
                        tickvals=[0, 30, 50, 70, 100],
                        ticktext=["0", "30", "50", "70", "100"],
                        title="RSI",
                        tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                        title_font=dict(size=12, color="#64748b", family="DM Sans"),
                        gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0",
                        zeroline=False,
                    ),
                )
                st.plotly_chart(fig_rsi, use_container_width=True)

            if "BB_Upper" in df.columns:
                st.markdown('<div class="section-header">Bollinger Bands</div>', unsafe_allow_html=True)
                fig_bb = go.Figure()
                fig_bb.add_trace(go.Scatter(x=df["Date"], y=df["BB_Upper"],
                                            line=dict(color="#cbd5e1", width=1), name="Upper Band"))
                fig_bb.add_trace(go.Scatter(x=df["Date"], y=df["BB_Lower"],
                                            line=dict(color="#cbd5e1", width=1), name="Lower Band",
                                            fill="tonexty", fillcolor="rgba(147,197,253,0.15)"))
                fig_bb.add_trace(go.Scatter(x=df["Date"], y=df["BB_Middle"],
                                            line=dict(color="#3b82f6", width=1.5, dash="dash"), name="Middle (SMA)"))
                fig_bb.add_trace(go.Scatter(x=df["Date"], y=df["Close"],
                                            line=dict(color="#1d4ed8", width=2), name="Price",
                                            hovertemplate="$%{y:,.2f}<extra>Price</extra>"))
                fig_bb.update_layout(
                    height=320, template=None,
                    plot_bgcolor="#ffffff", paper_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=10, r=60, t=45, b=30),
                    hovermode="x unified",
                    font=dict(family="DM Sans, system-ui, sans-serif"),
                    hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                    font=dict(color="white", size=12, family="DM Sans")),
                    legend=dict(
                        orientation="h", yanchor="top", y=0.99, xanchor="left", x=0.01,
                        font=dict(size=11, family="DM Sans", color="#374151"),
                        bgcolor="rgba(255,255,255,0.85)", bordercolor="#e2e8f0", borderwidth=1,
                    ),
                    xaxis=dict(
                        type="date", tickformat="%b '%y", title=None,
                        tickfont=dict(size=11, color="#94a3b8", family="DM Sans"),
                        gridcolor="#f1f5f9", showline=True, linecolor="#e2e8f0",
                        zeroline=False,
                    ),
                    yaxis=dict(
                        tickprefix="$", tickformat=",.2f",
                        title=None,
                        side="right",
                        tickfont=dict(size=11, color="#94a3b8", family="DM Sans"),
                        gridcolor="#f1f5f9", showline=False,
                        zeroline=False,
                        autorange=True,
                        rangemode="normal",
                    ),
                )
                st.plotly_chart(fig_bb, use_container_width=True)

            if mc_summary:
                _is_custom = forecast_method == "Custom Forecast"
                _header    = "Custom Forecast" if _is_custom else "Monte Carlo Forecast"
                st.markdown(f'<div class="section-header">{_header}</div>', unsafe_allow_html=True)

                # ── Metric cards — row 1: price scenarios ─────────────────────
                _r1 = st.columns(5)
                for col, label, value, color in [
                    (_r1[0],"Bear (P5)",  f"${mc_summary['Bear Case (P5)']:,.2f}","#dc2626"),
                    (_r1[1],"Low (P25)",  f"${mc_summary['Low Case (P25)']:,.2f}","#1d4ed8"),
                    (_r1[2],"Median",     f"${mc_summary['Median (P50)']:,.2f}",  "#0f172a"),
                    (_r1[3],"Bull (P75)", f"${mc_summary['Bull Case (P75)']:,.2f}","#4a9eff"),
                    (_r1[4],"Best (P95)", f"${mc_summary['Best Case (P95)']:,.2f}","#059669"),
                ]:
                    with col:
                        st.markdown(f"""
                        <div class="metric-card">
                            <div class="metric-label">{label}</div>
                            <div class="metric-value" style="color:{color}">{value}</div>
                        </div>""", unsafe_allow_html=True)

                # ── Metric cards — row 2: stats ────────────────────────────────
                _r2_items = [("Prob. of Gain", mc_summary["Prob. of Gain"], "#1d4ed8")]
                if _is_custom:
                    _r2_items += [
                        ("GARCH Vol", mc_summary.get("Ann. Volatility (GARCH)", "—"), "#4a9eff"),
                        ("ML Drift",  mc_summary.get("ML Drift (daily)", "—"),        "#059669"),
                    ]
                _r2 = st.columns(len(_r2_items))
                for col, (_lbl, _val, _clr) in zip(_r2, _r2_items):
                    with col:
                        st.markdown(f"""
                        <div class="metric-card">
                            <div class="metric-label">{_lbl}</div>
                            <div class="metric-value" style="color:{_clr}">{_val}</div>
                        </div>""", unsafe_allow_html=True)

                # ── Simulated price-path fan chart ────────────────────────────
                _n_cols = min(200, mc_sim_df.shape[1])
                if mc_sim_df.empty or _n_cols == 0:
                    st.warning("Monte Carlo simulation produced no paths.")
                    pcts = None
                else:
                    pcts = np.percentile(mc_sim_df.iloc[:, :_n_cols].values, [5,25,50,75,95], axis=1)
                if pcts is not None:
                    x      = list(range(len(pcts[0])))
                    fig_mc = go.Figure()
                    fig_mc.add_trace(go.Scatter(x=x, y=pcts[4], name="P95", line=dict(color="#059669", width=1.5),
                                                hovertemplate="Day %{x} — Best: $%{y:,.2f}<extra></extra>"))
                    fig_mc.add_trace(go.Scatter(x=x, y=pcts[3], name="P75", line=dict(color="#4a9eff", width=1),
                                                fill="tonexty", fillcolor="rgba(59,130,246,0.1)",
                                                hovertemplate="Day %{x} — Bull: $%{y:,.2f}<extra></extra>"))
                    fig_mc.add_trace(go.Scatter(x=x, y=pcts[2], name="Median", line=dict(color="#0f172a", width=2),
                                                hovertemplate="Day %{x} — Median: $%{y:,.2f}<extra></extra>"))
                    fig_mc.add_trace(go.Scatter(x=x, y=pcts[1], name="P25", line=dict(color="#1d4ed8", width=1),
                                                fill="tonexty", fillcolor="rgba(29,78,216,0.06)",
                                                hovertemplate="Day %{x} — Low: $%{y:,.2f}<extra></extra>"))
                    fig_mc.add_trace(go.Scatter(x=x, y=pcts[0], name="P5", line=dict(color="#dc2626", width=1.5),
                                                hovertemplate="Day %{x} — Bear: $%{y:,.2f}<extra></extra>"))
                    fig_mc.update_layout(
                        height=370, template=None,
                        plot_bgcolor="#ffffff", paper_bgcolor="rgba(0,0,0,0)",
                        margin=dict(l=10, r=60, t=30, b=40),
                        hovermode="x unified",
                        font=dict(family="DM Sans, system-ui, sans-serif"),
                        hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                        font=dict(color="white", size=12, family="DM Sans")),
                        legend=dict(
                            orientation="h", yanchor="top", y=0.99, xanchor="left", x=0.01,
                            font=dict(size=11, family="DM Sans", color="#374151"),
                            bgcolor="rgba(255,255,255,0.85)", bordercolor="#e2e8f0", borderwidth=1,
                        ),
                        xaxis=dict(
                            title="Trading Days",
                            tickvals=[0, 50, 100, 150, 200, 250],
                            tickformat=",d",
                            tickfont=dict(size=11, color="#94a3b8", family="DM Sans"),
                            title_font=dict(size=12, color="#64748b", family="DM Sans"),
                            gridcolor="#f1f5f9", showline=True, linecolor="#e2e8f0",
                            zeroline=False,
                        ),
                        yaxis=dict(
                            tickprefix="$", tickformat=",.0f",
                            title=None,
                            side="right",
                            autorange=True,
                            rangemode="normal",
                            zeroline=False,
                            tickfont=dict(size=11, color="#94a3b8", family="DM Sans"),
                            gridcolor="#f1f5f9", showline=False,
                        ),
                    )
                    st.plotly_chart(fig_mc, use_container_width=True)

                # ── Custom Forecast extra charts ──────────────────────────────
                if _is_custom and custom_garch_vols is not None:
                    _garch_x  = list(range(len(custom_garch_vols)))
                    _ann_vols = (custom_garch_vols * np.sqrt(252) * 100).tolist()
                    _vol_mean = float(np.mean(_ann_vols))

                    col_garch, col_drift = st.columns(2)

                    with col_garch:
                        st.markdown('<div class="section-header" style="font-size:0.85rem">GARCH Volatility Forecast</div>',
                                    unsafe_allow_html=True)
                        fig_gv = go.Figure()
                        # shaded area under curve
                        fig_gv.add_trace(go.Scatter(
                            x=_garch_x, y=_ann_vols,
                            name="Ann. Vol (%)",
                            mode="lines",
                            line=dict(color="#4a9eff", width=2.5),
                            fill="tozeroy", fillcolor="rgba(74,158,255,0.18)",
                            hovertemplate="Day %{x}: %{y:.2f}%<extra></extra>",
                        ))
                        # long-run mean reference line
                        fig_gv.add_hline(
                            y=_vol_mean,
                            line_dash="dot", line_color="#1d4ed8", line_width=1.5,
                            annotation_text=f"Mean {_vol_mean:.1f}%",
                            annotation_font=dict(color="#1d4ed8", size=10),
                            annotation_position="top right",
                        )
                        fig_gv.update_layout(
                            title=dict(text="GARCH Volatility Forecast",
                                       font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
                            height=250, template=None,
                            plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                            margin=dict(l=60, r=90, t=50, b=50),
                            font=dict(family="DM Sans, system-ui, sans-serif"),
                            hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                            font=dict(color="white", size=12, family="DM Sans")),
                            xaxis=dict(
                                title="Trading Days",
                                gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                                tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                title_font=dict(size=12, color="#64748b", family="DM Sans"),
                            ),
                            yaxis=dict(
                                title="Ann. Volatility (%)",
                                ticksuffix="%", tickformat=".1f",
                                gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                                tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                title_font=dict(size=12, color="#64748b", family="DM Sans"),
                            ),
                            showlegend=False,
                        )
                        st.plotly_chart(fig_gv, use_container_width=True)

                    with col_drift:
                        st.markdown('<div class="section-header" style="font-size:0.85rem">ML Predicted Drift Signal</div>',
                                    unsafe_allow_html=True)
                        _drift_pct = (custom_ml_drift or 0) * 100
                        _drift_color = "#059669" if _drift_pct >= 0 else "#dc2626"
                        _drift_label = "Bullish" if _drift_pct >= 0 else "Bearish"
                        st.markdown(f"""
                        <div style="display:flex;flex-direction:column;align-items:center;
                                    justify-content:center;height:180px;
                                    background:#ffffff;border-radius:2px;
                                    border:1px solid #e2e8f0">
                            <div style="font-size:2.4rem;font-weight:700;color:{_drift_color}">
                                {_drift_pct:+.4f}%
                            </div>
                            <div style="font-size:0.85rem;color:#6b7a8d;margin-top:0.4rem">
                                Daily drift per step &nbsp;·&nbsp;
                                <span style="color:{_drift_color};font-weight:600">{_drift_label}</span>
                            </div>
                            <div style="font-size:0.72rem;color:#6b7a8d;margin-top:0.3rem">
                                Random Forest + XGBoost ensemble
                            </div>
                        </div>""", unsafe_allow_html=True)

            st.markdown('<div class="section-header">Volume</div>', unsafe_allow_html=True)
            vol_colors = ["#22c55e" if r >= 0 else "#ef4444" for r in df["Daily_Return"].fillna(0)]
            fig_vol = go.Figure()
            fig_vol.add_trace(go.Bar(x=df["Date"], y=df["Volume"], marker_color=vol_colors, opacity=0.85,
                                     name="Volume",
                                     hovertemplate="<b>%{x|%b %d, %Y}</b><br>Volume: %{y:,.0f}<extra></extra>"))
            if "Volume" in df.columns:
                _vol_ma20 = df["Volume"].rolling(20, min_periods=5).mean()
                fig_vol.add_trace(go.Scatter(
                    x=df["Date"], y=_vol_ma20, name="20d Avg",
                    line=dict(color="#2563eb", width=1.5, dash="dot"),
                    hovertemplate="20d Avg: %{y:,.0f}<extra></extra>",
                ))
            fig_vol.update_layout(
                title=dict(text="Volume", font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
                height=260, template=None,
                plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=60, r=90, t=50, b=50),
                showlegend=True,
                font=dict(family="DM Sans, system-ui, sans-serif"),
                hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                font=dict(color="white", size=12, family="DM Sans")),
                legend=dict(
                    orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    font=dict(size=11, family="DM Sans", color="#374151"),
                    bgcolor="rgba(255,255,255,0.9)", bordercolor="#e2e8f0", borderwidth=1,
                ),
                xaxis=dict(
                    type="date", tickformat="%b '%y", title=None,
                    tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                    title_font=dict(size=12, color="#64748b", family="DM Sans"),
                    gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0",
                ),
                yaxis=dict(
                    tickformat=".2s",
                    title="Volume",
                    tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                    title_font=dict(size=12, color="#64748b", family="DM Sans"),
                    gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0",
                    zeroline=False,
                ),
            )
            st.plotly_chart(fig_vol, use_container_width=True)

            if corr_matrix is not None:
                st.markdown('<div class="section-header">Correlation Matrix</div>', unsafe_allow_html=True)
                fig_corr = px.imshow(
                    corr_matrix,
                    text_auto=".2f",
                    color_continuous_scale=["#dc2626", "#ffffff", "#1d4ed8"],
                    zmin=-1, zmax=1,
                    aspect="equal",
                )
                fig_corr.update_traces(
                    xgap=2, ygap=2,
                    hovertemplate="<b>%{x} vs %{y}</b><br>Correlation: %{z:.2f}<extra></extra>",
                    textfont=dict(size=11, family="DM Sans"),
                )
                fig_corr.update_layout(
                    title=dict(text="Correlation Matrix — Daily Returns",
                        font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
                    height=320,
                    font=dict(family="DM Sans"),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="#f8fafc",
                    margin=dict(l=60, r=90, t=50, b=50),
                    hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                        font=dict(color="white", size=12, family="DM Sans")),
                    coloraxis_colorbar=dict(
                        title="Correlation",
                        tickvals=[-1, -0.5, 0, 0.5, 1],
                        ticktext=["-1.0", "-0.5", "0.0", "0.5", "1.0"],
                        tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                        title_font=dict(size=12, color="#64748b", family="DM Sans"),
                        thickness=14, len=0.8,
                    ),
                    xaxis=dict(tickfont=dict(size=12, color="#374151", family="DM Sans"),
                               showline=False, gridcolor="#e2e8f0"),
                    yaxis=dict(tickfont=dict(size=12, color="#374151", family="DM Sans"),
                               showline=False, gridcolor="#e2e8f0"),
                )
                st.plotly_chart(fig_corr, use_container_width=True)

            if news_list:
                st.markdown('<div class="section-header">Recent News</div>', unsafe_allow_html=True)
                for item in news_list[:8]:
                    st.markdown(f"""
                    <div style="padding:0.6rem 0;border-bottom:1px solid #e2e8f0">
                        <div style="font-size:0.82rem;font-weight:500">
                            <a href="{item['URL']}" target="_blank"
                               style="text-decoration:none;color:#0f172a">{item['Headline']}</a>
                        </div>
                        <div style="font-size:0.72rem;color:#6b7a8d;margin-top:2px">
                            {item['Publisher']} &nbsp;·&nbsp; {item['Date']}
                        </div>
                    </div>""", unsafe_allow_html=True)

            if peer_df is not None and not peer_df.empty:
                st.markdown('<div class="section-header">Peer Comparison</div>', unsafe_allow_html=True)

                _peer_colors = ["#2E75B6", "#00B0F0", "#FFC000", "#FF4136", "#2ECC71"]
                _chart_layout = dict(
                    plot_bgcolor="#ffffff",
                    paper_bgcolor="#f8fafc",
                    font=dict(color="#0f172a", family="IBM Plex Sans"),
                    xaxis=dict(gridcolor="#e2e8f0", showgrid=True, color="#6b7a8d"),
                    yaxis=dict(gridcolor="#e2e8f0", showgrid=True, color="#6b7a8d"),
                    legend=dict(orientation="h", y=1.04, x=0,
                                bgcolor="rgba(0,0,0,0)", font=dict(size=11, color="#0f172a")),
                    margin=dict(l=60, r=20, t=50, b=50),
                    hovermode="x unified",
                )

                # ── 1. Cumulative Return Overlay ──────────────────────────────
                if peer_price_dfs:
                    fig_cum = go.Figure()
                    for _ci, (_pt, _pdf) in enumerate(peer_price_dfs.items()):
                        if "Cumulative_Index" not in _pdf.columns or _pdf.empty:
                            continue
                        _x = _pdf["Date"] if "Date" in _pdf.columns else _pdf.index
                        _is_main = (_pt == ticker_input)
                        fig_cum.add_trace(go.Scatter(
                            x=_x,
                            y=_pdf["Cumulative_Index"],
                            name=_pt,
                            mode="lines",
                            line=dict(
                                color=_peer_colors[_ci % len(_peer_colors)],
                                width=2.5 if _is_main else 1.8,
                                dash="solid" if _is_main else "dot" if _ci > 0 else "solid",
                            ),
                            hovertemplate=f"<b>{_pt}</b>: %{{y:.1f}}<extra></extra>",
                        ))
                    fig_cum.update_layout(
                        title=dict(text="Cumulative Return Comparison (Base = 100)",
                                   font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
                        height=380,
                        plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                        margin=dict(l=60, r=90, t=50, b=50),
                        hovermode="x unified",
                        font=dict(family="DM Sans, system-ui, sans-serif"),
                        hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                        font=dict(color="white", size=12, family="DM Sans")),
                        legend=dict(
                            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                            font=dict(size=11, family="DM Sans", color="#374151"),
                            bgcolor="rgba(255,255,255,0.9)", bordercolor="#e2e8f0", borderwidth=1,
                        ),
                        xaxis=dict(type="date", tickformat="%b '%y", title=None,
                                   gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                                   tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                   title_font=dict(size=12, color="#64748b", family="DM Sans")),
                        yaxis=dict(title="Index (Start = 100)", tickformat=".0f",
                                   gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                                   tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                   title_font=dict(size=12, color="#64748b", family="DM Sans")),
                    )
                    st.plotly_chart(fig_cum, use_container_width=True)

                # ── 2. Key Metrics Bar Charts ─────────────────────────────────
                if peer_price_dfs:
                    _mrows = []
                    for _pt, _pdf in peer_price_dfs.items():
                        if _pdf.empty or "Daily_Return" not in _pdf.columns:
                            continue
                        _ret = _pdf["Daily_Return"].dropna()
                        if len(_ret) < 5:
                            continue
                        _ann_ret = (1 + _ret.mean()) ** 252 - 1
                        _ann_vol = _ret.std() * np.sqrt(252)
                        _sharpe  = (_ann_ret / _ann_vol) if _ann_vol > 0 else 0
                        _cum     = _pdf["Cumulative_Index"]
                        _max_dd  = ((_cum - _cum.cummax()) / _cum.cummax()).min()
                        _mrows.append({
                            "Ticker":           _pt,
                            "Ann. Return (%)":  round(_ann_ret * 100, 2),
                            "Volatility (%)":   round(_ann_vol * 100, 2),
                            "Sharpe Ratio":     round(_sharpe, 2),
                            "Max Drawdown (%)": round(_max_dd * 100, 2),
                        })

                    if _mrows:
                        _mdf    = pd.DataFrame(_mrows)
                        _ticks  = _mdf["Ticker"].tolist()
                        _colors = [_peer_colors[i % len(_peer_colors)]
                                   for i in range(len(_ticks))]

                        _mc1, _mc2 = st.columns(2)

                        with _mc1:
                            # Annualised Return + Volatility grouped bar
                            fig_rv = go.Figure()
                            fig_rv.add_trace(go.Bar(
                                name="Ann. Return (%)",
                                x=_ticks,
                                y=_mdf["Ann. Return (%)"],
                                marker_color=[
                                    "#2ECC71" if v >= 0 else "#FF4136"
                                    for v in _mdf["Ann. Return (%)"]
                                ],
                                hovertemplate="%{x}: %{y:.2f}%<extra>Ann. Return</extra>",
                            ))
                            fig_rv.add_trace(go.Bar(
                                name="Volatility (%)",
                                x=_ticks,
                                y=_mdf["Volatility (%)"],
                                marker_color="#FFC000",
                                hovertemplate="%{x}: %{y:.2f}%<extra>Volatility</extra>",
                            ))
                            fig_rv.update_layout(
                                barmode="group",
                                title=dict(text="Ann. Return vs Volatility",
                                           font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
                                height=300,
                                plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                                margin=dict(l=60, r=90, t=50, b=50),
                                hovermode="x unified",
                                font=dict(family="DM Sans, system-ui, sans-serif"),
                                hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                                font=dict(color="white", size=12, family="DM Sans")),
                                legend=dict(
                                    orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                                    font=dict(size=11, family="DM Sans", color="#374151"),
                                    bgcolor="rgba(255,255,255,0.9)", bordercolor="#e2e8f0", borderwidth=1,
                                ),
                                xaxis=dict(title="Ticker", gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                                           tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                           title_font=dict(size=12, color="#64748b", family="DM Sans")),
                                yaxis=dict(title="Percent (%)", ticksuffix="%", tickformat=".1f",
                                           gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                                           tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                           title_font=dict(size=12, color="#64748b", family="DM Sans")),
                            )
                            st.plotly_chart(fig_rv, use_container_width=True)

                        with _mc2:
                            # Sharpe Ratio bars
                            fig_sh = go.Figure(go.Bar(
                                x=_ticks,
                                y=_mdf["Sharpe Ratio"],
                                marker_color=[
                                    "#2ECC71" if v >= 1 else "#FFC000" if v >= 0 else "#FF4136"
                                    for v in _mdf["Sharpe Ratio"]
                                ],
                                hovertemplate="%{x}: %{y:.2f}<extra>Sharpe</extra>",
                            ))
                            fig_sh.update_layout(
                                title=dict(text="Sharpe Ratio Comparison",
                                           font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
                                showlegend=False,
                                height=300,
                                plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                                margin=dict(l=60, r=90, t=50, b=50),
                                hovermode="x unified",
                                font=dict(family="DM Sans, system-ui, sans-serif"),
                                hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                                font=dict(color="white", size=12, family="DM Sans")),
                                xaxis=dict(title="Ticker", gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                                           tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                           title_font=dict(size=12, color="#64748b", family="DM Sans")),
                                yaxis=dict(title="Sharpe Ratio", tickformat=".2f",
                                           gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                                           tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                           title_font=dict(size=12, color="#64748b", family="DM Sans")),
                            )
                            st.plotly_chart(fig_sh, use_container_width=True)

                        # Max Drawdown full-width
                        fig_dd = go.Figure(go.Bar(
                            x=_ticks,
                            y=_mdf["Max Drawdown (%)"],
                            marker_color="#FF4136",
                            hovertemplate="%{x}: %{y:.2f}%<extra>Max Drawdown</extra>",
                        ))
                        fig_dd.update_layout(
                            title=dict(text="Maximum Drawdown Comparison",
                                       font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
                            showlegend=False,
                            height=280,
                            plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                            margin=dict(l=60, r=90, t=50, b=50),
                            hovermode="x unified",
                            font=dict(family="DM Sans, system-ui, sans-serif"),
                            hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                            font=dict(color="white", size=12, family="DM Sans")),
                            xaxis=dict(title="Ticker", gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                                       tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                       title_font=dict(size=12, color="#64748b", family="DM Sans")),
                            yaxis=dict(title="Max Drawdown (%)", ticksuffix="%", tickformat=".1f",
                                       gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                                       tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                       title_font=dict(size=12, color="#64748b", family="DM Sans")),
                        )
                        st.plotly_chart(fig_dd, use_container_width=True)

                # ── 3. Company Info Table ─────────────────────────────────────
                _show_cols = [c for c in
                              ["Ticker", "Company", "Exchange", "Market Cap ($B)", "Employees", "Country"]
                              if c in peer_df.columns]
                st.dataframe(peer_df[_show_cols], use_container_width=True, hide_index=True)

            st.markdown('<div class="section-header">Automated Analysis Summary</div>', unsafe_allow_html=True)
            st.markdown(f"""
            <div class="summary-box">{summary_text}</div>
            """, unsafe_allow_html=True)

            st.markdown("---")
            excel_buf.seek(0)
            _dl2_col1, _dl2_col2 = st.columns(2)
            with _dl2_col1:
                st.download_button(
                    label="⬇  Export to Excel",
                    data=excel_buf,
                    file_name=f"{ticker_input}_{period_label}_Analysis.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True, type="primary", key="download_bottom",
                )
            with _dl2_col2:
                if pptx_buf:
                    pptx_buf.seek(0)
                    st.download_button(
                        label="⬇  Export to PowerPoint",
                        data=pptx_buf,
                        file_name=f"{ticker_input}_{period_label}_Analysis.pptx",
                        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                        use_container_width=True, type="primary", key="download_pptx_bottom",
                    )

        st.markdown(render_section("Data & Methodology", _disc.DIVIDENDS), unsafe_allow_html=True)
        st.markdown(render_inline(_disc.SHORT), unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# TAB 2 — PORTFOLIO BUILDER
# ═════════════════════════════════════════════════════════════════════════════
with tab2:
    render_portfolio_builder(POLYGON_API_KEY, is_pro=st.session_state.get("is_pro", False))

# ═════════════════════════════════════════════════════════════════════════════
# TAB 3 — BOND ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════
with tab3:

    st.markdown("""
    <div style="background:linear-gradient(135deg,#0c1e35,#1e3a5f);border:1px solid #e2e8f0;
                border-radius:3px;padding:1.5rem 2rem;margin-bottom:1.5rem">
        <div style="font-family:'IBM Plex Mono',monospace;color:#1d4ed8;font-size:1.1rem;
                    font-weight:500;margin-bottom:4px">🏦 Bond Analysis</div>
        <div style="color:#6b7a8d;font-size:0.85rem">
            Analyse bond ETFs · Price history · Volatility · Drawdown · Yield proxy
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Bond universe browser ─────────────────────────────────────────────────
    st.markdown('<div class="section-header">Bond Universe</div>', unsafe_allow_html=True)
    st.markdown("<div style='font-size:0.85rem;color:#6b7a8d;margin-bottom:0.75rem'>"
                "Browse available bond ETFs by category:</div>", unsafe_allow_html=True)

    bond_cat_cols = st.columns(3)
    for i, (category, tickers) in enumerate(BOND_UNIVERSE.items()):
        with bond_cat_cols[i % 3]:
            ticker_list = "  ·  ".join(tickers[:5])
            st.markdown(f"""
            <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:2px;
                        padding:0.85rem 1rem;margin-bottom:0.75rem">
                <div style="font-size:0.72rem;font-weight:600;letter-spacing:0.5px;
                            text-transform:uppercase;color:#6b7a8d;margin-bottom:0.35rem">{category}</div>
                <div style="font-family:'IBM Plex Mono',monospace;font-size:0.78rem;color:#0f172a">{ticker_list}</div>
            </div>""", unsafe_allow_html=True)

    # ── Analysis form ─────────────────────────────────────────────────────────
    st.markdown('<div class="section-header">Analyse a Bond ETF</div>', unsafe_allow_html=True)
    b_col1, b_col2, b_col3 = st.columns([2, 1, 1])
    with b_col1:
        bond_ticker = st.text_input("Bond ETF Ticker", placeholder="e.g. TLT, AGG, HYG",
                                    key="bond_ticker_input").strip().upper()
    with b_col2:
        bond_period = st.radio("Period", ["1y", "2y", "5y", "10y"], index=2,
                               horizontal=True, key="bond_period")
    with b_col3:
        bond_benchmark = st.selectbox("Benchmark", ["None", "AGG", "TLT", "BND"],
                                      key="bond_benchmark")

    run_bond = st.button("▶  Analyse Bond", type="primary", key="run_bond_btn")

    if run_bond and bond_ticker:
        benchmarks = [bond_benchmark] if bond_benchmark != "None" and bond_benchmark != bond_ticker else []
        with st.spinner(f"Fetching data for {bond_ticker}..."):
            try:
                bdf = fetch_bond_data(bond_ticker, period=bond_period,
                                      benchmark_tickers=benchmarks or None,
                                      api_key=POLYGON_API_KEY)
            except Exception as e:
                st.error(f"❌ {e}")
                st.stop()

        if bdf is None or bdf.empty:
            st.error(f"No data returned for {bond_ticker}. Check the ticker and try again.")
            st.stop()

        # ── Key metrics ───────────────────────────────────────────────────────
        st.markdown('<div class="section-header">Key Metrics</div>', unsafe_allow_html=True)
        latest       = bdf.iloc[-1]
        ret_1y       = bdf["Close"].pct_change(252).iloc[-1] * 100
        vol_20d      = bdf["Volatility_20d"].iloc[-1] * 100
        drawdown_60d = bdf["Drawdown_60d"].iloc[-1] * 100
        sharpe       = bdf["Sharpe_Ratio"].iloc[-1]
        duration_lbl = bdf["Duration_Bucket"].iloc[-1]
        pct_52w_high = bdf["Pct_From_52W_High"].iloc[-1] * 100

        m_cols = st.columns(6)
        for col, label, value, color in [
            (m_cols[0], "Price",          f"${latest['Close']:,.2f}",        "#0f172a"),
            (m_cols[1], "1Y Return",      f"{ret_1y:+.1f}%",                 "#059669" if ret_1y > 0 else "#dc2626"),
            (m_cols[2], "Ann. Volatility",f"{vol_20d:.1f}%",                 "#0f172a"),
            (m_cols[3], "60d Drawdown",   f"{drawdown_60d:.1f}%",            "#dc2626" if drawdown_60d < -5 else "#1d4ed8"),
            (m_cols[4], "Sharpe Ratio",   f"{sharpe:.2f}" if not pd.isna(sharpe) else "N/A",
                                                                              "#059669" if not pd.isna(sharpe) and sharpe > 1 else "#1d4ed8"),
            (m_cols[5], "vs 52W High",    f"{pct_52w_high:+.1f}%",           "#059669" if pct_52w_high > -5 else "#dc2626"),
        ]:
            with col:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-label">{label}</div>
                    <div class="metric-value" style="color:{color}">{value}</div>
                </div>""", unsafe_allow_html=True)

        st.markdown(f"""
        <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:2px;
                    padding:0.6rem 1rem;margin-top:0.75rem;font-size:0.85rem;color:#4a9eff">
            <strong>Duration:</strong> {duration_lbl} &nbsp;·&nbsp;
            <strong>Ticker:</strong> {bond_ticker} &nbsp;·&nbsp;
            <strong>Period:</strong> {bond_period}
        </div>""", unsafe_allow_html=True)

        # ── Price chart with MAs ──────────────────────────────────────────────
        st.markdown('<div class="section-header">Price History</div>', unsafe_allow_html=True)
        fig_price = go.Figure()
        fig_price.add_trace(go.Scatter(x=bdf["Date"], y=bdf["Close"],
                                       line=dict(color="#0f172a", width=1.5), name="Price"))
        for ma, color in [(20, "#1d4ed8"), (50, "#1d4ed8"), (200, "#4a9eff")]:
            col_name = f"MA{ma}"
            if col_name in bdf.columns:
                fig_price.add_trace(go.Scatter(x=bdf["Date"], y=bdf[col_name],
                                               line=dict(color=color, width=1, dash="dot"),
                                               name=f"{ma}d MA"))
        if benchmarks:
            bench_col = f"{benchmarks[0]}_Cumulative"
            if bench_col in bdf.columns:
                # Rescale benchmark to start at same price as bond
                scale = bdf["Close"].iloc[0] / (bdf[bench_col].iloc[0] / 100)
                fig_price.add_trace(go.Scatter(
                    x=bdf["Date"], y=bdf[bench_col] / 100 * scale,
                    line=dict(color="#6b7a8d", width=1, dash="dash"),
                    name=benchmarks[0],
                ))
        fig_price.update_layout(
            title=dict(text=f"{bond_ticker} — Price History",
                       font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
            height=380, template=None,
            plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=60, r=90, t=50, b=50),
            hovermode="x unified",
            font=dict(family="DM Sans, system-ui, sans-serif"),
            hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                            font=dict(color="white", size=12, family="DM Sans")),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                font=dict(size=11, family="DM Sans", color="#374151"),
                bgcolor="rgba(255,255,255,0.9)", bordercolor="#e2e8f0", borderwidth=1,
            ),
            xaxis=dict(type="date", tickformat="%b '%y", title=None,
                       gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                       tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                       title_font=dict(size=12, color="#64748b", family="DM Sans")),
            yaxis=dict(title="Price ($)", tickprefix="$", tickformat=",.2f", autorange=True,
                       gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                       tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                       title_font=dict(size=12, color="#64748b", family="DM Sans")),
        )
        st.plotly_chart(fig_price, use_container_width=True)

        # ── Drawdown chart ────────────────────────────────────────────────────
        st.markdown('<div class="section-header">Drawdown</div>', unsafe_allow_html=True)
        fig_dd = go.Figure()
        fig_dd.add_trace(go.Scatter(x=bdf["Date"], y=bdf["Drawdown_60d"] * 100,
                                    fill="tozeroy", fillcolor="rgba(255,77,77,0.12)",
                                    line=dict(color="#dc2626", width=1.5), name="60d Drawdown"))
        fig_dd.add_trace(go.Scatter(x=bdf["Date"], y=bdf["Drawdown_20d"] * 100,
                                    line=dict(color="#1d4ed8", width=1, dash="dot"),
                                    name="20d Drawdown"))
        fig_dd.update_layout(
            title=dict(text="Drawdown Analysis",
                       font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
            height=220, template=None,
            plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=60, r=90, t=50, b=50),
            hovermode="x unified",
            font=dict(family="DM Sans, system-ui, sans-serif"),
            hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                            font=dict(color="white", size=12, family="DM Sans")),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                font=dict(size=11, family="DM Sans", color="#374151"),
                bgcolor="rgba(255,255,255,0.9)", bordercolor="#e2e8f0", borderwidth=1,
            ),
            xaxis=dict(type="date", tickformat="%b '%y", title=None,
                       gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                       tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                       title_font=dict(size=12, color="#64748b", family="DM Sans")),
            yaxis=dict(title="Drawdown (%)", ticksuffix="%", tickformat=".1f",
                       gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                       tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                       title_font=dict(size=12, color="#64748b", family="DM Sans")),
        )
        st.plotly_chart(fig_dd, use_container_width=True)

        # ── Volatility & momentum ─────────────────────────────────────────────
        v_col1, v_col2 = st.columns(2)
        with v_col1:
            st.markdown('<div class="section-header">Rolling Volatility (20d)</div>',
                        unsafe_allow_html=True)
            fig_vol = go.Figure()
            fig_vol.add_trace(go.Scatter(x=bdf["Date"], y=bdf["Volatility_20d"] * 100,
                                         line=dict(color="#4a9eff", width=1.5), name="Volatility"))
            fig_vol.update_layout(
                title=dict(text="Rolling Volatility (20d)",
                           font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
                height=220, template=None,
                plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=60, r=90, t=50, b=50),
                hovermode="x unified",
                font=dict(family="DM Sans, system-ui, sans-serif"),
                hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                font=dict(color="white", size=12, family="DM Sans")),
                xaxis=dict(type="date", tickformat="%b '%y", title=None,
                           gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                           tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                           title_font=dict(size=12, color="#64748b", family="DM Sans")),
                yaxis=dict(title="Ann. Vol (%)", ticksuffix="%", tickformat=".1f",
                           gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                           tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                           title_font=dict(size=12, color="#64748b", family="DM Sans")),
            )
            st.plotly_chart(fig_vol, use_container_width=True)

        with v_col2:
            st.markdown('<div class="section-header">20d Price Momentum</div>',
                        unsafe_allow_html=True)
            mom = bdf["Price_Momentum_20d"] * 100
            fig_mom = go.Figure()
            fig_mom.add_trace(go.Bar(x=bdf["Date"], y=mom,
                                     marker_color=["#059669" if v >= 0 else "#dc2626" for v in mom],
                                     name="Momentum"))
            fig_mom.add_hline(y=0, line_color="#6b7a8d", line_width=1)
            fig_mom.update_layout(
                title=dict(text="20d Price Momentum",
                           font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
                height=220, template=None,
                plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=60, r=90, t=50, b=50),
                hovermode="x unified",
                font=dict(family="DM Sans, system-ui, sans-serif"),
                hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                font=dict(color="white", size=12, family="DM Sans")),
                xaxis=dict(type="date", tickformat="%b '%y", title=None,
                           gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                           tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                           title_font=dict(size=12, color="#64748b", family="DM Sans")),
                yaxis=dict(title="Return (%)", ticksuffix="%", tickformat=".1f",
                           gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0", linewidth=1,
                           tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                           title_font=dict(size=12, color="#64748b", family="DM Sans")),
            )
            st.plotly_chart(fig_mom, use_container_width=True)

        st.markdown("""
        <div class="disclaimer">
            ⚠ Bond ETF analysis is for informational purposes only and does not constitute
            financial advice. Data provided by Polygon.io. Past performance is not indicative
            of future results.
        </div>
        """, unsafe_allow_html=True)

    elif run_bond and not bond_ticker:
        st.warning("Please enter a bond ETF ticker.")


# =============================================================================
# TAB 4 — STRESS TEST & PORTFOLIO AUTOPSY
# =============================================================================
with tab4:
    render_stress_test(POLYGON_API_KEY, is_pro=st.session_state.get("is_pro", False))
