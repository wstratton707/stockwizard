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

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import auth
import chart_theme as ct

from tracker import track_portfolio, dollars_to_lots, amount_to_shares
from market_data import get_ticker_profiles
from portfolio_excel import build_tracked_portfolio_excel
from portfolio_docx import build_portfolio_docx

try:
    from portfolio_pptx import build_portfolio_review_pptx
    _PPTX_OK = True
except Exception:                       # python-pptx missing on this instance
    _PPTX_OK = False
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
def _cached_profiles(tickers_key):
    # Company metadata (sector/industry/P/E/beta) moves slowly — a day's TTL
    # keeps repeat exports instant without re-hitting Yahoo per ticker.
    return get_ticker_profiles(list(tickers_key))


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_track(portfolio_id, holdings_key, _holdings, _api_key):
    # holdings_key (a JSON string) is the cache key; _holdings/_api_key are passed
    # unhashable so they don't bloat the hash. Recomputes when holdings change.
    return track_portfolio(_holdings, api_key=_api_key)


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
                profiles = _cached_profiles(tickers)
                if _kind == "excel":
                    buf = build_tracked_portfolio_excel(name, res, profiles)
                elif _kind == "word":
                    buf = build_portfolio_docx(name, res, profiles)
                else:
                    buf = build_portfolio_review_pptx(name, res, profiles)
                if buf is None:
                    st.error("That format isn't available on this instance.")
                else:
                    st.session_state[_key] = buf.getvalue()
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
    st.caption(f"Tracked since {res['inception_date']}  ·  {_ann_return_str(m)}"
               f"  ·  Sharpe {m.get('Sharpe Ratio', 'N/A')}")

    # Value vs benchmark vs contributed
    curve = res["curve"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=curve.index, y=curve["Portfolio"],
                             name="Your Portfolio",
                             line=dict(color=ct.color.brand, width=ct.stroke.value)))
    if "SP500" in curve.columns and not curve["SP500"].isna().all():
        fig.add_trace(go.Scatter(x=curve.index, y=curve["SP500"],
                                 name="S&P 500 (same $)",
                                 line=dict(color=ct.color.ink_muted, width=ct.stroke.price,
                                           dash="dot")))
    fig.add_trace(go.Scatter(x=curve.index, y=curve["Contrib"],
                             name="Invested",
                             line=dict(color=ct.color.value_line, width=ct.stroke.price,
                                       dash="dash")))
    ct.style(
        fig,
        height=340,
        # A portfolio can be days old, so yearly ticks would label one point.
        x=ct.time_axis(fy_ticks=False),
        # Not a filled area, and forcing the base to zero would flatten a curve
        # whose whole story is the move above the invested line.
        y=ct.value_axis(zero=False),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"curve_{pid}")

    # Holdings table
    if res["holdings"]:
        hdf = pd.DataFrame(res["holdings"])
        hdf = hdf.rename(columns={
            "ticker": "Ticker", "shares": "Shares", "last_price": "Price",
            "value": "Value", "weight_pct": "Weight %", "gain_pct": "Gain %"})
        show = ["Ticker", "Shares", "Price", "Value", "Weight %", "Gain %"]
        st.dataframe(hdf[show], use_container_width=True, hide_index=True)

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
