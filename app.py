import os
import re
import sys
import csv
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import time

# One chart style for the whole app. Every figure below routes its layout
# through ct.style() — see chart_theme.py for why that replaced inline styling.
import chart_theme as ct

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
    cached_run_monte_carlo,
    cached_detect_support_resistance, cached_build_correlation_matrix,
    cached_get_analyst_data,
)
from portfolio_data import BOND_UNIVERSE, BOND_DURATION_MAP, suggest_peers
from analysis import (
    detect_support_resistance, build_correlation_matrix,
    run_monte_carlo, generate_summary_paragraph,
    compute_fundamentals, dcf_valuation, market_beta
)
# Report builders are imported at the point of use, not here.
# Between them they pull in matplotlib, openpyxl, python-docx and python-pptx
# — about 100 MB of resident memory that every visitor paid for even though
# only the ones who click Export ever need it. Python never releases an
# imported module, so an eager import is a permanent tax on the whole process.
from importlib.util import find_spec
PPTX_AVAILABLE = find_spec("pptx") is not None   # cheap: does not import pptx
DOCX_AVAILABLE = find_spec("docx") is not None   # cheap: does not import docx
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


@st.cache_data(ttl=900, show_spinner=False)
def _cached_report_news(ticker, company):
    """Report-shaped rows from the same multi-source pipeline the page uses."""
    try:
        from news_research import report_news_rows
        return report_news_rows(ticker, POLYGON_API_KEY, company_name=company or None)
    except Exception:
        return []


@st.cache_data(ttl=900, show_spinner=False)
def _cached_dense_bars(ticker, start, end, interval):
    """Intraday bars for DRAWING the price chart. Never for computing anything.

    Cached 15 minutes: intraday rolls constantly, so a long TTL would draw a
    stale last hour beside a live quote. Returns None on any failure, and the
    caller falls back to the daily series it already has - this is a live
    provider call that cannot be served from the Supabase price cache (that
    stores daily bars), and the provider throttles the web host, so failing
    to a slightly chunkier chart is the correct degradation.
    """
    try:
        from market_data import get_bars
        df = get_bars(ticker, start, end, interval=interval,
                      polygon_key=POLYGON_API_KEY)
        return df if df is not None and len(df) > 0 else None
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_sec_filings(ticker):
    """EDGAR's filing index for a ticker. Cached an hour: an 8-K can land any
    time, but the list does not change minute to minute."""
    try:
        from data import fetch_sec_filings
        return fetch_sec_filings(ticker, log=lambda m: None)
    except Exception:
        return []


@st.cache_data(ttl=86400, show_spinner=False)
def _cached_fin_supplement(ticker):
    """Capex / FCF / balance-sheet fields Polygon's endpoints don't return.
    Cached a day — these only move on a filing."""
    try:
        from market_data import get_financials_supplement
        return get_financials_supplement(ticker)
    except Exception:
        return None


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


def _render_filings(ticker):
    """What the company itself has published, above what has been written about it.

    This leads the News page's per-ticker view on purpose. A news feed is
    commentary; the 8-K is the event being commented on, and the 10-Q is where
    the earnings number in the headline actually came from. Both are public, free
    and already reachable through the EDGAR integration the fundamentals use.

    Renders nothing at all when EDGAR has no filings for the symbol - ETFs,
    crypto and most foreign tickers - rather than an empty shell or an error.
    """
    from data import key_filings

    filings = _cached_sec_filings(ticker)
    if not filings:
        return

    st.markdown('<div class="section-header">Company Filings '
                '<span style="font-weight:500;color:#94a3b8;letter-spacing:0;'
                'text-transform:none;font-size:0.7rem">· primary sources, '
                'straight from SEC EDGAR</span></div>', unsafe_allow_html=True)

    key = key_filings(filings)
    if key:
        _kcols = st.columns(len(key))
        for _c, _f in zip(_kcols, key):
            with _c:
                # The card is a div and the link is inline inside it. Wrapping
                # block-level divs in the anchor made Streamlit's sanitiser split
                # it into several anchors, and each one drew the 2px rule - so
                # every card carried a doubled line above its title.
                st.markdown(f"""
                <div style="border-top:2px solid #1d4ed8;padding:0.8rem 1rem 0.9rem 0;
                            height:100%">
                    <div style="margin-bottom:0.25rem">
                        <a href="{_f['url']}" target="_blank" rel="noopener"
                           style="text-decoration:none;font-weight:700;color:#1d4ed8;
                                  font-size:0.95rem">{_f['form']}</a>
                    </div>
                    <div style="color:#64748b;font-size:0.75rem;line-height:1.5;
                                margin-bottom:0.5rem">{_f['description']}</div>
                    <div style="color:#94a3b8;font-size:0.72rem;
                                font-family:'JetBrains Mono',monospace">
                        Filed {_f['filed']}</div>
                </div>
                """, unsafe_allow_html=True)

    # The full index, most recent first. Capped: a large filer's recent block runs
    # to tens of thousands of entries, nearly all insider forms and prospectus
    # supplements, and nobody scrolls that.
    with st.expander(f"All recent filings ({min(len(filings), 40)} of {len(filings):,})"):
        st.caption("Every document the company has filed with the SEC recently, "
                   "newest first. Form 4s are insider trades; 424B2s are offering "
                   "prospectuses.")
        _rows = []
        for _f in filings[:40]:
            _rows.append({
                "Form":   _f["form"],
                "Filed":  _f["filed"],
                "Period": _f["period"] or "—",
                "What it is": _f["description"] or "—",
                "Open":   _f["url"],
            })
        st.dataframe(
            pd.DataFrame(_rows), use_container_width=True, hide_index=True,
            column_config={"Open": st.column_config.LinkColumn("Open", display_text="View")},
        )


def _render_statements(ticker):
    """Revenue, earnings and cash flow as filed, not as summarised.

    The Analysis page already builds a full fundamentals view; this is the short
    version, so someone who came to read the news can see the numbers the news is
    about without changing pages.
    """
    try:
        fin = cached_fetch_sec_financials(ticker)
    except Exception:
        fin = {}
    inc = (fin or {}).get("income_statement")
    if inc is None or getattr(inc, "empty", True):
        return

    st.markdown('<div class="section-header">Financial Statements '
                '<span style="font-weight:500;color:#94a3b8;letter-spacing:0;'
                'text-transform:none;font-size:0.7rem">· as filed with the SEC'
                '</span></div>', unsafe_allow_html=True)

    _show = inc.head(4).copy()
    _money = [c for c in ("revenues", "net_income_loss", "operating_income_loss")
              if c in _show.columns]
    # The frame's period column is "Period" (the fiscal year end), not
    # "fiscal_year" - the earlier guess would have printed row numbers.
    _periods = _show["Period"] if "Period" in _show.columns else _show.index
    _out = pd.DataFrame({"Fiscal year end": list(_periods)})
    _label = {"revenues": "Revenue", "net_income_loss": "Net income",
              "operating_income_loss": "Operating income"}
    for _c in _money:
        _out[_label[_c]] = [f"${v/1e9:,.1f}B" if pd.notna(v) else "—" for v in _show[_c]]
    st.dataframe(_out, use_container_width=True, hide_index=True)
    st.caption("Annual figures from EDGAR XBRL. Full statements, ratios and "
               "valuation are on the Analysis page.")


# ── Presentation helpers ──────────────────────────────────────────────────────
# These were defined inline, mid-render, inside whichever conditional happened to
# need them: `_fv` inside `if fund.get("ok")`, the `_wpi_*` family inside the
# discounted-cash-flow block, and so on. They are pure formatters - a number in,
# a string out - and nesting them that way made the sections around them
# impossible to gate, because a section could not be skipped without also
# skipping the definition of the function it calls.
#
# Lifting them to module scope is the whole of this change. Nothing about their
# behaviour moves; what moves is the ability to wrap the sections that use them.

def _us_market_open():
    try:
        from zoneinfo import ZoneInfo
        from datetime import time as _t
        now_et = datetime.now(ZoneInfo("America/New_York"))
        return now_et.weekday() < 5 and _t(9, 30) <= now_et.time() <= _t(16, 0)
    except Exception:
        return False


def _fmt_vol(v):
    if v >= 1e9: return f"{v/1e9:.2f}B"
    if v >= 1e6: return f"{v/1e6:.2f}M"
    if v >= 1e3: return f"{v/1e3:.1f}K"
    return f"{v:,.0f}"


def _fv(v, suffix="", na="N/A"):
    return f"{v}{suffix}" if v is not None else na


def _mv(v, suffix="", na="—"):
    return f"{v}{suffix}" if v is not None else na


def _dir(x):
    return "" if x is None else ("pos" if x >= 0 else "neg")


def _pos0(x):
    return "pos" if (x or 0) > 0 else ("neg" if (x is not None and x < 0) else "")


def _wpi_pct(x, dp=1, sign=False):
    """Decimal → percent. `—` when the model didn't produce one."""
    if x is None:
        return "—"
    return f"{x * 100:+.{dp}f}%" if sign else f"{x * 100:.{dp}f}%"


def _wpi_usd(x, dp=2):
    return "—" if x is None else f"${x:,.{dp}f}"


def _wpi_mag(x):
    """Compact signed $ magnitude — cash flows, debt, EV."""
    if x is None:
        return "—"
    _a, _sg = abs(x), ("-" if x < 0 else "")
    if _a >= 1e12: return f"{_sg}${_a / 1e12:,.2f}T"
    if _a >= 1e9:  return f"{_sg}${_a / 1e9:,.2f}B"
    if _a >= 1e6:  return f"{_sg}${_a / 1e6:,.1f}M"
    return f"{_sg}${_a:,.0f}"


def _wpi_cnt(x):
    """Share counts — a count, not a currency."""
    if x is None:
        return "—"
    if abs(x) >= 1e9: return f"{x / 1e9:,.2f}B"
    if abs(x) >= 1e6: return f"{x / 1e6:,.1f}M"
    return f"{x:,.0f}"


def fmt_large(n):
    if not n: return "N/A"
    if n > 1e12: return f"${n/1e12:.2f}T"
    if n > 1e9:  return f"${n/1e9:.2f}B"
    if n > 1e6:  return f"${n/1e6:.1f}M"
    return f"${n:,.0f}"


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
from live_data import get_live_price, get_top_movers, get_tape_prices
import auth
from payments import render_pricing_section, create_checkout_session, verify_session, check_subscription
from portfolio_builder import render_portfolio_builder
from your_portfolios import render_your_portfolios
from legal import render_terms, render_privacy, render_legal_links
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


@st.cache_data(show_spinner=False)
def _b64_img(path):
    """Read an image file as a base64 string (for inline data: URIs). '' on miss.

    Cached because this runs at module scope, and module scope in Streamlit means
    once per rerun — so the nav mark was being re-read and re-encoded on every
    single click. The file cannot change without a redeploy.
    """
    try:
        import base64
        with open(path, "rb") as _f:
            return base64.b64encode(_f.read()).decode("ascii")
    except Exception:
        return ""


_MARK_B64  = _b64_img(_MARK_PATH)
_page_icon = _FAVICON if os.path.exists(_FAVICON) else "◈"

# set_page_config below fixes the tab a crawler never waits for. This fixes the
# HTML it is actually served — see seo.py. Silent and non-fatal by design.
#
# Guarded to once per session: apply() is idempotent but still opens and compares
# four files, and at module scope that would repeat on every rerun for a result
# that cannot change while the process lives.
import seo as _seo
if not st.session_state.get("_seo_applied"):
    _seo.apply()
    st.session_state["_seo_applied"] = True
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
# Styles live in styles.css, injected into the page as <style>.
#
# The <style> element itself must be re-emitted every rerun — Streamlit removes
# elements a run doesn't produce — but READING the 69KB file every time was
# waste, since it cannot change without a redeploy. The comment here used to say
# "load once", which is what it should have been doing and wasn't.
_CSS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "styles.css")


@st.cache_data(show_spinner=False)
def _load_css(path):
    """Read styles.css and strip its comments before shipping it to the browser.

    styles.css is heavily commented — deliberately, they explain why rules exist
    — but Streamlit emits this <style> block INSIDE <body>, not <head>, and
    Google's snippet extractor sampled the prose in those comments. The search
    result for "quantwizard" read:

        QuantWizard
        There is one action now ("Generate research package") with format as a
        secondary radio, so borrowing another product's brand colour …

    which is a note to a developer, presented to the public as a description of
    the product. The comments belong in the repository, not in the payload: they
    are ~14KB of English that no browser reads and that a crawler can mistake for
    content. Stripping them also trims what crosses the wire on every rerun.

    Deliberately conservative — only /* ... */ pairs, which is the only comment
    syntax CSS has.
    """
    import re
    with open(path, "r", encoding="utf-8") as f:
        css = f.read()
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Collapse the blank lines the comments left behind.
    css = re.sub(r"\n\s*\n+", "\n", css).strip()
    return f"<style>\n{css}\n</style>"


st.markdown(_load_css(_CSS_PATH), unsafe_allow_html=True)

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

# ── Check returning from Stripe ───────────────────────────────────────────────
# DEV_MODE_FREE: skip all Stripe session verification — preserved, not deleted.
params = st.query_params
if not DEV_MODE_FREE and "session_id" in params:
    ok, email = verify_session(params["session_id"])
    if ok:
        st.session_state["is_pro"] = True
        # No longer mirrors the address into ?email=. That param was how Pro
        # survived a refresh, and it was also an authorisation hole: anyone who
        # knew a subscriber's address could append it to the URL and be granted
        # Pro by the lookup below. Identity now comes from the signed session.
        st.query_params.clear()
        st.success("Welcome to QuantWizard Pro!")

# ── Re-verify Pro status on refresh, against the SIGNED-IN identity ───────────
# DEV_MODE_FREE: skip subscription lookup — preserved, not deleted.
elif not DEV_MODE_FREE and not st.session_state.get("is_pro"):
    _auth_email = auth.current_email()
    if _auth_email and not st.session_state.get("_sub_checked"):
        st.session_state["_sub_checked"] = True
        if check_subscription(_auth_email):
            st.session_state["is_pro"] = True

# ── Top navigation ────────────────────────────────────────────────────────────
# Custom sticky navbar (replaces Streamlit's empty built-in header). Uses native
# buttons in a keyed container so nav clicks are reruns — session state (e.g. a
# built portfolio) survives — while ?page= keeps the URL shareable.
# "terms" and "privacy" are routable but deliberately absent from the navbar —
# they are reached from the footer strip that renders on every page.
_PAGES = ("home", "analysis", "research", "builder", "portfolios",
          "terms", "privacy")
# Old links keep working. The page was called "News" until it grew SEC filings
# and financial statements above the feed, at which point the name described
# only the thing at the bottom of it. Anything already pointing at ?page=news -
# a bookmark, a shared link, an exported workbook - still lands correctly.
_PAGE_ALIASES = {"news": "research"}
# The current page lives in session state, seeded from the URL on first load.
#
# It used to be read straight from st.query_params here, at the top, while the
# navbar that CHANGES it renders 120 lines further down — so a nav click could
# only take effect by calling st.rerun(), executing this ~3,500-line script
# twice. Worse than the wasted time: the first run rendered the navbar and then
# aborted, so Streamlit tore down everything below it and the page sat visibly
# broken until the second run rebuilt it. Measured before the change, a
# Home -> Analysis click showed a torn-down page on 2 of 3 attempts. That is the
# "it looks like it crashed" flash.
#
# Reading from session state lets the handler below set the page and have the
# dispatch at the bottom of this file see it in the SAME run: one execution, no
# teardown. The URL is still written so links stay shareable.
if "_page" not in st.session_state:
    _requested = st.query_params.get("page", "home")
    st.session_state["_page"] = _PAGE_ALIASES.get(_requested, _requested)
_page = st.session_state["_page"]
_page = _PAGE_ALIASES.get(_page, _page)
if _page not in _PAGES:
    _page = "home"
st.session_state["_page"] = _page


def _goto(pg, rerun=False):
    """Navigate. `rerun` is needed only by callers below the page dispatch.

    The navbar runs BEFORE the dispatch, so setting the state is enough for this
    run to render the new page. Buttons inside a page's own body (the Home CTAs)
    run AFTER their page was chosen, so they still have to restart the script.
    """
    st.session_state["_page"] = pg
    st.query_params["page"] = pg
    if rerun:
        st.rerun()


# ── Analysis windows ──────────────────────────────────────────────────────────
# Two different windows, because they answer different questions.
#
# HISTORY is what we fetch. It used to be a user-facing "Date Range" slider
# defaulting to 1 year, which quietly degraded every statistic derived from it:
# NFLX's beta over one year measured 0.27 (it fell while the market rose), and
# that number fed straight into the forecast's expected return. Estimates like
# beta, normal P/E, volatility and correlation only get better with more data,
# and choosing a lookback is not a decision a reader should have to make.
#
# METRICS is what risk statistics are measured over. Sharpe across ten years
# blends 2016 with 2025; across one year it is noise. Three is the usual
# compromise, and the window is printed next to the numbers so each one has a
# definition attached.
HISTORY_YEARS = 10
METRICS_YEARS = 3


# ── Analysis jump rail ────────────────────────────────────────────────────────
# The Analysis page is one long scroll of ~14 sections, and which ones exist
# depends on the asset (crypto / ETF / stock) and the module checkboxes. So the
# rail is built from the sections that actually rendered rather than a fixed
# list, which would link to anchors that aren't on the page. Streamlit re-runs
# this module top-to-bottom on every interaction, so the list resets itself.
_NAV_SECTIONS = []          # [(anchor_id, rail_label)]


def _sec_id(aid, label):
    """Register a section in the jump rail; returns its id attribute.

    Called inline inside the existing section-header markup so no extra DOM
    node is introduced and the header spacing is untouched.
    """
    _NAV_SECTIONS.append((aid, label))
    return f' id="{aid}"'


def _jump_anchor(aid, label):
    """Scroll target for a section that has no visible header of its own."""
    _NAV_SECTIONS.append((aid, label))
    st.markdown(f'<div class="jump-anchor" id="{aid}"></div>', unsafe_allow_html=True)


_JUMP_SPY_JS = """
<script>
(function () {
  var P = window.parent, D = P.document, tries = 0;
  function scroller() {
    return D.querySelector('section[data-testid="stMain"]')
        || D.querySelector('.stMain') || D.scrollingElement;
  }
  function init() {
    var rail = D.querySelector('.jump-rail');
    if (!rail) return false;
    var links = Array.prototype.slice.call(rail.querySelectorAll('a[data-jump]'));
    if (!links.length) return false;
    var sc = scroller(), host = (sc === D.scrollingElement) ? P : sc;
    function sync() {
      var cur = null;
      links.forEach(function (a) {
        var el = D.getElementById(a.getAttribute('data-jump'));
        if (el && el.getBoundingClientRect().top <= 160) cur = a;
      });
      if (!cur) cur = links[0];
      links.forEach(function (a) { a.classList.toggle('is-active', a === cur); });
    }
    // The scroll container survives Streamlit re-runs but this iframe does not,
    // so drop the previous handler or they stack up one per re-run.
    if (sc.__qwJumpSync) host.removeEventListener('scroll', sc.__qwJumpSync);
    sc.__qwJumpSync = sync;
    host.addEventListener('scroll', sync, { passive: true });
    P.addEventListener('resize', sync, { passive: true });
    sync();
    return true;
  }
  var iv = setInterval(function () {
    if (init() || ++tries > 60) clearInterval(iv);
  }, 200);
})();
</script>
"""


def _render_jump_rail():
    """Fixed left-hand section nav for the Analysis results."""
    if not _NAV_SECTIONS:
        return
    items = "".join(f'<a href="#{aid}" data-jump="{aid}">{label}</a>'
                    for aid, label in _NAV_SECTIONS)
    st.markdown(f'<nav class="jump-rail" aria-label="On this page">'
                f'<div class="jump-rail-head">On this page</div>{items}</nav>',
                unsafe_allow_html=True)
    # Streamlit strips <script> from st.markdown, so the scroll-spy has to run
    # from a components iframe reaching into the parent document. It only sets
    # the active class — if it never initialises, the links still jump.
    st.components.v1.html(_JUMP_SPY_JS, height=0)

with st.container(key="topnav"):
    # Brand (left) · flexible spacer · nav links · sign-in control (far right).
    # The trailing column is the account control; it sits outside the page-link
    # loop because it isn't a page — it's identity, and it belongs visually
    # separated from navigation.
    # Column 4 was 0.95, sized for "News". "Research" is as long as "Analysis"
    # and wrapped to "Resear / ch", so it gets Analysis's width and the slack
    # comes off the brand column and the spacer.
    _nc = st.columns([2.2, 0.4, 0.95, 1.25, 1.3, 2.0, 1.85, 1.5],
                     vertical_alignment="center")
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
            [("Home", "home"), ("Analysis", "analysis"), ("Research", "research"),
             ("Portfolio Builder", "builder"), ("Your Portfolios", "portfolios")], start=2):
        # Every tab renders unstyled. Which one is active cannot be known while
        # this loop runs — the click that decides it happens inside the loop —
        # so the highlight is applied by CSS immediately afterwards instead.
        # That is what lets navigation avoid a second script run.
        if _nc[_i].button(_lbl, key=f"nav_{_pg}", use_container_width=True,
                          type="tertiary"):
            _goto(_pg)
    with _nc[7]:
        auth.render_nav_control()

# Re-read after the navbar: if a tab was just clicked this is the new page, and
# the dispatch at the bottom of this file renders it in this same run.
_page = st.session_state["_page"]

# Paint the active tab. Mirrors the `stBaseButton-primary` rule in styles.css,
# which no longer applies now that every nav button is tertiary.
st.markdown(
    f"<style>.st-key-topnav .st-key-nav_{_page} button{{"
    f"background:rgba(56,189,248,0.16)!important;"
    f"border:1px solid rgba(56,189,248,0.32)!important}}</style>",
    unsafe_allow_html=True)

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
    # `price`, not `px` — plotly.express is imported as px at module scope, and a
    # loop variable of the same name shadows it inside this function. Harmless
    # today because nothing here plots, and a silent trap for whoever first does.
    for sym, price, chg, up in items:
        chg_class = "t-up" if up else "t-dn"
        arrow     = "▲" if up else "▼"
        chg_part  = f'<span class="{chg_class}">{arrow} {chg}</span>' if chg else ""
        items_html += (f'<span class="t-item"><span class="t-sym">{sym}</span>'
                       f'<span class="t-px">{price}</span>{chg_part}</span>'
                       f'<span class="t-div">●</span>')
    doubled = items_html * 2  # seamless loop
    return f'<div class="ticker-tape-wrap"><div class="ticker-tape">{doubled}</div></div>'

# Streamlit-level cache on top of live_data.py's module dicts so these
# lightweight but frequently-called fetches don't hit Polygon on every
# tab switch / widget toggle.
@st.cache_data(ttl=60, max_entries=1, show_spinner=False)
def _cached_tape(_api_key):
    return get_tape_prices(_api_key)

# ── Ticker tape ───────────────────────────────────────────────────────────────
# Directly beneath the navbar, full-bleed, on every page. It used to sit inside
# the Home page body below the hero, which meant the one element proving the
# data is live was invisible everywhere else in the app.
_tape_items = _cached_tape(POLYGON_API_KEY)
if _tape_items:
    with st.container(key="tapebar"):
        st.markdown(_tape_html(_tape_items), unsafe_allow_html=True)


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
            if st.button("Upgrade to Pro", use_container_width=True):
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
    # The consent notice sits at the point of collection, not only in the footer —
    # this form is the first place we ask anyone for personal data.
    st.markdown(
        '<div style="font-size:0.68rem;color:#94a3b8;line-height:1.5;'
        'margin:-0.35rem 0 0.5rem">By joining you agree to our '
        '<a href="?page=terms" target="_self" style="color:#64748b">Terms</a> and '
        '<a href="?page=privacy" target="_self" style="color:#64748b">Privacy '
        'Policy</a>. We will not share your address.</div>',
        unsafe_allow_html=True)
    if st.button("Join Waitlist", use_container_width=True):
        # Was: append to a local waitlist.csv. Streamlit Community Cloud runs on
        # an ephemeral container that is rebuilt on every push and recycled when
        # the app sleeps, so every address collected since the last restart was
        # thrown away — we were destroying the one asset this form exists to build.
        _wl_email = (email_input or "").strip().lower()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", _wl_email):
            # `"@" in email` accepted "@" itself. Not RFC-complete, deliberately —
            # just enough to reject the obvious typos that make an address dead.
            st.error("Please enter a valid email.")
        else:
            from database import save_waitlist_email
            if not save_waitlist_email(_wl_email, source=_page):
                # Supabase unconfigured or unreachable. Never drop the signup on
                # the floor: stderr reaches the Streamlit Cloud app log (Manage
                # app → logs; greppable for WAITLIST-FALLBACK), and the CSV is a
                # second chance if someone reads it before the container recycles.
                print(f"[WAITLIST-FALLBACK] {_wl_email} "
                      f"{datetime.now().isoformat()} source={_page}",
                      file=sys.stderr, flush=True)
                try:
                    _wl_path = os.path.join(os.path.dirname(__file__), "waitlist.csv")
                    _wl_new  = not os.path.exists(_wl_path)
                    with open(_wl_path, "a", newline="", encoding="utf-8") as _f:
                        _w = csv.writer(_f)
                        if _wl_new:
                            _w.writerow(["email", "timestamp", "source"])
                        _w.writerow([_wl_email, datetime.now().isoformat(), _page])
                except Exception:
                    pass
            # Same message either way: from the visitor's side it worked, and it
            # did — we captured the address. Showing an error would lose the
            # signup and look broken for a problem that isn't theirs.
            st.success("Thanks! We'll be in touch.")

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
    # The sample workbook's metadata is loaded here, not further down the page,
    # because the sample is now the hero's primary call to action. It is the
    # lowest-friction proof the product has: a real generated report, served as
    # a static file, so it opens in two seconds with no signup, no ticker entry,
    # no 30-second build and no cold start. Asking someone to generate their own
    # before they have seen one is the higher-friction path, so it is secondary.
    _sm = None
    try:
        import json as _json
        _sm_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "static", "sample_report.json")
        with open(_sm_path, encoding="utf-8") as _f:
            _sm = _json.load(_f)
        _sm["_size"] = f"{_sm.get('bytes', 0) / 1_048_576:.1f} MB"
        _sm["_date"] = datetime.strptime(_sm["generated"], "%Y-%m-%d").strftime("%d %b %Y")
    except Exception:
        _sm = None   # no sample built yet — the CTA simply doesn't appear

    # Hero leads with the artifact, not the workflow. The report used to be the
    # third clause of a three-part sentence ("...then export a report"), which
    # framed the thing we actually charge for as an afterthought of the thing
    # that is free.
    st.markdown("""
    <div class="home-hero">
      <span class="home-hero-badge">Institutional tools · retail price</span>
      <h1 class="home-hero-title">A full equity research report —<br>on any stock, in 30 seconds.</h1>
      <p class="home-hero-sub">Valuation, Monte Carlo, fundamentals, peers and risk — exported to a
      polished <b>Excel</b> workbook, <b>PowerPoint</b> deck or <b>Word</b> doc. The work that takes an
      analyst an afternoon. Stock analysis and portfolio tools included.</p>
    </div>
    """, unsafe_allow_html=True)

    if _sm:
        from icons import icon as _icon
        st.markdown(
            f'<a class="sample-dl sample-dl-hero" href="app/static/{_sm["file"]}" download>'
            f'{_icon("download", 17)}'
            f'<span><b>See a real one — {_sm["ticker"]} research report</b>'
            f'<span class="sample-dl-sub">Excel · {_sm["period"]} · {_sm["_size"]} · '
            f'generated {_sm["_date"]}</span></span></a>', unsafe_allow_html=True)

    _hc = st.columns([1.1, 1.1, 2.8])
    if _hc[0].button("Analyze a stock", type="primary", use_container_width=True, key="cta_analyze"):
        _goto("analysis", rerun=True)
    if _hc[1].button("Build a portfolio", use_container_width=True, key="cta_build"):
        _goto("builder", rerun=True)

    # Quick start lived here: three cards reading "Start with analysis",
    # "Build your first portfolio" and "Track what matters". "What you can do"
    # below says the same three things about the same three products, with
    # buttons that actually go there — so the page introduced itself twice
    # before making any case for itself. The version with the CTAs survives.

    # The ticker tape used to render here. It is now under the navbar, so it
    # appears on every page rather than only this one.

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
                _goto(_pg, rerun=True)
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
        # Framed by the job it does, not by its specifications. Sheet count is
        # not a reason to buy; having something you can actually send someone is.
        st.markdown("""<ul class="home-spotlight-ul">
          <li>Something you can actually send — to your investment club, your
              partner, or your own future self when you're deciding whether to sell</li>
          <li>Every number sourced and dated, so you can defend the conclusion</li>
          <li>Valuation, Monte Carlo, fundamentals, peers and risk in one file</li>
        </ul>""", unsafe_allow_html=True)
    with _bc[1]:
        if st.button("Generate one →", type="primary", use_container_width=True, key="cta_report"):
            _goto("analysis", rerun=True)

    # ── Pre-built sample, repeated ────────────────────────────────────────────
    # Same static workbook as the hero CTA (metadata already loaded above, so
    # this costs no second file read). Repeated deliberately: the hero catches
    # people who want proof immediately, this catches the ones who scrolled the
    # carousel first and are now convinced enough to open one.
    if _sm:
        st.markdown(
            f'<a class="sample-dl" href="app/static/{_sm["file"]}" download>'
            f'{_icon("download", 17)}'
            f'<span><b>See a real one — {_sm["ticker"]} research report</b>'
            f'<span class="sample-dl-sub">Excel · {_sm["period"]} · {_sm["_size"]} · '
            f'generated {_sm["_date"]}</span></span></a>', unsafe_allow_html=True)

    # ── The product pitch ─────────────────────────────────────────────
    # Why QuantWizard, What's Included, How It Works, Methodology and Pricing
    # used to live under the Analysis page's ticker box, in the branch that
    # renders when nobody has typed anything yet. So the entire case for the
    # product was shown only to someone who had ALREADY decided to analyse
    # something, and vanished the moment they typed a ticker - while Home, the
    # page whose job is to make that case, was a hero and two buttons.
    #
    # It also made "How It Works" read as a contradiction: it describes the
    # Portfolio Builder's flow (risk tolerance, capital, horizon, then rank and
    # optimise), which is accurate here beside the other products and was
    # nonsense on a page whose only control is a ticker box.

    # ── Problem section ───────────────────────────────────────────────────
    st.markdown("""
    <div class="section-header">Why QuantWizard</div>
    """, unsafe_allow_html=True)

    p1, p2 = st.columns(2)
    for col, icon, problem, solution in [
        (p1, "show_chart", "A price chart isn't risk.",
         "We give you the numbers that actually matter — volatility, drawdown, Sharpe against the live T-bill, and a 1,000-path forecast."),
        (p2, "search", "A screener hands you a list.",
         "They won't build the portfolio. We rank 320+ names daily, then optimise the weights to your risk tolerance."),
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
    <div style="color:#64748b;font-size:0.85rem;margin:-0.4rem 0 1rem;max-width:62ch">
        Full stock analysis is free and needs no account. Pro adds the portfolio tools.
    </div>
    """, unsafe_allow_html=True)

    # The free product leads, full width, and the Pro grid follows it.
    #
    # These were four equal cards in a 2x2, which gave Stock Analysis — the thing
    # anyone can use right now, without an account — exactly the same weight as
    # three products behind a paywall. Home's job is to convert on the free one.
    #
    # "Bond & Portfolio Autopsy" was also two unrelated products sharing a title
    # and a bullet list: ETF analysis across six categories, and a CSV upload
    # that attributes P&L across your own holdings. Nothing about them is one
    # feature, and bundling them made both sound vaguer than they are.
    fc_free = st.container()
    fp1, fp2 = st.columns(2)
    fp3, fp4 = st.columns(2)

    for col, icon, title, tier, items in [
        (fc_free, "monitoring", "Stock Analysis", "Free",
         ["Bollinger Bands, RSI, GARCH volatility", "Monte Carlo simulation (1,000 paths)",
          "Peer comparison — chosen automatically, same sector and size",
          "10-yr fundamentals + F-Score & Z-Score", "Excel + PowerPoint export"]),
        (fp1, "account_balance_wallet", "Portfolio Builder", "Pro",
         ["320+ stocks ranked by multi-factor score", "5-year backtest with quarterly rebalancing",
          "Mean-variance optimization", "Portfolio Monte Carlo with milestone projections",
          "Diversification score + correlation heatmap"]),
        (fp2, "local_fire_department", "Stress Test", "Pro",
         ["5 historical crashes: 2008, COVID, 2022, dot-com, 2018", "Beta-based shock from your holdings' sensitivity",
          "Portfolio return vs S&P 500 per crash", "Dollar impact calculator",
          "Correlation culprit detection"]),
        (fp3, "account_balance", "Bond Analysis", "Pro",
         ["60+ bond ETFs across 6 categories", "Duration and credit-quality breakdown",
          "Yield vs interest-rate sensitivity", "Benchmark comparison"]),
        (fp4, "biotech", "Portfolio Autopsy", "Pro",
         ["Upload your holdings CSV — see what broke", "P&L attribution per position",
          "Rolling volatility + drawdown charts", "Benchmark comparison"]),
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
         "Correlated multi-asset simulation via <b>Cholesky decomposition</b> of the historical return "
         "correlation matrix. Per-asset drift is <b>CAPM</b> — risk-free rate + beta × a 5% equity risk "
         "premium — so the projection is driven by how much market risk a holding carries, not by which "
         "names recently ran up. Log-normal paths with an Itô correction; a fixed seed, so the same "
         "inputs give the same answer."),
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

    # The Pricing section lived here: a Free / Pro $9.99 comparison. Removed
    # while the pricing model is still undecided - publishing a price is a
    # commitment, and the tier badges on the cards above already say which
    # tools are paid without naming a number.

    # ── Built by ──────────────────────────────────────────────────────────────
    # Lives at the bottom of the front page, above the footer. It used to sit on
    # the Analysis page's empty state, which meant it only appeared to someone
    # who had navigated to Analysis and then not typed a ticker — the one visitor
    # least likely to care who built it.
    st.markdown('<div class="section-header">Built By</div>', unsafe_allow_html=True)
    _fc, _ = st.columns([3, 2])
    _fc.markdown("""
    <div class="founder-card" style="display:flex;align-items:flex-start;gap:1.25rem">
        <img src="https://raw.githubusercontent.com/wstratton707/stockwizard/main/assets/IMG_0434.jpeg"
             alt="Wyatt Stratton"
             style="width:64px;height:64px;border-radius:50%;object-fit:cover;
                    flex-shrink:0;border:2px solid #1d4ed8">
        <div>
            <div class="founder-name">Wyatt Stratton</div>
            <div class="founder-role">Founder</div>
            <div class="founder-school">Indiana University Bloomington</div>
            <div class="founder-quote" style="margin-top:0.6rem">
                &ldquo;I built QuantWizard because I was tired of spending hours pulling
                financial data manually. Any investor deserves a professional report
                in seconds.&rdquo;
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

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
    # ── Analysis inputs ────────────────────────────────────────────────
    # A ticker and a button. Everything else is behind Advanced options.
    #
    # This panel used to open with thirteen controls: a ticker, a date-range
    # checkbox, two benchmark checkboxes, a peers box, six module checkboxes and
    # two forecast sliders. Every one of them already defaulted to the right
    # thing, so the settings were not wrong - they were just visible, and they
    # asked the reader to understand the methodology before seeing any output.
    #
    # Four are gone rather than moved:
    #   Correlation Matrix, Support & Resistance - both compute over data that
    #     has already been fetched, through memoised wrappers. Switching them off
    #     saved nothing, so the toggle only ever cost a decision.
    #   Simulations, Horizon - implementation details of the forecast. Nobody
    #     writing a research report can weigh 100 paths against 5,000, and the
    #     single-stock Monte Carlo allocates three unbatched arrays of
    #     days x sims, so the slider's top end was a memory footgun on a box that
    #     has hit its ceiling twice this month. Fixed at the defaults they had.
    _inputs_box = st.container(key="analysis-inputs")
    with _inputs_box:
        bar_size = "day"
        _today = datetime.today().date()

        # ?ticker=MSFT prefills and runs the analysis, so a link can point at a
        # specific name. This is what makes the ticker links inside the exported
        # workbooks land on that company's page instead of a blank form.
        _qt = (st.query_params.get("ticker") or "").strip().upper()
        if _qt and not st.session_state.get("analysis_ticker"):
            st.session_state["analysis_ticker"] = _qt
            st.session_state["analysis_ran"] = True

        st.markdown('<div class="field-label">Ticker</div>', unsafe_allow_html=True)
        # Pressing Enter runs the analysis just like the button does (see the
        # `not run_btn and not ticker_input` landing-page test below).
        ticker_input = st.text_input(
            "", placeholder="Enter a ticker — e.g. AAPL, SPY, BTC",
            key="analysis_ticker",
            on_change=lambda: st.session_state.update(
                analysis_ran=bool(st.session_state.get("analysis_ticker", "").strip())),
            label_visibility="collapsed"
        ).strip().upper()

        run_btn = st.button(
            "Run Analysis", type="primary", use_container_width=True,
            on_click=lambda: st.session_state.update(analysis_ran=True))

        st.caption("Financial analysis, valuation, peer comparison, risk metrics, "
                   "technicals and a price forecast.")

        # ── Advanced options ─────────────────────────────────────────
        # Only things that cost a network call or genuinely change the answer.
        with st.expander("Advanced options", expanded=False):
            st.markdown('<div class="field-label">Benchmarks</div>',
                        unsafe_allow_html=True)
            _b1, _b2 = st.columns(2)
            include_spy = _b1.checkbox("S&P 500 (SPY)", value=True)
            include_qqq = _b2.checkbox("NASDAQ (QQQ)", value=True)

            st.markdown('<div class="field-label">Peer Comparison</div>',
                        unsafe_allow_html=True)
            peers_input = st.text_input(
                "", placeholder="Automatic — same sector, closest in size",
                label_visibility="collapsed",
                help="Leave empty and QuantWizard picks peers from the same "
                     "sector, closest to this company in market value.")

            st.markdown('<div class="field-label">Report Modules</div>',
                        unsafe_allow_html=True)
            _m1, _m2, _m3 = st.columns(3)
            do_mc     = _m1.checkbox("Price Forecast",    value=True)
            # Like News, this only reaches the exported report — nothing on the
            # page renders a sector comparison.
            do_sector = _m2.checkbox("Sector Comparison", value=True,
                                     help="Adds a sector-ETF comparison to the "
                                          "exported report.")
            do_news   = _m3.checkbox("News Headlines",    value=True,
                                     help="Includes recent headlines in the "
                                          "exported report. Read them on the "
                                          "Research page.")

            # Off by default: the standard run fetches HISTORY_YEARS and reports
            # risk over METRICS_YEARS, so nobody has to pick a lookback to get a
            # sound answer. Turning it on tailors the whole analysis - chart,
            # risk metrics, forecast, correlation and the report - to exactly the
            # dates chosen. Measured on AAPL, 5Y vs 1Y moves annualised
            # volatility 28.1% -> 25.2% and Sharpe 0.56 -> 1.37, so it is doing
            # real work even when the page looks similar; the metric cards name
            # the window they used.
            st.markdown('<div class="field-label">Analysis Window</div>',
                        unsafe_allow_html=True)
            custom_range = st.checkbox(
                "Custom date range", value=False, key="use_custom_range",
                help=f"Off: {HISTORY_YEARS} years of history, with risk measured "
                     f"over the last {METRICS_YEARS}. On: everything is measured "
                     f"between the two dates you pick.")
            if custom_range:
                _dc1, _dc2 = st.columns(2)
                _cs = _dc1.date_input("From", value=_today - timedelta(days=365),
                                      max_value=_today, key="custom_start")
                _ce = _dc2.date_input("To", value=_today,
                                      max_value=_today, key="custom_end")
                if _cs >= _ce:
                    st.error("The start date has to be before the end date.")
                    st.stop()
                if (_ce - _cs).days < 30:
                    st.error("Pick a range of at least a month — anything shorter "
                             "can't support a volatility or drawdown figure.")
                    st.stop()
                date_start   = _cs.strftime("%Y-%m-%d")
                date_end     = _ce.strftime("%Y-%m-%d")
                period_label = f"{date_start} to {date_end}"
            else:
                date_end     = _today.strftime("%Y-%m-%d")
                date_start   = (_today - timedelta(days=365 * HISTORY_YEARS)
                                ).strftime("%Y-%m-%d")
                period_label = f"{HISTORY_YEARS}Y"

        # Always included, and no longer switchable: see the note above.
        do_corr = True
        do_sr   = True
        do_peers = True
        forecast_method = "Monte Carlo"
        n_sims, n_horizon = 1000, 252


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

        # Live price ticker. The full hero panel (day/52W range bars, etc.) is
        # rendered once df is loaded below.
        live = get_live_price(_poly_ticker, POLYGON_API_KEY)


        # The analysis body. This was `if mode == "Investor Mode":`, left behind
        # when Day Trader Mode was removed and `mode` became a constant — the
        # test could only ever be true, and it sits directly inside
        # `if run_btn or ticker_input:` which already decided the same thing.
        #
        # The condition is now that parent's, restated, rather than a comparison
        # against a variable that no longer means anything. The block is NOT
        # unwrapped: dedenting it would re-indent 2,289 lines, 85 of which start
        # at column 0 inside multi-line HTML strings, for no behavioural gain.
        if run_btn or ticker_input:

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

                # Peers, when the user hasn't named any. The checkbox above has
                # always defaulted to on, but the fetch was gated on a manually
                # typed list, so for anyone who didn't fill that box in, "Peer
                # Comparison: on" produced no peer section whatsoever. Same
                # sector, closest in size, from the ranked universe precompute
                # already refreshes daily.
                peers_auto = False
                if do_peers and not peers_list and not is_crypto:
                    peers_list = suggest_peers(
                        ticker_input, sector=sector,
                        market_cap=float(company_details.get("Market Cap") or 0),
                        api_key=POLYGON_API_KEY)
                    peers_auto = bool(peers_list)

                # ── Deferred groups ──────────────────────────────────────
                # Peers, the sector ETF and the news feed used to be fetched
                # here, before a single pixel rendered. Measured cold on MSFT
                # they are 2.74s, ~0s and 1.45s of an 8.1s run - and peers alone
                # is a third of the wait for a section most visitors never
                # scroll to.
                #
                # They are accessors now, not locals. Each one calls the same
                # `cached_*` wrapper it always did, so the FIRST caller pays and
                # every later one is free, and no consumer has to know whether
                # someone else already ran it. That property is what makes the
                # tabbed layout safe to build on top: a section can be rendered
                # first, last or not at all without a NameError or a repeated
                # fetch, which a pre-computed local could not promise.
                def _load_peers():
                    """(peer_df, peer_price_dfs). Empty when peers are off."""
                    if not (do_peers and peers_list and not is_crypto):
                        return None, {}
                    _pdf_map = {}
                    _pdfr = cached_fetch_peer_comparison(
                        ticker_input, tuple(peers_list), POLYGON_API_KEY)
                    for _pt in [ticker_input] + peers_list[:4]:
                        try:
                            _one = cached_fetch_ohlcv(_pt, "5y", POLYGON_API_KEY,
                                                      start_override=date_start,
                                                      end_override=date_end,
                                                      bar_size=bar_size)
                            # .copy() — cached_fetch_ohlcv returns a memoised df;
                            # the derived columns below would mutate the cache.
                            _one = _one.copy()
                            _one["Daily_Return"]     = _one["Close"].pct_change()
                            _one["Cumulative_Index"] = (
                                1 + _one["Daily_Return"].fillna(0)).cumprod() * 100
                            _pdf_map[_pt] = _one
                        except Exception:
                            pass
                    return _pdfr, _pdf_map

                def _load_sector():
                    if not (do_sector and not is_crypto):
                        return None
                    return cached_fetch_sector_data(
                        ticker_input, POLYGON_API_KEY, sector,
                        start_override=date_start, end_override=date_end,
                        bar_size=bar_size)

                def _load_news():
                    """Only the exported report reads this now — see the note
                    where the on-page news section used to be."""
                    if not do_news:
                        return []
                    return _cached_report_news(ticker_input,
                                               company_details.get("Name", ""))

                # Proxy key for the heavy-computation caches. It must identify the
                # WINDOW, not just where it ends: a 1Y and a 5Y pull taken on the
                # same day share the same last date, so keying on that alone made
                # the Date Range slider a no-op for Monte Carlo, support/resistance
                # and the correlation matrix — every window after the first served
                # the first one's result from cache, computed off a different mu
                # and sigma.
                _win_key = (f"{df['Date'].iloc[0]}|{df['Date'].iloc[-1]}|{len(df)}"
                            if "Date" in df.columns and len(df) else "")

                # Risk window. On a custom range the user has already said what
                # period they mean, so everything is measured over it. Otherwise
                # risk is measured over METRICS_YEARS: a Sharpe computed across
                # the full ten-year pull blends regimes, and one computed across
                # a single year is mostly noise.
                if custom_range:
                    dfm, _mwin = df, "selected range"
                else:
                    _mw_from = (pd.to_datetime(df["Date"].iloc[-1])
                                - pd.DateOffset(years=METRICS_YEARS))
                    dfm = df[pd.to_datetime(df["Date"]) >= _mw_from]
                    # Fall back rather than quote a ratio off a handful of bars.
                    if len(dfm) < 120:
                        dfm, _mwin = df, "full history"
                    else:
                        _mwin = f"{METRICS_YEARS}Y"
                _mw_key = (f"{dfm['Date'].iloc[0]}|{dfm['Date'].iloc[-1]}|{len(dfm)}"
                           if len(dfm) else _win_key)

                corr_matrix = None
                if do_corr:
                    progress.progress(60, text="Building correlation matrix...")
                    corr_matrix = cached_build_correlation_matrix(
                        ticker_input, _mw_key,
                        tuple(benchmarks) if benchmarks else (), dfm,
                    )

                resistance = support = None
                if do_sr:
                    progress.progress(65, text="Detecting support & resistance...")
                    resistance, support = cached_detect_support_resistance(
                        ticker_input, _win_key, df,
                    )

                mc_sim_df = mc_summary = None
                if do_mc:
                    progress.progress(75, text="Running Monte Carlo simulation...")
                    # Volatility and beta come from the risk window, not the
                    # full pull — a decade of history would price today's
                    # forecast off regimes that are long gone.
                    mc_sim_df, mc_summary = cached_run_monte_carlo(
                        ticker_input, _mw_key, n_sims, n_horizon, dfm,
                    )

                progress.progress(85, text="Generating summary...")
                ret      = dfm["Daily_Return"].dropna()
                ann_ret  = ret.mean() * 252
                ann_std  = ret.std() * np.sqrt(252)
                # Excess-return Sharpe/Sortino: subtract the risk-free rate so
                # these match the portfolio engine (portfolio_analysis.py) and
                # the standard definition. Without it the headline ratios were
                # inflated and inconsistent across the app. The Sortino
                # denominator is downside deviation about zero — see
                # analysis.downside_deviation.
                from analysis import downside_deviation as _dd_fn
                downside = _dd_fn(ret)
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
                            price=float(df["Close"].iloc[-1]),
                            supplement=_cached_fin_supplement(ticker_input))
                    except Exception:
                        _fund_report = {"ok": False}

                # Wall-Street consensus for the report (cached → the on-screen
                # Analyst View below reuses the same call for free). {} for crypto
                # or when no Finnhub key is configured.
                _analyst_report = {} if is_crypto else cached_get_analyst_data(ticker_input)

                # Forward DCF fair value (FCF-based), and the reverse solve for
                # what growth today's price implies. Reuses the fundamentals
                # computed above; degrades to {"ok": False} for crypto or names
                # without positive free cash flow.
                #
                # Beta drives the discount rate, and the discount rate drives the
                # implied-growth headline — on identical financials, a beta of
                # 0.55 vs 1.35 moves it from -1.6% to +13.8%. So it is computed
                # from whichever benchmark the user included rather than left at
                # the flat rate every company used to share. With no benchmark
                # selected, dcf_valuation falls back to its default and says so
                # in `wacc_basis`, instead of inventing a beta.
                _dcf_report = {"ok": False}
                if not is_crypto and _fund_report.get("ok"):
                    _beta = None
                    for _bt in ("SPY", "QQQ"):
                        if f"{_bt}_Return" in df.columns:
                            _beta = market_beta(df["Daily_Return"], df[f"{_bt}_Return"])
                            if _beta is not None:
                                break
                    try:
                        _dcf_report = dcf_valuation(
                            _fund_report, float(df["Close"].iloc[-1]),
                            beta=_beta,
                            # Lets the model say when an unlevered FCF DCF is the
                            # wrong instrument for the filer — banks and brokers.
                            sector=(company_details or {}).get("Sector"))
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
            # Headline return is a trailing year unless a range was chosen. With
            # a ten-year pull the old "period return" put a decade's gain in the
            # slot the eye reads as recent performance.
            if custom_range:
                period_ret, _ret_label = ((latest["Close"] / first["Close"] - 1) * 100,
                                          "Selected range")
            else:
                _1y_from = pd.to_datetime(df["Date"].iloc[-1]) - pd.DateOffset(years=1)
                _df1y    = df[pd.to_datetime(df["Date"]) >= _1y_from]
                _base    = _df1y["Close"].iloc[0] if len(_df1y) > 1 else first["Close"]
                period_ret = (latest["Close"] / _base - 1) * 100
                _ret_label = "1Y" if len(_df1y) > 1 else period_label
            pos_neg    = lambda v: "positive" if v > 0 else ("negative" if v < 0 else "neutral")

            # Reports build on demand: a click builds the file (with a spinner),
            # caches it in session for this exact ticker+period, then swaps in a
            # download button. The mirrored buttons lower on the page share the
            # same cached file, so building once serves both.

            # One action, not three. Three coequal buttons framed the report as
            # three products, invited a single user to build all three (each one
            # renders matplotlib charts into a workbook or deck, which is the
            # memory spike on a small instance), and fought the pricing model —
            # there is one purchase here, not one per file format. Format is a
            # preference chosen once, so it is a secondary control.
            _FORMATS = [
                ("Excel workbook", "excel", ".xlsx",
                 "The full model — every metric, the DCF with live assumption "
                 "cells you can change, and the underlying data."),
            ]
            if PPTX_AVAILABLE:
                _FORMATS.append(("PowerPoint deck", "pptx", ".pptx",
                                 "The conclusions, laid out to present."))
            if DOCX_AVAILABLE:
                _FORMATS.append(("Word memo", "word", ".docx",
                                 "The written thesis, ready to edit."))

            # A written report covers a stated period, so this one control stays —
            # but it belongs here, next to the download, not in the inputs panel
            # where it silently set the lookback for every statistic on the page.
            _RPT_YEARS = {"1 year": 1, "3 years": 3, "5 years": 5,
                          "10 years": HISTORY_YEARS}

            def _stock_exports(suffix):
                # Sign-in and quota are checked before the format picker, so a
                # signed-out visitor is asked once rather than after choosing.
                from entitlements import (require_export, record,
                                          render_quota_note)
                _ok, _user = require_export(f"stock_{suffix}")
                if not _ok:
                    return

                _labels = [f[0] for f in _FORMATS]
                _pick = st.radio("Format", _labels, horizontal=True,
                                 key=f"fmt_{suffix}", label_visibility="collapsed")
                _name, _kind, _ext, _blurb = next(
                    f for f in _FORMATS if f[0] == (_pick or _labels[0]))
                st.markdown(
                    f'<div style="font-size:0.8rem;color:var(--muted);'
                    f'margin:-0.35rem 0 0.6rem">{_blurb}</div>',
                    unsafe_allow_html=True)

                # On a custom range the report covers exactly what was asked for;
                # there is no second period to choose.
                if custom_range:
                    _rdf, _rlabel = df, period_label
                    st.caption(f"Covers your selected range · {period_label}")
                else:
                    _rp = st.selectbox("Report period", list(_RPT_YEARS), index=2,
                                       key=f"rptp_{suffix}")
                    _rfrom = (pd.to_datetime(df["Date"].iloc[-1])
                              - pd.DateOffset(years=_RPT_YEARS[_rp]))
                    _rdf = df[pd.to_datetime(df["Date"]) >= _rfrom]
                    if len(_rdf) < 30:
                        _rdf = df
                    _rlabel = _rp.replace(" year", "Y").replace("s", "")

                # The narrative must describe the window the workbook describes.
                # `summary_text` above is built from `df` — the full 10-year pull
                # — with Sharpe and Sortino from `dfm`, the 3-year risk window.
                # That is right for the on-screen panel, which analyses the whole
                # history, and wrong for a 5-year report: the Dashboard paragraph
                # quoted a ten-year return beside five-year metric cells.
                # Rebuild it here, where the report period is finally known, from
                # one set of statistics measured on that period.
                from analysis import window_stats as _win_stats
                _rstats = _win_stats(_rdf)
                _summary_win = generate_summary_paragraph(
                    ticker_input, _rdf, company_details, mc_summary,
                    _rstats["sharpe"], _rstats["sortino"],
                    forecast_method=forecast_method, stats=_rstats)

                _id_key, _buf_key = f"_{_kind}_id", f"_{_kind}_buf"
                _report_id = f"{ticker_input}|{_rlabel}|{bar_size}"
                _ready = st.session_state.get(_id_key) == _report_id

                _bc = st.columns([1.6, 2.4])
                with _bc[0]:
                    if not _ready and st.button("Generate research package",
                                                type="primary",
                                                use_container_width=True,
                                                key=f"gen_{_kind}_{suffix}"):
                        with st.spinner(f"Building your {_name.lower()}…"):
                            # A report is a standalone document, so it fetches
                            # everything regardless of what the reader looked at
                            # on screen. Export is an explicit action and a few
                            # seconds here is the right trade; a thinner file
                            # would not be.
                            peer_df, peer_price_dfs = _load_peers()
                            sector_df = _load_sector()
                            news_list = _load_news()
                            try:
                                if _kind == "excel":
                                    from excel_builder import build_excel
                                    st.session_state[_buf_key] = build_excel(
                                        ticker_input, _rdf, _rlabel,
                                        company_details=company_details, sector_df=sector_df,
                                        mc_sim_df=mc_sim_df, mc_summary=mc_summary,
                                        news_list=news_list, peer_df=peer_df,
                                        corr_matrix=corr_matrix,
                                        resistance_levels=resistance, support_levels=support,
                                        summary_text=_summary_win,
                                        bar_size=bar_size, fundamentals=_fund_report,
                                        analyst_data=_analyst_report, dcf=_dcf_report,
                                    )
                                elif _kind == "pptx":
                                    # dcf= was missing here while Excel and Word
                                    # both passed it, so the deck's valuation
                                    # slide had nothing to render.
                                    from pptx_builder import build_stock_pptx
                                    st.session_state[_buf_key] = build_stock_pptx(
                                        ticker_input, _rdf, _rlabel,
                                        company_details=company_details,
                                        mc_sim_df=mc_sim_df, mc_summary=mc_summary,
                                        news_list=news_list, summary_text=_summary_win,
                                        fundamentals=_fund_report, dcf=_dcf_report,
                                    )
                                else:
                                    from docx_builder import build_stock_docx
                                    st.session_state[_buf_key] = build_stock_docx(
                                        ticker_input, _rdf, _rlabel,
                                        company_details=company_details,
                                        mc_summary=mc_summary, news_list=news_list,
                                        summary_text=_summary_win,
                                        fundamentals=_fund_report,
                                        analyst_data=_analyst_report, dcf=_dcf_report,
                                        sector_df=sector_df, peer_df=peer_df,
                                    )
                            except Exception:
                                st.session_state[_buf_key] = None
                            st.session_state[_id_key] = _report_id
                        # Count the build, not the download — downloading the
                        # same file twice is one report. A failed build isn't
                        # counted at all.
                        if st.session_state.get(_buf_key) is not None:
                            record(_user, f"stock_{_kind}",
                                   f"{ticker_input} {_rlabel}")
                        _ready = True
                    if _ready:
                        _buf = st.session_state.get(_buf_key)
                        if _buf is not None:
                            _buf.seek(0)
                            _mime = {
                                "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                "pptx":  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                                "word":  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            }[_kind]
                            st.download_button(
                                f"Download {_name} ({_ext})", data=_buf,
                                file_name=f"{ticker_input}_{_rlabel.replace(' ', '')}"
                                          f"_Analysis{_ext}",
                                mime=_mime, use_container_width=True,
                                key=f"dl_{_kind}_{suffix}",
                            )
                        else:
                            st.caption(f"{_name} export isn’t available.")
                render_quota_note(_user)

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
            st.markdown(f"""<div class="stock-hero"{_sec_id("sec-snapshot", "Snapshot")}>
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
<div class="stock-hero-stat-sub">{_ret_label}</div>
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
            # The window is in the label, not just the tooltip: a Sharpe ratio
            # with no stated period is not a number anyone can check.
            _mw_note = (f" Measured over the {_mwin}."
                        if _mwin in ("selected range", "full history")
                        else f" Measured over the last {METRICS_YEARS} years.")
            _TOOLTIPS = {
                f"Sharpe Ratio ({_mwin})":    "Risk-adjusted return (excess of the risk-free rate). Above 1.0 is good, above 2.0 is excellent. Higher = better return per unit of risk." + _mw_note,
                f"Sortino Ratio ({_mwin})":   "Like Sharpe but only penalises downside volatility. Higher is better." + _mw_note,
                f"Ann. Volatility ({_mwin})": "Annualized standard deviation of daily returns. Higher = more price swings. S&P 500 averages ~15%." + _mw_note,
            }
            _row_items = [
                (f"Sharpe Ratio ({_mwin})",    f"{sharpe:.2f}"  if pd.notna(sharpe)  else "N/A",
                                    pos_neg(sharpe)  if pd.notna(sharpe)  else "neutral"),
                (f"Sortino Ratio ({_mwin})",   f"{sortino:.2f}" if pd.notna(sortino) else "N/A",
                                    pos_neg(sortino) if pd.notna(sortino) else "neutral"),
                (f"Ann. Volatility ({_mwin})", f"{vol_val*100:.1f}%" if pd.notna(vol_val) else "N/A", "neutral"),
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

            # ── What The Data Shows (summary up top) ──────────────────────────
            # Surface the plain-English readout here instead of only at the very
            # bottom. It describes what the numbers did — deliberately not what to
            # do about them; this platform states findings, it does not advise.
            if summary_text:
                st.markdown(
                    f'<div style="background:linear-gradient(135deg,var(--brand-1) 0%,var(--brand-2) 100%);'
                    f'border:1px solid rgba(59,130,246,0.3);border-radius:12px;'
                    f'padding:1.2rem 1.5rem;margin:1.4rem 0 0.4rem;box-shadow:0 4px 16px rgba(15,23,42,0.09)">'
                    f'<div style="font-size:0.66rem;font-weight:700;letter-spacing:1.2px;'
                    f'text-transform:uppercase;color:#60a5fa;margin-bottom:0.5rem;'
                    f'display:flex;align-items:center;gap:0.4rem">'
                    f'<span class="material-symbols-outlined" style="font-size:1rem">lightbulb</span> What The Data Shows</div>'
                    f'<div style="color:#cbd5e1;font-size:0.9rem;line-height:1.75;'
                    f'font-family:var(--font-sans)">{summary_text}</div></div>',
                    unsafe_allow_html=True)

            # ── Valuation Lens — price vs. earnings-justified fair value ──────
            if not is_crypto and VALUATION_AVAILABLE:
                with st.spinner("Building the valuation view…"):
                    _vdata = _cached_valuation(ticker_input)
                if _vdata:
                    st.markdown(
                        f'<div class="section-header"'
                        f'{_sec_id("sec-valuation", "Valuation Lens")}>Valuation Lens '
                        '<span style="font-weight:500;color:#94a3b8;letter-spacing:0;'
                        'text-transform:none;font-size:0.7rem">· price vs. earnings-justified fair value</span></div>',
                        unsafe_allow_html=True)
                    # ── facts ──
                    # Prefer the latest trailing-twelve-month EPS so this figure
                    # matches where the chart's fair-value line actually ends.
                    # Reading annual core EPS here while the line rode TTM put a
                    # headline fair value on screen the chart disagreed with.
                    _core      = _vdata.get("eps_core") or _vdata.get("eps") or [None]
                    _ttm_e     = _vdata.get("ttm_eps") or []
                    _core_last = (_ttm_e[-1] if _ttm_e else
                                  (_core[-1] if _core else None))
                    _eps_basis = "TTM EPS" if _ttm_e else "core (3-yr median) EPS"
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
                    # Verdict colour now comes from a CSS class, not inline hex,
                    # so the badge tracks the chart palette (see .vf-badge).
                    if _disc_pct is None:
                        _verd, _vcls = "—", "fair"
                    elif _disc_pct > 15:
                        _verd, _vcls = "Above its own history", "over"
                    elif _disc_pct < -15:
                        _verd, _vcls = "Below its own history", "under"
                    else:
                        _verd, _vcls = "In line with history", "fair"
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

                    _prem_s = f"{_disc_pct:+.0f}%" if _disc_pct is not None else "—"
                    _fcol, _mcol = st.columns([1, 3.2])
                    with _fcol:
                        st.markdown(f"""<div class="val-facts">
                          <div class="vf-group">Fast facts</div>
                          <div class="vf-row"><span>Current price</span><b>{_cur_s}</b></div>
                          <div class="vf-row"><span>Blended P/E</span><b>{_bpe_s}</b></div>
                          <div class="vf-row"><span>EPS yield</span><b>{_eps_s}</b></div>
                          <div class="vf-row"><span>Dividend yield</span><b>{_dy_s}</b></div>
                          <div class="vf-group">Valuation</div>
                          <div class="vf-row"><span>Normal P/E</span><span class="vf-pill value">{_npe:g}x</span></div>
                          <div class="vf-row"><span>Fair value</span><b>{_fair_s}</b></div>
                          <div class="vf-row"><span>Premium / discount</span><b>{_prem_s}</b></div>
                          <div class="vf-row"><span>Assessment</span><span class="vf-badge {_vcls}">{_verd}</span></div>
                          <div class="vf-group">Company</div>
                          <div class="vf-row"><span>Sector</span><b>{_sector}</b></div>
                          <div class="vf-row"><span>Market cap</span><b>{_mcap_s}</b></div>
                        </div>""", unsafe_allow_html=True)
                    with _mcol:
                        # Display window. Deliberately does NOT recompute the
                        # normal P/E — that is the stock's long-run multiple, and
                        # rebasing it per zoom level would redefine fair value
                        # every time the user changed the range.
                        _n_yrs   = len(_vdata["years"])
                        _ranges  = [("MAX", None), ("15Y", 15), ("10Y", 10),
                                    ("5Y", 5), ("3Y", 3), ("1Y", 1)]
                        _ranges  = [r for r in _ranges if r[1] is None or r[1] < _n_yrs]
                        _labels  = [r[0] for r in _ranges]
                        try:
                            _pick = st.segmented_control(
                                "Range", _labels, default="MAX", key="lens_range",
                                label_visibility="collapsed")
                        except Exception:
                            _pick = st.radio("Range", _labels, index=0, horizontal=True,
                                             key="lens_range", label_visibility="collapsed")
                        _yb = dict(_ranges).get(_pick or "MAX")

                        _tab_v, _tab_e, _tab_d = st.tabs(["Valuation", "Earnings", "Dividends"])
                        with _tab_v:
                            _vfig = build_valuation_figure(_vdata, years_back=_yb)
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
                        f"Fair value = {_eps_basis} × the stock's own historical normal "
                        f"P/E, from SEC-filed earnings"
                        + (", updated each quarter." if _ttm_e else ".")
                        + " A valuation lens, not a price target — always do your "
                          "own research.")
                    st.markdown("---")

            # ── Analyst View (Finnhub: consensus + earnings surprises) ────────
            if not is_crypto:
                _adata = cached_get_analyst_data(ticker_input)
                _rec, _earn = _adata.get("recommendation"), _adata.get("earnings")
                if _rec or _earn:
                    st.markdown(
                        f'<div class="section-header"'
                        f'{_sec_id("sec-analyst", "Analyst View")}>Analyst View '
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
                    supplement=_cached_fin_supplement(ticker_input),
                )
                if fund.get("ok"):

                    _n_yrs = len(_fin_raw["income_statement"]) if isinstance(_fin_raw, dict) and _fin_raw.get("income_statement") is not None else 0
                    _hist  = f"{_n_yrs}-yr history · " if _n_yrs > 1 else ""
                    st.markdown(
                        f'<div class="section-header"'
                        f'{_sec_id("sec-fundamentals", "Fundamentals")}>Fundamentals &amp; Valuation '
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
                            name="Revenue ($B)", marker_color=ct.color.brand,
                            marker_line_width=0, opacity=0.9))
                        fig_fund.add_trace(go.Bar(
                            x=_t["periods"], y=[(x or 0) / 1e9 for x in _t["net_income"]],
                            name="Net Income ($B)", marker_color=ct._rgba(ct.color.brand, 0.38),
                            marker_line_width=0))
                        fig_fund.add_trace(go.Scatter(
                            x=_t["periods"], y=_t["operating_margin"], name="Operating Margin (%)",
                            yaxis="y2", line=dict(color=ct.color.value_line, width=ct.stroke.value),
                            marker=dict(size=ct.marker.size, color=ct.marker.fill,
                                        line=dict(color=ct.color.value_line,
                                                  width=ct.marker.stroke_width)),
                            mode="lines+markers"))
                        ct.style(
                            fig_fund,
                            barmode="group", height=330, crosshair=False,
                            margin=dict(l=56, r=56, t=44, b=34),
                            x=ct.category_axis(),
                            y=ct.value_axis(prefix="", tick_format=",.1f", title="$ Billions"),
                            y2=dict(title="Op. Margin %", overlaying="y", side="right",
                                    showgrid=False,
                                    tickfont=dict(size=ct.font.size.axis,
                                                  color=ct.color.value_line,
                                                  family=ct.font.data),
                                    title_font=dict(size=ct.font.size.fact_label,
                                                    color=ct.color.value_line,
                                                    family=ct.font.data)),
                            title=dict(text="Revenue, Net Income & Operating Margin",
                                       font=dict(size=13, color=ct.color.ink,
                                                 family=ct.font.data),
                                       x=0, xanchor="left", y=0.97, yanchor="top"),
                        )
                        st.plotly_chart(fig_fund, use_container_width=True)

                    # Free cash flow trend (EDGAR-powered)
                    _tf = _t.get("fcf")
                    if _tf and any(x is not None for x in _tf):
                        fig_fcf = go.Figure()
                        fig_fcf.add_trace(go.Bar(
                            x=_t["periods"], y=[(x or 0) / 1e9 for x in _tf],
                            marker_color=[ct.color.positive if (x or 0) >= 0 else ct.color.negative
                                          for x in _tf],
                            marker_line_width=0,
                            name="Free Cash Flow ($B)",
                            hovertemplate="%{x}: $%{y:.1f}B<extra>Free Cash Flow</extra>"))
                        # zero=False + an explicit zero line: FCF is signed, so
                        # the meaningful reference is the axis crossing, not a
                        # floor pinned under the most negative year.
                        ct.style(
                            fig_fcf,
                            height=240, legend=None, crosshair=False,
                            margin=dict(l=56, r=20, t=38, b=34),
                            x=ct.category_axis(),
                            y=ct.value_axis(prefix="", tick_format=",.1f", zero=False,
                                            title="$ Billions", zeroline=True,
                                            zerolinecolor=ct.color.rule),
                            title=dict(text="Free Cash Flow",
                                       font=dict(size=13, color=ct.color.ink,
                                                 family=ct.font.data),
                                       x=0, xanchor="left"),
                        )
                        st.plotly_chart(fig_fcf, use_container_width=True)

                    # ── What's Priced In ──────────────────────────────────────
                    # Renders `_dcf_report` (analysis.dcf_valuation) — the same
                    # two-stage FCF model the Excel report ships. It replaces an
                    # older net-income reverse-DCF one-liner that quoted a
                    # different, less rigorous implied-growth number and showed
                    # none of the assumptions behind it.
                    #
                    # Every rate below is read from the report, never assumed:
                    # the WACC is CAPM-derived per company, so hardcoding a
                    # discount rate here would silently misstate the model.
                    _dcfr = _dcf_report if isinstance(_dcf_report, dict) else {"ok": False}





                    st.markdown(
                        f'<div class="section-header"'
                        f'{_sec_id("sec-priced-in", "What’s Priced In")}>What&rsquo;s Priced In '
                        '<span style="font-weight:500;color:var(--dim);letter-spacing:0;'
                        'text-transform:none;font-size:0.7rem">'
                        '· two-stage discounted free cash flow</span></div>',
                        unsafe_allow_html=True)

                    # Scoped styles. Colour, radius and rules all come from the
                    # design tokens in styles.css — no literals here, so this
                    # block moves with the palette instead of drifting from it.
                    # NOTE: flush-left, like every other raw-HTML f-string on
                    # this page (see the comment above the hero block).
                    st.markdown("""<style>
.wpi{font-family:var(--font-chart);font-variant-numeric:tabular-nums;color:var(--text)}
.wpi-lbl{font-size:10px;text-transform:uppercase;letter-spacing:0.08em;
color:var(--muted);font-weight:600;line-height:1.3}
.wpi-head{display:flex;align-items:flex-start;gap:1.8rem;flex-wrap:wrap;
border-top:1px solid var(--border);border-bottom:1px solid var(--border);
padding:1.05rem 0;margin:0 0 1.15rem}
.wpi-big{font-size:2.5rem;font-weight:600;line-height:1;letter-spacing:-0.02em;
margin-top:0.35rem;color:var(--accent);font-variant-numeric:tabular-nums}
.wpi-big.hot{color:var(--red)}
.wpi-big.cool{color:var(--green)}
.wpi-big.na{color:var(--muted);font-size:2rem}
.wpi-read{flex:1 1 320px;min-width:250px;align-self:center;
font-size:0.85rem;line-height:1.6;color:var(--text2)}
.wpi-read b{color:var(--text);font-weight:600;font-variant-numeric:tabular-nums}
.wpi-fv{display:flex;flex-wrap:wrap;margin:0 0 1.5rem;
border-top:1px solid var(--border);border-bottom:1px solid var(--border)}
.wpi-fv-c{flex:1 1 150px;padding:0.75rem 0 0.8rem 1.15rem;
border-left:1px solid var(--border)}
.wpi-fv-c:first-child{padding-left:0;border-left:none}
.wpi-fv-v{font-size:1.2rem;font-weight:500;margin-top:0.3rem;color:var(--text);
font-variant-numeric:tabular-nums}
.wpi-fv-v.pos{color:var(--green)}
.wpi-fv-v.neg{color:var(--red)}
.wpi-scroll{overflow-x:auto;margin:0.5rem 0 0.35rem}
.wpi-sens{width:100%;min-width:540px;border-collapse:collapse;
font-family:var(--font-chart);font-variant-numeric:tabular-nums;font-size:0.8rem}
.wpi-sens th,.wpi-sens td{padding:6px 10px;text-align:right;white-space:nowrap;
border:1px solid var(--border)}
.wpi-sens thead th{font-size:0.64rem;text-transform:uppercase;letter-spacing:0.07em;
color:var(--muted);font-weight:700;background:var(--surface2)}
/* Column hints, not table-layout:fixed — the grid should read as a grid rather
   than as one wide label column, but a long figure must never clip. */
.wpi-sens th.cnr,.wpi-sens th.rh{width:24%}
.wpi-sens td,.wpi-sens thead th:not(.cnr){width:15.2%}
.wpi-sens th.cnr{text-align:left;font-weight:600}
.wpi-sens th.rh{text-align:left;font-size:0.76rem;font-weight:600;
color:var(--muted);background:var(--surface2)}
.wpi-sens td{color:var(--text);font-weight:500}
.wpi-sens td.up{background:color-mix(in srgb,var(--green) 8%,transparent)}
.wpi-sens td.up2{background:color-mix(in srgb,var(--green) 18%,transparent)}
.wpi-sens td.dn{background:color-mix(in srgb,var(--red) 7%,transparent)}
.wpi-sens td.dn2{background:color-mix(in srgb,var(--red) 16%,transparent)}
.wpi-sens td.na{color:var(--muted);font-weight:400}
.wpi-sens td.mid{outline:1px solid var(--accent);outline-offset:-1px;font-weight:700}
.wpi-note{font-size:0.72rem;line-height:1.55;color:var(--muted);margin:0.4rem 0 0}
</style>""", unsafe_allow_html=True)

                    if not _dcfr.get("ok"):
                        # Say why, and say nothing was substituted for it. A
                        # silently missing section reads as a broken page.
                        _why = str(_dcfr.get("reason") or "not enough data").strip()
                        st.markdown(
                            f'<p class="wpi wpi-note" style="border-top:1px solid var(--border);'
                            f'padding-top:0.85rem">A discounted-cash-flow read isn&rsquo;t available '
                            f'for {ticker_input} — {_why}. Nothing has been estimated in its '
                            f'place.</p>',
                            unsafe_allow_html=True)
                    else:
                        _mig  = _dcfr.get("market_implied_growth")
                        _bg   = _dcfr.get("base_growth")
                        _wacc = _dcfr.get("wacc")
                        _tg   = _dcfr.get("terminal_growth")
                        _yrs  = _dcfr.get("years")
                        _px   = _dcfr.get("price")
                        _fv   = _dcfr.get("fair_value")
                        _up   = _dcfr.get("upside")
                        _hist = _g.get("eps_cagr")
                        _hzn  = f"{_yrs} years" if _yrs else "the forecast horizon"
                        _base_clause = (f" The model&rsquo;s own base case is <b>{_wpi_pct(_bg)}</b>."
                                        if _bg is not None else "")

                        # 1 ── Headline. Same three-way tone as before, restated
                        # for free cash flow: the DCF projects FCF, so comparing
                        # it to delivered EPS growth is the honest framing.
                        if _mig is None:
                            _tone = "na"
                            _big  = "—"
                            _read = ("Today&rsquo;s price sits outside the growth range this model can "
                                     "solve, so there is no single implied rate to quote."
                                     + _base_clause)
                        else:
                            _migp = _mig * 100
                            _big  = f"{_migp:.1f}%"
                            if _hist is not None and _migp > _hist + 3:
                                _tone = "hot"
                                _read = (f"To justify today&rsquo;s price the company has to compound free "
                                         f"cash flow at <b>{_migp:.1f}%</b> a year for {_hzn} — well above "
                                         f"the <b>{_hist:.1f}%</b> earnings growth it has actually "
                                         f"delivered. The price assumes growth accelerates from "
                                         f"here.{_base_clause}")
                            elif _hist is not None and _migp < _hist - 3:
                                _tone = "cool"
                                _read = (f"To justify today&rsquo;s price the company only has to compound "
                                         f"free cash flow at <b>{_migp:.1f}%</b> a year for {_hzn} — below "
                                         f"the <b>{_hist:.1f}%</b> earnings growth it has delivered. "
                                         f"Expectations look conservative.{_base_clause}")
                            elif _hist is not None:
                                _tone = ""
                                _read = (f"To justify today&rsquo;s price the company has to compound free "
                                         f"cash flow at <b>{_migp:.1f}%</b> a year for {_hzn} — roughly in "
                                         f"line with the <b>{_hist:.1f}%</b> earnings growth it has "
                                         f"delivered.{_base_clause}")
                            else:
                                _tone = ""
                                _read = (f"To justify today&rsquo;s price the company has to compound free "
                                         f"cash flow at <b>{_migp:.1f}%</b> a year for {_hzn}, discounted "
                                         f"at <b>{_wpi_pct(_wacc)}</b> with a <b>{_wpi_pct(_tg)}</b> "
                                         f"terminal rate.{_base_clause}")

                        _wpi = ['<div class="wpi">', '<div class="wpi-head">',
                                '<div><div class="wpi-lbl">Market-implied FCF growth</div>',
                                f'<div class="wpi-big {_tone}">{_big}</div></div>',
                                f'<div class="wpi-read">{_read}</div>', '</div>']

                        # 2 ── Fair value vs price.
                        _up_cls = "" if _up is None else ("pos" if _up >= 0 else "neg")
                        _up_lbl = "Downside to fair value" if (_up is not None and _up < 0) \
                                  else "Upside to fair value"
                        _wpi.append(
                            '<div class="wpi-fv">'
                            '<div class="wpi-fv-c"><div class="wpi-lbl">DCF fair value</div>'
                            f'<div class="wpi-fv-v">{_wpi_usd(_fv)}</div></div>'
                            '<div class="wpi-fv-c"><div class="wpi-lbl">Current price</div>'
                            f'<div class="wpi-fv-v">{_wpi_usd(_px)}</div></div>'
                            f'<div class="wpi-fv-c"><div class="wpi-lbl">{_up_lbl}</div>'
                            f'<div class="wpi-fv-v {_up_cls}">{_wpi_pct(_up, 1, sign=True)}</div>'
                            '</div></div>')

                        # 3 ── Scenarios. Reuses .fund-table so the row rhythm
                        # matches the fundamentals grid directly above.
                        _scn = _dcfr.get("scenarios") or {}
                        _wpi.append(
                            '<table class="fund-table">'
                            '<tr class="grp"><td>Scenario</td><td class="v">FCF growth</td>'
                            '<td class="v">Fair value</td><td class="v">Upside</td></tr>')
                        for _sk, _sn in (("bear", "Bear"), ("base", "Base"), ("bull", "Bull")):
                            _s   = _scn.get(_sk) or {}
                            _sup = _s.get("upside")
                            _scl = "" if _sup is None else ("pos" if _sup >= 0 else "neg")
                            _wpi.append(
                                f'<tr><td class="k">{_sn}</td>'
                                f'<td class="v">{_wpi_pct(_s.get("growth"))}</td>'
                                f'<td class="v">{_wpi_usd(_s.get("fair_value"))}</td>'
                                f'<td class="v {_scl}">{_wpi_pct(_sup, 1, sign=True)}</td></tr>')
                        _wpi.append('</table>')

                        # 4 ── Sensitivity. An HTML table, not a heatmap: the
                        # point of this grid is that you can read the numbers.
                        # The tint is a low-alpha mix of the semantic tokens so
                        # the figure on top of it stays legible.
                        _sen  = _dcfr.get("sensitivity") or {}
                        _wax  = _sen.get("wacc_axis") or []
                        _tax  = _sen.get("tg_axis") or []
                        _grid = _sen.get("grid") or []
                        if _wax and _tax and _grid:
                            _wmid = next((i for i, w in enumerate(_wax)
                                          if _wacc is not None and abs(w - _wacc) < 5e-5), None)
                            _tmid = next((i for i, t in enumerate(_tax)
                                          if _tg is not None and abs(t - _tg) < 5e-5), None)
                            _hdr  = "".join(f'<th>{_wpi_pct(_t)}</th>' for _t in _tax)
                            _body = []
                            for _ri, _w in enumerate(_wax):
                                _row   = _grid[_ri] if _ri < len(_grid) else []
                                _cells = []
                                for _ci in range(len(_tax)):
                                    _cv = _row[_ci] if _ci < len(_row) else None
                                    if _cv is None or not _px:
                                        _ccls, _ctxt = "na", "—"
                                    else:
                                        _d = _cv / _px - 1
                                        _ccls = ("up2" if _d >= 0.25 else "up" if _d > 0 else
                                                 "dn2" if _d <= -0.25 else "dn" if _d < 0 else "")
                                        _ctxt = _wpi_usd(_cv)
                                    if _ri == _wmid and _ci == _tmid:
                                        _ccls = (_ccls + " mid").strip()
                                    _cells.append(f'<td class="{_ccls}">{_ctxt}</td>')
                                _body.append(f'<tr><th class="rh">{_wpi_pct(_w)}</th>'
                                             + "".join(_cells) + '</tr>')
                            _wpi.append(
                                '<div class="wpi-lbl" style="margin:1.6rem 0 0.15rem">'
                                'Fair value sensitivity</div>'
                                '<div class="wpi-scroll"><table class="wpi-sens"><thead>'
                                f'<tr><th class="cnr">WACC &darr; &nbsp;/&nbsp; Terminal growth &rarr;</th>'
                                f'{_hdr}</tr></thead><tbody>' + "".join(_body)
                                + '</tbody></table></div>'
                                '<p class="wpi-note">Each cell is the fair value per share at that '
                                'discount rate and terminal growth rate. Green sits above '
                                f'today&rsquo;s price of {_wpi_usd(_px)}, red below it; the outlined '
                                'cell is the base case above.</p>')

                        # 5 ── Assumptions. The section is only as credible as
                        # the inputs it will show you, so they are shown.
                        _pvx, _pvt = _dcfr.get("pv_explicit"), _dcfr.get("pv_terminal")
                        _tshare = (_pvt / (_pvx + _pvt)
                                   if (_pvx is not None and _pvt is not None and (_pvx + _pvt))
                                   else None)
                        _shares = _dcfr.get("shares")
                        _eqv    = _dcfr.get("equity_value")

                        # Where the discount rate came from. This is the single
                        # most load-bearing assumption in the section — the same
                        # financials priced at a 0.55 beta vs a 1.35 beta imply
                        # -1.6% vs +13.8% growth — so it is shown, not asserted.
                        # A fallback rate is labelled as one rather than passed
                        # off as company-specific.
                        _wb = _dcfr.get("wacc_basis") or {}
                        if _wb.get("beta") is not None:
                            # The estimation window is part of the assumption:
                            # beta over 1y and over 5y are different numbers for
                            # the same company, so quoting one without saying
                            # which is incomplete.
                            _rate_basis = (f"CAPM &middot; &beta; {_wb['beta']:.2f} "
                                           f"({'selected range' if custom_range else period_label}"
                                           f" vs benchmark) &middot; "
                                           f"Rf {_wpi_pct(_wb.get('risk_free'), 2)} &middot; "
                                           f"ERP {_wpi_pct(_wb.get('erp'), 1)}")
                            _rate_extra = [
                                ("Cost of equity", _wpi_pct(_wb.get("cost_of_equity"), 2)),
                                ("Cost of debt (assumed)", _wpi_pct(_wb.get("cost_of_debt"), 2)),
                                ("Equity / debt weight",
                                 f"{_wpi_pct(_wb.get('equity_weight'))} / {_wpi_pct(_wb.get('debt_weight'))}"),
                            ]
                        else:
                            _rate_basis = ("Default rate &mdash; no benchmark selected, "
                                           "so no beta could be estimated")
                            _rate_extra = []

                        _wpi.append('<table class="fund-table" style="margin-top:1.5rem">')
                        for _grp, _items in (
                            ("Assumptions, stated openly", [
                                ("Discount rate (WACC)", _wpi_pct(_wacc, 2)),
                                ("How the rate was set", _rate_basis),
                                *_rate_extra,
                                ("Terminal growth",      _wpi_pct(_tg, 2)),
                                ("Forecast horizon",     f"{_yrs} years" if _yrs else "—"),
                                ("Base-case FCF growth", _wpi_pct(_bg, 2)),
                                ("Base free cash flow",  _wpi_mag(_dcfr.get("base_fcf"))),
                                ("Net debt",             _wpi_mag(_dcfr.get("net_debt"))),
                                ("Shares outstanding",   _wpi_cnt(_shares)),
                                ("Terminal share of value", _wpi_pct(_tshare)),
                            ]),
                            ("Value bridge", [
                                (f"PV of years 1&ndash;{_yrs}" if _yrs else "PV of forecast years",
                                 _wpi_mag(_pvx)),
                                ("PV of terminal value",  _wpi_mag(_pvt)),
                                ("Terminal value (undiscounted)",
                                 _wpi_mag(_dcfr.get("terminal_value"))),
                                ("Enterprise value",      _wpi_mag(_dcfr.get("enterprise_value"))),
                                ("Equity value",          _wpi_mag(_eqv)),
                                ("Equity value / share",
                                 _wpi_usd(_eqv / _shares) if (_eqv is not None and _shares) else "—"),
                            ]),
                        ):
                            _wpi.append(f'<tr class="grp"><td colspan="4">{_grp}</td></tr>')
                            for _i in range(0, len(_items), 2):
                                _cells = ""
                                for _j in range(2):
                                    if _i + _j < len(_items):
                                        _k, _val = _items[_i + _j]
                                        _cells += (f'<td class="{"k pair2" if _j else "k"}">{_k}</td>'
                                                   f'<td class="v">{_val}</td>')
                                    else:
                                        _cells += '<td class="k"></td><td class="v"></td>'
                                _wpi.append(f'<tr>{_cells}</tr>')
                        _wpi.append('</table></div>')
                        st.markdown("".join(_wpi), unsafe_allow_html=True)
                        st.caption(
                            "Two-stage DCF on free cash flow: stage-one growth fades linearly to "
                            "the terminal rate over the horizon, discounted at the company's own "
                            "CAPM cost of capital, then bridged from enterprise value to equity "
                            "with net debt. Market-implied growth is the same model solved "
                            "backwards from today's price. A lens on expectations, not a price "
                            "target — always do your own research.")

            # ── ETF Profile Panel ─────────────────────────────────────────────
            if is_etf:
                meta     = etf_details.get("meta", {})
                holdings = etf_details.get("holdings", [])
                if meta or holdings:
                    st.markdown(f'<div class="section-header"'
                                f'{_sec_id("sec-etf", "ETF Profile")}>ETF Profile</div>',
                                unsafe_allow_html=True)
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
                                marker_color=ct.color.brand, marker_line_width=0,
                                text=[f"{w:.1f}%" for _, w in holdings],
                                textposition="outside",
                                textfont=dict(size=ct.font.size.grid, family=ct.font.data,
                                              color=ct.color.ink_muted),
                            ))
                            # Horizontal bars: the value axis is x here, so the
                            # gridlines belong to x and the category axis is y.
                            ct.style(
                                fig_h,
                                height=300, legend=None, crosshair=False,
                                margin=dict(l=64, r=32, t=38, b=34),
                                x=ct.pct_axis(tick_format=".1f", title="Weight (%)", zero=True),
                                y=ct.category_axis(autorange="reversed"),
                                title=dict(text="Top Holdings by Weight",
                                           font=dict(size=13, color=ct.color.ink,
                                                     family=ct.font.data),
                                           x=0, xanchor="left"),
                            )
                            st.plotly_chart(fig_h, use_container_width=True)

            # ── Crypto Market Data Panel ──────────────────────────────────────
            if is_crypto and crypto_details:
                st.markdown(f'<div class="section-header"'
                            f'{_sec_id("sec-market-data", "Market Data")}>Market Data</div>',
                            unsafe_allow_html=True)
                cc1, cc2, cc3, cc4, cc5, cc6 = st.columns(6)
                mc_usd   = crypto_details.get("market_cap_usd", 0)
                vol_24h  = crypto_details.get("volume_24h", 0)
                ath_val  = crypto_details.get("ath", 0)
                ath_pct  = crypto_details.get("ath_pct", 0)
                p7d      = crypto_details.get("price_change_7d", 0)
                p30d     = crypto_details.get("price_change_30d", 0)
                circ     = crypto_details.get("circulating_supply", 0)
                max_sup  = crypto_details.get("max_supply", 0)


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
            # for themselves — the jump rail still needs a target, hence the
            # zero-height anchor rather than a heading.
            _jump_anchor("sec-chart", "Price Chart")
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

            # ── Range control ────────────────────────────────────────────
            # This used to be Plotly's in-plot `rangeselector`, which changes the
            # x-range CLIENT-side and leaves the y-range alone. On a 15-year
            # series that made every short window unreadable: picking 1Y kept a
            # y-axis spanning $0-$340 while the year's prices lived between $210
            # and $340, so two thirds of the panel was empty fill and the actual
            # movement was squeezed into the top third. (Yahoo's 1Y view spans
            # 225-350 — fitted to the window, which is the whole point of
            # choosing a window.)
            #
            # Slicing the frame here instead means the y-axis autoranges over
            # exactly what is on screen, the tick format can suit the span, and
            # the rendered figure is the whole truth — no client-side state that
            # a screenshot or an export would miss.
            _RANGES = [("1D", 1), ("5D", 5), ("1M", 31), ("3M", 92), ("6M", 183),
                       ("1Y", 365), ("3Y", 1095), ("All", None)]
            _span_days = (pd.to_datetime(df["Date"].iloc[-1])
                          - pd.to_datetime(df["Date"].iloc[0])).days
            # A button earns its place only if it shows LESS than the whole
            # series; otherwise it is "All" under another name.
            _range_opts = [lbl for lbl, d in _RANGES
                           if d is None or _span_days > d * 1.15]
            # The widget key persists across tickers. Analysing a 15-year name
            # and then a recent listing would leave "3Y" selected on a control
            # that no longer offers it, which Streamlit treats as an error
            # rather than a fallback — so clear a selection this ticker can't
            # honour before the widget is built.
            if st.session_state.get("main_chart_range") not in _range_opts:
                st.session_state.pop("main_chart_range", None)
            st.markdown('<div class="field-label" style="margin-top:0.35rem">Range</div>',
                        unsafe_allow_html=True)
            _range_sel = st.radio("Range", _range_opts,
                                  index=len(_range_opts) - 1, horizontal=True,
                                  key="main_chart_range", label_visibility="collapsed")
            _range_days = dict(_RANGES)[_range_sel]
            if _range_days is None:
                _cdf = df
            else:
                _cutoff = pd.to_datetime(df["Date"].iloc[-1]) - pd.Timedelta(days=_range_days)
                _cdf = df[pd.to_datetime(df["Date"]) >= _cutoff]
                if len(_cdf) < 2:
                    # A single-session range legitimately slices to one daily row.
                    # Falling back to the FULL history here (which is what this
                    # guard used to do) handed the y-range a 200-day average
                    # spanning fifteen years, so a day that traded $322-$327 was
                    # drawn on a $280-$330 axis. Keep just enough daily rows to
                    # read a previous close from.
                    _cdf = df.tail(10)
            # Tick density and label shape follow the window: "12 Mar" reads
            # wrong across fifteen years and "'26" reads wrong across one month.
            _shown_days = (pd.to_datetime(_cdf["Date"].iloc[-1])
                           - pd.to_datetime(_cdf["Date"].iloc[0])).days
            _tickfmt = ("%d %b" if _shown_days <= 190 else
                        "%b '%y" if _shown_days <= 1200 else "%Y")


            # ── Drawing frame ────────────────────────────────────────────────
            # `_cdf` is daily and stays that way: every statistic on this page is
            # computed from daily bars and hard-codes it, from Close.rolling(200)
            # to ret.std() * sqrt(252). Handing those hourly bars would silently
            # turn MA 200 into 200 HOURS - about 29 trading days - understate
            # annualised volatility by roughly sqrt(7), and carry that error into
            # Sharpe, Sortino, beta and the forecast. Every number would change
            # and every one would still look plausible.
            #
            # So the denser series is for the LINE ONLY. `_pdf` is what gets
            # drawn; `_cdf` remains what everything else measures. Moving
            # averages below are deliberately still plotted from `_cdf`, so
            # "MA 50" keeps meaning fifty days on a chart drawn hourly.
            #
            # Measured on AAPL: a month goes from 23 points to 154. Hourly is
            # capped at 6M because the provider only serves ~730 days of it, and
            # beyond a year the daily series is already 250+ points - dense
            # enough that more would cost payload for no visible gain. 5-minute
            # bars were rejected outright: 1,716 points for one month is 75x the
            # payload for a curve indistinguishable at this width.
            # Interval per range, the ladder every finance site climbs: fine bars
            # over a short window, coarse bars over a long one. The provider's own
            # ceilings decide where it stops - 1-minute is 8 days per request,
            # 5/15/30-minute is the last 60 days, hourly is ~730 - so past a year
            # there is nothing finer than daily to be had, and daily is already
            # 250+ points by then.
            #
            # `_lookback` is how far back to ASK. A day's chart cannot request a
            # single day: ask for one and a weekend or a holiday returns nothing,
            # so it asks for a week and keeps the last session.
            _DENSE_FOR = {
                "1D": ("1min",  7),
                "5D": ("5min",  9),
                "1M": ("30min", 33),
                "3M": ("1hour", 95),
                "6M": ("1hour", 186),
            }
            _pdf = _cdf
            _sessions = 0          # >0 means "keep only the last N sessions"
            _dense_iv = None
            if _range_sel in _DENSE_FOR:
                _dense_iv, _lookback = _DENSE_FOR[_range_sel]
                _anchor = pd.to_datetime(df["Date"].iloc[-1])
                _d0 = (_anchor - pd.Timedelta(days=_lookback)).strftime("%Y-%m-%d")
                _d1 = (_anchor + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                _dense = _cached_dense_bars(ticker_input, _d0, _d1, _dense_iv)
                if _dense is not None and len(_dense) > 0:
                    _sessions = {"1D": 1, "5D": 5}.get(_range_sel, 0)
                    if _sessions:
                        # Trim to whole sessions rather than a rolling window, so
                        # "1D" is a trading day and not the last 24 hours.
                        _days = pd.to_datetime(_dense["Date"]).dt.normalize()
                        _keep = sorted(_days.unique())[-_sessions:]
                        _dense = _dense[_days.isin(_keep)]
                    if len(_dense) > len(_cdf) or _sessions:
                        _pdf = _dense
                else:
                    _dense_iv = None          # fell back to daily; no rangebreaks

            # Labels follow the window, not the calendar: a single session wants
            # clock time, a week wants weekday and time, a decade wants years.
            if _range_sel == "1D":
                _tickfmt = "%H:%M"
            elif _range_sel == "5D":
                _tickfmt = "%a %H:%M"

            # US regular session is 09:30-16:00 Eastern, and the provider returns
            # bars stamped in that zone.
            _rangebreaks = []
            if _dense_iv:
                _rangebreaks = [
                    dict(bounds=["sat", "mon"]),
                    dict(bounds=[16, 9.5], pattern="hour"),
                ]
            elif _range_sel in ("1M", "3M", "6M", "1Y"):
                # Daily bars still leave weekend holes worth closing.
                _rangebreaks = [dict(bounds=["sat", "mon"])]

            fig = go.Figure()

            # ── Price — line / area / candle depending on selection ───────────
            if _chart_type == "Candlestick" and {"Open","High","Low","Close"}.issubset(_pdf.columns):
                fig.add_trace(go.Candlestick(
                    x=_pdf["Date"], open=_pdf["Open"], high=_pdf["High"],
                    low=_pdf["Low"], close=_pdf["Close"],
                    name="Price",
                    increasing_line_color=ct.color.positive, decreasing_line_color=ct.color.negative,
                    increasing_fillcolor=ct.color.positive, decreasing_fillcolor=ct.color.negative,
                ))
            elif _chart_type == "Line":
                fig.add_trace(go.Scatter(
                    x=_pdf["Date"], y=_pdf["Close"],
                    name="Price",
                    line=dict(color=ct.color.ink, width=ct.stroke.price),
                    hovertemplate="$%{y:,.2f}<extra>Price</extra>",
                ))
            else:  # Area (default)
                # "tozeroy", not an invisible base trace at the series minimum.
                # That base trace spanned the full width at a constant y, so it
                # dragged the axis down to the all-time low no matter what was
                # selected. tozeroy fills to the bottom of the plot and is
                # clipped there, which looks identical and constrains nothing.
                fig.add_trace(go.Scatter(
                    x=_pdf["Date"], y=_pdf["Close"],
                    name="Price",
                    line=dict(color=ct.color.ink, width=ct.stroke.price),
                    fill="tozeroy",
                    # A brand tint, not an ink tint: ink at 5% on white renders as
                    # flat grey, which is the exact bland look this redesign is
                    # meant to remove.
                    fillcolor=ct._rgba(ct.color.brand, 0.06),
                    hovertemplate="$%{y:,.2f}<extra>Price</extra>",
                ))

            # ── Moving averages — gated by checkboxes ─────────────────────────
            _ma_cfg = [
                # Moving averages are supporting series: hairline, and separated
                # mostly by dash length rather than by colour. Three saturated
                # hues here would compete with the price line for attention.
                (20,  ct.color.ink_faint,  1.0, "dot",      "MA 20",  _show_ma20),
                (50,  ct.color.value_line, 1.0, "dash",     "MA 50",  _show_ma50),
                (200, ct.color.brand,      1.0, "longdash", "MA 200", _show_ma200),
            ]
            # A daily moving average has one value per day, so across a single
            # session it is a single point and across a week it is five - a
            # stub, not a trend. Yahoo shows no averages on its intraday chart
            # either. They return with the ranges that span enough days to draw
            # one honestly.
            _ma_ok = _range_sel not in ("1D", "5D") and len(_cdf) >= 10
            for ma, color, width, dash, label, enabled in _ma_cfg:
                if _ma_ok and enabled and f"MA{ma}" in _cdf.columns:
                    fig.add_trace(go.Scatter(
                        x=_cdf["Date"], y=_cdf[f"MA{ma}"],
                        name=label,
                        line=dict(color=color, width=width, dash=dash),
                        opacity=0.9,
                        hovertemplate=f"$%{{y:,.2f}}<extra>MA {ma}</extra>",
                    ))

            # ── Volume bars on secondary axis (optional) ──────────────────────
            if _show_vol and "Volume" in _cdf.columns:
                _vol_colors = ["#059669" if c >= o else "#dc2626"
                               for c, o in zip(_pdf["Close"], _pdf["Open"])] \
                              if "Open" in _pdf.columns else "#94a3b8"
                fig.add_trace(go.Bar(
                    x=_pdf["Date"], y=_pdf["Volume"],
                    name="Volume", marker_color=_vol_colors,
                    opacity=0.35, yaxis="y2",
                    hovertemplate="%{y:,.0f}<extra>Volume</extra>",
                ))

            # ── Visible y-range ──────────────────────────────────────────────
            # Set explicitly rather than left to autorange, for one reason:
            # "tozeroy" counts the fill's own extent, so an area chart always
            # autoranges down to $0 however narrow the price band is. That is
            # what left the 1Y view spanning $0-$340 for a year that traded
            # between $226 and $340. The range covers every series actually
            # drawn — the price, whichever moving averages are switched on, and
            # the candle wicks — so nothing plotted can fall outside it.
            _y_series = [_pdf["Close"]]
            if _chart_type == "Candlestick" and {"High", "Low"}.issubset(_pdf.columns):
                _y_series += [_pdf["High"], _pdf["Low"]]
            # `_ma_ok` gates this too. The axis should describe what is on the
            # chart and nothing else: including an average that was never plotted
            # is what zoomed a quiet day out to a fifteen-year price band.
            for _ma, _en in ((20, _show_ma20), (50, _show_ma50), (200, _show_ma200)):
                if _ma_ok and _en and f"MA{_ma}" in _cdf.columns:
                    _y_series.append(_cdf[f"MA{_ma}"].dropna())
            _y_min = min(float(s.min()) for s in _y_series if len(s))
            _y_max = max(float(s.max()) for s in _y_series if len(s))
            # The reference line is part of the picture: a day that gapped up and
            # never looked back would otherwise push it off the top of the plot,
            # leaving the move to be read against nothing.
            if _range_sel == "1D" and len(_cdf) >= 2:
                _pc = float(_cdf["Close"].iloc[-2])
                _y_min, _y_max = min(_y_min, _pc), max(_y_max, _pc)
            _y_pad = max((_y_max - _y_min) * 0.06, _y_max * 0.005)
            # A price axis never goes below zero, however much padding the band
            # asks for — a floor of -$8 under a 15-year chart is nonsense.
            _y_floor = max(0.0, _y_min - _y_pad)
            _y_ceil  = _y_max + _y_pad

            if _show_sr and resistance:
                # Only show resistance levels INSIDE chart range and above current price
                _res_above = sorted(
                    [r for r in resistance if _y_floor < r < _y_ceil],
                    reverse=True,
                )[:2]
                for _i, r in enumerate(_res_above):
                    fig.add_shape(type="line", x0=0, x1=1, xref="paper",
                                  y0=r, y1=r,
                                  line=dict(color=ct.color.negative, width=1, dash="dash"),
                                  opacity=0.4, layer="below")
                    fig.add_annotation(
                        x=0.006, xref="paper", y=r, yref="y",
                        text=f"Resist ${r:,.0f}",
                        showarrow=False, xanchor="left", yshift=8,
                        font=dict(color=ct.color.negative, size=10, family=ct.font.data),
                        bgcolor="rgba(255,255,255,0.9)",
                        bordercolor=ct._rgba(ct.color.negative, 0.35), borderwidth=1,
                        borderpad=3,
                    )

            if _show_sr and support:
                _sup_below = sorted(
                    [s for s in support if _y_floor < s < _y_ceil]
                )[:2]
                for _i, s in enumerate(_sup_below):
                    fig.add_shape(type="line", x0=0, x1=1, xref="paper",
                                  y0=s, y1=s,
                                  line=dict(color=ct.color.positive, width=1, dash="dash"),
                                  opacity=0.4, layer="below")
                    fig.add_annotation(
                        x=0.006, xref="paper", y=s, yref="y",
                        text=f"Support ${s:,.0f}",
                        showarrow=False, xanchor="left", yshift=8,
                        font=dict(color=ct.color.positive, size=10, family=ct.font.data),
                        bgcolor="rgba(255,255,255,0.9)",
                        bordercolor=ct._rgba(ct.color.positive, 0.35), borderwidth=1,
                        borderpad=3,
                    )

            # ── Previous close, on the single-session view ────────────────────
            # A day's move is meaningless without the level it moved from, which
            # is why every intraday chart draws this line. The value comes from
            # the DAILY frame - the close before the session on screen - not from
            # the intraday bars, which start at the open.
            if _range_sel == "1D" and len(_cdf) >= 2:
                _prev_close = float(_cdf["Close"].iloc[-2])
                fig.add_hline(
                    y=_prev_close, line_dash="dot", line_width=1,
                    line_color=ct.color.ink_muted, opacity=0.55,
                    annotation_text=f"Prev close ${_prev_close:,.2f}",
                    annotation_position="top left",
                    annotation_font=dict(size=10, color=ct.color.ink_muted,
                                         family=ct.font.data),
                )

            # ── Current price tag ─────────────────────────────────────────────
            if _show_tag:
                _last = _pdf["Close"].iloc[-1]
                fig.add_shape(type="line", x0=0, x1=1, xref="paper",
                              y0=_last, y1=_last,
                              line=dict(color=ct.color.ink_muted, width=1, dash="dot"),
                              opacity=0.7, layer="above")
                fig.add_annotation(
                    # Past the right-hand tick labels, not on top of them.
                    x=1.0, xref="paper", y=_last, yref="y", xshift=56,
                    text=f"<b>${_last:,.2f}</b>",
                    showarrow=False, xanchor="left",
                    font=dict(color=ct.color.paper, size=11, family=ct.font.data),
                    bgcolor=ct.color.ink,
                    borderpad=4,
                )

            ct.style(
                fig,
                height=480,
                # The price axis is on the RIGHT, so the left gutter is only
                # breathing room. The right margin carries the axis (~54px) and
                # the last-price tag beyond it. The range buttons used to live
                # inside the plot and cost 70px of top margin; they are now a
                # Streamlit control above the chart, and that height goes to the
                # plot instead.
                margin=dict(l=24, r=118, t=28, b=48),
                x=ct.time_axis(
                    fy_ticks=False, title=None, tickformat=_tickfmt,
                    nticks=8, automargin=True,
                    rangeslider=dict(visible=False),
                    # Give no width to time the market was shut. Without this an
                    # intraday chart spends most of its axis on nights and
                    # weekends: a 1-minute day is 390 minutes of trading inside a
                    # 1,440-minute box, so the price action gets a quarter of the
                    # plot and the line leaps across the gaps. Weekends go for
                    # every dense range; the overnight bound only applies where
                    # bars are intraday, since daily bars have no hours to hide.
                    rangebreaks=_rangebreaks,
                ),
                y=ct.value_axis(tick_format=",.2f", zero=False, title=None,
                                nticks=6, side="right", automargin=True,
                                range=[_y_floor, _y_ceil]),
                # namelength=-1 keeps long trace names from being truncated in the
                # unified tooltip.
                hoverlabel=dict(namelength=-1, **ct.hover()),
            )
            if _show_vol and "Volume" in _cdf.columns:
                fig.update_layout(yaxis2=dict(
                    title=None, overlaying="y", side="left",   # hidden; keeps off the price axis
                    showgrid=False, showticklabels=False,
                    range=[0, float(_cdf["Volume"].max() * 5)],
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
                _jump_anchor("sec-indicator", "Indicators")
                st.markdown('<div class="field-label" style="margin-top:0.5rem">Indicator</div>',
                            unsafe_allow_html=True)
                _ind_view = st.radio("Indicator", _ind_opts, horizontal=True,
                                     key="tech_indicator", label_visibility="collapsed")

            if _ind_view == "RSI" and "RSI14" in df.columns:
                st.markdown('<div class="section-header">RSI (14)</div>', unsafe_allow_html=True)
                fig_rsi = go.Figure()
                fig_rsi.add_trace(go.Scatter(x=df["Date"], y=df["RSI14"],
                                             line=dict(color=ct.color.ink, width=ct.stroke.price), name="RSI",
                                             hovertemplate="RSI: %{y:.1f}<extra></extra>"))
                fig_rsi.add_hrect(y0=70, y1=100, fillcolor="rgba(239,68,68,0.08)", line_width=0)
                fig_rsi.add_hrect(y0=0, y1=30, fillcolor="rgba(22,163,74,0.08)", line_width=0)
                fig_rsi.add_hline(y=70, line_dash="dash", line_color=ct.color.negative, line_width=1, opacity=0.6)
                fig_rsi.add_hline(y=50, line_dash="dot",  line_color=ct.color.ink_muted, line_width=1, opacity=0.5)
                fig_rsi.add_hline(y=30, line_dash="dash", line_color=ct.color.positive, line_width=1, opacity=0.6)
                # Anchor zone labels INSIDE the plot at the left edge so they
                # don't float in the right margin.
                fig_rsi.add_annotation(
                    xref="paper", x=0.005, y=85, text="Overbought",
                    showarrow=False, xanchor="left",
                    font=dict(size=10, color=ct.color.negative, family=ct.font.data),
                )
                fig_rsi.add_annotation(
                    xref="paper", x=0.005, y=15, text="Oversold",
                    showarrow=False, xanchor="left",
                    font=dict(size=10, color=ct.color.positive, family=ct.font.data),
                )
                # Margins match the price chart above so the two plot areas line
                # up: a stacked indicator whose x-axis is offset from the price
                # it explains is worse than no indicator at all.
                ct.style(
                    fig_rsi,
                    height=200,
                    margin=dict(l=52, r=112, t=20, b=30),
                    legend=None,
                    x=ct.time_axis(fy_ticks=False, title=None, tickformat="%b '%y"),
                    y=ct.plain_axis(range=[0, 100], tickvals=[30, 50, 70], title=None),
                )
                st.plotly_chart(fig_rsi, use_container_width=True)

            if _ind_view == "Bollinger" and "BB_Upper" in df.columns:
                st.markdown('<div class="section-header">Bollinger Bands</div>', unsafe_allow_html=True)
                fig_bb = go.Figure()
                fig_bb.add_trace(go.Scatter(x=df["Date"], y=df["BB_Upper"],
                                            line=dict(color=ct.color.ink_muted, width=1), name="Upper Band"))
                fig_bb.add_trace(go.Scatter(x=df["Date"], y=df["BB_Lower"],
                                            line=dict(color=ct.color.ink_muted, width=1), name="Lower Band",
                                            fill="tonexty", fillcolor="rgba(147,197,253,0.15)"))
                fig_bb.add_trace(go.Scatter(x=df["Date"], y=df["BB_Middle"],
                                            line=dict(color=ct.color.value_line, width=ct.stroke.price, dash="dash"), name="Middle (SMA)"))
                fig_bb.add_trace(go.Scatter(x=df["Date"], y=df["Close"],
                                            line=dict(color=ct.color.ink, width=ct.stroke.price), name="Price",
                                            hovertemplate="$%{y:,.2f}<extra>Price</extra>"))
                ct.style(
                    fig_bb,
                    height=320,
                    margin=dict(l=52, r=112, t=40, b=30),
                    x=ct.time_axis(fy_ticks=False, title=None, tickformat="%b '%y"),
                    y=ct.value_axis(tick_format=",.2f", zero=False, title=None),
                )
                st.plotly_chart(fig_bb, use_container_width=True)

            if _ind_view == "MACD" and "MACD" in df.columns:
                st.markdown('<div class="section-header">MACD</div>', unsafe_allow_html=True)
                fig_macd = go.Figure()
                fig_macd.add_trace(go.Scatter(
                    x=df["Date"], y=df["MACD"], name="MACD",
                    line=dict(color=ct.color.ink, width=ct.stroke.price),
                    hovertemplate="MACD: %{y:.3f}<extra></extra>"))
                fig_macd.add_trace(go.Scatter(
                    x=df["Date"], y=df["MACD_Signal"], name="Signal",
                    line=dict(color=ct.color.value_line, width=ct.stroke.price),
                    hovertemplate="Signal: %{y:.3f}<extra></extra>"))
                _hist_colors = [ct.color.positive if (v or 0) >= 0 else ct.color.negative
                                for v in df["MACD_Hist"].fillna(0)]
                fig_macd.add_trace(go.Bar(
                    x=df["Date"], y=df["MACD_Hist"], name="Histogram",
                    marker_color=_hist_colors, opacity=0.5,
                    hovertemplate="Hist: %{y:.3f}<extra></extra>"))
                ct.style(
                    fig_macd,
                    height=240,
                    margin=dict(l=52, r=112, t=20, b=30),
                    x=ct.time_axis(fy_ticks=False, title=None, tickformat="%b '%y"),
                    y=ct.plain_axis(title=None, zeroline=True,
                                    zerolinecolor=ct.color.rule),
                )
                st.plotly_chart(fig_macd, use_container_width=True)

            if mc_summary:
                _header    = "Monte Carlo Forecast"
                st.markdown(f'<div class="section-header"'
                            f'{_sec_id("sec-forecast", "Forecast")}>{_header}</div>',
                            unsafe_allow_html=True)

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
                _r2 = st.columns(len(_r2_items))
                for col, (_lbl, _val, _clr) in zip(_r2, _r2_items):
                    with col:
                        st.markdown(f"""
                        <div class="metric-card">
                            <div class="metric-label">{_lbl}</div>
                            <div class="metric-value" style="color:{_clr}">{_val}</div>
                        </div>""", unsafe_allow_html=True)

                # ── Simulated price-path fan chart ────────────────────────────
                # Percentiles over ALL paths, not the first 200. The metric cards
                # above use the full set, so sampling here made the fan's endpoints
                # disagree with the Bear/Median/Best figures printed right above it.
                if mc_sim_df.empty or mc_sim_df.shape[1] == 0:
                    st.warning("Monte Carlo simulation produced no paths.")
                    pcts = None
                else:
                    pcts = np.percentile(mc_sim_df.values, [5,25,50,75,95], axis=1)
                if pcts is not None:
                    x      = list(range(len(pcts[0])))
                    fig_mc = go.Figure()
                    fig_mc.add_trace(go.Scatter(x=x, y=pcts[4], name="P95",
                                                line=dict(color=ct.color.positive, width=1.25),
                                                hovertemplate="Day %{x} — Best: $%{y:,.2f}<extra></extra>"))
                    fig_mc.add_trace(go.Scatter(x=x, y=pcts[3], name="P75",
                                                line=dict(color=ct.color.brand, width=1),
                                                fill="tonexty", fillcolor=ct._rgba(ct.color.brand, 0.10),
                                                hovertemplate="Day %{x} — Bull: $%{y:,.2f}<extra></extra>"))
                    fig_mc.add_trace(go.Scatter(x=x, y=pcts[2], name="Median",
                                                line=dict(color=ct.color.ink, width=1.75),
                                                hovertemplate="Day %{x} — Median: $%{y:,.2f}<extra></extra>"))
                    fig_mc.add_trace(go.Scatter(x=x, y=pcts[1], name="P25",
                                                line=dict(color=ct.color.brand, width=1),
                                                fill="tonexty", fillcolor=ct._rgba(ct.color.brand, 0.06),
                                                hovertemplate="Day %{x} — Low: $%{y:,.2f}<extra></extra>"))
                    fig_mc.add_trace(go.Scatter(x=x, y=pcts[0], name="P5",
                                                line=dict(color=ct.color.negative, width=1.25),
                                                hovertemplate="Day %{x} — Bear: $%{y:,.2f}<extra></extra>"))
                    # Legend moves OUTSIDE the plot: a legend floating over the
                    # data in a white box covers the fan it is describing.
                    ct.style(
                        fig_mc,
                        height=370,
                        margin=dict(l=52, r=20, t=30, b=40),
                        legend="top-left",
                        x=ct.linear_axis(title="Trading Days",
                                         tickvals=[0, 50, 100, 150, 200, 250],
                                         tickformat=",d"),
                        y=ct.value_axis(zero=False, title=None),
                    )
                    st.plotly_chart(fig_mc, use_container_width=True)


            st.markdown(f'<div class="section-header"'
                        f'{_sec_id("sec-volume", "Volume")}>Volume</div>',
                        unsafe_allow_html=True)
            vol_colors = [ct.color.positive if r >= 0 else ct.color.negative
                          for r in df["Daily_Return"].fillna(0)]
            fig_vol = go.Figure()
            fig_vol.add_trace(go.Bar(x=df["Date"], y=df["Volume"], marker_color=vol_colors, opacity=0.85,
                                     name="Volume",
                                     hovertemplate="<b>%{x|%b %d, %Y}</b><br>Volume: %{y:,.0f}<extra></extra>"))
            if "Volume" in df.columns:
                _vol_ma20 = df["Volume"].rolling(20, min_periods=5).mean()
                fig_vol.add_trace(go.Scatter(
                    x=df["Date"], y=_vol_ma20, name="20d Avg",
                    line=dict(color=ct.color.brand, width=1.25, dash="dot"),
                    hovertemplate="20d Avg: %{y:,.0f}<extra></extra>",
                ))
            # No in-chart title: the "Volume" section header directly above
            # already says it, and printing it twice is just noise.
            ct.style(
                fig_vol,
                height=260,
                margin=dict(l=52, r=112, t=26, b=30),
                x=ct.time_axis(fy_ticks=False, title=None, tickformat="%b '%y"),
                y=ct.value_axis(prefix="", tick_format=".2s", title=None),
            )
            st.plotly_chart(fig_vol, use_container_width=True)

            if corr_matrix is not None:
                st.markdown(f'<div class="section-header"'
                            f'{_sec_id("sec-correlation", "Correlation")}>Correlation Matrix</div>',
                            unsafe_allow_html=True)
                # Square cells are kept deliberately (px.imshow's default): a
                # correlation matrix is read as a grid, and stretching the cells
                # to the container width turns it into something that scans as a
                # stacked bar. The problem was never the aspect, it was the
                # container — a ~300px square was being centred in ~900px of
                # column, so the matrix looked stranded and its x labels sat on
                # the bottom edge. Narrower column below, taller margin here.
                fig_corr = px.imshow(
                    corr_matrix,
                    text_auto=".2f",
                    color_continuous_scale=ct.color.diverging,
                    zmin=-1, zmax=1,
                    aspect="equal",
                )
                fig_corr.update_traces(
                    xgap=2, ygap=2,
                    hovertemplate="<b>%{x} vs %{y}</b><br>Correlation: %{z:.2f}<extra></extra>",
                    textfont=dict(size=11, family=ct.font.data),
                )
                ct.style(
                    fig_corr,
                    height=300,
                    # r=96, not 20. The colorbar and its tick labels live in the
                    # right margin, and aspect="equal" shrinks the plot area to a
                    # square — so Plotly places the bar at 1.02 of the SQUARE,
                    # which lands further right than a naive margin allows for.
                    # Measured in the browser: the "-1.0" label overflowed the
                    # 543px figure by 14px at r=72. This leaves ~24px of slack.
                    margin=dict(l=64, r=96, t=20, b=48),
                    legend=None, grid=False, crosshair=False,
                    x=ct.category_axis(tickfont=dict(size=12, color=ct.color.ink,
                                                     family=ct.font.data),
                                       automargin=True),
                    y=ct.category_axis(tickfont=dict(size=12, color=ct.color.ink,
                                                     family=ct.font.data),
                                       automargin=True),
                    coloraxis_colorbar=dict(
                        # No title: the section header two lines up already says
                        # "Correlation Matrix", and in the narrower column the
                        # word clipped to "Correlati…" against the right edge.
                        tickvals=[-1, -0.5, 0, 0.5, 1],
                        ticktext=["-1.0", "-0.5", "0.0", "0.5", "1.0"],
                        tickfont=dict(size=11, color=ct.color.ink_muted,
                                      family=ct.font.data),
                        title_font=dict(size=12, color=ct.color.ink_muted,
                                        family=ct.font.data),
                        thickness=14, len=0.8,
                    ),
                )
                _cm_l, _cm_mid, _cm_r = st.columns([1, 2.2, 1])
                with _cm_mid:
                    st.plotly_chart(fig_corr, use_container_width=True)

            # News moved to the Research page, which now leads with SEC
            # filings and financial statements and is where a reader goes to
            # ask what is happening to a company. Showing the same feed here
            # meant two pages carrying it, which is the duplication just
            # removed from Home. `news_list` is still fetched for the
            # EXPORTED report: a standalone research document should carry
            # the headlines it was written against, even though the page
            # sends you elsewhere to read them.

            # Fetched here rather than up front. Memoised, so the export path
            # above may already have paid for it, or may not have run at all -
            # neither this section nor that one needs to know.
            peer_df, peer_price_dfs = _load_peers()
            if peer_df is not None and not peer_df.empty:
                st.markdown(f'<div class="section-header"'
                            f'{_sec_id("sec-peers", "Peer Comparison")}>Peer Comparison</div>',
                            unsafe_allow_html=True)
                # Say where the comparison set came from. A reader who does not
                # know these were picked by sector and size has no way to judge
                # whether they are the right companies to be compared against.
                if peers_auto:
                    # Deliberately does not name the sector. `sector` here is
                    # Polygon's SIC description ("Electronic Computers" for
                    # AAPL), not a GICS sector, and the peers were matched on the
                    # ranked universe's sector instead - so printing this string
                    # would caption the right companies with the wrong reason.
                    st.caption(f"Peers chosen automatically — companies in the same "
                               f"sector, closest to {ticker_input} in market value. "
                               f"Name your own under **Advanced options** to override.")

                # `_chart_layout` used to be defined here and was never applied to
                # anything — every peer chart below re-specified its layout inline.
                # ct.style() is the real shared layout now, so it is gone.

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
                                # The analysed ticker is always ink and always
                                # solid; peers take the categorical ramp. The
                                # subject should never be mistakable for a peer.
                                color=ct.color.ink if _is_main else ct.series_color(_ci),
                                width=ct.stroke.value if _is_main else ct.stroke.price,
                                dash="solid" if _is_main else "dot" if _ci > 0 else "solid",
                            ),
                            hovertemplate=f"<b>{_pt}</b>: %{{y:.1f}}<extra></extra>",
                        ))
                    ct.style(
                        fig_cum,
                        height=380,
                        margin=dict(l=52, r=20, t=26, b=30),
                        x=ct.time_axis(fy_ticks=False, title=None, tickformat="%b '%y"),
                        y=ct.value_axis(prefix="", tick_format=".0f", zero=False,
                                        title="Index (Start = 100)"),
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
                        # (No per-ticker colour list here: every bar chart below
                        # colours by VALUE — positive/negative, or Sharpe band —
                        # not by series identity. A `_peer_colors` lookup used to
                        # sit here, referencing a name that was never defined, so
                        # entering any peer ticker raised NameError and took the
                        # whole Analysis page down with a traceback.)

                        _mc1, _mc2 = st.columns(2)

                        with _mc1:
                            # Annualised Return + Volatility grouped bar
                            fig_rv = go.Figure()
                            fig_rv.add_trace(go.Bar(
                                name="Ann. Return (%)",
                                x=_ticks,
                                y=_mdf["Ann. Return (%)"],
                                marker_color=[
                                    ct.color.positive if v >= 0 else ct.color.negative
                                    for v in _mdf["Ann. Return (%)"]
                                ],
                                marker_line_width=0,
                                hovertemplate="%{x}: %{y:.2f}%<extra>Ann. Return</extra>",
                            ))
                            fig_rv.add_trace(go.Bar(
                                name="Volatility (%)",
                                x=_ticks,
                                y=_mdf["Volatility (%)"],
                                marker_color=ct.color.value_line, marker_line_width=0,
                                hovertemplate="%{x}: %{y:.2f}%<extra>Volatility</extra>",
                            ))
                            ct.style(
                                fig_rv,
                                height=300, barmode="group", crosshair=False,
                                margin=dict(l=52, r=20, t=26, b=40),
                                x=ct.category_axis(title="Ticker"),
                                y=ct.pct_axis(tick_format=".1f", title="Percent (%)",
                                              zeroline=True, zerolinecolor=ct.color.rule),
                                title=dict(text="Ann. Return vs Volatility",
                                           font=dict(size=13, color=ct.color.ink,
                                                     family=ct.font.data),
                                           x=0, xanchor="left"),
                            )
                            st.plotly_chart(fig_rv, use_container_width=True)

                        with _mc2:
                            # Sharpe Ratio bars
                            fig_sh = go.Figure(go.Bar(
                                x=_ticks,
                                y=_mdf["Sharpe Ratio"],
                                marker_color=[
                                    ct.color.positive if v >= 1 else
                                    ct.color.value_line if v >= 0 else ct.color.negative
                                    for v in _mdf["Sharpe Ratio"]
                                ],
                                marker_line_width=0,
                                hovertemplate="%{x}: %{y:.2f}<extra>Sharpe</extra>",
                            ))
                            ct.style(
                                fig_sh,
                                height=300, legend=None, crosshair=False,
                                margin=dict(l=52, r=20, t=26, b=40),
                                x=ct.category_axis(title="Ticker"),
                                y=ct.plain_axis(tick_format=".2f", title="Sharpe Ratio",
                                                zeroline=True, zerolinecolor=ct.color.rule),
                                title=dict(text="Sharpe Ratio Comparison",
                                           font=dict(size=13, color=ct.color.ink,
                                                     family=ct.font.data),
                                           x=0, xanchor="left"),
                            )
                            st.plotly_chart(fig_sh, use_container_width=True)

                        # Max Drawdown full-width
                        fig_dd = go.Figure(go.Bar(
                            x=_ticks,
                            y=_mdf["Max Drawdown (%)"],
                            marker_color=ct.color.negative, marker_line_width=0,
                            hovertemplate="%{x}: %{y:.2f}%<extra>Max Drawdown</extra>",
                        ))
                        ct.style(
                            fig_dd,
                            height=280, legend=None, crosshair=False,
                            margin=dict(l=52, r=20, t=26, b=40),
                            x=ct.category_axis(title="Ticker"),
                            y=ct.pct_axis(tick_format=".1f", title="Max Drawdown (%)"),
                            title=dict(text="Maximum Drawdown Comparison",
                                       font=dict(size=13, color=ct.color.ink,
                                                 family=ct.font.data),
                                       x=0, xanchor="left"),
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
            _jump_anchor("sec-report", "Download Report")
            _stock_exports("bottom")

            # Rendered last so it lists exactly the sections this run produced;
            # it is position:fixed, so where it sits in the DOM doesn't matter.
            _render_jump_rail()

        st.markdown(render_section("Data & Methodology", _disc.DIVIDENDS), unsafe_allow_html=True)
        st.markdown(render_inline(_disc.SHORT), unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# NEWS — market-wide pulse + per-ticker research
# ═════════════════════════════════════════════════════════════════════════════
elif _page == "research":
    # The ticker search leads. It was underneath a twenty-story market feed, so
    # the page's primary action - look up one company - sat below a screen and a
    # half of something else. Market news still follows, and is what the page
    # shows when nobody has asked about anything in particular.
    st.markdown('<div class="section-header">Research a ticker '
                '<span style="font-weight:500;color:#94a3b8;letter-spacing:0;'
                'text-transform:none;font-size:0.7rem">· filings, statements '
                'and news</span></div>', unsafe_allow_html=True)
    _nt = st.text_input("Ticker", key="news_ticker",
                        placeholder="e.g. AAPL, MSFT, NVDA",
                        label_visibility="collapsed")
    if _nt and _nt.strip():
        _nt_clean = _nt.strip().upper()
        # Filings and statements first: what the company published, before what
        # was written about it.
        _render_filings(_nt_clean)
        _render_statements(_nt_clean)
        _render_stock_news(_nt_clean)
        st.markdown("---")
    else:
        st.caption("Enter a ticker for its SEC filings and financial statements, "
                   "then its news tone, catalysts and sourced brief.")

    st.markdown('<div class="section-header">Market News '
                '<span style="font-weight:500;color:#94a3b8;letter-spacing:0;'
                'text-transform:none;font-size:0.7rem">· across the market, '
                'theme-tagged</span></div>', unsafe_allow_html=True)

    import html as _html_mod
    _pulse   = _cached_market_pulse()
    _m_arts  = _pulse.get("articles") or []
    _trend   = _pulse.get("trending") or []

    if not _m_arts:
        st.info("Market news is unavailable right now \u2014 the news provider did not "
                "return any stories. Per-ticker research above still works.")
    else:
        if _trend:
            st.caption("Most-mentioned tickers in recent market coverage")
            st.markdown(
                '<div class="news-chips">' +
                "".join(f'<span class="news-chip">{_html_mod.escape(str(_tk))} \u00b7 {_n}</span>'
                        for _tk, _n in _trend) +
                '</div>', unsafe_allow_html=True)
        st.markdown(_news_feed_html(_m_arts, 20), unsafe_allow_html=True)

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

# =============================================================================
# LEGAL
# =============================================================================
elif _page == "terms":
    render_terms()

elif _page == "privacy":
    render_privacy()

# ── Footer link strip (every page) ───────────────────────────────────────────
# Outside the routing chain so there is exactly one place the legal documents are
# linked from, and no page can be built that forgets them.
st.markdown(render_legal_links(), unsafe_allow_html=True)
