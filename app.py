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
# Cached wrappers — same call shape, returns memoised results for the TTLs
# defined in cached_fetchers.py. Dramatically speeds up re-runs (toggling a
# chart checkbox, switching tabs, etc.) because expensive Polygon/FMP calls
# return instantly from in-process memory.
from cached_fetchers import (
    cached_validate_ticker, cached_detect_asset_type,
    cached_fetch_stock_data, cached_fetch_ohlcv,
    cached_fetch_company_details, cached_fetch_financials,
    cached_fetch_sec_financials, cached_fetch_news,
    cached_fetch_peer_comparison, cached_fetch_sector_data,
    cached_fetch_next_earnings, cached_fetch_crypto_data,
    cached_fetch_crypto_details, cached_fetch_etf_details,
    cached_run_monte_carlo, cached_run_custom_forecast,
    cached_detect_support_resistance, cached_build_correlation_matrix,
    cached_get_analyst_data,
)
try:
    from streamlit_autorefresh import st_autorefresh
    _HAS_AUTOREFRESH = True
except ImportError:
    _HAS_AUTOREFRESH = False
from portfolio_data import BOND_UNIVERSE, BOND_DURATION_MAP
from analysis import (
    detect_support_resistance, build_correlation_matrix,
    run_monte_carlo, run_custom_forecast, generate_summary_paragraph,
    compute_fundamentals, dcf_valuation
)
from excel_builder import build_excel
from pptx_builder import build_stock_pptx, build_portfolio_pptx, PPTX_AVAILABLE
try:
    from docx_builder import build_stock_docx
    DOCX_AVAILABLE = True
except Exception:
    DOCX_AVAILABLE = False
try:
    from valuation import (get_valuation_data as _get_valuation_data,
                           build_valuation_figure, build_eps_figure, build_dividend_figure)
    VALUATION_AVAILABLE = True
except Exception:
    VALUATION_AVAILABLE = False


# NOTE ON PARAMETER NAMES: st.cache_data deliberately EXCLUDES any argument whose
# name starts with an underscore from the cache key (that's the documented escape
# hatch for unhashable args). These take plain strings, so they must NOT be
# underscore-prefixed — doing so leaves an empty cache key, and every ticker gets
# served the first ticker's result. Do not rename these back.
@st.cache_data(ttl=3600, show_spinner=False)
def _cached_valuation(ticker):
    """~15yr price-vs-earnings valuation series (EDGAR + yfinance). Cached 1h; the
    underlying fetch is slow, so this keeps repeat views instant."""
    if not VALUATION_AVAILABLE:
        return None
    try:
        return _get_valuation_data(ticker)
    except Exception:
        return None


# ── News research (multi-source + AI brief) — all cached to bound API/LLM cost ─
@st.cache_data(ttl=900, show_spinner=False)
def _cached_news(ticker, company):
    try:
        from news_research import aggregate_news
        return aggregate_news(ticker, POLYGON_API_KEY, company_name=company or None)
    except Exception:
        return []


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_catalysts(ticker):
    try:
        from news_research import get_catalysts
        return get_catalysts(ticker)
    except Exception:
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_news_brief(ticker, company):
    try:
        from news_research import ai_news_brief
        return ai_news_brief(ticker, _cached_news(ticker, company),
                             company_name=company or None)
    except Exception:
        return None


@st.cache_data(ttl=900, show_spinner=False)
def _cached_market_pulse():
    """Market-wide feed + trending tickers for the News page. One Polygon call."""
    try:
        from news_research import market_pulse
        try:
            from portfolio_data import SECTOR_UNIVERSE
            uni = {t for v in SECTOR_UNIVERSE.values() for t in v}
        except Exception:
            uni = None
        return market_pulse(POLYGON_API_KEY, universe=uni)
    except Exception:
        return {"articles": [], "trending": []}


def _news_feed_html(articles, n=12):
    """Compact, linked news cards with a theme chip + sentiment dot."""
    import html as _html
    out = []
    for a in articles[:n]:
        dot = {"Positive": "#15803d", "Negative": "#b91c1c"}.get(a["sentiment"], "#94a3b8")
        title = _html.escape(a["title"] or "")
        out.append(
            f'<div class="news-card"><span class="news-dot" style="background:{dot}"></span>'
            f'<div class="news-card-b">'
            f'<a href="{a["url"]}" target="_blank" class="news-title">{title}</a>'
            f'<div class="news-meta"><span class="news-chip sm">{a["theme"]}</span>'
            f'{_html.escape(a["source"] or "")} · {a["date"]}</div></div></div>')
    return "".join(out)


def _render_stock_news(ticker, company_name=None):
    """Full per-stock news research: tone + catalysts + theme chips, a grounded
    AI brief (when a key is configured), then the multi-source article feed."""
    # Guarded like the cached helpers above: a missing/broken news_research must
    # cost us this section only, never the whole Analysis page.
    try:
        from news_research import sentiment_summary, theme_counts
    except Exception:
        st.caption("News research is unavailable right now.")
        return
    # Relevance matching keys off the company name as well as the symbol, since
    # most coverage writes "Nvidia", not "NVDA". Callers that already hold the
    # company details pass it in; the News page doesn't, so resolve it here
    # (cached, so this is free after the first lookup).
    if not company_name:
        try:
            from cached_fetchers import cached_fetch_company_details
            company_name = (cached_fetch_company_details(ticker, POLYGON_API_KEY)
                            or {}).get("Name") or None
        except Exception:
            company_name = None
    arts = _cached_news(ticker, company_name or "")
    if not arts:
        st.caption("No recent news found for this ticker.")
        return
    sent   = sentiment_summary(arts)
    themes = theme_counts(arts)
    cats   = _cached_catalysts(ticker)

    _tone = ("Bullish" if sent["score"] > 0.15 else
             "Bearish" if sent["score"] < -0.15 else "Mixed")
    _tcol = ("#15803d" if sent["score"] > 0.15 else
             "#b91c1c" if sent["score"] < -0.15 else "#64748b")
    _catbits = []
    if cats.get("next_earnings"):
        _catbits.append(f'Next earnings <b>{cats["next_earnings"]["date"]}</b>')
    if cats.get("latest_8k"):
        _catbits.append(f'<a href="{cats["latest_8k"]["url"]}" target="_blank" '
                        f'style="color:#1d4ed8;text-decoration:none">Latest 8-K · '
                        f'{cats["latest_8k"]["date"]}</a>')
    _chips = "".join(f'<span class="news-chip">{t} · {c}</span>'
                     for t, c in list(themes.items())[:5])
    st.markdown(
        f'<div class="news-top"><div class="news-tone" style="color:{_tcol}">'
        f'● News tone: {_tone}<span class="news-tone-sub"> &nbsp;'
        f'{sent["positive"]}+ / {sent["negative"]}− / {sent["neutral"]}○ · '
        f'{sent["n"]} stories</span></div>'
        f'<div class="news-cats">{" &nbsp;·&nbsp; ".join(_catbits)}</div></div>'
        f'<div class="news-chips">{_chips}</div>', unsafe_allow_html=True)

    brief = _cached_news_brief(ticker, company_name or "")
    if brief:
        with st.container(border=True):
            st.markdown("**AI briefing**  ·  *synthesised only from the sources below — "
                        "verify before acting; not investment advice*")
            st.markdown(brief["text"])
            with st.expander(f"Sources ({len(brief['sources'])})"):
                for s in brief["sources"]:
                    st.markdown(f"**[{s['n']}]** [{s['title']}]({s['url']}) — *{s['source']}*")

    st.markdown(_news_feed_html(arts, 12), unsafe_allow_html=True)
from live_data import get_live_price, get_intraday_data, get_top_movers, get_tape_prices
from payments import render_pricing_section, create_checkout_session, verify_session, check_subscription
from portfolio_builder import render_portfolio_builder
from your_portfolios import render_your_portfolios
from constants import DEV_MODE_FREE, get_risk_free_rate
from disclaimers import render_inline, render_section, render_footer
import disclaimers as _disc

# ── Page config ───────────────────────────────────────────────────────────────
# Brand assets: a bold gem-on-blue favicon (browser tab), the crystal-gem nav
# mark, and the full logo for big/dark placements. Each degrades gracefully.
_ASSETS    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_LOGO_PATH = os.path.join(_ASSETS, "logo_full.png")   # cleaned full logo (dark placements)
_FAVICON   = os.path.join(_ASSETS, "favicon.png")     # tab icon
_MARK_PATH = os.path.join(_ASSETS, "mark.png")        # crystal-gem nav mark


def _b64_img(path):
    """Read an image file as a base64 string (for inline data: URIs). '' on miss."""
    try:
        import base64
        with open(path, "rb") as _f:
            return base64.b64encode(_f.read()).decode("ascii")
    except Exception:
        return ""


_MARK_B64  = _b64_img(_MARK_PATH)
_page_icon = _FAVICON if os.path.exists(_FAVICON) else "◈"
st.set_page_config(
    page_title="QuantWizard",
    page_icon=_page_icon,
    layout="wide",
    # "auto" keeps the sidebar open on desktop but auto-collapses it on mobile,
    # so phone users see the hero/content first instead of a full-screen sidebar.
    initial_sidebar_state="auto",
)

POLYGON_API_KEY  = os.environ.get("POLYGON_API_KEY", "").strip()
FMP_API_KEY      = os.environ.get("FMP_API_KEY", "").strip()
SHOW_PRICING     = False  # Set True when ready to accept payments

if not POLYGON_API_KEY:
    st.error("POLYGON_API_KEY is not configured. Contact support or check your environment variables.")
    st.stop()

# ── CSS ───────────────────────────────────────────────────────────────────────
# Styles live in styles.css — load once, inject into the page as <style>.
_CSS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "styles.css")
with open(_CSS_PATH, "r", encoding="utf-8") as _f:
    st.markdown(f"<style>\n{_f.read()}\n</style>", unsafe_allow_html=True)

# ── Plotly defaults ───────────────────────────────────────────────────────────
# Hide the Plotly modebar (camera/zoom/pan icons) on every chart by default.
# The toolbar screams "dev dashboard" — pro fintech sites never show it. Done
# via monkey-patch so existing st.plotly_chart() callers don't need updating.
_HIDE_TOOLBAR_CONFIG = {"displayModeBar": False, "displaylogo": False}
_orig_plotly_chart = st.plotly_chart
def _plotly_chart_no_toolbar(*args, **kwargs):
    user_config = kwargs.get("config") or {}
    kwargs["config"] = {**_HIDE_TOOLBAR_CONFIG, **user_config}
    return _orig_plotly_chart(*args, **kwargs)
st.plotly_chart = _plotly_chart_no_toolbar


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
        st.success("Welcome to QuantWizard Pro!")

# ── Re-verify Pro status on page refresh via saved email ──────────────────────
# DEV_MODE_FREE: skip subscription lookup — preserved, not deleted.
elif not DEV_MODE_FREE and not st.session_state.get("is_pro"):
    saved_email = params.get("email", "")
    if saved_email and not st.session_state.get("_sub_checked"):
        st.session_state["_sub_checked"] = True
        if check_subscription(saved_email):
            st.session_state["is_pro"]     = True
            st.session_state["user_email"] = saved_email

# ── Top navigation ────────────────────────────────────────────────────────────
# Custom sticky navbar (replaces Streamlit's empty built-in header). Uses native
# buttons in a keyed container so nav clicks are reruns — session state (e.g. a
# built portfolio) survives — while ?page= keeps the URL shareable.
_PAGES = ("home", "analysis", "news", "builder", "portfolios")
_page  = st.query_params.get("page", "home")
if _page not in _PAGES:
    _page = "home"

def _goto(pg):
    st.query_params["page"] = pg
    st.rerun()

with st.container(key="topnav"):
    # Brand (left) · flexible spacer · nav links clustered to the right.
    _nc = st.columns([2.4, 1.5, 0.95, 1.25, 0.95, 2.0, 1.85], vertical_alignment="center")
    _brand_mark = (
        f'<img class="topnav-mark-img" src="data:image/png;base64,{_MARK_B64}" alt="QuantWizard">'
        if _MARK_B64 else
        '<span class="topnav-mark">'
        '<span class="material-symbols-outlined">candlestick_chart</span></span>')
    _nc[0].markdown(
        '<div class="topnav-brand">' + _brand_mark +
        '<span class="topnav-word">Quant<b>Wizard</b></span></div>',
        unsafe_allow_html=True)
    for _i, (_lbl, _pg) in enumerate(
            [("Home", "home"), ("Analysis", "analysis"), ("News", "news"),
             ("Portfolio Builder", "builder"), ("Your Portfolios", "portfolios")], start=2):
        if _nc[_i].button(_lbl, key=f"nav_{_pg}", use_container_width=True,
                          type="primary" if _page == _pg else "tertiary"):
            _goto(_pg)

# ── Header ────────────────────────────────────────────────────────────────────
_logo_html = (
    f'<img src="https://raw.githubusercontent.com/wstratton707/stockwizard/main/assets/logo.png" '
    f'alt="QuantWizard" style="width:48px;height:48px;border-radius:10px;flex-shrink:0;'
    f'box-shadow:0 4px 12px rgba(59,130,246,0.4)">'
) if os.path.exists(_LOGO_PATH) else '<div class="main-header-logo-icon">W</div>'

# (The old global header card moved into the Home page hero — see routing below.)

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

# Streamlit-level cache on top of live_data.py's module dicts so these
# lightweight but frequently-called fetches don't hit Polygon on every
# tab switch / widget toggle.
@st.cache_data(ttl=60, max_entries=1, show_spinner=False)
def _cached_tape(_api_key):
    return get_tape_prices(_api_key)

@st.cache_data(ttl=300, max_entries=1, show_spinner=False)
def _cached_movers(_api_key):
    return get_top_movers(_api_key)

# ── Report carousel (client-side: images baked into the iframe → instant, no rerun) ──
_REPORT_SLIDES = [
    ("assets/rep_dashboard.png",    "Dashboard — metrics, risk & sparklines"),
    ("assets/rep_charts.png",       "Charts — price, volume, Bollinger & RSI"),
    ("assets/rep_fundamentals.png", "Fundamentals — valuation, margins, growth & quality"),
    ("assets/rep_monte_carlo.png",  "Monte Carlo — forecast summary & simulations"),
]

@st.cache_data(show_spinner=False)
def _report_carousel_html():
    """Client-side HTML/JS carousel of the report sheets — cycling is instant, with
    no Streamlit rerun per click.

    Images are referenced as static URLs, not base64-inlined. Inlining them put
    ~1.9 MB through the websocket on every Home render (93% of the page's DOM,
    and uncacheable); as static WebP files the browser caches them across visits
    and only the visible slide is fetched eagerly. Falls back to inlining the PNG
    if the WebP is missing, so the preview can never silently disappear.
    """
    import base64
    _here = os.path.dirname(__file__)
    parts, caps = [], []
    for path, cap in _REPORT_SLIDES:
        webp = os.path.join(_here, "static",
                            os.path.basename(path).replace(".png", ".webp"))
        png = os.path.join(_here, path)
        if os.path.exists(webp):
            # Relative so it still resolves under a baseUrlPath deployment.
            parts.append("app/static/" + os.path.basename(webp))
        elif os.path.exists(png):
            parts.append("data:image/png;base64," +
                         base64.b64encode(open(png, "rb").read()).decode())
        else:
            continue
        caps.append(cap)
    if not parts:
        return "<div style='color:#94a3b8'>Sample report preview unavailable.</div>"
    # Deliberately NOT loading="lazy" on the hidden slides: they're display:none, so
    # a lazy image is never in-viewport and never fetches — the first arrow click
    # would then stall on a cold download, which is exactly what this carousel
    # exists to avoid. Low fetch priority keeps them off the critical path instead,
    # and at ~120 KB of WebP each they cost little to prefetch.
    imgs = "".join(
        f'<img class="{"on" if i == 0 else ""}" src="{src}" decoding="async"'
        f'{"" if i == 0 else " fetchpriority=\"low\""}>'
        for i, src in enumerate(parts))
    caps_js = ",".join('"' + c.replace('"', "") + '"' for c in caps)
    tmpl = """<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box;margin:0;padding:0;font-family:system-ui,-apple-system,Arial,sans-serif}
body{background:transparent}
.wrap{display:flex;align-items:center;gap:12px}
.frame{flex:1;text-align:center;min-width:0}
.frame img{display:none;max-height:600px;max-width:100%;width:auto;margin:0 auto;border:1px solid #e2e8f0;border-radius:8px;box-shadow:0 6px 20px rgba(15,39,71,.10)}
.frame img.on{display:inline-block}
.arrow{flex:0 0 auto;width:42px;height:60px;border:1px solid #e2e8f0;border-radius:8px;background:#eef2f7;color:#64748b;font-size:1.05rem;cursor:pointer}
.arrow:hover{background:#dbe4ee;color:#0f2747}
.cap{text-align:center;color:#64748b;font-size:.82rem;margin-top:.6rem;font-weight:500}
.dots{text-align:center;margin-top:.5rem}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#cbd5e1;margin:0 3px;cursor:pointer}
.dot.on{background:#38bdf8}
</style></head><body>
<div class="wrap">
<button class="arrow" onclick="go(-1)">&#9664;</button>
<div class="frame" id="frame">__IMGS__</div>
<button class="arrow" onclick="go(1)">&#9654;</button>
</div>
<div class="cap" id="cap"></div>
<div class="dots" id="dots"></div>
<script>
var caps=[__CAPS__],i=0,imgs=document.querySelectorAll('#frame img'),dots=document.getElementById('dots');
for(var n=0;n<imgs.length;n++){var d=document.createElement('span');d.className='dot'+(n===0?' on':'');(function(k){d.onclick=function(){show(k)}})(n);dots.appendChild(d);}
function show(n){i=(n+imgs.length)%imgs.length;for(var k=0;k<imgs.length;k++)imgs[k].className=(k===i?'on':'');var ds=dots.children;for(var k=0;k<ds.length;k++)ds[k].className='dot'+(k===i?' on':'');document.getElementById('cap').textContent=caps[i]+' · '+(i+1)+' / '+imgs.length;}
function go(d){show(i+d)}
document.addEventListener('keydown',function(e){if(e.key==='ArrowLeft')go(-1);if(e.key==='ArrowRight')go(1)});
show(0);
</script></body></html>"""
    return tmpl.replace("__IMGS__", imgs).replace("__CAPS__", caps_js)

# Ticker tape is rendered on the Home page (see routing below), not globally.

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    # ── Pro status / upgrade ──────────────────────────────────────────────────
    if DEV_MODE_FREE:
        # No user-facing "dev mode" banner — it reads as unfinished. Payments stay
        # disabled via DEV_MODE_FREE; the UI just doesn't advertise it.
        pass
    elif SHOW_PRICING:
        # ── Original payment UI preserved below — do not delete ──────────────
        if st.session_state["is_pro"]:
            st.markdown("""
            <div style="background:linear-gradient(135deg,var(--brand-1),var(--brand-2));
                        border:1px solid rgba(59,130,246,0.3);border-radius:2px;
                        padding:0.75rem 1rem;margin-bottom:1.25rem;text-align:center">
                <span style="color:#1d4ed8;font-weight:700;font-size:0.82rem;
                             letter-spacing:0.5px"><span class="material-symbols-outlined" style="font-size:0.95rem;vertical-align:middle">bolt</span> PRO MEMBER</span>
            </div>
            """, unsafe_allow_html=True)
        else:
            if st.button("Upgrade to Pro — $9.99/mo", use_container_width=True):
                st.session_state["show_payment"] = True
                st.rerun()
    else:
        st.markdown("""
        <div style="background:linear-gradient(135deg,var(--brand-1),var(--brand-2));
                    border:1px solid rgba(59,130,246,0.3);border-radius:2px;
                    padding:0.7rem 1rem;margin-bottom:1.25rem;text-align:center">
            <span style="color:#1d4ed8;font-weight:700;font-size:0.8rem;letter-spacing:0.5px">
<span class="material-symbols-outlined" style="font-size:0.95rem;vertical-align:middle">bolt</span> QUANTWIZARD
            </span>
        </div>
        """, unsafe_allow_html=True)


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
    st.markdown("### Upgrade to QuantWizard Pro")
    col1, col2 = st.columns([2, 1])
    with col1:
        email_for_payment = st.text_input("Your email address", placeholder="you@email.com", key="pay_email")
    with col2:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("Continue to Payment →", type="primary"):
            if email_for_payment and "@" in email_for_payment:
                base_url = os.environ.get("BASE_URL", "https://stockwizard-fhpncsuzkzaxy6bs427f9q.streamlit.app")
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

# ── Page routing ──────────────────────────────────────────────────────────────
if _page == "home":
    # ── Hero ──────────────────────────────────────────────────────────────────
    st.markdown("""
    <div class="home-hero">
      <span class="home-hero-badge">Institutional tools · retail price</span>
      <h1 class="home-hero-title">Institutional-grade equity &amp;<br>portfolio research — in seconds.</h1>
      <p class="home-hero-sub">Analyze any stock, ETF, crypto or bond. Build a risk-optimized
      portfolio and track it forward. Then export a <b>professional research report</b> with one click.</p>
    </div>
    """, unsafe_allow_html=True)
    _hc = st.columns([1.1, 1.1, 2.8])
    if _hc[0].button("Analyze a stock", type="primary", use_container_width=True, key="cta_analyze"):
        _goto("analysis")
    if _hc[1].button("Build a portfolio", use_container_width=True, key="cta_build"):
        _goto("builder")

    st.markdown("""
    <div class="guide-panel">
      <div class="guide-header">Quick start</div>
      <div class="guide-grid">
        <div class="guide-card">
          <span class="material-symbols-outlined">query_stats</span>
          <div>
            <strong>Start with analysis</strong>
            <p>Check any ticker, review the technicals and fundamentals, and export a polished report.</p>
          </div>
        </div>
        <div class="guide-card">
          <span class="material-symbols-outlined">pie_chart</span>
          <div>
            <strong>Build your first portfolio</strong>
            <p>Pick a balanced starting point, then refine allocations and risk settings in a few steps.</p>
          </div>
        </div>
        <div class="guide-card">
          <span class="material-symbols-outlined">account_balance_wallet</span>
          <div>
            <strong>Track what matters</strong>
            <p>Monitor forward performance against the S&amp;P 500 and keep your portfolio ideas organized.</p>
          </div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Live ticker tape ──────────────────────────────────────────────────────
    _tape_items = _cached_tape(POLYGON_API_KEY)
    if _tape_items:
        st.markdown(_tape_html(_tape_items), unsafe_allow_html=True)

    # ── What you can do ───────────────────────────────────────────────────────
    st.markdown('<div class="home-section-title">What you can do</div>', unsafe_allow_html=True)
    _cards = [
        ("query_stats", "Analysis", "Deep-dive any ticker — price action, technical signals, fundamentals, "
                           "a Monte Carlo forecast, and peers.", "Open Analysis", "analysis"),
        ("pie_chart", "Portfolio Builder", "Build a risk-optimized portfolio (CAPM expected returns) from a "
                                    "ranked universe, backtested over 5 years.", "Open Builder", "builder"),
        ("account_balance_wallet", "Your Portfolios", "Save a portfolio and track its real performance from day one — "
                                  "value vs the S&P 500, drawdown, and holdings.", "Open Portfolios", "portfolios"),
    ]
    # A numbered, ruled index rather than a 3-up card grid. The card grid is the
    # single most template-ish element in this kind of app — a direct competitor
    # ships the identical pattern — and it reads as filler. A contents-page list
    # suits a research product and matches the editorial display face.
    st.markdown('<div class="index-rule"></div>', unsafe_allow_html=True)
    for _i, (_ic, _ti, _desc, _btn, _pg) in enumerate(_cards, 1):
        _c = st.columns([8, 2], vertical_alignment="center")
        with _c[0]:
            st.markdown(f"""<div class="index-row">
              <span class="index-num">{_i:02d}</span>
              <div>
                <div class="index-title">{_ti}</div>
                <div class="index-desc">{_desc}</div>
              </div>
            </div>""", unsafe_allow_html=True)
        with _c[1]:
            if st.button(_btn, key=f"card_{_pg}", use_container_width=True):
                _goto(_pg)
        st.markdown('<div class="index-rule"></div>', unsafe_allow_html=True)

    # ── Excel-export spotlight (the hero feature) — carousel ──────────────────
    st.markdown('<div class="home-section-title">The one-click professional report</div>',
                unsafe_allow_html=True)
    st.markdown("""
    <div class="home-spotlight-lead">
      <h3>A full analyst report on any stock — in ~30 seconds.</h3>
      <p>Every analysis exports to a polished multi-sheet <b>Excel</b> workbook (plus a
      <b>PowerPoint</b> deck) — the kind of report that takes an analyst hours. Flip through
      a few pages below.</p>
    </div>
    """, unsafe_allow_html=True)

    st.components.v1.html(_report_carousel_html(), height=700, scrolling=False)

    _bc = st.columns([2, 1], vertical_alignment="center")
    with _bc[0]:
        st.markdown("""<ul class="home-spotlight-ul">
          <li>10 formatted, brand-styled sheets + a PowerPoint deck</li>
          <li>Monte Carlo forecast + full risk metrics</li>
          <li>Shareable — send it to a client or a group chat</li>
        </ul>""", unsafe_allow_html=True)
    with _bc[1]:
        if st.button("Generate one →", type="primary", use_container_width=True, key="cta_report"):
            _goto("analysis")

    # ── Pre-built sample ──────────────────────────────────────────────────────
    # A real workbook, generated ahead of time by scripts/generate_sample_report.py
    # and served as a static file. Someone can hold the actual output without
    # waiting on a live run — no API call, no rate limit, no cold start. Served
    # over HTTP rather than st.download_button so it costs no rerun and no memory.
    # The date is read from the sidecar the generator writes, so the label can't
    # drift away from the file.
    try:
        import json as _json
        from icons import icon as _icon
        _sm_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "static", "sample_report.json")
        with open(_sm_path, encoding="utf-8") as _f:
            _sm = _json.load(_f)
        _sm_size = f"{_sm.get('bytes', 0) / 1_048_576:.1f} MB"
        _sm_date = datetime.strptime(_sm["generated"], "%Y-%m-%d").strftime("%d %b %Y")
        st.markdown(
            f'<a class="sample-dl" href="app/static/{_sm["file"]}" download>'
            f'{_icon("download", 17)}'
            f'<span><b>See a real one — {_sm["ticker"]} research report'
            f'</b><span class="sample-dl-sub">Excel · {_sm["period"]} · {_sm_size} · '
            f'generated {_sm_date}</span></span></a>', unsafe_allow_html=True)
    except Exception:
        pass   # no sample built yet — the section simply doesn't appear

    st.markdown('<div class="home-footer">QuantWizard · For informational purposes only · '
                'Not investment advice</div>', unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════
elif _page == "analysis":

    # Wrapped in a keyed container purely so styles.css can scope this panel via
    # .st-key-analysis-inputs: the app has seven expanders and the others should
    # keep the quiet default treatment. (`key` on st.expander itself sets widget
    # identity but emits no CSS class — only containers do.)
    _inputs_box = st.container(key="analysis-inputs")
    with _inputs_box.expander("Analysis inputs",
                     expanded=not st.session_state.get("analysis_ran", False)):
        # Day Trader Mode removed — Investor Mode is the only experience now.
        mode = "Investor Mode"

        # ── Ticker ────────────────────────────────────────────────────────────────
        st.markdown('<div class="field-label">Ticker</div>',
                    unsafe_allow_html=True)
        # Pressing Enter here runs the analysis just like the Run Analysis button
        # does (see the `not run_btn and not ticker_input` landing-page test below),
        # so it has to collapse this panel the same way. Without it the results
        # rendered underneath a full-height, still-open form and users couldn't tell
        # anything had happened without scrolling. Clearing the field re-opens the
        # panel, which matches the landing-page state we fall back to.
        ticker_input = st.text_input(
            "", placeholder="e.g. AAPL, SPY, BTC, ETH",
            key="analysis_ticker",
            on_change=lambda: st.session_state.update(
                analysis_ran=bool(st.session_state.get("analysis_ticker", "").strip())),
            label_visibility="collapsed"
        ).strip().upper()

        # ── Date range ────────────────────────────────────────────────────────────
        if mode == "Investor Mode":
            st.markdown('<div class="field-label">Date Range</div>',
                        unsafe_allow_html=True)
            _SLIDER_OPTIONS = ["1M","3M","6M","1Y","2Y","5Y","10Y"]
            _SLIDER_DAYS    = {"1M":30,"3M":90,"6M":180,"1Y":365,"2Y":730,
                               "5Y":1825,"10Y":3650}
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
            st.markdown('<div class="field-label">Candle Size</div>',
                        unsafe_allow_html=True)
            tf_options = {"1 Min":"1min","5 Min":"5min","15 Min":"15min","1 Hour":"1hour"}
            tf_label   = st.radio("", list(tf_options.keys()), index=1,
                                  horizontal=True, label_visibility="collapsed")
            st.session_state["candle_tf"] = tf_options[tf_label]

        # ── Benchmarks ────────────────────────────────────────────────────────────
        st.markdown('<div class="field-label">Benchmarks</div>',
                    unsafe_allow_html=True)
        include_spy = st.checkbox("S&P 500 (SPY)", value=True)
        include_qqq = st.checkbox("NASDAQ (QQQ)", value=True)

        if mode == "Investor Mode":
            # ── Peer comparison ───────────────────────────────────────────────────
            st.markdown('<div class="field-label">Peer Comparison</div>',
                        unsafe_allow_html=True)
            peers_input = st.text_input("", placeholder="e.g. GOOGL, AMZN",
                                        label_visibility="collapsed")

            # ── Report modules ────────────────────────────────────────────────────
            st.markdown('<div class="field-label">Report Modules</div>',
                        unsafe_allow_html=True)
            do_mc     = st.checkbox("Price Forecast", value=True)
            do_sector = st.checkbox("Sector Comparison",    value=True)
            do_corr   = st.checkbox("Correlation Matrix",   value=True)
            do_sr     = st.checkbox("Support & Resistance", value=True)
            do_news   = st.checkbox("News Headlines",       value=True)
            do_peers  = st.checkbox("Peer Comparison",      value=True)

            if do_mc:
                st.markdown('<div class="field-label">Forecast Settings</div>',
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
                        'font-family:var(--font-sans)">'
                        'Our <span style="color:#1d4ed8 !important;font-weight:600">Custom Forecast</span> '
                        'combines three models — '
                        '<span style="color:#1d4ed8 !important;font-weight:500">GARCH</span> volatility modeling, '
                        '<span style="color:#1d4ed8 !important;font-weight:500">Monte Carlo</span> simulation, '
                        'and a <span style="color:#1d4ed8 !important;font-weight:500">ML ensemble</span> '
                        '(Random Forest / XGBoost) — for smarter, more adaptive price projections. '
                        'GARCH captures volatility clustering, Monte Carlo simulates thousands of '
                        'price paths, and the ML model adds a data-driven drift signal — '
                        'all powered by multi-source market data (Polygon, Yahoo Finance &amp; Finnhub).</div>',
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
        # Collapse this form once an analysis has run so results aren't buried
        # below a full-height input panel (the flag is set before the rerun, so
        # the form is already collapsed when results render). Re-open to re-run.
        run_btn = st.button(
            "Run Analysis", type="primary", use_container_width=True,
            on_click=lambda: st.session_state.update(analysis_ran=True))


    # ── Landing page (no ticker entered) ─────────────────────────────────────
    if not run_btn and not ticker_input:

        # ── Hero ──────────────────────────────────────────────────────────────
        st.markdown("""
        <div style="background:linear-gradient(135deg,var(--brand-1) 0%,var(--brand-2) 100%);
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
                       line-height:1.2;margin:0 0 1rem;font-family:var(--font-display)">
                Deep Analysis on Any<br>Stock, ETF, Crypto or Bond
            </h2>
            <p style="color:#b0c4de;font-size:1.05rem;max-width:520px;
                      line-height:1.65;margin:0 0 2rem">
                Technicals, fundamentals, risk and a Monte&nbsp;Carlo forecast —
                worked through and exported to Excel in about 30 seconds.
            </p>
            <div style="display:flex;gap:0.5rem;align-items:center;margin-bottom:2.5rem;
                        color:#b0c4de;font-size:0.92rem;font-weight:500">
                <span style="color:#60a5fa;font-size:1.05rem;line-height:1">&#8593;</span>
                Enter a ticker in the panel above &mdash; free, no account needed.
            </div>
            <div style="display:flex;gap:3rem;flex-wrap:wrap;border-top:1px solid rgba(255,255,255,0.08);
                        padding-top:1.5rem">
                <div>
                    <div style="color:#ffffff;font-size:1.5rem;font-weight:700;
                                font-family:'JetBrains Mono',monospace">320+</div>
                    <div style="color:#b0c4de;font-size:0.75rem;text-transform:uppercase;
                                letter-spacing:0.5px">Stocks Ranked Daily</div>
                </div>
                <div>
                    <div style="color:#ffffff;font-size:1.5rem;font-weight:700;
                                font-family:'JetBrains Mono',monospace">10Y</div>
                    <div style="color:#b0c4de;font-size:0.75rem;text-transform:uppercase;
                                letter-spacing:0.5px">Price History</div>
                </div>
                <div>
                    <div style="color:#ffffff;font-size:1.5rem;font-weight:700;
                                font-family:'JetBrains Mono',monospace">1,000</div>
                    <div style="color:#b0c4de;font-size:0.75rem;text-transform:uppercase;
                                letter-spacing:0.5px">Monte Carlo Paths</div>
                </div>
                <div>
                    <div style="color:#ffffff;font-size:1.5rem;font-weight:700;
                                font-family:'JetBrains Mono',monospace">5</div>
                    <div style="color:#b0c4de;font-size:0.75rem;text-transform:uppercase;
                                letter-spacing:0.5px">Crash Scenarios</div>
                </div>
                <div>
                    <div style="color:#ffffff;font-size:1.5rem;font-weight:700;
                                font-family:'JetBrains Mono',monospace">Daily</div>
                    <div style="color:#b0c4de;font-size:0.75rem;text-transform:uppercase;
                                letter-spacing:0.5px">Market Data</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ── Market movers ─────────────────────────────────────────────────────
        st.markdown("""
        <div class="section-header">Market Movers · Last Close</div>
        """, unsafe_allow_html=True)

        with st.spinner("Loading market data..."):
            gainers, losers = _cached_movers(POLYGON_API_KEY)

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
                        <span style="font-family:'JetBrains Mono',monospace;font-size:0.83rem;color:#6b7a8d">{g['Price']}</span>
                        <span style="color:#059669;font-family:'JetBrains Mono',monospace;font-weight:700;font-size:0.88rem">{g['Change']}</span>
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
                        <span style="font-family:'JetBrains Mono',monospace;font-size:0.83rem;color:#6b7a8d">{l['Price']}</span>
                        <span style="color:#dc2626;font-family:'JetBrains Mono',monospace;font-weight:700;font-size:0.88rem">{l['Change']}</span>
                    </div>""", unsafe_allow_html=True)
            else:
                st.markdown('<span style="color:#38bdf8;font-size:0.85rem">Market data unavailable right now.</span>', unsafe_allow_html=True)

        # ── Problem section ───────────────────────────────────────────────────
        st.markdown("""
        <div class="section-header">Why QuantWizard</div>
        """, unsafe_allow_html=True)

        p1, p2, p3 = st.columns(3)
        for col, icon, problem, solution in [
            (p1, "show_chart", "A price chart isn't risk.",
             "We give you the numbers that actually matter — volatility, drawdown, Sharpe against the live T-bill, and a 1,000-path forecast."),
            (p2, "search", "Screeners hand you a list.",
             "They won't build the portfolio. We rank 320+ names daily, then optimise the weights to your risk tolerance."),
            (p3, "payments", "Advisors charge thousands.",
             "Monte Carlo, efficient frontier, GARCH — the models a quant desk runs, for $9.99 a month."),
        ]:
            with col:
                st.markdown(f"""
                <div style="border-top:2px solid #0f2747;padding:0.95rem 1.4rem 0.5rem 0;height:100%">
                    <div style="margin-bottom:0.75rem"><span class="material-symbols-outlined"
                         style="font-size:1.7rem;color:#1d4ed8">{icon}</span></div>
                    <div style="font-weight:600;color:#0f172a;font-size:0.88rem;
                                margin-bottom:0.5rem">{problem}</div>
                    <div style="color:#64748b;font-size:0.82rem;line-height:1.6">{solution}</div>
                </div>
                """, unsafe_allow_html=True)

        # ── Feature cards ─────────────────────────────────────────────────────
        st.markdown("""
        <div class="section-header">What's Included</div>
        """, unsafe_allow_html=True)

        fc1, fc2 = st.columns(2)
        fc3, fc4 = st.columns(2)

        for col, icon, title, tier, items in [
            (fc1, "monitoring", "Stock Analysis", "Free",
             ["Bollinger Bands, RSI, GARCH volatility", "Monte Carlo simulation (1,000 paths)",
              "Peer comparison vs sector", "10-yr fundamentals + F-Score & Z-Score", "Excel + PowerPoint export"]),
            (fc2, "account_balance_wallet", "Portfolio Builder", "Pro",
             ["320+ stocks ranked by multi-factor score", "5-year backtest with quarterly rebalancing",
              "Mean-variance optimization", "Portfolio Monte Carlo with milestone projections",
              "Diversification score + correlation heatmap"]),
            (fc3, "local_fire_department", "Stress Test", "Pro",
             ["5 historical crashes: 2008, COVID, 2022, dot-com, 2018", "Beta-based shock from your holdings' sensitivity",
              "Portfolio return vs S&P 500 per crash", "Dollar impact calculator",
              "Correlation culprit detection"]),
            (fc4, "account_balance", "Bond & Portfolio Autopsy", "Pro",
             ["Bond ETF analysis across 6 categories", "Upload your holdings CSV — see what broke",
              "P&L attribution per position", "Rolling volatility + drawdown charts",
              "Benchmark comparison"]),
        ]:
            with col:
                tier_color = "#1d4ed8" if tier == "Pro" else "#059669"
                tier_bg    = "rgba(29,78,216,0.08)" if tier == "Pro" else "rgba(5,150,105,0.08)"
                items_html = "".join(f"<li style='margin-bottom:0.3rem'>{i}</li>" for i in items)
                st.markdown(f"""
                <div style="border-top:2px solid {tier_color};padding:0.95rem 1.4rem 1.1rem 0;margin-bottom:1.1rem">
                    <div style="display:flex;align-items:center;justify-content:space-between;
                                margin-bottom:0.75rem">
                        <div style="display:flex;align-items:center;gap:0.5rem">
                            <span class="material-symbols-outlined"
                                  style="font-size:1.45rem;color:{tier_color}">{icon}</span>
                            <span style="font-weight:700;color:#0f172a;font-size:0.95rem">{title}</span>
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
        <div class="section-header">How It Works</div>
        """, unsafe_allow_html=True)

        h1, h2, h3 = st.columns(3)
        for col, num, title, desc in [
            (h1, "1", "Set your constraints",
             "Risk tolerance, capital, horizon, sectors. Two minutes."),
            (h2, "2", "We rank and optimise",
             "320+ names scored daily on Sharpe and momentum, best-in-sector selected, weights solved by mean-variance."),
            (h3, "3", "Take the report",
             "Five-year backtest, Monte Carlo with milestone probabilities, and a formatted Excel and PowerPoint pack."),
        ]:
            with col:
                st.markdown(f"""
                <div style="border-top:2px solid #0f2747;padding:0.95rem 1.4rem 0.5rem 0">
                    <div style="font-family:'JetBrains Mono',monospace;font-size:0.76rem;font-weight:600;color:#3b82f6;letter-spacing:0.6px;margin-bottom:0.5rem">0{num}</div>
                    <div style="font-weight:700;color:#0f172a;font-size:0.9rem;
                                margin-bottom:0.4rem">{title}</div>
                    <div style="color:#64748b;font-size:0.81rem;line-height:1.6">{desc}</div>
                </div>
                """, unsafe_allow_html=True)

        # ── Methodology & Data ────────────────────────────────────────────────
        st.markdown("""
        <div class="section-header">Methodology &amp; Data</div>
        <div style="color:#64748b;font-size:0.84rem;line-height:1.6;margin-bottom:1.25rem;max-width:680px">
            We show our work. Every number below is computed with standard, citable formulas —
            and we're upfront about the data and its limits.
        </div>
        """, unsafe_allow_html=True)

        _METHOD = [
            ("dataset", "Data &amp; Freshness",
             "Live quotes are <b>real-time</b> via <b>Finnhub</b>; daily price history comes from "
             "<b>Yahoo Finance</b> (same-day close) with <b>Polygon</b> as a fallback. "
             "Fundamentals come straight from <b>SEC EDGAR</b> filings (10-K/10-Q), updated each filing. "
             "Intraday charts are delayed on the free data tier."),
            ("balance", "Risk-Adjusted Returns",
             "Sharpe and Sortino use <b>excess return over the live 3-month T-bill</b> (FRED), not raw return. "
             "Volatility is the annualized standard deviation of daily returns (σ·√252)."),
            ("query_stats", "Backtesting",
             "Portfolio backtests use a <b>time-weighted NAV</b> that separates your contributions from market "
             "performance, benchmark SPY on the <b>same contribution schedule</b>, and charge realistic "
             "rebalancing cost on traded value only."),
            ("casino", "Monte Carlo",
             "Correlated multi-asset simulation via <b>Cholesky decomposition</b>. Per-asset drift is blended "
             "70/30 toward a 7% long-run mean and <b>capped at 12%</b> — deliberately conservative to avoid "
             "over-optimistic projections."),
            ("account_balance", "Fundamental Quality",
             "From EDGAR statements: <b>Piotroski F-Score</b> (9-point profitability/leverage/efficiency test), "
             "<b>Altman Z-Score</b> (distress risk), free cash flow (operating cash flow − capex), and standard "
             "valuation multiples."),
            ("local_fire_department", "Stress Test",
             "<b>Real historical performance</b> through each crash (2008, COVID, 2022, dot-com, 2018); a "
             "position too new to have existed then falls back to a beta estimate. Correlations rise in real "
             "crises, so future losses can differ."),
        ]
        _mcards = "".join(
            f'<div style="border-top:1px solid #e2e8f0;padding:0.9rem 1.3rem 0.9rem 0">'
            f'<div style="margin-bottom:0.5rem;display:flex;align-items:center;gap:0.5rem">'
            f'<span class="material-symbols-outlined" style="color:#1d4ed8;font-size:1.4rem">{ic}</span>'
            f'<span style="font-weight:700;color:#0f172a;font-size:0.9rem">{ti}</span></div>'
            f'<div style="color:#64748b;font-size:0.8rem;line-height:1.6">{tx}</div></div>'
            for ic, ti, tx in _METHOD
        )
        st.markdown(
            f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));'
            f'gap:0 1.9rem">{_mcards}</div>'
            f'<div style="color:#94a3b8;font-size:0.76rem;line-height:1.6;margin-top:1rem;max-width:680px">'
            f'These tools are for research and education, not investment advice. Estimates and forecasts are '
            f'not predictions — past performance and modeled scenarios do not guarantee future results.</div>',
            unsafe_allow_html=True,
        )

        # ── Pricing ───────────────────────────────────────────────────────────
        st.markdown("""
        <div class="section-header">Pricing</div>
        """, unsafe_allow_html=True)

        pr1, pr2 = st.columns(2)
        with pr1:
            st.markdown("""
            <div style="border:1px solid #e2e8f0;border-radius:12px;padding:1.75rem;
                        background:#ffffff">
                <div style="font-size:0.72rem;font-weight:700;letter-spacing:0.5px;
                            text-transform:uppercase;color:#64748b;margin-bottom:0.5rem">Free</div>
                <div style="font-size:2rem;font-weight:700;color:#0f172a;
                            font-family:'JetBrains Mono',monospace;margin-bottom:0.25rem">$0</div>
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
                            font-family:'JetBrains Mono',monospace;margin-bottom:0.25rem">$9.99
                    <span style="font-size:0.9rem;font-weight:400;color:#64748b">/month</span>
                </div>
                <div style="color:#64748b;font-size:0.82rem;margin-bottom:1.25rem">Cancel anytime</div>
                <ul style="color:#0f172a;font-size:0.83rem;line-height:1.8;padding-left:1.1rem;margin:0 0 1.25rem">
                    <li>Everything in Free</li>
                    <li><strong>Portfolio Builder</strong> — 320+ stocks, 5-year backtest</li>
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
        <div class="section-header">Built By</div>
        """, unsafe_allow_html=True)

        _fc, _ = st.columns([3, 2])
        with _fc:
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
                        "I built QuantWizard because I was tired of spending hours pulling financial
                        data manually. Any investor deserves a professional report in seconds."
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
            valid, info = cached_validate_ticker(ticker_input, POLYGON_API_KEY)

        if not valid:
            # Reference API may be rate-limited — try fetching price data directly
            # Only hard-stop if the ticker looks obviously wrong (non-alphanumeric)
            import re
            if not re.match(r'^[A-Z0-9.\-]{1,10}$', ticker_input):
                st.error(f"Ticker '{ticker_input}' not found. Check the symbol and try again.")
                st.stop()
            # Otherwise continue — fetch_stock_data will raise if the ticker is truly invalid

        # Detect asset type (stock / etf / crypto)
        asset_type = cached_detect_asset_type(ticker_input, POLYGON_API_KEY)
        is_crypto  = asset_type == "crypto"
        is_etf     = asset_type == "etf"
        # For Polygon API calls, crypto needs the X: prefix
        _poly_ticker = CRYPTO_TICKERS.get(ticker_input, (f"X:{ticker_input}USD", None))[0] \
                       if is_crypto else ticker_input

        # Live price ticker — fetched here so Day Trader Mode can use it too.
        # The full hero panel (with day/52W range bars, etc.) is rendered after
        # df is loaded inside Investor Mode below.
        live = get_live_price(_poly_ticker, POLYGON_API_KEY)

        # Day Trader Mode skips the full hero — show the slim live card here.
        if mode == "Day Trader Mode" and live:
            asset_tag = ""
            if is_crypto:
                asset_tag = '<span class="stock-hero-tag crypto" style="margin-left:0.6rem">CRYPTO</span>'
            elif is_etf:
                asset_tag = '<span class="stock-hero-tag etf" style="margin-left:0.6rem">ETF</span>'
            sign       = "+" if live["change"] >= 0 else ""
            change_cls = "live-change-pos" if live["change"] >= 0 else "live-change-neg"
            _dt_live   = live.get("source") == "finnhub"
            _dt_qlabel = "● Live" if _dt_live else "● Delayed quote"
            _dt_qcolor = "#059669" if _dt_live else "#94a3b8"
            _dt_qsub   = "Real-time (Finnhub)" if _dt_live else "~15-min delayed (free data tier)"
            st.markdown(f"""
            <div class="live-ticker">
                <div>
                    <span style="color:#6b7a8d;font-size:0.8rem;font-weight:600;
                                 letter-spacing:0.5px;text-transform:uppercase">{ticker_input}</span>{asset_tag}
                    <div class="live-price">${live['price']:,.2f}</div>
                    <span class="{change_cls}">{sign}{live['change']:,.2f} ({sign}{live['pct']:.2f}%)</span>
                </div>
                <div style="text-align:right">
                    <div><span style="color:{_dt_qcolor};font-size:0.78rem">{_dt_qlabel}</span></div>
                    <div style="color:#6b7a8d;font-size:0.75rem;margin-top:4px">As of {live['time']}</div>
                    <div style="color:#6b7a8d;font-size:0.72rem">{_dt_qsub}</div>
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
                               font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left",
                               y=0.97, yanchor="top"),
                    height=500, template=None,
                    plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=60, r=90, t=58, b=50),
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
                        orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
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
                                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
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
                <div style="margin-bottom:0.5rem"><span class="material-symbols-outlined" style="font-size:1.8rem;color:#94a3b8">lock</span></div>
                <div style="color:#fff;font-weight:600;font-size:1.1rem;margin-bottom:0.5rem">
                    Day Trader Mode is Pro Only
                </div>
                <div style="color:#6b7a8d;font-size:0.88rem;margin-bottom:1.25rem">
                    Get intraday charts (15-min delayed), technical signals, and full day-trading tools for $9.99/month
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
                    df = cached_fetch_crypto_data(ticker_input, POLYGON_API_KEY,
                                                  start_override=date_start,
                                                  end_override=date_end,
                                                  bar_size=bar_size)
                else:
                    df = cached_fetch_stock_data(ticker_input, tuple(benchmarks), POLYGON_API_KEY,
                                                 start_override=date_start,
                                                 end_override=date_end,
                                                 bar_size=bar_size)

                progress.progress(25, text="Fetching details...")
                if is_crypto:
                    company_details = {}
                    crypto_details  = cached_fetch_crypto_details(ticker_input)
                    sector          = "Cryptocurrency"
                else:
                    company_details = cached_fetch_company_details(ticker_input, POLYGON_API_KEY)
                    crypto_details  = {}
                    sector          = company_details.get("Sector", "Unknown")

                etf_details = cached_fetch_etf_details(ticker_input, FMP_API_KEY) if is_etf else {}

                news_list = []
                if do_news:
                    progress.progress(35, text="Fetching news...")
                    news_list = cached_fetch_news(ticker_input, POLYGON_API_KEY,
                                                  company_details.get("Name", ""))

                peer_df       = None
                peer_price_dfs = {}   # {ticker: df} with Cumulative_Index + Close
                if do_peers and peers_list and not is_crypto:
                    progress.progress(45, text="Fetching peer data...")
                    peer_df = cached_fetch_peer_comparison(ticker_input, tuple(peers_list), POLYGON_API_KEY)
                    for _pt in [ticker_input] + peers_list[:4]:
                        try:
                            _pdf = cached_fetch_ohlcv(_pt, "5y", POLYGON_API_KEY,
                                                      start_override=date_start,
                                                      end_override=date_end,
                                                      bar_size=bar_size)
                            # .copy() — cached_fetch_ohlcv returns a memoised df;
                            # we add Daily_Return + Cumulative_Index columns below,
                            # which would mutate the cache without this.
                            _pdf = _pdf.copy()
                            _pdf["Daily_Return"]     = _pdf["Close"].pct_change()
                            _pdf["Cumulative_Index"] = (1 + _pdf["Daily_Return"].fillna(0)).cumprod() * 100
                            peer_price_dfs[_pt] = _pdf
                        except Exception:
                            pass

                sector_df = None
                if do_sector and not is_crypto:
                    progress.progress(50, text="Fetching sector ETF...")
                    sector_df = cached_fetch_sector_data(ticker_input, POLYGON_API_KEY, sector,
                                                         start_override=date_start,
                                                         end_override=date_end,
                                                         bar_size=bar_size)

                # Proxy key for heavy-computation caches: ticker + last date
                # (df content changes → last date changes → cache invalidates).
                _last_date_key = str(df["Date"].iloc[-1]) if "Date" in df.columns and len(df) else ""

                corr_matrix = None
                if do_corr:
                    progress.progress(60, text="Building correlation matrix...")
                    corr_matrix = cached_build_correlation_matrix(
                        ticker_input, _last_date_key,
                        tuple(benchmarks) if benchmarks else (), df,
                    )

                resistance = support = None
                if do_sr:
                    progress.progress(65, text="Detecting support & resistance...")
                    resistance, support = cached_detect_support_resistance(
                        ticker_input, _last_date_key, df,
                    )

                mc_sim_df = mc_summary = None
                custom_garch_vols = custom_ml_drift = None
                if do_mc:
                    if forecast_method == "Custom Forecast":
                        progress.progress(75, text="Running Custom Forecast (GARCH + ML + Monte Carlo)...")
                        mc_sim_df, custom_garch_vols, custom_ml_drift, mc_summary = \
                            cached_run_custom_forecast(
                                ticker_input, _last_date_key, n_sims, n_horizon, df,
                            )
                    else:
                        progress.progress(75, text="Running Monte Carlo simulation...")
                        mc_sim_df, mc_summary = cached_run_monte_carlo(
                            ticker_input, _last_date_key, n_sims, n_horizon, df,
                        )

                progress.progress(85, text="Generating summary...")
                ret      = df["Daily_Return"].dropna()
                ann_ret  = ret.mean() * 252
                ann_std  = ret.std() * np.sqrt(252)
                downside = ret[ret < 0].std() * np.sqrt(252)
                # Excess-return Sharpe/Sortino: subtract the risk-free rate so
                # these match the portfolio engine (portfolio_analysis.py) and
                # the standard definition. Without it the headline ratios were
                # inflated and inconsistent across the app.
                rfr      = get_risk_free_rate()
                sharpe   = (ann_ret - rfr) / ann_std  if ann_std  else np.nan
                sortino  = (ann_ret - rfr) / downside if downside else np.nan
                summary_text = generate_summary_paragraph(
                    ticker_input, df, company_details, mc_summary, sharpe, sortino,
                    forecast_method=forecast_method)

                # Fundamentals for the report (cached → the on-screen panel below
                # reuses the same call for free). EDGAR-first, Polygon fallback.
                _fund_report = {"ok": False}
                if not is_crypto:
                    try:
                        _fr = (cached_fetch_sec_financials(ticker_input)
                               or cached_fetch_financials(ticker_input, POLYGON_API_KEY))
                        _fund_report = compute_fundamentals(
                            _fr, market_cap=company_details.get("Market Cap"),
                            price=float(df["Close"].iloc[-1]))
                    except Exception:
                        _fund_report = {"ok": False}

                # Wall-Street consensus for the report (cached → the on-screen
                # Analyst View below reuses the same call for free). {} for crypto
                # or when no Finnhub key is configured.
                _analyst_report = {} if is_crypto else cached_get_analyst_data(ticker_input)

                # Forward DCF fair value (FCF-based) for the report. Reuses the
                # fundamentals already computed above; degrades to {"ok": False}
                # for crypto or names without positive free cash flow.
                _dcf_report = {"ok": False}
                if not is_crypto and _fund_report.get("ok"):
                    try:
                        _dcf_report = dcf_valuation(_fund_report, float(df["Close"].iloc[-1]))
                    except Exception:
                        _dcf_report = {"ok": False}

                # Reports (Excel / PowerPoint) build on demand when the user
                # clicks Export below — not on every analysis — so results appear
                # immediately instead of waiting on openpyxl + python-pptx each run.
                progress.progress(100, text="Complete!")
                time.sleep(0.3)
                progress.empty()
                logs.empty()

            except Exception as e:
                progress.empty()
                logs.empty()
                _msg = str(e)
                if "rate limit" in _msg.lower() or "429" in _msg:
                    st.warning(
                        "**Market data is busy right now.** QuantWizard runs on a "
                        "shared data plan with a per-minute request cap, and it's "
                        "temporarily maxed out. Wait about **30 seconds**, then click "
                        "**▶ Run Analysis** again — it almost always goes through on "
                        "the next try.",
                        icon="⏳",
                    )
                else:
                    # Clean message for users; full traces go to the server logs,
                    # not the screen (a stack trace in the UI looks broken).
                    st.error(f"Couldn't complete the analysis for **{ticker_input}**: {_msg}")
                import traceback as _tb
                print(_tb.format_exc())   # server-side log for debugging
                st.stop()

            # Results
            latest     = df.iloc[-1]
            first      = df.iloc[0]
            period_ret = (latest["Close"] / first["Close"] - 1) * 100
            pos_neg    = lambda v: "positive" if v > 0 else ("negative" if v < 0 else "neutral")

            # Reports build on demand: a click builds the file (with a spinner),
            # caches it in session for this exact ticker+period, then swaps in a
            # download button. The mirrored buttons lower on the page share the
            # same cached file, so building once serves both.
            _report_id = f"{ticker_input}|{period_label}|{bar_size}"

            def _stock_exports(suffix):
                c1, c2, c3 = st.columns(3)
                with c1:
                    _ready = st.session_state.get("_excel_id") == _report_id
                    if not _ready and st.button("Export to Excel",
                                                use_container_width=True,
                                                key=f"gen_excel_{suffix}"):
                        with st.spinner("Building your Excel workbook…"):
                            st.session_state["_excel_buf"] = build_excel(
                                ticker_input, df, period_label,
                                company_details=company_details, sector_df=sector_df,
                                mc_sim_df=mc_sim_df, mc_summary=mc_summary,
                                news_list=news_list, peer_df=peer_df,
                                corr_matrix=corr_matrix,
                                resistance_levels=resistance, support_levels=support,
                                summary_text=summary_text,
                                bar_size=bar_size, fundamentals=_fund_report,
                                analyst_data=_analyst_report, dcf=_dcf_report,
                            )
                            st.session_state["_excel_id"] = _report_id
                        _ready = True
                    if _ready:
                        st.session_state["_excel_buf"].seek(0)
                        st.download_button(
                            "Download Excel (.xlsx)",
                            data=st.session_state["_excel_buf"],
                            file_name=f"{ticker_input}_{period_label}_Analysis.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True, key=f"dl_excel_{suffix}",
                        )
                with c2:
                    if PPTX_AVAILABLE:
                        _readyp = st.session_state.get("_pptx_id") == _report_id
                        if not _readyp and st.button("Export to PowerPoint",
                                                     use_container_width=True,
                                                     key=f"gen_pptx_{suffix}"):
                            with st.spinner("Building your PowerPoint deck…"):
                                try:
                                    st.session_state["_pptx_buf"] = build_stock_pptx(
                                        ticker_input, df, period_label,
                                        company_details=company_details,
                                        mc_sim_df=mc_sim_df, mc_summary=mc_summary,
                                        news_list=news_list, summary_text=summary_text,
                                        fundamentals=_fund_report,
                                    )
                                except Exception:
                                    st.session_state["_pptx_buf"] = None
                                st.session_state["_pptx_id"] = _report_id
                            _readyp = True
                        if _readyp:
                            _pb = st.session_state.get("_pptx_buf")
                            if _pb is not None:
                                _pb.seek(0)
                                st.download_button(
                                    "Download PowerPoint (.pptx)", data=_pb,
                                    file_name=f"{ticker_input}_{period_label}_Analysis.pptx",
                                    mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                                    use_container_width=True, key=f"dl_pptx_{suffix}",
                                )
                            else:
                                st.caption("PowerPoint export isn't available.")
                with c3:
                    if DOCX_AVAILABLE:
                        _readyw = st.session_state.get("_word_id") == _report_id
                        if not _readyw and st.button("Export to Word",
                                                     use_container_width=True,
                                                     key=f"gen_word_{suffix}"):
                            with st.spinner("Writing your Word report…"):
                                try:
                                    st.session_state["_word_buf"] = build_stock_docx(
                                        ticker_input, df, period_label,
                                        company_details=company_details,
                                        mc_summary=mc_summary, news_list=news_list,
                                        summary_text=summary_text,
                                        fundamentals=_fund_report,
                                        analyst_data=_analyst_report, dcf=_dcf_report,
                                    )
                                except Exception:
                                    st.session_state["_word_buf"] = None
                                st.session_state["_word_id"] = _report_id
                            _readyw = True
                        if _readyw:
                            _wb = st.session_state.get("_word_buf")
                            if _wb is not None:
                                _wb.seek(0)
                                st.download_button(
                                    "Download Word (.docx)", data=_wb,
                                    file_name=f"{ticker_input}_{period_label}_Report.docx",
                                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                    use_container_width=True, key=f"dl_word_{suffix}",
                                )
                            else:
                                st.caption("Word export isn't available.")

            _stock_exports("top")
            st.markdown("---")

            # ── Stock Hero Panel ──────────────────────────────────────────────
            # One panel: identity (ticker + name + tags), big price + change,
            # plus a stat ribbon with day range, 52W range, volume, etc.
            _company_name = (company_details.get("Name") if not is_crypto
                             else crypto_details.get("name", ticker_input))
            _company_name = _company_name or ticker_input
            _exchange     = company_details.get("Exchange", "") if not is_crypto else "Crypto"
            _sector_lbl   = sector if sector and sector != "Unknown" else ""
            # Sector strings from Polygon are SHOUTY ALL-CAPS sometimes — soften
            # to Title Case and truncate so the tag stays readable.
            if _sector_lbl and _sector_lbl.isupper():
                _sector_lbl = _sector_lbl.title().replace("&", "&amp;")
            if len(_sector_lbl) > 36:
                _sector_lbl = _sector_lbl[:33] + "…"

            # Tag chips (sector, exchange, asset-type, live)
            _tags = []
            if _sector_lbl:
                _tags.append(f'<span class="stock-hero-tag">{_sector_lbl}</span>')
            if _exchange:
                _tags.append(f'<span class="stock-hero-tag">{_exchange}</span>')
            if is_crypto:
                _tags.append('<span class="stock-hero-tag crypto">Crypto</span>')
            elif is_etf:
                _tags.append('<span class="stock-hero-tag etf">ETF</span>')
            # "Live" only when the US market is actually open (or a 24/7 crypto
            # market). After the close, Finnhub returns the last-trade (16:00)
            # price — that's the close, not a live tick, so labelling it "Live"
            # overclaims. Show "At close" instead.
            def _us_market_open():
                try:
                    from zoneinfo import ZoneInfo
                    from datetime import time as _t
                    now_et = datetime.now(ZoneInfo("America/New_York"))
                    return now_et.weekday() < 5 and _t(9, 30) <= now_et.time() <= _t(16, 0)
                except Exception:
                    return False
            _mkt_open = bool(is_crypto) or _us_market_open()
            if live:
                if live.get("source") == "finnhub" and _mkt_open:
                    _tags.append('<span class="stock-hero-tag live">● Live</span>')
                elif live.get("source") == "finnhub":
                    _tags.append('<span class="stock-hero-tag">At close</span>')
                else:
                    _tags.append('<span class="stock-hero-tag">Delayed</span>')

            # Price + change — prefer live tick, fall back to last close
            _price_now    = float(live["price"]) if live else float(latest["Close"])
            _change_abs   = float(live["change"]) if live else (float(latest["Close"]) - float(df["Close"].iloc[-2]) if len(df) > 1 else 0.0)
            _change_pct   = float(live["pct"])    if live else (_change_abs / float(df["Close"].iloc[-2]) * 100 if len(df) > 1 and df["Close"].iloc[-2] else 0.0)
            _change_cls   = "pos" if _change_abs >= 0 else "neg"
            _change_arrow = "▲" if _change_abs >= 0 else "▼"
            _change_sign  = "+" if _change_abs >= 0 else ""
            # Always show a freshness stamp: a delayed-quote time when we have a
            # live tick, otherwise the date of the last close (so an EOD price is
            # never shown without saying how old it is).
            try:
                _close_dt = pd.to_datetime(latest["Date"]).strftime("%b %d, %Y")
            except Exception:
                _close_dt = ""
            if live:
                if live.get("source") == "finnhub" and _mkt_open:
                    _fresh = "Live"
                elif live.get("source") == "finnhub":
                    _fresh = "At close"
                else:
                    _fresh = "Delayed (~15 min)"
                _live_meta = f'<div class="stock-hero-meta">{_fresh} · as of {live["time"]}</div>'
            elif _close_dt:
                _live_meta = f'<div class="stock-hero-meta">Last close · {_close_dt}</div>'
            else:
                _live_meta = ""

            # Day range fill % (where current price sits between today's low and high)
            _day_open  = float(latest.get("Open",  _price_now))
            _day_high  = float(latest.get("High",  _price_now))
            _day_low   = float(latest.get("Low",   _price_now))
            _day_span  = max(_day_high - _day_low, 1e-9)
            _day_pct   = max(0.0, min(100.0, (_price_now - _day_low) / _day_span * 100))

            # 52W range fill %
            _w52_high  = float(latest["52W_High"]) if pd.notna(latest.get("52W_High")) else _day_high
            _w52_low   = float(latest["52W_Low"])  if pd.notna(latest.get("52W_Low"))  else _day_low
            _w52_span  = max(_w52_high - _w52_low, 1e-9)
            _w52_pct   = max(0.0, min(100.0, (_price_now - _w52_low) / _w52_span * 100))

            # Volume vs 20d average
            _vol       = float(latest.get("Volume", 0))
            _vol_avg   = float(latest.get("Vol_MA20", 0)) if pd.notna(latest.get("Vol_MA20")) else 0
            def _fmt_vol(v):
                if v >= 1e9: return f"{v/1e9:.2f}B"
                if v >= 1e6: return f"{v/1e6:.2f}M"
                if v >= 1e3: return f"{v/1e3:.1f}K"
                return f"{v:,.0f}"
            _vol_ratio = (_vol / _vol_avg - 1) * 100 if _vol_avg > 0 else None
            # Below-average volume isn't bad; use neutral muted color, only highlight
            # green when volume is meaningfully above average (>+10%).
            if _vol_ratio is None:
                _vol_sub = '<div class="stock-hero-stat-sub">&nbsp;</div>'
            else:
                _vol_color = "#059669" if _vol_ratio > 10 else "#94a3b8"
                _vol_sign  = "+" if _vol_ratio >= 0 else ""
                _vol_sub   = (f'<div class="stock-hero-stat-sub">'
                              f'<span style="color:{_vol_color}">{_vol_sign}{_vol_ratio:.0f}%</span>'
                              f' vs 20-day avg</div>')

            _prev_close = (live['prev'] if live and live.get('prev')
                           else float(df['Close'].iloc[-2]) if len(df) > 1 else _price_now)
            _period_cls = "pos" if period_ret > 0 else ("neg" if period_ret < 0 else "")

            # NOTE: HTML below is intentionally flush-left. Streamlit's markdown
            # parser treats 4+ space-indented lines as code blocks, which would
            # leak literal </div> text into the page. Do not re-indent.
            st.markdown(f"""<div class="stock-hero">
<div class="stock-hero-top">
<div class="stock-hero-id">
<div class="stock-hero-symbol">{ticker_input}</div>
<div class="stock-hero-name">{_company_name}</div>
<div class="stock-hero-tags">{''.join(_tags)}</div>
</div>
<div class="stock-hero-price-block">
<div class="stock-hero-price">${_price_now:,.2f}</div>
<span class="stock-hero-change {_change_cls}"><span class="stock-hero-change-arrow">{_change_arrow}</span>{_change_sign}{_change_abs:,.2f} ({_change_sign}{_change_pct:.2f}%)</span>
{_live_meta}
</div>
</div>
<div class="stock-hero-ribbon">
<div class="stock-hero-stat">
<div class="stock-hero-stat-lbl">Open</div>
<div class="stock-hero-stat-val">${_day_open:,.2f}</div>
<div class="stock-hero-stat-sub">Prev close ${_prev_close:,.2f}</div>
</div>
<div class="stock-hero-stat">
<div class="stock-hero-stat-lbl">Day Range</div>
<div class="range-bar"><div class="range-bar-marker" style="left:{_day_pct:.1f}%"></div></div>
<div class="range-bar-labels"><span>${_day_low:,.2f}</span><span>${_day_high:,.2f}</span></div>
</div>
<div class="stock-hero-stat">
<div class="stock-hero-stat-lbl">52-Week Range</div>
<div class="range-bar"><div class="range-bar-marker" style="left:{_w52_pct:.1f}%"></div></div>
<div class="range-bar-labels"><span>${_w52_low:,.2f}</span><span>${_w52_high:,.2f}</span></div>
</div>
<div class="stock-hero-stat">
<div class="stock-hero-stat-lbl">Volume</div>
<div class="stock-hero-stat-val">{_fmt_vol(_vol)}</div>
{_vol_sub}
</div>
<div class="stock-hero-stat">
<div class="stock-hero-stat-lbl">Period Return</div>
<div class="stock-hero-stat-val {_period_cls}">{period_ret:+.2f}%</div>
<div class="stock-hero-stat-sub">{period_label}</div>
</div>
</div>
</div>""", unsafe_allow_html=True)

            # ── Secondary Key Metrics row (4 aligned cards) ───────────────────
            # Asset-specific 4th metric
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
                earnings_date = cached_fetch_next_earnings(ticker_input, POLYGON_API_KEY)
                extra_label = "Last Earnings"
                extra_value = earnings_date[:10] if earnings_date and earnings_date != "N/A" else "N/A"

            # Use the full-period annualised volatility (same series feeding the
            # Sharpe ratio and the Monte Carlo summary) so return, volatility and
            # Sharpe all reconcile. Previously this card showed the trailing
            # 20-day vol, which silently disagreed with the Sharpe denominator.
            vol_val = ann_std
            _TOOLTIPS = {
                "Sharpe Ratio":    "Risk-adjusted return (excess of the risk-free rate). Above 1.0 is good, above 2.0 is excellent. Higher = better return per unit of risk.",
                "Sortino Ratio":   "Like Sharpe but only penalises downside volatility. Higher is better.",
                "Ann. Volatility": "Annualized standard deviation of daily returns over the period. Higher = more price swings. S&P 500 averages ~15%.",
            }
            _row_items = [
                ("Sharpe Ratio",    f"{sharpe:.2f}"  if pd.notna(sharpe)  else "N/A",
                                    pos_neg(sharpe)  if pd.notna(sharpe)  else "neutral"),
                ("Sortino Ratio",   f"{sortino:.2f}" if pd.notna(sortino) else "N/A",
                                    pos_neg(sortino) if pd.notna(sortino) else "neutral"),
                ("Ann. Volatility", f"{vol_val*100:.1f}%" if pd.notna(vol_val) else "N/A", "neutral"),
                (extra_label,       extra_value,                                            "neutral"),
            ]
            row_cols = st.columns(len(_row_items))
            for col, (label, value, cls) in zip(row_cols, _row_items):
                tip = _TOOLTIPS.get(label, "")
                tip_html = f'<span class="tooltip-wrap"> ⓘ<span class="tooltip-text">{tip}</span></span>' if tip else ""
                with col:
                    st.markdown(f"""
                    <div class="metric-card">
                        <div class="metric-label">{label}{tip_html}</div>
                        <div class="metric-value {cls}">{value}</div>
                    </div>""", unsafe_allow_html=True)

            # ── The Bottom Line (verdict up top) ──────────────────────────────
            # Surface the plain-English takeaway here instead of only at the very
            # bottom, so the app delivers on "tells you what to do" immediately.
            if summary_text:
                st.markdown(
                    f'<div style="background:linear-gradient(135deg,var(--brand-1) 0%,var(--brand-2) 100%);'
                    f'border:1px solid rgba(59,130,246,0.3);border-radius:12px;'
                    f'padding:1.2rem 1.5rem;margin:1.4rem 0 0.4rem;box-shadow:0 4px 16px rgba(15,23,42,0.09)">'
                    f'<div style="font-size:0.66rem;font-weight:700;letter-spacing:1.2px;'
                    f'text-transform:uppercase;color:#60a5fa;margin-bottom:0.5rem;'
                    f'display:flex;align-items:center;gap:0.4rem">'
                    f'<span class="material-symbols-outlined" style="font-size:1rem">lightbulb</span> The Bottom Line</div>'
                    f'<div style="color:#cbd5e1;font-size:0.9rem;line-height:1.75;'
                    f'font-family:var(--font-sans)">{summary_text}</div></div>',
                    unsafe_allow_html=True)

            # ── Valuation Lens — price vs. earnings-justified fair value ──────
            if not is_crypto and VALUATION_AVAILABLE:
                with st.spinner("Building the valuation view…"):
                    _vdata = _cached_valuation(ticker_input)
                if _vdata:
                    st.markdown(
                        '<div class="section-header">Valuation Lens '
                        '<span style="font-weight:500;color:#94a3b8;letter-spacing:0;'
                        'text-transform:none;font-size:0.7rem">· price vs. earnings-justified fair value</span></div>',
                        unsafe_allow_html=True)
                    # ── facts ──
                    _core      = _vdata.get("eps_core") or _vdata.get("eps") or [None]
                    _core_last = _core[-1] if _core else None
                    _fair_last = _core_last * _vdata["normal_pe"] if _core_last else None
                    _cur       = _vdata.get("current_price")
                    _bpe       = _vdata.get("blended_pe")
                    _npe       = _vdata["normal_pe"]
                    # NB: not `_disc` — that name is the `disclaimers` module alias
                    # (see the import block); rebinding it here is module-scope in a
                    # Streamlit script and broke the footer's _disc.DIVIDENDS.
                    _disc_pct  = ((_cur / _fair_last - 1) * 100) if (_fair_last and _cur) else None
                    _div_last  = next((v for v in reversed(_vdata.get("div") or []) if v), None)
                    _dyield    = (_div_last / _cur * 100) if (_div_last and _cur) else None
                    _epsyield  = (100.0 / _bpe) if _bpe else None
                    if _disc_pct is None:
                        _verd, _vcol, _vbg = "—", "#475569", "#f1f5f9"
                    elif _disc_pct > 15:
                        _verd, _vcol, _vbg = "Overvalued", "#b91c1c", "#fee2e2"
                    elif _disc_pct < -15:
                        _verd, _vcol, _vbg = "Undervalued", "#15803d", "#dcfce7"
                    else:
                        _verd, _vcol, _vbg = "Near fair value", "#475569", "#f1f5f9"
                    _mcap   = company_details.get("Market Cap")
                    _mcap_s = (f"${_mcap/1e12:.2f}T" if _mcap and _mcap >= 1e12 else
                               f"${_mcap/1e9:.1f}B"  if _mcap and _mcap >= 1e9  else
                               f"${_mcap/1e6:.0f}M"  if _mcap else "—")
                    _cur_s  = f"${_cur:,.2f}"      if _cur else "—"
                    _bpe_s  = f"{_bpe:g}x"         if _bpe else "—"
                    _eps_s  = f"{_epsyield:.1f}%"  if _epsyield else "—"
                    _dy_s   = f"{_dyield:.1f}%"    if _dyield else "—"
                    _fair_s = f"${_fair_last:,.2f}" if _fair_last else "—"
                    _sector = company_details.get("Sector") or "—"

                    _fcol, _mcol = st.columns([1, 3.2])
                    with _fcol:
                        st.markdown(f"""<div class="val-facts">
                          <div class="vf-group">Fast facts</div>
                          <div class="vf-row"><span>Current price</span><b>{_cur_s}</b></div>
                          <div class="vf-row"><span>Blended P/E</span><b>{_bpe_s}</b></div>
                          <div class="vf-row"><span>EPS yield</span><b>{_eps_s}</b></div>
                          <div class="vf-row"><span>Dividend yield</span><b>{_dy_s}</b></div>
                          <div class="vf-group">Valuation</div>
                          <div class="vf-row"><span>Normal P/E</span><b>{_npe:g}x</b></div>
                          <div class="vf-row"><span>Fair value</span><b style="color:#f28e1c">{_fair_s}</b></div>
                          <div class="vf-row"><span>Assessment</span><span class="vf-badge" style="color:{_vcol};background:{_vbg}">{_verd}</span></div>
                          <div class="vf-group">Company</div>
                          <div class="vf-row"><span>Sector</span><b>{_sector}</b></div>
                          <div class="vf-row"><span>Market cap</span><b>{_mcap_s}</b></div>
                        </div>""", unsafe_allow_html=True)
                    with _mcol:
                        _tab_v, _tab_e, _tab_d = st.tabs(["Valuation", "Earnings", "Dividends"])
                        with _tab_v:
                            _vfig = build_valuation_figure(_vdata)
                            if _vfig is not None:
                                st.plotly_chart(_vfig, use_container_width=True)
                        with _tab_e:
                            _efig = build_eps_figure(_vdata)
                            if _efig is not None:
                                st.plotly_chart(_efig, use_container_width=True)
                        with _tab_d:
                            _dfig = build_dividend_figure(_vdata)
                            if _dfig is not None:
                                st.plotly_chart(_dfig, use_container_width=True)
                            else:
                                st.caption("This company doesn't pay a dividend.")
                    st.caption(
                        "Fair value = core (3-yr median) EPS × the stock's own historical "
                        "normal P/E, from SEC-filed earnings. A valuation lens, not a price "
                        "target — always do your own research.")
                    st.markdown("---")

            # ── Analyst View (Finnhub: consensus + earnings surprises) ────────
            if not is_crypto:
                _adata = cached_get_analyst_data(ticker_input)
                _rec, _earn = _adata.get("recommendation"), _adata.get("earnings")
                if _rec or _earn:
                    st.markdown(
                        '<div class="section-header">Analyst View '
                        '<span style="font-weight:500;color:#94a3b8;letter-spacing:0;'
                        'text-transform:none;font-size:0.7rem">· Wall Street consensus via Finnhub</span></div>',
                        unsafe_allow_html=True)
                    _ac1, _ac2 = st.columns([3, 2])
                    with _ac1:
                        from market_data import consensus_from_recommendation
                        _cons = consensus_from_recommendation(_rec)
                        if _cons:
                            _sb, _bb, _hh = _cons["strong_buy"], _cons["buy"], _cons["hold"]
                            _sl, _ssl     = _cons["sell"], _cons["strong_sell"]
                            _tot          = _cons["total"]
                            _verdict, _vcol = _cons["verdict"], _cons["color"]
                            def _seg(_n, _c):
                                return f'<div style="width:{_n / _tot * 100:.1f}%;background:{_c}"></div>' if _n else ""
                            _bar = (_seg(_sb, "#059669") + _seg(_bb, "#34d399") + _seg(_hh, "#cbd5e1")
                                    + _seg(_sl, "#f59e0b") + _seg(_ssl, "#dc2626"))
                            st.markdown(
                                f'<div style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;'
                                f'padding:1.1rem 1.25rem">'
                                f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
                                f'margin-bottom:0.6rem"><span style="font-size:1.35rem;font-weight:700;'
                                f'color:{_vcol}">{_verdict}</span>'
                                f'<span style="font-size:0.78rem;color:#64748b">{_tot} analysts · {(_rec or {}).get("period","")[:7]}</span></div>'
                                f'<div style="display:flex;height:9px;border-radius:5px;overflow:hidden;'
                                f'background:#f1f5f9">{_bar}</div>'
                                f'<div style="display:flex;gap:1rem;margin-top:0.6rem;font-size:0.72rem;flex-wrap:wrap">'
                                f'<span style="color:#059669">● {_sb + _bb} Buy</span>'
                                f'<span style="color:#94a3b8">● {_hh} Hold</span>'
                                f'<span style="color:#dc2626">● {_sl + _ssl} Sell</span></div></div>',
                                unsafe_allow_html=True)
                    with _ac2:
                        if _earn:
                            _rows = ""
                            for _e in _earn:
                                _a, _est = _e.get("actual"), _e.get("estimate")
                                if _a is None or _est is None:
                                    continue
                                _beat = _a >= _est
                                _col  = "#16a34a" if _beat else "#dc2626"
                                _sp   = _e.get("surprisePercent")
                                _sps  = f"{_sp:+.1f}%" if isinstance(_sp, (int, float)) else ""
                                _rows += (f'<div style="display:flex;justify-content:space-between;'
                                          f'padding:0.3rem 0;border-bottom:1px solid #f1f5f9;font-size:0.76rem">'
                                          f'<span style="color:#64748b">{str(_e.get("period",""))[:7]}</span>'
                                          f'<span style="font-family:\'JetBrains Mono\',monospace;color:#0f172a">'
                                          f'${_a} vs ${_est}</span>'
                                          f'<span style="color:{_col};font-weight:600">{_sps}</span></div>')
                            if _rows:
                                st.markdown(
                                    f'<div style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;'
                                    f'padding:0.7rem 1.1rem"><div style="font-size:0.64rem;font-weight:700;'
                                    f'letter-spacing:0.5px;text-transform:uppercase;color:#64748b;'
                                    f'margin-bottom:0.3rem">Earnings vs Estimate (EPS)</div>{_rows}</div>',
                                    unsafe_allow_html=True)
                    st.markdown("<div style='height:0.7rem'></div>", unsafe_allow_html=True)

            # ── Fundamentals & Valuation (stocks only) ────────────────────────
            # SEC-sourced statements via Polygon /vX/reference/financials, turned
            # into margins/returns/leverage/growth/valuation + a reverse-DCF lens.
            if not is_crypto:
                # SEC EDGAR is the primary source (10+ yrs, free, authoritative);
                # fall back to Polygon's 4-period financials for filers EDGAR
                # doesn't cover (some foreign/ADR names).
                _fin_raw = cached_fetch_sec_financials(ticker_input)
                if not _fin_raw:
                    _fin_raw = cached_fetch_financials(ticker_input, POLYGON_API_KEY)
                fund = compute_fundamentals(
                    _fin_raw, market_cap=company_details.get("Market Cap"),
                    price=float(df["Close"].iloc[-1]),
                )
                if fund.get("ok"):
                    def _fv(v, suffix="", na="N/A"):
                        return f"{v}{suffix}" if v is not None else na

                    _n_yrs = len(_fin_raw["income_statement"]) if isinstance(_fin_raw, dict) and _fin_raw.get("income_statement") is not None else 0
                    _hist  = f"{_n_yrs}-yr history · " if _n_yrs > 1 else ""
                    st.markdown(
                        f'<div class="section-header">Fundamentals &amp; Valuation '
                        f'<span style="font-weight:500;color:#94a3b8;letter-spacing:0;'
                        f'text-transform:none;font-size:0.7rem">· {_hist}FY ending {fund["as_of"]} '
                        f'· {fund.get("source", "Polygon")}</span></div>',
                        unsafe_allow_html=True)

                    _v, _m, _r, _l, _g = (fund["valuation"], fund["margins"],
                                          fund["returns"], fund["leverage"], fund["growth"])
                    _q, _fc = fund["quality"], fund["fcf"]
                    _fs, _z, _zone, _fy = _q["f_score"], _q["z_score"], _q["z_zone"], _fc["fcf_yield"]
                    _fs_cls = ("pos" if (_fs is not None and _fs >= 7)
                               else "neg" if (_fs is not None and _fs <= 3) else "")
                    _z_cls  = {"safe": "pos", "distress": "neg"}.get(_zone, "")
                    _qtips = {
                        "Piotroski F-Score": "9-point test of fundamental strength (profitability, leverage, efficiency vs last year). 8-9 strong, 0-2 weak.",
                        "Altman Z-Score": "Bankruptcy-risk score. Above 2.99 = safe, 1.81-2.99 grey, below 1.81 = distress. Not meaningful for banks.",
                        "FCF Yield": "Free cash flow (operating cash flow − capex) ÷ market cap. Higher = more cash per dollar of value.",
                        "EV / EBITDA": "Enterprise value ÷ EBITDA — a capital-structure-neutral valuation multiple.",
                    }

                    def _mv(v, suffix="", na="—"):
                        return f"{v}{suffix}" if v is not None else na
                    def _dir(x):
                        return "" if x is None else ("pos" if x >= 0 else "neg")
                    def _pos0(x):
                        return "pos" if (x or 0) > 0 else ("neg" if (x is not None and x < 0) else "")

                    # Ordered by the question each metric answers — how expensive,
                    # how profitable, how fast-growing, how financially sound.
                    _fund_groups = [
                        ("Valuation", [
                            ("P/E",            _mv(_v["pe"], "×"),   "", ""),
                            ("P/S",            _mv(_v["ps"], "×"),   "", ""),
                            ("P/B",            _mv(_v["pb"], "×"),   "", ""),
                            ("EV / EBITDA",    _mv(fund["ev_ebitda"], "×"), "", _qtips["EV / EBITDA"]),
                            ("Earnings Yield", _mv(_v["earnings_yield"], "%"), _pos0(_v["earnings_yield"]), ""),
                            ("FCF Yield",      _mv(_fy, "%"), _pos0(_fy), _qtips["FCF Yield"]),
                        ]),
                        ("Profitability", [
                            ("Gross Margin",     _mv(_m["gross"], "%"),     "pos" if _m["gross"] else "", ""),
                            ("Operating Margin", _mv(_m["operating"], "%"), "pos" if _m["operating"] else "", ""),
                            ("Net Margin",       _mv(_m["net"], "%"),       "pos" if _m["net"] else "", ""),
                            ("Return on Equity", _mv(_r["roe"], "%"),       "pos" if _r["roe"] else "", ""),
                        ]),
                        ("Growth", [
                            ("Revenue Growth (YoY)", _mv(_g["revenue_yoy"], "%"), _dir(_g["revenue_yoy"]), ""),
                            ("EPS Growth (YoY)",     _mv(_g["eps_yoy"], "%"),     _dir(_g["eps_yoy"]), ""),
                        ]),
                        ("Financial Health", [
                            ("Current Ratio", _mv(_l["current_ratio"]), "", ""),
                            ("Debt / Equity", _mv(_l["debt_to_equity"]), "", ""),
                            ("Piotroski F-Score", f"{_fs} / 9" if _fs is not None else "—", _fs_cls, _qtips["Piotroski F-Score"]),
                            ("Altman Z-Score", f"{_z} · {_zone.title()}" if _z is not None else "—", _z_cls, _qtips["Altman Z-Score"]),
                        ]),
                    ]
                    _rows = ['<table class="fund-table">']
                    for _cat, _metrics in _fund_groups:
                        _rows.append(f'<tr class="grp"><td colspan="4">{_cat}</td></tr>')
                        for _i in range(0, len(_metrics), 2):
                            _cells = ""
                            for _j in range(2):
                                if _i + _j < len(_metrics):
                                    _lbl, _val, _cls, _tip = _metrics[_i + _j]
                                    _th = (f'<span class="tooltip-wrap"> ⓘ<span class="tooltip-text">{_tip}</span></span>'
                                           if _tip else "")
                                    _kcls = "k pair2" if _j == 1 else "k"
                                    _cells += f'<td class="{_kcls}">{_lbl}{_th}</td><td class="v {_cls}">{_val}</td>'
                                else:
                                    _cells += '<td class="k"></td><td class="v"></td>'
                            _rows.append(f'<tr>{_cells}</tr>')
                    _rows.append('</table>')
                    st.markdown("".join(_rows), unsafe_allow_html=True)

                    # ── Fundamentals vs Peers ─────────────────────────────────
                    # Context for the metrics above: how this name stacks up against
                    # the entered peers on valuation, profitability and quality —
                    # same EDGAR-sourced compute_fundamentals, one row each.
                    if peers_list and not is_crypto:
                        def _peer_fund_row(_tk, _fd):
                            if not _fd or not _fd.get("ok"):
                                return None
                            return {
                                "Ticker":       _tk,
                                "P/E":          _fd["valuation"]["pe"],
                                "P/S":          _fd["valuation"]["ps"],
                                "Net Margin %": _fd["margins"]["net"],
                                "ROE %":        _fd["returns"]["roe"],
                                "Rev Grow %":   _fd["growth"]["revenue_yoy"],
                                "F-Score /9":   _fd["quality"]["f_score"],
                                "Z-Score":      _fd["quality"]["z_score"],
                            }
                        _frows = []
                        _mrow = _peer_fund_row(ticker_input, fund)
                        if _mrow:
                            _frows.append(_mrow)
                        for _pt in peers_list[:4]:
                            try:
                                _pfin = cached_fetch_sec_financials(_pt)
                                if not _pfin:
                                    continue
                                _pmc  = (cached_fetch_company_details(_pt, POLYGON_API_KEY) or {}).get("Market Cap")
                                _row  = _peer_fund_row(_pt, compute_fundamentals(_pfin, market_cap=_pmc))
                                if _row:
                                    _frows.append(_row)
                            except Exception:
                                pass
                        if len(_frows) > 1:
                            st.markdown('<div class="section-header">Fundamentals vs Peers</div>',
                                        unsafe_allow_html=True)
                            st.dataframe(pd.DataFrame(_frows).set_index("Ticker"),
                                         use_container_width=True)
                            st.markdown(
                                "<div style='font-size:0.72rem;color:#94a3b8;margin-top:0.25rem'>"
                                "Blanks mean a metric wasn't available for that peer (e.g. no market cap "
                                "for valuation multiples, or banks for margins). F-Score 8–9 = strong; "
                                "Z-Score &gt; 2.99 = safe zone.</div>",
                                unsafe_allow_html=True)

                    # Revenue & net income trend with operating margin
                    _t = fund["trend"]
                    if any(x is not None for x in _t["revenue"]):
                        fig_fund = go.Figure()
                        fig_fund.add_trace(go.Bar(
                            x=_t["periods"], y=[(x or 0) / 1e9 for x in _t["revenue"]],
                            name="Revenue ($B)", marker_color="#1d4ed8", opacity=0.85))
                        fig_fund.add_trace(go.Bar(
                            x=_t["periods"], y=[(x or 0) / 1e9 for x in _t["net_income"]],
                            name="Net Income ($B)", marker_color="#93c5fd", opacity=0.9))
                        fig_fund.add_trace(go.Scatter(
                            x=_t["periods"], y=_t["operating_margin"], name="Operating Margin (%)",
                            yaxis="y2", line=dict(color="#059669", width=2.5), mode="lines+markers"))
                        fig_fund.update_layout(
                            barmode="group", height=330, template=None,
                            plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                            margin=dict(l=55, r=60, t=58, b=40),
                            title=dict(text="Revenue, Net Income & Operating Margin",
                                       font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left",
                                       y=0.97, yanchor="top"),
                            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                                        font=dict(size=11, family="DM Sans", color="#374151")),
                            font=dict(family="DM Sans, system-ui, sans-serif"),
                            hovermode="x unified",
                            hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                            font=dict(color="white", size=12, family="DM Sans")),
                            xaxis=dict(tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                       gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0"),
                            yaxis=dict(title="$ Billions", gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0",
                                       tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                       title_font=dict(size=12, color="#64748b", family="DM Sans")),
                            yaxis2=dict(title="Op. Margin %", overlaying="y", side="right", showgrid=False,
                                        tickfont=dict(size=11, color="#059669", family="DM Sans"),
                                        title_font=dict(size=12, color="#059669", family="DM Sans")),
                        )
                        st.plotly_chart(fig_fund, use_container_width=True)

                    # Free cash flow trend (EDGAR-powered)
                    _tf = _t.get("fcf")
                    if _tf and any(x is not None for x in _tf):
                        fig_fcf = go.Figure()
                        fig_fcf.add_trace(go.Bar(
                            x=_t["periods"], y=[(x or 0) / 1e9 for x in _tf],
                            marker_color=["#059669" if (x or 0) >= 0 else "#dc2626" for x in _tf],
                            name="Free Cash Flow ($B)",
                            hovertemplate="%{x}: $%{y:.1f}B<extra>Free Cash Flow</extra>"))
                        fig_fcf.update_layout(
                            height=240, template=None,
                            plot_bgcolor="#f8fafc", paper_bgcolor="rgba(0,0,0,0)",
                            margin=dict(l=55, r=20, t=42, b=40), showlegend=False,
                            title=dict(text="Free Cash Flow",
                                       font=dict(size=13, color="#0f172a", family="DM Sans"), x=0, xanchor="left"),
                            font=dict(family="DM Sans, system-ui, sans-serif"), hovermode="x unified",
                            hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                            font=dict(color="white", size=12, family="DM Sans")),
                            xaxis=dict(tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                       gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0"),
                            yaxis=dict(title="$ Billions", gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0",
                                       zeroline=True, zerolinecolor="#cbd5e1",
                                       tickfont=dict(size=11, color="#64748b", family="DM Sans"),
                                       title_font=dict(size=12, color="#64748b", family="DM Sans")),
                        )
                        st.plotly_chart(fig_fcf, use_container_width=True)

                    # Reverse-DCF valuation lens — states what growth is priced in
                    _ig, _hist = fund["implied_growth"], _g["eps_cagr"]
                    if _ig is not None:
                        _igp = _ig * 100
                        if _hist is not None and _igp > _hist + 3:
                            _msg = (f"The market is pricing in <b>{_igp:.1f}%</b> annual earnings growth for 10 years — "
                                    f"well above the <b>{_hist:.1f}%</b> the company actually delivered. "
                                    f"The valuation assumes growth accelerates.")
                            _vc, _vbg, _vbd = "#b45309", "#fffbeb", "#fde68a"
                        elif _hist is not None and _igp < _hist - 3:
                            _msg = (f"The market is pricing in <b>{_igp:.1f}%</b> annual earnings growth for 10 years — "
                                    f"below the <b>{_hist:.1f}%</b> historical rate. Expectations look conservative.")
                            _vc, _vbg, _vbd = "#047857", "#ecfdf5", "#a7f3d0"
                        elif _hist is not None:
                            _msg = (f"The market is pricing in <b>{_igp:.1f}%</b> annual earnings growth for 10 years — "
                                    f"roughly in line with the <b>{_hist:.1f}%</b> historical rate.")
                            _vc, _vbg, _vbd = "#1d4ed8", "#eff6ff", "#bfdbfe"
                        else:
                            _msg = (f"The market is pricing in <b>{_igp:.1f}%</b> annual earnings growth for 10 years "
                                    f"(reverse-DCF · 9% discount · 2.5% terminal).")
                            _vc, _vbg, _vbd = "#1d4ed8", "#eff6ff", "#bfdbfe"
                        st.markdown(
                            f'<div style="background:{_vbg};border:1px solid {_vbd};border-left:4px solid {_vc};'
                            f'border-radius:10px;padding:1rem 1.25rem;margin-top:0.5rem;font-size:0.85rem;'
                            f'color:#334155;line-height:1.6"><span style="font-weight:700;color:{_vc};'
                            f'text-transform:uppercase;font-size:0.68rem;letter-spacing:0.5px">'
                            f"Reverse-DCF · What's Priced In</span><br>{_msg}</div>",
                            unsafe_allow_html=True)

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

            # ── Chart customization controls ──────────────────────────────────
            # Wider columns + shorter labels so nothing wraps awkwardly. The
            # main "Price Chart" heading is removed since the controls speak
            # for themselves.
            _ctrl1, _ctrl2, _ctrl3, _ctrl4, _ctrl5, _ctrl6, _ctrl7 = \
                st.columns([1.6, 0.7, 0.7, 0.8, 0.8, 0.6, 0.9])
            with _ctrl1:
                _chart_type = st.selectbox("Type", ["Area", "Line", "Candlestick"],
                                           index=0, key="main_chart_type")
            with _ctrl2:
                _show_ma20  = st.checkbox("MA 20",  value=False, key="main_show_ma20")
            with _ctrl3:
                _show_ma50  = st.checkbox("MA 50",  value=True,  key="main_show_ma50")
            with _ctrl4:
                _show_ma200 = st.checkbox("MA 200", value=True,  key="main_show_ma200")
            with _ctrl5:
                _show_vol   = st.checkbox("Volume", value=False, key="main_show_volume")
            with _ctrl6:
                # Support/Resistance is an opt-in overlay now (off by default) —
                # it cluttered the default chart and most investors don't use it.
                _show_sr    = st.checkbox("S/R",    value=False, key="main_show_sr")
            with _ctrl7:
                _show_tag   = st.checkbox("Marker", value=True, key="main_show_tag")

            fig = go.Figure()

            # Compact y-axis padding so the chart breathes
            _close_min = float(df["Close"].min())

            # ── Price — line / area / candle depending on selection ───────────
            if _chart_type == "Candlestick" and {"Open","High","Low","Close"}.issubset(df.columns):
                fig.add_trace(go.Candlestick(
                    x=df["Date"], open=df["Open"], high=df["High"],
                    low=df["Low"], close=df["Close"],
                    name="Price",
                    increasing_line_color="#059669", decreasing_line_color="#dc2626",
                    increasing_fillcolor="#059669", decreasing_fillcolor="#dc2626",
                ))
            elif _chart_type == "Line":
                fig.add_trace(go.Scatter(
                    x=df["Date"], y=df["Close"],
                    name="Price",
                    line=dict(color="#1d4ed8", width=2.5),
                    hovertemplate="$%{y:,.2f}<extra>Price</extra>",
                ))
            else:  # Area (default)
                # Invisible base trace at min price — used for "tonexty" fill
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

            # ── Moving averages — gated by checkboxes ─────────────────────────
            _ma_cfg = [
                (20,  "#f59e0b", 1.0,  "dot",      "MA 20",  _show_ma20),
                (50,  "#8b5cf6", 1.2,  "dash",     "MA 50",  _show_ma50),
                (200, "#f97316", 1.5,  "longdash", "MA 200", _show_ma200),
            ]
            for ma, color, width, dash, label, enabled in _ma_cfg:
                if enabled and f"MA{ma}" in df.columns:
                    fig.add_trace(go.Scatter(
                        x=df["Date"], y=df[f"MA{ma}"],
                        name=label,
                        line=dict(color=color, width=width, dash=dash),
                        opacity=0.9,
                        hovertemplate=f"$%{{y:,.2f}}<extra>MA {ma}</extra>",
                    ))

            # ── Volume bars on secondary axis (optional) ──────────────────────
            if _show_vol and "Volume" in df.columns:
                _vol_colors = ["#059669" if c >= o else "#dc2626"
                               for c, o in zip(df["Close"], df["Open"])] \
                              if "Open" in df.columns else "#94a3b8"
                fig.add_trace(go.Bar(
                    x=df["Date"], y=df["Volume"],
                    name="Volume", marker_color=_vol_colors,
                    opacity=0.35, yaxis="y2",
                    hovertemplate="%{y:,.0f}<extra>Volume</extra>",
                ))

            # ── S/R lines — clamped to visible y-range so labels don't float ─
            _y_min = float(df["Close"].min())
            _y_max = float(df["Close"].max())
            _y_pad = (_y_max - _y_min) * 0.05
            _y_floor = _y_min - _y_pad
            _y_ceil  = _y_max + _y_pad

            if _show_sr and resistance:
                # Only show resistance levels INSIDE chart range and above current price
                _res_above = sorted(
                    [r for r in resistance if _y_min < r < _y_ceil],
                    reverse=True,
                )[:2]
                for _i, r in enumerate(_res_above):
                    fig.add_shape(type="line", x0=0, x1=1, xref="paper",
                                  y0=r, y1=r,
                                  line=dict(color="#ef4444", width=1, dash="dash"),
                                  opacity=0.4, layer="below")
                    fig.add_annotation(
                        x=1.005, xref="paper", y=r, yref="y",
                        text=f"Resist ${r:,.0f}",
                        showarrow=False, xanchor="left",
                        font=dict(color="#ef4444", size=10, family="DM Sans"),
                        bgcolor="rgba(255,255,255,0.9)",
                        bordercolor="#fecaca", borderwidth=1,
                        borderpad=3,
                    )

            if _show_sr and support:
                _sup_below = sorted(
                    [s for s in support if _y_floor < s < _y_max]
                )[:2]
                for _i, s in enumerate(_sup_below):
                    fig.add_shape(type="line", x0=0, x1=1, xref="paper",
                                  y0=s, y1=s,
                                  line=dict(color="#16a34a", width=1, dash="dash"),
                                  opacity=0.4, layer="below")
                    fig.add_annotation(
                        x=1.005, xref="paper", y=s, yref="y",
                        text=f"Support ${s:,.0f}",
                        showarrow=False, xanchor="left",
                        font=dict(color="#16a34a", size=10, family="DM Sans"),
                        bgcolor="rgba(255,255,255,0.9)",
                        bordercolor="#bbf7d0", borderwidth=1,
                        borderpad=3,
                    )

            # ── Current price tag ─────────────────────────────────────────────
            if _show_tag:
                _last = df["Close"].iloc[-1]
                fig.add_shape(type="line", x0=0, x1=1, xref="paper",
                              y0=_last, y1=_last,
                              line=dict(color="#94a3b8", width=1, dash="dot"),
                              opacity=0.7, layer="above")
                fig.add_annotation(
                    x=1.005, xref="paper", y=_last, yref="y",
                    text=f"<b>${_last:,.2f}</b>",
                    showarrow=False, xanchor="left",
                    font=dict(color="white", size=11, family="DM Sans"),
                    bgcolor="#2563eb",
                    borderpad=4,
                )

            fig.update_layout(
                height=480, template=None,
                plot_bgcolor="#ffffff", paper_bgcolor="rgba(0,0,0,0)",
                # Right margin holds both the y-axis labels and the S/R + price
                # tags (anchored just past the plot); keep it wide enough that the
                # widest "Resist $000" / price box isn't clipped by the card edge.
                margin=dict(l=10, r=124, t=70, b=30),
                hovermode="x unified",
                font=dict(family="DM Sans, system-ui, sans-serif"),
                hoverlabel=dict(
                    bgcolor="#0f172a", bordercolor="#334155",
                    font=dict(color="white", size=12, family="DM Sans"),
                    namelength=-1,
                ),
                # Legend: horizontal strip ABOVE the plot area, transparent
                legend=dict(
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0,
                    font=dict(size=11, family="DM Sans", color="#64748b"),
                    bgcolor="rgba(0,0,0,0)", bordercolor="rgba(0,0,0,0)",
                    itemsizing="constant",
                ),
                xaxis=dict(
                    title=None,
                    type="date",
                    tickformat="%b '%y",
                    tickfont=dict(size=11, color="#94a3b8", family="DM Sans"),
                    gridcolor="#e2e8f0",
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
                        x=0.0, xanchor="left", y=1.02, yanchor="bottom",
                    ),
                ),
                yaxis=dict(
                    title=None,
                    side="right",
                    tickformat="$,.2f",
                    tickfont=dict(size=11, color="#94a3b8", family="DM Sans"),
                    gridcolor="#e2e8f0",
                    showline=False,
                    zeroline=False,
                    autorange=True,
                    rangemode="normal",
                    nticks=6,
                ),
                xaxis_rangeslider_visible=False,
            )
            if _show_vol and "Volume" in df.columns:
                fig.update_layout(yaxis2=dict(
                    title=None, overlaying="y", side="left",
                    showgrid=False, showticklabels=False,
                    range=[0, float(df["Volume"].max() * 5)],
                ))
            st.plotly_chart(fig, use_container_width=True, config={
                "displaylogo": False,
                "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
            })

            # ── One switchable indicator below the price chart ────────────────
            # Replaces the old always-on stack of RSI + Bollinger (+ MACD) charts
            # with a single view the user flips between — less scrolling, less
            # clutter. "None" hides it entirely.
            _ind_opts = ["None"]
            if "RSI14" in df.columns:    _ind_opts.append("RSI")
            if "MACD" in df.columns:     _ind_opts.append("MACD")
            if "BB_Upper" in df.columns: _ind_opts.append("Bollinger")
            _ind_view = "None"
            if len(_ind_opts) > 1:
                st.markdown('<div class="field-label" style="margin-top:0.5rem">Indicator</div>',
                            unsafe_allow_html=True)
                _ind_view = st.radio("Indicator", _ind_opts, horizontal=True,
                                     key="tech_indicator", label_visibility="collapsed")

            if _ind_view == "RSI" and "RSI14" in df.columns:
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
                # Anchor zone labels INSIDE the plot at the left edge so they
                # don't float in the right margin.
                fig_rsi.add_annotation(
                    xref="paper", x=0.005, y=85, text="Overbought",
                    showarrow=False, xanchor="left",
                    font=dict(size=10, color="#ef4444", family="DM Sans"),
                )
                fig_rsi.add_annotation(
                    xref="paper", x=0.005, y=15, text="Oversold",
                    showarrow=False, xanchor="left",
                    font=dict(size=10, color="#16a34a", family="DM Sans"),
                )
                fig_rsi.update_layout(
                    height=200, template=None,
                    plot_bgcolor="#ffffff", paper_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=10, r=95, t=20, b=30),
                    hovermode="x unified",
                    showlegend=False,
                    font=dict(family="DM Sans, system-ui, sans-serif"),
                    hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                    font=dict(color="white", size=12, family="DM Sans")),
                    xaxis=dict(
                        type="date", tickformat="%b '%y", title=None,
                        tickfont=dict(size=11, color="#94a3b8", family="DM Sans"),
                        gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0",
                        zeroline=False,
                    ),
                    yaxis=dict(
                        range=[0, 100], side="right",
                        tickvals=[30, 50, 70],
                        title=None,
                        tickfont=dict(size=11, color="#94a3b8", family="DM Sans"),
                        gridcolor="#e2e8f0", showline=False,
                        zeroline=False,
                    ),
                )
                st.plotly_chart(fig_rsi, use_container_width=True)

            if _ind_view == "Bollinger" and "BB_Upper" in df.columns:
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
                    margin=dict(l=10, r=95, t=40, b=30),
                    hovermode="x unified",
                    font=dict(family="DM Sans, system-ui, sans-serif"),
                    hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                    font=dict(color="white", size=12, family="DM Sans")),
                    # Legend OUTSIDE the plot — top-right strip, transparent
                    legend=dict(
                        orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0,
                        font=dict(size=11, family="DM Sans", color="#64748b"),
                        bgcolor="rgba(0,0,0,0)", bordercolor="rgba(0,0,0,0)",
                    ),
                    xaxis=dict(
                        type="date", tickformat="%b '%y", title=None,
                        tickfont=dict(size=11, color="#94a3b8", family="DM Sans"),
                        gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0",
                        zeroline=False,
                    ),
                    yaxis=dict(
                        tickformat="$,.2f",
                        title=None, side="right",
                        tickfont=dict(size=11, color="#94a3b8", family="DM Sans"),
                        gridcolor="#e2e8f0", showline=False,
                        zeroline=False, autorange=True, rangemode="normal",
                    ),
                )
                st.plotly_chart(fig_bb, use_container_width=True)

            if _ind_view == "MACD" and "MACD" in df.columns:
                st.markdown('<div class="section-header">MACD</div>', unsafe_allow_html=True)
                fig_macd = go.Figure()
                fig_macd.add_trace(go.Scatter(
                    x=df["Date"], y=df["MACD"], name="MACD",
                    line=dict(color="#4a9eff", width=1.5),
                    hovertemplate="MACD: %{y:.3f}<extra></extra>"))
                fig_macd.add_trace(go.Scatter(
                    x=df["Date"], y=df["MACD_Signal"], name="Signal",
                    line=dict(color="#1d4ed8", width=1.5),
                    hovertemplate="Signal: %{y:.3f}<extra></extra>"))
                _hist_colors = ["#059669" if (v or 0) >= 0 else "#dc2626"
                                for v in df["MACD_Hist"].fillna(0)]
                fig_macd.add_trace(go.Bar(
                    x=df["Date"], y=df["MACD_Hist"], name="Histogram",
                    marker_color=_hist_colors, opacity=0.5,
                    hovertemplate="Hist: %{y:.3f}<extra></extra>"))
                fig_macd.update_layout(
                    height=240, template=None,
                    plot_bgcolor="#ffffff", paper_bgcolor="rgba(0,0,0,0)",
                    margin=dict(l=10, r=95, t=20, b=30),
                    hovermode="x unified",
                    font=dict(family="DM Sans, system-ui, sans-serif"),
                    hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155",
                                    font=dict(color="white", size=12, family="DM Sans")),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                xanchor="right", x=1.0,
                                font=dict(size=11, family="DM Sans", color="#64748b"),
                                bgcolor="rgba(0,0,0,0)"),
                    xaxis=dict(type="date", tickformat="%b '%y", title=None,
                               tickfont=dict(size=11, color="#94a3b8", family="DM Sans"),
                               gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0",
                               zeroline=False),
                    yaxis=dict(title=None, side="right",
                               tickfont=dict(size=11, color="#94a3b8", family="DM Sans"),
                               gridcolor="#e2e8f0", showline=False,
                               zeroline=True, zerolinecolor="#e2e8f0"),
                )
                st.plotly_chart(fig_macd, use_container_width=True)

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
                        # r wide enough that the right-side "$000" y labels aren't
                        # clipped by the chart card's edge.
                        margin=dict(l=10, r=78, t=30, b=40),
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
                            gridcolor="#e2e8f0", showline=True, linecolor="#e2e8f0",
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
                            gridcolor="#e2e8f0", showline=False,
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
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
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

            if not is_crypto:
                st.markdown('<div class="section-header">News &amp; Research '
                            '<span style="font-weight:500;color:#94a3b8;letter-spacing:0;'
                            'text-transform:none;font-size:0.7rem">· multi-source, theme-tagged'
                            ', AI-briefed</span></div>', unsafe_allow_html=True)
                _render_stock_news(ticker_input, company_details.get("Name"))
            elif news_list:
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
                    font=dict(color="#0f172a", family="DM Sans"),
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
                            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
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
                    _rfr = get_risk_free_rate()
                    for _pt, _pdf in peer_price_dfs.items():
                        if _pdf.empty or "Daily_Return" not in _pdf.columns:
                            continue
                        _ret = _pdf["Daily_Return"].dropna()
                        if len(_ret) < 5:
                            continue
                        # Match the headline metric definitions: arithmetic
                        # annualised return and excess-return Sharpe (minus the
                        # risk-free rate) so a stock's peer-chart Sharpe equals
                        # the value shown in its metric card.
                        _ann_ret = _ret.mean() * 252
                        _ann_vol = _ret.std() * np.sqrt(252)
                        _sharpe  = ((_ann_ret - _rfr) / _ann_vol) if _ann_vol > 0 else 0
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
                                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
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

            # (The plain-English summary is now surfaced as "The Bottom Line" up
            # top, right under the key metrics — no need to repeat it here.)
            st.markdown("---")
            _stock_exports("bottom")

        st.markdown(render_section("Data & Methodology", _disc.DIVIDENDS), unsafe_allow_html=True)
        st.markdown(render_inline(_disc.SHORT), unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# NEWS — market-wide pulse + per-ticker research
# ═════════════════════════════════════════════════════════════════════════════
elif _page == "news":
    st.markdown('<div class="section-header">Market News '
                '<span style="font-weight:500;color:#94a3b8;letter-spacing:0;'
                'text-transform:none;font-size:0.7rem">· across the market, '
                'theme-tagged</span></div>', unsafe_allow_html=True)

    import html as _html_mod
    _pulse   = _cached_market_pulse()
    _m_arts  = _pulse.get("articles") or []
    _trend   = _pulse.get("trending") or []

    if not _m_arts:
        st.info("Market news is unavailable right now — the news provider did not "
                "return any stories. Per-ticker research below still works.")
    else:
        if _trend:
            st.caption("Most-mentioned tickers in recent market coverage")
            st.markdown(
                '<div class="news-chips">' +
                "".join(f'<span class="news-chip">{_html_mod.escape(str(_tk))} · {_n}</span>'
                        for _tk, _n in _trend) +
                '</div>', unsafe_allow_html=True)
        st.markdown(_news_feed_html(_m_arts, 20), unsafe_allow_html=True)

    st.markdown('<div class="section-header">Research a ticker</div>',
                unsafe_allow_html=True)
    _nt = st.text_input("Ticker", key="news_ticker",
                        placeholder="e.g. AAPL, MSFT, NVDA",
                        label_visibility="collapsed")
    if _nt and _nt.strip():
        _render_stock_news(_nt.strip().upper())
    else:
        st.caption("Enter a ticker to see its news tone, catalysts and sourced brief.")

    # No news-specific disclaimer exists in `disclaimers`; the dividends one would
    # be plainly wrong here, so use the short general disclaimer only.
    st.markdown(render_inline(_disc.SHORT), unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# PORTFOLIO BUILDER
# ═════════════════════════════════════════════════════════════════════════════
elif _page == "builder":
    render_portfolio_builder(POLYGON_API_KEY, is_pro=st.session_state.get("is_pro", False))

# =============================================================================
# YOUR PORTFOLIOS (forward-tracked, mark-to-market)
# =============================================================================
elif _page == "portfolios":
    render_your_portfolios(POLYGON_API_KEY, is_pro=st.session_state.get("is_pro", False))
