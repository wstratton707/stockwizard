"""
your_portfolios.py — the "Your Portfolios" tab.

Forward-tracks portfolios the user saves: enter holdings once, and every time you
open the tab it marks them to market from the day you added them (via tracker.py)
and shows the value curve vs the S&P 500, headline metrics, and the holdings.

Beta login is email-only (keys your portfolios by email) — not secure, fine for
self-entered tickers; harden to real auth before paid launch.
"""

import json
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from tracker import track_portfolio, dollars_to_lots, amount_to_shares
from database import (
    save_tracked_portfolio, load_tracked_portfolios,
    update_tracked_portfolio, delete_tracked_portfolio,
    tracked_storage_status,
)


@st.cache_data(ttl=30, show_spinner=False)
def _storage_status():
    return tracked_storage_status()

DARK  = "#0f172a"
BLUE  = "#38bdf8"
GREEN = "#16a34a"
RED   = "#dc2626"
AMBER = "#f59e0b"
MUTED = "#94a3b8"


def _section_header(text):
    st.markdown(f"""
    <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;
                color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                margin-bottom:1rem;margin-top:1.75rem">{text}</div>
    """, unsafe_allow_html=True)


def _kpi_row(items):
    """items: list of (label, value, color)."""
    for col, (label, val, color) in zip(st.columns(len(items)), items):
        col.markdown(f"""
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
                    padding:0.7rem 0.9rem">
          <div style="font-size:1.35rem;font-weight:700;color:{color}">{val}</div>
          <div style="font-size:0.66rem;font-weight:600;letter-spacing:0.6px;
                      text-transform:uppercase;color:#94a3b8;margin-top:2px">{label}</div>
        </div>""", unsafe_allow_html=True)


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_track(portfolio_id, holdings_key, _holdings, _api_key):
    # holdings_key (a JSON string) is the cache key; _holdings/_api_key are passed
    # unhashable so they don't bloat the hash. Recomputes when holdings change.
    return track_portfolio(_holdings, api_key=_api_key)


def _alpha_str(v):
    if v is None or v == "N/A":
        return "N/A"
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return str(v)


# ── Login gate (email-only, beta) ───────────────────────────────────────────────
def _current_email():
    email = (st.session_state.get("user_email") or "").strip()
    if not email:
        qp = st.query_params.get("email", "")
        if qp:
            st.session_state["user_email"] = qp.strip()
            email = qp.strip()
    return email


def _render_login_gate():
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,#0f2747,#1f4e79);border-radius:14px;
                padding:1.6rem 1.8rem;color:#e2e8f0;margin-bottom:1.2rem">
      <div style="font-size:1.5rem;font-weight:700;color:#fff">Your Portfolios</div>
      <div style="font-size:0.95rem;color:#b0c4de;margin-top:0.4rem;max-width:640px">
        Save a portfolio and we'll track its real performance from the day you add it —
        value vs the S&P 500, return, drawdown, and your holdings. Forward-tracked, not
        backtested.
      </div>
    </div>""", unsafe_allow_html=True)
    with st.form("yp_login"):
        email = st.text_input("Your email", placeholder="you@example.com",
                              help="Beta sign-in. Keys your saved portfolios to this email.")
        ok = st.form_submit_button("Continue", type="primary")
    if ok:
        e = (email or "").strip().lower()
        if "@" not in e or "." not in e:
            st.error("Please enter a valid email.")
        else:
            st.session_state["user_email"] = e
            st.query_params["email"] = e   # survive a refresh
            st.rerun()
    st.caption("Beta sign-in is email-only and not secure — anyone with your email "
               "could view these. Don't store anything sensitive yet.")


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
    with st.expander("➕  New tracked portfolio", expanded=False):
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
    with st.expander("✏️  Edit holdings"):
        active = [l for l in holdings if not l.get("removed_date")]
        if active:
            st.caption("Current positions")
            for li, lot in enumerate(active):
                c1, c2 = st.columns([4, 1])
                c1.markdown(f"**{lot['ticker']}** · {float(lot['shares']):.3f} sh "
                            f"· since {lot['added_date']}")
                if c2.button("Sell", key=f"sell_{pid}_{li}"):
                    today = date.today().isoformat()
                    for l in holdings:
                        if l["ticker"] == lot["ticker"] and not l.get("removed_date"):
                            l["removed_date"] = today
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


# ── Per-portfolio card ──────────────────────────────────────────────────────────
def _render_card(p, api_key):
    name     = p.get("name", "Untitled")
    holdings = p.get("holdings", []) or []
    pid      = p.get("id")

    st.markdown(f"#### {name}")
    res = _cached_track(pid, json.dumps(holdings, sort_keys=True, default=str), holdings, api_key)

    if "error" in res:
        st.warning(res["error"])
        if st.button("🗑 Delete", key=f"del_err_{pid}"):
            delete_tracked_portfolio(pid)
            st.rerun()
        return

    m = res["metrics"]
    tr = float(m.get("Total Return", 0) or 0)
    dd = float(m.get("Max Drawdown", 0) or 0)
    _kpi_row([
        ("Current Value", f"${m.get('Final Value', 0):,.0f}", DARK),
        ("Total Return",  f"{tr:+.1f}%", GREEN if tr >= 0 else RED),
        ("vs S&P 500",    _alpha_str(m.get("vs S&P 500")),
                          GREEN if (isinstance(m.get("vs S&P 500"), (int, float)) and m["vs S&P 500"] >= 0) else RED),
        ("Max Drawdown",  f"{dd:.1f}%", RED),
    ])
    st.caption(f"Tracked since {res['inception_date']}  ·  "
               f"Ann. return {m.get('Ann. Return', 'N/A')}%  ·  Sharpe {m.get('Sharpe Ratio', 'N/A')}")

    # Value vs benchmark vs contributed
    curve = res["curve"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=curve.index, y=curve["Portfolio"],
                             name="Your Portfolio", line=dict(color=BLUE, width=2.5)))
    if "SP500" in curve.columns and not curve["SP500"].isna().all():
        fig.add_trace(go.Scatter(x=curve.index, y=curve["SP500"],
                                 name="S&P 500 (same $)", line=dict(color=MUTED, width=1.5, dash="dot")))
    fig.add_trace(go.Scatter(x=curve.index, y=curve["Contrib"],
                             name="Invested", line=dict(color=AMBER, width=1.3, dash="dash")))
    fig.update_layout(height=340, template="plotly_white",
                      margin=dict(l=0, r=0, t=10, b=0),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02),
                      yaxis=dict(tickprefix="$"), font=dict(family="DM Sans"))
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
        st.caption(f"⚠ {w}")

    _render_edit(pid, holdings, api_key)

    if st.button("🗑 Delete portfolio", key=f"del_{pid}"):
        delete_tracked_portfolio(pid)
        st.cache_data.clear()
        st.rerun()


# ── Entry point ──────────────────────────────────────────────────────────────────
def render_your_portfolios(api_key, is_pro=False):
    email = _current_email()
    if not email:
        _render_login_gate()
        return

    top = st.columns([3, 1])
    top[0].markdown(f"**Signed in as** `{email}`")
    if top[1].button("Sign out", key="yp_signout"):
        st.session_state["user_email"] = ""
        st.query_params.pop("email", None)
        st.rerun()

    _status = _storage_status()
    if _status == "no_creds":
        st.warning("⚠ Storage isn't configured on this app instance — Supabase keys "
                   "(`SUPABASE_URL` / `SUPABASE_KEY`) are missing. On Streamlit Cloud, add "
                   "them in **Settings → Secrets**. Portfolios won't save until then.")
    elif _status == "no_table":
        st.warning("⚠ Connected to Supabase, but the **tracked_portfolios** table isn't in "
                   "*this* project. Run the DDL from `database.py` in this project's SQL editor.")

    _render_create(email, api_key)

    portfolios = load_tracked_portfolios(email)
    if not portfolios:
        st.info("No tracked portfolios yet. Use **➕ New tracked portfolio** above to "
                "start tracking one from today (or any past date).")
        return

    for i, p in enumerate(portfolios):
        _render_card(p, api_key)
        if i < len(portfolios) - 1:
            st.divider()
