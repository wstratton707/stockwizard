"""
your_portfolios.py — the "Your Portfolios" tab.

Forward-tracks portfolios the user saves: enter holdings once, and every time you
open the tab it marks them to market from the day you added them (via tracker.py)
and shows the value curve vs the S&P 500, headline metrics, and the holdings.

Sign-in is real OIDC (see `auth.py`) — Streamlit's native `st.login`, with the
session cookie managed by Streamlit so it survives a refresh. Portfolios remain
keyed by email, so anyone who used the old email-only beta gate keeps their data
by signing in with the same address.
"""

import html as _html
import json
from datetime import date

import plotly.graph_objects as go
import streamlit as st

import auth
import chart_theme as ct

from tracker import track_portfolio, dollars_to_lots, amount_to_shares
from market_data import get_ticker_profiles, profiles_are_usable
# Report builders are imported at the point of use, not here.
# Between them they pull in matplotlib, openpyxl, python-docx and python-pptx
# — about 100 MB of resident memory that every visitor paid for even though
# only the ones who click Export ever need it. Python never releases an
# imported module, so an eager import is a permanent tax on the whole process.

from importlib.util import find_spec
# Feature detection WITHOUT importing. The old `try: import portfolio_pptx`
# pulled in pptx_builder -> matplotlib and portfolio_excel -> openpyxl on every
# page load, purely to find out whether python-pptx was installed.
_PPTX_OK = find_spec("pptx") is not None
from database import (
    save_tracked_portfolio, load_tracked_portfolios,
    update_tracked_portfolio, delete_tracked_portfolio,
    tracked_storage_status,
)


@st.cache_data(ttl=30, show_spinner=False)
def _storage_status():
    return tracked_storage_status()


def _section_header(text):
    st.markdown(f"""
    <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;
                color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                margin-bottom:1rem;margin-top:1.75rem">{text}</div>
    """, unsafe_allow_html=True)


def _stat_ribbon(items):
    """items: list of (label, value, tone) where tone is '', 'pos', 'neg' or 'na'.

    One ruled ribbon, not four grey rounded boxes. Rendered as a single markdown
    block rather than st.columns so the vertical rules actually meet the
    horizontal ones — Streamlit columns carry their own gap and the dividers
    never lined up.
    """
    cells = "".join(
        f'<div class="pf-stat">'
        f'<div class="pf-stat-val {tone}">{val}</div>'
        f'<div class="pf-stat-lbl">{label}</div></div>'
        for label, val, tone in items)
    st.markdown(f'<div class="pf-ribbon">{cells}</div>', unsafe_allow_html=True)


@st.cache_data(ttl=86400, show_spinner=False)
def _cached_profiles_ok(tickers_key):
    """Cached only when the fetch actually worked — see _profiles()."""
    return get_ticker_profiles(list(tickers_key))


def _profiles(tickers_key):
    """Company metadata, cached for a day only if it came back usable.

    Yahoo throttles bursts, and a throttled lookup returns a well-formed dict
    with every field empty. Caching that unconditionally meant one unlucky
    fetch produced a full day of reports with "Unknown" against every holding
    and no sector, style or beta analysis. Now a bad batch is retried on the
    next attempt instead of being remembered.
    """
    profiles = _cached_profiles_ok(tickers_key)
    if profiles_are_usable(profiles):
        return profiles
    _cached_profiles_ok.clear()             # don't let the failure stick
    profiles = get_ticker_profiles(list(tickers_key))
    return profiles


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_track(portfolio_id, holdings_key, _holdings, _api_key):
    # holdings_key (a JSON string) is the cache key; _holdings/_api_key are passed
    # unhashable so they don't bloat the hash. Recomputes when holdings change.
    return track_portfolio(_holdings, api_key=_api_key)


def _holdings_table(holdings) -> str:
    """The holdings grid, as HTML rather than st.dataframe.

    st.dataframe brought three things this table should not have: a floating
    eye/download/search/fullscreen toolbar that is component chrome rather than
    product, a fixed-height scroll region that clipped the last row in half, and
    no way to colour a single column. Gain % is the one column here where colour
    carries meaning, and it was rendering in black.

    Built to the same rules as the fact panel beside it — hairline row rules, no
    zebra, no vertical borders, tabular figures everywhere, numbers right, label
    left. Weight carries an inline bar so concentration is scannable without
    reading each number.
    """
    rows = list(holdings or [])
    if not rows:
        return ""

    max_w = max((float(h.get("weight_pct") or 0) for h in rows), default=0) or 1.0

    def _num(v, dp=2, dash="—"):
        return dash if v is None else f"{float(v):,.{dp}f}"

    body = []
    for h in sorted(rows, key=lambda r: float(r.get("weight_pct") or 0), reverse=True):
        g = h.get("gain_pct")
        g_cls = "" if g is None else ("pos" if float(g) >= 0 else "neg")
        g_txt = "—" if g is None else f"{float(g):+.2f}%"
        w = float(h.get("weight_pct") or 0)
        # Bar is scaled to the largest holding, not to 100 — at 18 positions
        # every bar would otherwise be a stub too short to compare.
        body.append(
            f'<tr>'
            f'<td class="t">{h.get("ticker","")}</td>'
            f'<td class="n">{_num(h.get("shares"), 4)}</td>'
            f'<td class="n">${_num(h.get("last_price"))}</td>'
            f'<td class="n">${_num(h.get("value"))}</td>'
            f'<td class="n w">'
            f'<span class="wbar" style="width:{(w / max_w) * 100:.1f}%"></span>'
            f'<span class="wnum">{w:.2f}%</span></td>'
            f'<td class="n {g_cls}">{g_txt}</td>'
            f'</tr>')

    tot_val = sum(float(h.get("value") or 0) for h in rows)
    tot_w   = sum(float(h.get("weight_pct") or 0) for h in rows)

    return (
        '<div class="hold-wrap"><table class="hold">'
        '<thead><tr>'
        '<th class="t">Ticker</th><th class="n">Shares</th><th class="n">Price</th>'
        '<th class="n">Value</th><th class="n">Weight</th><th class="n">Gain</th>'
        '</tr></thead>'
        f'<tbody>{"".join(body)}</tbody>'
        '<tfoot><tr>'
        f'<td class="t">{len(rows)} positions</td><td class="n"></td><td class="n"></td>'
        f'<td class="n">${tot_val:,.2f}</td><td class="n">{tot_w:.2f}%</td>'
        '<td class="n"></td>'
        '</tr></tfoot>'
        '</table></div>')


def _ann_return_str(m):
    """Annualised return, or an honest 'not yet' for a young portfolio.

    `compute_backtest_metrics` returns None below ~3 months rather than scaling a
    few weeks up to a year — which is how a one-month-old portfolio came to
    report -132.09%.
    """
    v = m.get("Ann. Return")
    if v is None or (isinstance(v, float) and v != v):
        return "Ann. return — needs 3 months"
    return f"Ann. return {float(v):+.1f}%"


def _alpha_str(v):
    if v is None or v == "N/A":
        return "N/A"
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return str(v)


# ── Identity ──────────────────────────────────────────────────────────────────
# Was: a free-text email box, mirrored into ?email= so it survived a refresh.
# That was not authentication — typing someone else's address returned their
# portfolios. Identity now comes from the signed OIDC session (see auth.py), and
# the address it yields is the same `user_email` these rows were always keyed by,
# so anyone who used the old beta gate keeps their data by signing in with the
# same address.
def _current_email():
    return auth.current_email()


def _render_hero():
    # Editorial header on the white canvas, matching Home. This was a blue
    # gradient rounded card, which made the inner pages look like a different
    # product from the landing page.
    st.markdown("""
<div class="page-head">
  <div class="page-head-eyebrow">Forward-tracked · not backtested</div>
  <div class="page-head-title">Your Portfolios</div>
  <p class="page-head-lede">Save a portfolio and we track its real performance from the day
  you add it — value against the S&amp;P 500 on the same money, total return, drawdown and
  your holdings.</p>
</div>""", unsafe_allow_html=True)


# ── Create form ─────────────────────────────────────────────────────────────────
def _parse_holdings_text(raw: str) -> dict:
    """'AAPL, 5000' per line  ->  {'AAPL': 5000.0}."""
    alloc = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.replace("\t", ",").split(",") if p.strip()]
        if len(parts) < 2:
            continue
        tk = parts[0].upper().lstrip("$")
        amt = parts[1].replace("$", "").replace(",", "")
        try:
            alloc[tk] = float(amt)
        except ValueError:
            continue
    return alloc


def _render_create(email, api_key):
    with st.expander("New tracked portfolio", expanded=False):
        with st.form("yp_new"):
            name = st.text_input("Name", placeholder="My Roth IRA")
            raw  = st.text_area(
                "Holdings — one per line as  TICKER, $AMOUNT",
                placeholder="AAPL, 5000\nMSFT, 3000\nVTI, 2000", height=120)
            inception = st.date_input("Start tracking from", value=date.today(),
                                      max_value=date.today())
            ok = st.form_submit_button("Create & track", type="primary")
        if ok:
            alloc = _parse_holdings_text(raw)
            if not name.strip():
                st.error("Give your portfolio a name.")
            elif not alloc:
                st.error("Add at least one holding, e.g.  AAPL, 5000")
            else:
                with st.spinner("Pricing holdings…"):
                    lots, skipped = dollars_to_lots(alloc, inception.isoformat(), api_key)
                if not lots:
                    st.error("Couldn't price any of those tickers — check the symbols.")
                else:
                    pid = save_tracked_portfolio(email, name, lots, inception.isoformat())
                    if pid:
                        if skipped:
                            st.warning(f"Skipped (no price data): {', '.join(skipped)}")
                        st.success(f"Tracking “{name.strip()}”.")
                        st.rerun()
                    else:
                        st.error("Couldn't save. Is the `tracked_portfolios` table set up "
                                 "in Supabase? (See database.py for the DDL.)")


def _render_edit(pid, holdings, api_key):
    """Add a new lot (today) or sell an existing position (removed_date = today)."""
    with st.expander("Edit holdings"):
        active = [l for l in holdings if not l.get("removed_date")]
        if active:
            st.caption("Current positions")
            for li, lot in enumerate(active):
                c1, c2 = st.columns([4, 1])
                c1.markdown(f"**{lot['ticker']}** · {float(lot['shares']):.3f} sh "
                            f"· since {lot['added_date']}")
                if c2.button("Sell", key=f"sell_{pid}_{li}"):
                    today = date.today().isoformat()
                    # Close only the lot whose button was clicked. Matching on
                    # ticker alone closed EVERY open lot of that symbol, so a
                    # second NVDA purchase was sold off by pressing Sell on the
                    # first. Identity is (ticker, shares, added_date).
                    for l in holdings:
                        if (l["ticker"] == lot["ticker"]
                                and l.get("added_date") == lot.get("added_date")
                                and float(l.get("shares", 0)) == float(lot.get("shares", 0))
                                and not l.get("removed_date")):
                            l["removed_date"] = today
                            break
                    update_tracked_portfolio(pid, holdings)
                    st.cache_data.clear()
                    st.rerun()
        with st.form(f"add_{pid}"):
            a1, a2, a3 = st.columns([2, 2, 1])
            tk  = a1.text_input("Add ticker", key=f"addtk_{pid}", placeholder="NVDA")
            amt = a2.number_input("$ amount", min_value=0.0, step=500.0, value=0.0,
                                  key=f"addamt_{pid}")
            a3.markdown("<div style='height:1.7rem'></div>", unsafe_allow_html=True)
            add = a3.form_submit_button("Add")
        if add:
            tk = (tk or "").strip().upper()
            if not tk or amt <= 0:
                st.error("Enter a ticker and a positive amount.")
            else:
                shares, fill_date, _ = amount_to_shares(tk, amt, date.today().isoformat(), api_key)
                if not shares:
                    st.error(f"Couldn't price {tk}.")
                else:
                    added = fill_date.strftime("%Y-%m-%d") if fill_date is not None else date.today().isoformat()
                    holdings.append({"ticker": tk, "shares": float(shares),
                                     "added_date": added, "removed_date": None})
                    update_tracked_portfolio(pid, holdings)
                    st.cache_data.clear()
                    st.rerun()


# ── Portfolio statistics panel ──────────────────────────────────────────────────
# A ruled panel beside the chart, not a grid of KPI cards. It answers five
# questions and stops: what can I expect, how risky is it, how concentrated am I,
# what do I actually own, and how does it compare to the market.
#
# Deliberately NOT shown: VaR (says nothing volatility and drawdown haven't),
# CAGR (the same number as annualised return), Sortino (Sharpe already carries
# the glance; Sortino is in the reports), cash weight (we don't track cash) and
# expense ratio (needs a per-ETF fetch this page doesn't make). Every extra row
# costs the reader attention that the rows that matter then don't get.
_TIP = {
    "exp": "What this portfolio should earn in an average year, from CAPM: the "
           "10-year Treasury plus the portfolio's beta times a 5% equity risk "
           "premium. An expectation, not a forecast.",
    "ann": "Actual return so far, scaled to a yearly rate. Needs about three "
           "months of history before it means anything.",
    "dy":  "Trailing dividends as a share of market value. Non-payers count as "
           "zero, because they are part of what you own.",
    "inc": "Roughly what the current holdings would pay out over a year if "
           "dividends stayed as they are.",
    "beta": "How much the portfolio moves for a 1% market move. Above 1 amplifies "
            "the market in both directions; below 1 damps it.",
    "vol": "How much the portfolio's value swings year to year. The S&P 500 runs "
           "about 15% over the long run.",
    "sharpe": "Return earned per unit of risk taken, above cash. Above 1 is good.",
    "dd":  "The deepest peak-to-trough fall so far — the loss you would have had "
           "to sit through.",
    "vs":  "Against an S&P 500 position funded on exactly your dates and amounts, "
           "so contributions can't flatter the comparison.",
    "big": "Your largest single holding. The bigger it is, the more one company "
           "decides your result.",
    "top5": "Share of the portfolio in its five largest positions.",
    "sec": "Share in your largest sector. Above 30% is usually treated as a "
           "concentration rather than a tilt.",
    "eff": "How many equally-sized holdings would carry the same concentration. "
           "Lower than your holding count means weight is uneven.",
    "n":   "Number of positions. Most company-specific risk is diversified away "
           "somewhere around 20-30 names.",
    "cap": "Where the money sits by company size, weighted by position.",
    "pe":  "Earnings-weighted P/E across holdings that earn money, so one "
           "expensive position can't dominate it.",
}


def _tip(text):
    return (f'<span class="tooltip-wrap"> ⓘ<span class="tooltip-text">'
            f'{_html.escape(text)}</span></span>')


def _row(label, value, tip_key=None, tone=""):
    t = _tip(_TIP[tip_key]) if tip_key else ""
    cls = f' class="{tone}"' if tone else ""
    return (f'<div class="vf-row"><span>{label}{t}</span>'
            f'<b{cls}>{value}</b></div>')


def _cap_profile(stats):
    """Weighted average market cap, as a size label."""
    rows = [r for r in stats["rows"] if r.get("market_cap")]
    tot = sum(r["value"] for r in rows)
    if not tot:
        return None
    wavg = sum(r["market_cap"] * r["value"] for r in rows) / tot
    if wavg >= 2e11:
        return "Mega cap"
    if wavg >= 1e10:
        return "Large cap"
    if wavg >= 2e9:
        return "Mid cap"
    return "Small cap"


def _stat_panel(res, profiles):
    """The ruled statistics panel. Returns HTML."""
    from portfolio_excel import _derive_portfolio_stats
    from constants import EQUITY_RISK_PREMIUM, get_long_risk_free_rate

    s = _derive_portfolio_stats(res, profiles)
    m = res.get("metrics", {}) or {}
    thin = s.get("thin_history")
    dash = "—"

    def pct(v, dp=1, sign=False):
        if v is None:
            return dash
        return f"{v:+.{dp}f}%" if sign else f"{v:.{dp}f}%"

    def w(v):
        return dash if v is None else f"{v:.1%}"

    # Return
    beta = s.get("beta")
    exp = (get_long_risk_free_rate() + beta * EQUITY_RISK_PREMIUM) if beta is not None else None
    dy = s.get("div_yield")
    income = (dy * s["total_value"]) if (dy and s.get("total_value")) else None
    ann = m.get("Ann. Return")
    vs = m.get("vs S&P 500")

    html = ['<div class="val-facts">']
    html.append('<div class="vf-group">Return outlook</div>')
    html.append(_row("Expected return (CAPM)", w(exp), "exp"))
    html.append(_row("Annualised so far",
                     "Needs 3 months" if ann is None else pct(ann, sign=True), "ann",
                     "" if ann is None else ("good" if ann >= 0 else "bad")))
    html.append(_row("vs S&P 500",
                     dash if not isinstance(vs, (int, float)) else f"{vs:+.1f} pts", "vs",
                     "" if not isinstance(vs, (int, float)) else
                     ("good" if vs >= 0 else "bad")))
    html.append(_row("Dividend yield", w(dy), "dy"))
    html.append(_row("Est. annual income",
                     dash if income is None else f"${income:,.0f}", "inc"))

    # Risk
    html.append('<div class="vf-group">Risk</div>')
    html.append(_row("Portfolio beta", dash if beta is None else f"{beta:.2f}", "beta",
                     "" if beta is None else
                     ("warn" if beta > 1.15 else "good" if beta < 0.85 else "")))
    _vol = s.get("vol")
    html.append(_row("Volatility (annual)",
                     "Needs 1 month" if thin else pct(_vol), "vol",
                     "" if (thin or _vol is None) else
                     ("bad" if _vol > 25 else "good" if _vol < 15 else "warn")))
    # None from the source means it declined to quote one — too short a window,
    # or a value outside the plausible range. Both read as "not yet", not as a
    # dash, because a dash suggests missing data rather than withheld data.
    _sh = m.get("Sharpe Ratio")
    _sh_ok = isinstance(_sh, (int, float))
    html.append(_row("Sharpe ratio",
                     "Needs 1 month" if (thin or not _sh_ok) else f"{_sh:.2f}",
                     "sharpe",
                     "" if (thin or not _sh_ok) else
                     ("good" if _sh > 1 else "warn" if _sh > 0 else "bad")))
    _dd = m.get("Max Drawdown")
    html.append(_row("Max drawdown", "Needs 1 month" if thin else pct(_dd), "dd",
                     "" if (thin or _dd is None) else
                     ("bad" if _dd < -20 else "warn" if _dd < -10 else "good")))

    # Concentration
    html.append('<div class="vf-group">Concentration</div>')
    lg = s.get("largest")
    html.append(_row("Largest position",
                     dash if not lg else f"{lg['ticker']} · {w(lg['w'])}", "big",
                     "" if not lg else ("bad" if lg["w"] > 0.25 else
                                        "warn" if lg["w"] > 0.15 else "good")))
    html.append(_row("Top 5 holdings", w(s["top5"]), "top5",
                     "bad" if s["top5"] > 0.70 else "warn" if s["top5"] > 0.50 else "good"))
    _named = [(k, v) for k, v in s["sectors"] if k not in ("Unknown", "Fund / ETF")]
    _tot = s["total_value"] or 1
    _secw = (_named[0][1] / _tot) if _named else None
    html.append(_row("Largest sector",
                     dash if not _named else f"{_named[0][0]} · {w(_secw)}", "sec",
                     "" if _secw is None else
                     ("bad" if _secw > 0.40 else "warn" if _secw > 0.30 else "good")))
    html.append(_row("Effective holdings",
                     dash if not s.get("eff_n") else f"{s['eff_n']:.1f} of {s['n']}", "eff"))

    # What you own
    html.append('<div class="vf-group">What you own</div>')
    html.append(_row("Holdings", str(s["n"]), "n",
                     "good" if s["n"] >= 15 else "warn" if s["n"] >= 8 else "bad"))
    html.append(_row("Size profile", _cap_profile(s) or dash, "cap"))
    html.append(_row("Weighted P/E",
                     dash if s.get("wpe") is None else f"{s['wpe']:.1f}x", "pe"))
    _fund = sum(v for k, v in s["sectors"] if k == "Fund / ETF") / _tot
    html.append(_row("Funds / ETFs", w(_fund) if _fund else "None"))
    html.append("</div>")
    return "".join(html), s


# ── Exports ─────────────────────────────────────────────────────────────────────
# Three formats off one analysis. Each builds on demand (they render matplotlib
# charts, which is the memory spike on a small instance), caches the bytes in
# session, then swaps the button for a download so a second click doesn't
# rebuild. Keyed per portfolio so two portfolios can't serve each other's file.
_EXPORTS = [
    ("excel", "Excel workbook", "xlsx",
     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
     "Five sheets — summary, holdings, allocation, risk and intelligence."),
    ("word", "Word report", "docx",
     "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
     "A written review: allocation, risk, attribution, strengths and considerations."),
    ("pptx", "PowerPoint deck", "pptx",
     "application/vnd.openxmlformats-officedocument.presentationml.presentation",
     "Ten client-ready slides for presenting the portfolio."),
]


def _render_exports(pid, name, res):
    # Reaching this page already required sign-in, so in practice the gate here
    # only enforces the quota — but it is checked rather than assumed.
    from entitlements import require_export, record, render_quota_note
    _exp_ok, _exp_user = require_export(f"tracked_{pid}")
    if not _exp_ok:
        return

    _formats = [f for f in _EXPORTS if f[0] != "pptx" or _PPTX_OK]
    _labels = [f[1] for f in _formats]
    _pick = st.radio("Report format", _labels, horizontal=True,
                     key=f"fmt_{pid}", label_visibility="collapsed")
    _kind, _label, _ext, _mime, _blurb = next(
        f for f in _formats if f[1] == (_pick or _labels[0]))
    st.caption(_blurb)

    _key = f"rep_{_kind}_{pid}"
    c1, c2, _sp = st.columns([1.35, 1.15, 2.5])
    if c1.button(f"Build {_label.lower()}", key=f"gen_{_kind}_{pid}"):
        with st.spinner(f"Building your {_label.lower()}…"):
            try:
                tickers = tuple(sorted(h["ticker"] for h in res["holdings"]))
                profiles = _profiles(tickers)
                if _kind == "excel":
                    from portfolio_excel import build_tracked_portfolio_excel
                    buf = build_tracked_portfolio_excel(name, res, profiles)
                elif _kind == "word":
                    from portfolio_docx import build_portfolio_docx
                    buf = build_portfolio_docx(name, res, profiles)
                else:
                    from portfolio_pptx import build_portfolio_review_pptx
                    buf = build_portfolio_review_pptx(name, res, profiles)
                if buf is None:
                    st.error("That format isn't available on this instance.")
                else:
                    st.session_state[_key] = buf.getvalue()
                    record(_exp_user, f"tracked_{_kind}", name)
            except Exception as e:
                st.error(f"Couldn't build the report: {e}")
                import traceback as _tb; print(_tb.format_exc())   # server log, not UI
    if _key in st.session_state:
        _safe = "".join(c if c.isalnum() or c in " -_" else "" for c in name).strip() \
                or "Portfolio"
        c2.download_button(
            f"Download .{_ext}", data=st.session_state[_key],
            file_name=f"QuantWizard {_safe} {date.today().isoformat()}.{_ext}",
            mime=_mime, key=f"dl_{_kind}_{pid}")
    render_quota_note(_exp_user)


# ── Per-portfolio card ──────────────────────────────────────────────────────────
def _render_card(p, api_key):
    name     = p.get("name", "Untitled")
    holdings = p.get("holdings", []) or []
    pid      = p.get("id")

    res = _cached_track(pid, json.dumps(holdings, sort_keys=True, default=str), holdings, api_key)

    _n_lots = len([h for h in holdings if not h.get("removed_date")])
    _since  = min((h.get("added_date") or "") for h in holdings) if holdings else ""

    st.markdown(
        f'<div class="pf-head"><div class="pf-name">{_html.escape(name)}</div>'
        f'<div class="pf-meta">{_n_lots} position{"s" if _n_lots != 1 else ""}'
        f'{" · since " + _since if _since else ""}</div></div>',
        unsafe_allow_html=True)

    if "error" in res:
        # A portfolio saved in the last few days has no second closing price yet.
        # That is the expected first state, not a failure — a warning box made a
        # successful save look broken the moment the user arrived.
        #
        # Measured in calendar days rather than `added_date == today`: lots snap
        # to the last trading day, so a portfolio created on a Saturday (or after
        # Friday's close) carries Friday's date and would otherwise miss this.
        _fresh = False
        try:
            _fresh = (date.today() - date.fromisoformat(_since)).days <= 4
        except (TypeError, ValueError):
            pass
        if _fresh:
            st.markdown(
                '<div class="pf-fresh"><div class="pf-fresh-t">Tracking starts with '
                'tomorrow&rsquo;s close</div><div class="pf-fresh-b">Your positions are '
                'recorded. Performance needs a second closing price to measure against, '
                'so the value curve and returns appear after the next trading day.'
                '</div></div>', unsafe_allow_html=True)
        else:
            st.info(res["error"])
        if st.button("Delete", key=f"del_err_{pid}"):
            delete_tracked_portfolio(pid)
            st.rerun()
        st.markdown("<div style='height:0.6rem'></div>", unsafe_allow_html=True)
        return

    m   = res["metrics"]
    tr  = float(m.get("Total Return", 0) or 0)
    dd  = float(m.get("Max Drawdown", 0) or 0)
    vs  = m.get("vs S&P 500")
    _vs_num = isinstance(vs, (int, float))
    _stat_ribbon([
        ("Current Value", f"${m.get('Final Value', 0):,.0f}", ""),
        ("Total Return",  f"{tr:+.1f}%", "pos" if tr >= 0 else "neg"),
        ("vs S&P 500",    _alpha_str(vs),
                          ("pos" if vs >= 0 else "neg") if _vs_num else "na"),
        ("Max Drawdown",  f"{dd:.1f}%", "neg" if dd < 0 else ""),
    ])
    # Sharpe is deliberately absent from this line. It used to print the raw
    # value here while the risk panel below said "Needs 1 month" for the same
    # metric — one screen, two answers, and the number on show was 7.157. The
    # panel is where risk metrics live, gated; this line carries provenance.
    st.caption(f"Tracked since {res['inception_date']}  ·  {_ann_return_str(m)}")

    # Statistics beside the chart. Profiles are fetched here rather than only at
    # export time so the numbers are on screen without a second click; the
    # day-long cache means this costs one fetch per portfolio per day.
    _panel_html = None
    try:
        with st.spinner("Loading portfolio statistics…"):
            _tk = tuple(sorted(h["ticker"] for h in res["holdings"]))
            _panel_html, _ = _stat_panel(res, _profiles(_tk))
    except Exception:
        import traceback as _tb; print(_tb.format_exc())   # server log, not UI

    _pcol, _ccol = st.columns([1, 2]) if _panel_html else (None, st.container())
    if _panel_html:
        _pcol.markdown(_panel_html, unsafe_allow_html=True)

    # Value vs benchmark vs contributed
    curve = res["curve"]
    fig = go.Figure()

    # One list, one loop, one guard. Each series is added only if it actually
    # carries a point, so the legend can never advertise a line that isn't
    # drawn — a legend entry with no series is indistinguishable from a
    # rendering bug to anyone reading the chart.
    #
    # Colour follows the data's importance rather than its brand: the portfolio
    # is ink, because it IS the subject; the benchmark recedes to muted grey;
    # the invested line is a reference rule, not a third competing series. The
    # portfolio line was previously brand blue at the heaviest weight in the
    # token set, which made the loudest thing on the chart the one the reader
    # already knows.
    _series = (
        ("Your Portfolio",   curve.get("Portfolio"),
         dict(color=ct.color.ink,        width=ct.stroke.value)),
        ("S&P 500 (same $)", curve.get("SP500"),
         dict(color=ct.color.ink_muted,  width=ct.stroke.price, dash="dot")),
        ("Invested",         curve.get("Contrib"),
         dict(color=ct.color.value_line, width=ct.stroke.price, dash="dash")),
    )
    for _name, _y, _line in _series:
        if _y is None or _y.dropna().empty:
            continue
        # mode="lines" explicitly: Plotly's default for a short series is
        # lines+markers, which put a dot on all fourteen observations and read
        # as a dashboard. Markers are for events, not for every point.
        fig.add_trace(go.Scatter(
            x=curve.index, y=_y, name=_name, mode="lines", line=_line,
            hovertemplate="%{y:$,.0f}<extra></extra>"))

    ct.style(
        fig,
        # Matched to the statistics panel beside it so the two align top and
        # bottom instead of one floating against the other.
        height=560 if _panel_html else 340,
        # A portfolio can be days old, so yearly ticks would label one point.
        x=ct.time_axis(fy_ticks=False),
        # Not a filled area, and forcing the base to zero would flatten a curve
        # whose whole story is the move above the invested line.
        y=ct.value_axis(zero=False),
        # Below the plot, not floating inside it — the legend was covering the
        # top-right of the plot area, which is where a rising series ends up.
        legend="bottom",
        # The default bottom padding (4 + 26) has to seat the date labels AND
        # the legend beneath them; at the default the labels sat on the
        # container's edge.
        margin=dict(l=ct.layout.plot_padding["left"],
                    r=ct.layout.plot_padding["right"],
                    t=ct.layout.plot_padding["top"] + 22, b=72),
    )
    _ccol.plotly_chart(fig, use_container_width=True, key=f"curve_{pid}")

    # Holdings table
    if res["holdings"]:
        st.markdown(_holdings_table(res["holdings"]), unsafe_allow_html=True)

    for w in res.get("warnings", []):
        st.caption(f"{w}")

    _render_edit(pid, holdings, api_key)

    _render_exports(pid, name, res)

    if st.button("Delete portfolio", key=f"del_{pid}"):
        delete_tracked_portfolio(pid)
        st.cache_data.clear()
        st.rerun()


# ── Entry point ──────────────────────────────────────────────────────────────────
def render_your_portfolios(api_key, is_pro=False):
    _render_hero()

    email = _current_email()
    if not email:
        # The gate renders its own prominent panel and a Sign in button. The old
        # version showed a small email box that read like an optional newsletter
        # signup, so it wasn't obvious anything was required.
        auth.require_sign_in(
            feature="Your Portfolios",
            blurb="Your portfolios are private to your account and tracked from "
                  "the day you save them. Sign in to see yours, or create an "
                  "account — it takes a few seconds and you'll stay signed in.")
        return

    top = st.columns([3, 1])
    top[0].markdown(f"**Signed in as** `{email}`")
    if top[1].button("Sign out", key="yp_signout"):
        auth.sign_out()
        st.rerun()

    _status = _storage_status()
    if _status != "ok":
        from database import supabase_project_url
        _proj = supabase_project_url()
        _msg = {
            "no_creds": (
                "Storage isn't configured on this app instance — Supabase keys "
                "(`SUPABASE_URL` / `SUPABASE_KEY`) are missing. Add them under "
                "**Manage app → Settings → Secrets** (TOML), then reboot the app."),
            "bad_url": (
                f"`SUPABASE_URL` is set to a REST endpoint rather than the project "
                f"URL:\n\n`{_proj}`\n\nIt should be just "
                f"`https://<project-ref>.supabase.co` — with no `/rest/v1` on the "
                f"end. Copy the **Project URL**, not the API endpoint, and update it "
                f"under **Manage app → Settings → Secrets**."),
            "bad_key": (
                f"Connected to `{_proj}`, but the key was rejected. Check "
                f"`SUPABASE_KEY` matches **this** project — a key from a different "
                f"project fails exactly like this."),
            "no_table": (
                f"Connected to Supabase, but the **tracked_portfolios** table isn't "
                f"in this project:\n\n`{_proj}`\n\nRun the DDL from `database.py` "
                f"in **that** project's SQL editor (not your local one)."),
            "unreachable": (
                f"Couldn't reach `{_proj}` — network issue or the project is "
                f"paused. Supabase free-tier projects pause after ~7 days idle; "
                f"resume it from the dashboard."),
        }.get(_status)
        if _msg:
            st.warning(_msg + "\n\nPortfolios won't save until this is fixed.")

    _render_create(email, api_key)

    portfolios = load_tracked_portfolios(email)
    if not portfolios:
        st.info("No tracked portfolios yet. Use **New tracked portfolio** above to "
                "start tracking one from today (or any past date).")
        return

    for i, p in enumerate(portfolios):
        _render_card(p, api_key)
        if i < len(portfolios) - 1:
            st.divider()
