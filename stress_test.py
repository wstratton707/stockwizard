"""
stress_test.py — Portfolio Stress Test & Autopsy
Two features:
  1. Stress Test    — run a portfolio through 5 historical crashes, see scenario returns vs S&P
  2. Portfolio Autopsy — upload a CSV of holdings, see which positions drove P&L
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import chart_theme as ct
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import time
from disclaimers import render_section, render_inline
import disclaimers as _disc

POLYGON_BASE  = "https://api.polygon.io"
_STRESS_CACHE: dict = {}
CACHE_TTL     = 3600

# ── Historical crash scenarios ─────────────────────────────────────────────────
# `start`/`end` and `market_shock` MUST describe the same window: holdings with
# price history are replayed over [start, end], while holdings too new fall back
# to beta × market_shock, and both land in the same portfolio average. The 2008
# entry used to pair a 2008-09-01 start (Lehman) with the −56% figure, which is
# measured from the October-2007 peak — so the replayed names fell ~47% while the
# estimated ones fell 56% × beta, and the card printed "−56%" beside a computed
# SPY of −47%. The window now matches the number.
#
# This dict is the single definition of these scenarios; the Portfolio Builder's
# backtest section reads it too, so the same crash can't mean two different date
# ranges in two different tabs.
CRASH_SCENARIOS: dict[str, dict] = {
    "2008 Financial Crisis": {
        "start": "2007-10-09", "end": "2009-03-09",
        "description":  "Oct-2007 peak to Mar-2009 trough  ·  S&P 500: −57%",
        "market_shock": -56.8,
        "color":        "#dc2626",
    },
    "COVID Crash (2020)": {
        "start": "2020-02-19", "end": "2020-03-23",
        "description":  "Pandemic lockdown  ·  S&P 500: −34% in 33 days",
        "market_shock": -34.0,
        "color":        "#ea580c",
    },
    "2022 Rate-Hike Bear": {
        "start": "2022-01-03", "end": "2022-10-12",
        "description":  "Fed rate hikes  ·  S&P 500: −25%, NASDAQ: −35%",
        "market_shock": -25.0,
        "color":        "#d97706",
    },
    "Dot-com Bust (2000–2002)": {
        "start": "2000-03-10", "end": "2002-10-09",
        "description":  "Tech bubble burst  ·  S&P 500: −49%, NASDAQ: −78%",
        "market_shock": -49.0,
        "color":        "#7c3aed",
    },
    "2018 Q4 Selloff": {
        "start": "2018-09-20", "end": "2018-12-24",
        "description":  "Fed tightening fears  ·  S&P 500: −20% in 13 weeks",
        "market_shock": -19.8,
        "color":        "#0369a1",
    },
}

# The subset that can fall inside a ~5-year backtest window, for the Portfolio
# Builder's "Historical Stress Tests" cards. Ordered most-recent-first.
RECENT_CRASHES = ["2022 Rate-Hike Bear", "COVID Crash (2020)", "2018 Q4 Selloff"]


# ── Parametric (beta-based) stress engine ──────────────────────────────────────
# The free Polygon tier can't reach the historical crash windows (2008/2020/…),
# so instead of replaying prices we ESTIMATE each holding's move as
# beta × the known S&P decline for that crash. Beta comes from ~2y of recent
# daily returns (which the free tier does provide), so the stress test works for
# any portfolio — including stocks that didn't exist in 2008.

def _estimate_betas(tickers: list, api_key: str, log=lambda m: None) -> tuple:
    """({ticker: beta_vs_SPY}, {ticker: ann_vol_pct}) from ~2y of daily returns.
    A ticker is simply absent when its data can't be fetched/aligned."""
    from portfolio_data import fetch_portfolio_prices
    all_t = list(dict.fromkeys(list(tickers) + ["SPY"]))
    try:
        _, _close_df, returns_df, _failed = fetch_portfolio_prices(
            all_t, period_years=2, api_key=api_key, log=log)
    except Exception:
        return {}, {}
    if returns_df is None or "SPY" not in returns_df.columns:
        return {}, {}
    spy     = returns_df["SPY"]
    var_spy = float(spy.var())
    betas, vols = {}, {}
    for t in tickers:
        if t in returns_df.columns and var_spy > 0:
            aligned = pd.concat([returns_df[t], spy], axis=1).dropna()
            if len(aligned) >= 30:
                betas[t] = float(aligned.iloc[:, 0].cov(aligned.iloc[:, 1]) / var_spy)
                vols[t]  = float(returns_df[t].std() * (252 ** 0.5) * 100)
    return betas, vols


def _real_window_return(ticker: str, start: str, end: str, api_key: str) -> float | None:
    """Actual % return for a ticker over a historical window, or None if the
    ticker has no data spanning it (e.g. it IPO'd after the crash)."""
    try:
        from market_data import get_bars
        df = get_bars(ticker, start, end, interval="day", polygon_key=api_key)
        if df is not None and len(df) >= 2:
            first, last = float(df["Close"].iloc[0]), float(df["Close"].iloc[-1])
            if first > 0:
                return (last / first - 1) * 100
    except Exception:
        pass
    return None


def run_stress_test(tickers: list, weights: dict, api_key: str) -> dict:
    """
    Hybrid stress test. For each crash, each holding uses its REAL historical
    performance over the window when it has data (yfinance gives deep history);
    holdings too new to have existed fall back to a beta × (S&P decline) estimate.
    Returns the shape the renderer expects:
      {scenario: {ticker: ret | None, '__portfolio__', '__spy__', '__beta__',
                  '__estimated__': bool}}
    """
    betas = None   # lazily computed only if a fallback estimate is actually needed

    results: dict = {}
    for scenario_name, scenario in CRASH_SCENARIOS.items():
        shock        = scenario["market_shock"]
        start, end   = scenario["start"], scenario["end"]
        scenario_ret: dict = {}
        weighted_sum = weight_total = 0.0
        any_estimated = False

        for t in tickers:
            real = _real_window_return(t, start, end, api_key)
            if real is not None:
                ret = real                                  # actual historical move
            else:
                if betas is None:                           # compute betas once, on demand
                    betas, _ = _estimate_betas(tickers, api_key)
                b = betas.get(t)
                if b is None:
                    scenario_ret[t] = None                  # no data and no beta
                    continue
                ret = max(b * shock, -95.0)                 # estimated; floor at ~-100%
                any_estimated = True
            scenario_ret[t] = round(ret, 2)
            w = weights.get(t, 1 / len(tickers))
            weighted_sum += ret * w
            weight_total += w

        spy_real = _real_window_return("SPY", start, end, api_key)
        scenario_ret["__portfolio__"] = round(weighted_sum / weight_total, 2) if weight_total > 0 else None
        scenario_ret["__spy__"]       = round(spy_real, 2) if spy_real is not None else shock
        scenario_ret["__estimated__"] = any_estimated
        results[scenario_name] = scenario_ret

    return results


# ── Rendering ─────────────────────────────────────────────────────────────────

def render_stress_test(api_key: str, is_pro: bool = False):
    """Main entry point — renders Stress Test & Portfolio Autopsy."""

    st.markdown("""
    <div style="background:linear-gradient(135deg,var(--brand-1),var(--brand-2));border-radius:14px;
                padding:1.6rem 1.8rem;color:#e2e8f0;margin-bottom:1.2rem">
        <div style="font-size:1.5rem;font-weight:700;color:#fff">Stress Test &amp; Portfolio Autopsy</div>
        <div style="font-size:0.95rem;color:#b0c4de;margin-top:0.4rem;max-width:640px">
            Run your holdings through historical crashes — upload a CSV to see what broke
            your portfolio and how it would behave next time.
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 1 — STRESS TEST
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("""
    <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;
                color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                margin-bottom:1rem;margin-top:0.5rem">Historical Crash Scenarios</div>
    """, unsafe_allow_html=True)

    st.markdown(
        "<div style='font-size:0.83rem;color:#6b7a8d;margin-bottom:1rem'>"
        "See how your holdings <b>actually performed</b> through history's worst crashes — real returns over "
        "each crash window. Any position too new to have existed back then falls back to a beta-based "
        "estimate, so it works for any portfolio.</div>",
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns([3, 1])
    with c1:
        tickers_raw = st.text_input(
            "Tickers (comma-separated)",
            placeholder="e.g.  AAPL, MSFT, NVDA, AGG",
            key="stress_tickers",
        )
    with c2:
        portfolio_value = st.number_input(
            "Portfolio Value ($)",
            min_value=1_000, max_value=100_000_000, value=100_000, step=1_000,
            key="stress_port_val",
        )

    weights_raw = st.text_input(
        "Weights % — optional, comma-separated (leave blank for equal weight)",
        placeholder="e.g.  30, 25, 25, 20",
        key="stress_weights",
    )

    run_stress = st.button("▶  Run Stress Test", type="primary", key="run_stress_btn")

    if run_stress and tickers_raw.strip():
        tickers = [t.strip().upper() for t in tickers_raw.split(",") if t.strip()]

        # Parse weights
        if weights_raw.strip():
            try:
                raw_w = [float(w.strip()) for w in weights_raw.split(",") if w.strip()]
                if len(raw_w) != len(tickers):
                    st.error(f"Number of weights ({len(raw_w)}) must match tickers ({len(tickers)}).")
                    st.stop()
                total_w = sum(raw_w)
                if total_w <= 0:
                    st.error("Weights must add up to more than zero.")
                    st.stop()
                weights = {t: w / total_w for t, w in zip(tickers, raw_w)}
            except ValueError:
                st.error("Invalid weights — enter numbers separated by commas.")
                st.stop()
        else:
            weights = {t: 1.0 / len(tickers) for t in tickers}

        with st.spinner("Estimating portfolio sensitivity (beta) and stressing each scenario..."):
            results = run_stress_test(tickers, weights, api_key)

        # ── Scenario cards ─────────────────────────────────────────────────────
        st.markdown("""
        <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;
                    color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                    margin-bottom:1rem;margin-top:1.5rem">Results</div>
        """, unsafe_allow_html=True)

        if all(d.get("__portfolio__") is None for d in results.values()):
            st.markdown(
                "<div style='background:#fffbeb;border:1px solid #fde68a;border-radius:8px;"
                "padding:1rem 1.25rem;font-size:0.84rem;color:#92400e;line-height:1.55;"
                "margin-bottom:1rem'><b>Couldn’t run the stress test right now.</b> We need historical "
                "prices for your tickers (and SPY), and the data came back empty — most likely a temporary "
                "rate limit or an unrecognized symbol. Try again in a minute.</div>",
                unsafe_allow_html=True,
            )
        else:
            _any_est = any(d.get("__estimated__") for d in results.values())
            _note = ("Real historical performance over each crash window."
                     if not _any_est else
                     "Real historical performance over each crash window; positions too new to have existed "
                     "then are beta-estimated.")
            st.markdown(
                f"<div style='font-size:0.78rem;color:#64748b;margin-bottom:0.9rem'>{_note} "
                f"Past crashes don't predict future ones — correlations often rise in a crisis.</div>",
                unsafe_allow_html=True,
            )

        for scenario_name, scenario_data in results.items():
            port_ret    = scenario_data.get("__portfolio__")
            desc        = CRASH_SCENARIOS[scenario_name]["description"]

            # No price data for this crash window (e.g. paywalled deep history)
            if port_ret is None:
                st.markdown(f"""
                <div style="background:#f8fafc;border:1px dashed #cbd5e1;border-radius:8px;
                            padding:0.85rem 1.25rem;margin-bottom:0.7rem">
                    <div style="font-weight:600;font-size:0.88rem;color:#475569">{scenario_name}</div>
                    <div style="font-size:0.73rem;color:#94a3b8">{desc}&nbsp;·&nbsp;Couldn’t estimate — no recent data</div>
                </div>""", unsafe_allow_html=True)
                continue

            spy_ret     = scenario_data.get("__spy__")
            port_dollar = portfolio_value * port_ret / 100

            card_color  = "#dc2626" if port_ret < 0 else "#16a34a"
            bg          = "rgba(220,38,38,0.04)" if port_ret < 0 else "rgba(22,163,74,0.04)"
            border      = "#fca5a5" if port_ret < 0 else "#86efac"

            # Per-ticker chips
            chip_parts = []
            for t in tickers:
                tr = scenario_data.get(t)
                tc = "#dc2626" if (tr or 0) < 0 else "#16a34a"
                val_str = f"{tr:+.1f}%" if tr is not None else "N/A"
                chip_parts.append(
                    f"<span style='font-family:\"JetBrains Mono\",monospace;font-size:0.73rem'>"
                    f"<span style='color:#64748b'>{t}</span>&nbsp;"
                    f"<span style='color:{tc};font-weight:500'>{val_str}</span></span>"
                )
            chips_html = "<span style='color:#cbd5e1'> &nbsp;·&nbsp; </span>".join(chip_parts)

            # vs S&P delta
            if spy_ret is not None:
                delta     = port_ret - spy_ret
                delta_col = "#16a34a" if delta >= 0 else "#dc2626"
                spy_html  = (f"<span style='color:{delta_col};font-size:0.72rem'>"
                             f"{delta:+.1f}% vs S&P 500</span>")
            else:
                spy_html = ""

            st.markdown(f"""
            <div style="background:{bg};border:1px solid {border};border-radius:8px;
                        padding:1rem 1.25rem;margin-bottom:0.7rem">
                <div style="display:flex;justify-content:space-between;align-items:flex-start">
                    <div>
                        <div style="font-weight:600;font-size:0.88rem;color:#0f172a;
                                    margin-bottom:2px">{scenario_name}</div>
                        <div style="font-size:0.73rem;color:#6b7a8d">{desc}</div>
                    </div>
                    <div style="text-align:right;flex-shrink:0;margin-left:1rem">
                        <div style="font-family:'JetBrains Mono',monospace;font-size:1.45rem;
                                    font-weight:600;color:{card_color};line-height:1">{port_ret:+.1f}%</div>
                        <div style="font-size:0.72rem;color:#6b7a8d;margin-top:2px">
                            ${port_dollar:+,.0f}&nbsp;&nbsp;{spy_html}
                        </div>
                    </div>
                </div>
                <div style="border-top:1px solid {border};margin-top:0.65rem;padding-top:0.55rem">
                    {chips_html}
                </div>
            </div>
            """, unsafe_allow_html=True)

        # ── Portfolio vs S&P bar chart (available scenarios only) ──────────────
        s_names   = [s for s in results if results[s].get("__portfolio__") is not None]
        if s_names:
            p_rets    = [results[s]["__portfolio__"] for s in s_names]
            spy_rets  = [results[s].get("__spy__") or 0 for s in s_names]

            fig = go.Figure()
            fig.add_trace(go.Bar(
                name="Your Portfolio",
                x=s_names,
                y=p_rets,
                # Sign carries the meaning here — a scenario is a gain or a loss.
                marker_color=[ct.color.positive if r >= 0 else ct.color.negative
                              for r in p_rets],
                text=[f"{r:+.1f}%" for r in p_rets],
                textposition="outside",
                textfont=dict(size=ct.font.size.axis, family=ct.font.data),
            ))
            fig.add_trace(go.Bar(
                name="S&P 500 (SPY)",
                x=s_names,
                y=spy_rets,
                # The benchmark is context, not a result: unsigned and recessive.
                marker_color=ct._rgba(ct.color.ink_muted, 0.55),
                text=[f"{r:+.1f}%" for r in spy_rets],
                textposition="outside",
                textfont=dict(size=ct.font.size.axis, family=ct.font.data),
            ))
            ct.style(
                fig,
                height=380,
                # automargin reserves whatever the angled scenario names need,
                # instead of the hardcoded 90px bottom margin this used to carry.
                x=ct.category_axis(tickangle=-18, automargin=True),
                # Zero is the win/lose boundary on this chart, so it stays drawn.
                y=ct.pct_axis(zeroline=True, zerolinecolor=ct.color.rule,
                              zerolinewidth=ct.stroke.rule),
                legend="top-left",
                crosshair=False,
                barmode="group",
            )
            st.plotly_chart(fig, use_container_width=True)

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 2 — PORTFOLIO AUTOPSY
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("""
    <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;
                color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                margin-bottom:1rem;margin-top:0.25rem">Portfolio Autopsy — What Broke My Holdings?</div>
    """, unsafe_allow_html=True)

    st.markdown(
        "<div style='font-size:0.83rem;color:#6b7a8d;margin-bottom:0.5rem'>"
        "Upload a CSV with columns <b>Ticker</b> and <b>Shares</b> (or <b>Value</b> or <b>Weight</b>) "
        "to see exactly which positions drove your P&amp;L and which holdings moved together.</div>",
        unsafe_allow_html=True,
    )

    # Sample CSV download
    sample_csv = "Ticker,Shares,Price\nAAPL,50,180.00\nMSFT,30,420.00\nNVDA,20,850.00\nAGG,100,95.00\n"
    st.download_button(
        "Download sample CSV",
        data=sample_csv,
        file_name="sample_holdings.csv",
        mime="text/csv",
        key="autopsy_sample_dl",
    )

    col_up, col_per = st.columns([2, 1])
    with col_up:
        uploaded = st.file_uploader("Upload holdings CSV", type=["csv"], key="autopsy_upload")
    with col_per:
        lookback = st.selectbox(
            "Lookback period",
            ["3 months", "6 months", "1 year", "2 years"],
            index=1,
            key="autopsy_period",
        )

    if uploaded is not None:
        try:
            holdings_df = pd.read_csv(uploaded)
            holdings_df.columns = [c.strip() for c in holdings_df.columns]

            # Auto-detect key columns
            def _find_col(df, candidates):
                for name in df.columns:
                    if name.lower() in candidates:
                        return name
                return None

            ticker_col = _find_col(holdings_df, {"ticker", "symbol", "stock"})
            if ticker_col is None:
                st.error("CSV must have a 'Ticker' or 'Symbol' column.")
                st.stop()

            value_col  = _find_col(holdings_df, {"value", "market_value", "amount"})
            shares_col = _find_col(holdings_df, {"shares", "qty", "quantity", "units"})
            price_col  = _find_col(holdings_df, {"price", "cost", "purchase_price", "avg_cost"})
            weight_col = _find_col(holdings_df, {"weight", "allocation", "pct", "percent"})

            tickers = [str(t).strip().upper() for t in holdings_df[ticker_col].tolist() if str(t).strip()]

            period_map   = {"3 months": 0.25, "6 months": 0.5, "1 year": 1.0, "2 years": 2.0}
            period_years = period_map[lookback]

            with st.spinner("Fetching price history..."):
                from portfolio_data import fetch_portfolio_prices
                try:
                    _, close_df, returns_df, failed = fetch_portfolio_prices(
                        tickers, period_years=period_years, api_key=api_key,
                    )
                except Exception as e:
                    st.error(f"Data fetch failed: {e}")
                    st.stop()

            if failed:
                st.warning(f"Could not fetch data for: {', '.join(failed)}")

            avail = [t for t in tickers if t in close_df.columns]
            if not avail:
                st.error("No valid price data for the tickers provided.")
                st.stop()

            # Build weights from CSV
            holdings_df = holdings_df[holdings_df[ticker_col].str.strip().str.upper().isin(avail)].copy()
            holdings_df[ticker_col] = holdings_df[ticker_col].str.strip().str.upper()

            if value_col:
                values = holdings_df.set_index(ticker_col)[value_col].astype(float)
                weights = (values / values.sum()).to_dict()
            elif shares_col and price_col:
                holdings_df["_val"] = holdings_df[shares_col].astype(float) * holdings_df[price_col].astype(float)
                values = holdings_df.set_index(ticker_col)["_val"].astype(float)
                weights = (values / values.sum()).to_dict()
            elif shares_col:
                # Use latest price from fetched data to compute values
                latest_prices = {t: float(close_df[t].dropna().iloc[-1]) for t in avail if t in close_df.columns}
                holdings_df["_val"] = holdings_df.apply(
                    lambda r: float(r[shares_col]) * latest_prices.get(r[ticker_col], 1), axis=1
                )
                values = holdings_df.set_index(ticker_col)["_val"].astype(float)
                weights = (values / values.sum()).to_dict()
            elif weight_col:
                wvals  = holdings_df.set_index(ticker_col)[weight_col].astype(float)
                weights = (wvals / wvals.sum()).to_dict()
            else:
                weights = {t: 1.0 / len(avail) for t in avail}

            # Period returns per position
            pos_returns: dict[str, float] = {}
            for t in avail:
                prices = close_df[t].dropna()
                if len(prices) >= 2:
                    pos_returns[t] = (prices.iloc[-1] - prices.iloc[0]) / prices.iloc[0] * 100

            # P&L contribution = weight × position return
            contributions = {
                t: weights.get(t, 0) * pos_returns.get(t, 0)
                for t in avail if t in pos_returns
            }
            total_port_ret = sum(contributions.values())

            # Sort worst to best
            sorted_items = sorted(contributions.items(), key=lambda x: x[1])

            # ── Summary banner ────────────────────────────────────────────────
            ret_color = "#16a34a" if total_port_ret >= 0 else "#dc2626"
            losers    = sum(1 for v in contributions.values() if v < 0)
            gainers   = sum(1 for v in contributions.values() if v >= 0)

            st.markdown(f"""
            <div style="display:flex;gap:1rem;margin-bottom:1.25rem;flex-wrap:wrap">
                <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
                            padding:1rem 1.5rem;flex:1;min-width:140px">
                    <div style="font-size:0.65rem;font-weight:600;letter-spacing:0.5px;
                                text-transform:uppercase;color:#64748b;margin-bottom:0.35rem">
                        Portfolio Return ({lookback})</div>
                    <div style="font-family:'JetBrains Mono',monospace;font-size:1.35rem;
                                font-weight:600;color:{ret_color}">{total_port_ret:+.2f}%</div>
                </div>
                <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
                            padding:1rem 1.5rem;flex:1;min-width:140px">
                    <div style="font-size:0.65rem;font-weight:600;letter-spacing:0.5px;
                                text-transform:uppercase;color:#64748b;margin-bottom:0.35rem">
                        Positions</div>
                    <div style="font-family:'JetBrains Mono',monospace;font-size:1.35rem;
                                font-weight:600;color:#0f172a">{len(avail)}</div>
                </div>
                <div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;
                            padding:1rem 1.5rem;flex:1;min-width:140px">
                    <div style="font-size:0.65rem;font-weight:600;letter-spacing:0.5px;
                                text-transform:uppercase;color:#64748b;margin-bottom:0.35rem">
                        Hurt Portfolio</div>
                    <div style="font-family:'JetBrains Mono',monospace;font-size:1.35rem;
                                font-weight:600;color:#dc2626">{losers}</div>
                </div>
                <div style="background:#f0fdf4;border:1px solid #86efac;border-radius:8px;
                            padding:1rem 1.5rem;flex:1;min-width:140px">
                    <div style="font-size:0.65rem;font-weight:600;letter-spacing:0.5px;
                                text-transform:uppercase;color:#64748b;margin-bottom:0.35rem">
                        Helped Portfolio</div>
                    <div style="font-family:'JetBrains Mono',monospace;font-size:1.35rem;
                                font-weight:600;color:#16a34a">{gainers}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # ── P&L attribution bar chart ─────────────────────────────────────
            ctickers  = [x[0] for x in sorted_items]
            cvalues   = [x[1] for x in sorted_items]

            fig_at = go.Figure()
            fig_at.add_trace(go.Bar(
                x=ctickers,
                y=cvalues,
                # Helped vs hurt is the whole point of this chart — keep it by sign.
                marker_color=[ct.color.positive if v >= 0 else ct.color.negative
                              for v in cvalues],
                text=[f"{v:+.2f}%" for v in cvalues],
                textposition="outside",
                textfont=dict(size=ct.font.size.axis, family=ct.font.data),
                hovertemplate="<b>%{x}</b><br>P&L contribution: %{y:+.2f}%<extra></extra>",
            ))
            fig_at.add_hline(y=0, line_color=ct.color.rule, line_width=ct.stroke.rule)
            ct.style(
                fig_at,
                height=320,
                x=ct.category_axis(automargin=True),
                y=ct.pct_axis(tick_format=".1f"),
                legend=None,
                crosshair=False,
            )
            st.plotly_chart(fig_at, use_container_width=True)

            # ── Position breakdown table ──────────────────────────────────────
            st.markdown("""
            <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;
                        color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                        margin-bottom:1rem;margin-top:0.75rem">Position Breakdown</div>
            """, unsafe_allow_html=True)

            detail_rows = []
            for t, contrib in sorted_items:
                pos_ret = pos_returns.get(t, 0)
                w       = weights.get(t, 0)
                detail_rows.append({
                    "Ticker":             t,
                    "Weight":             f"{w * 100:.1f}%",
                    "Return":             f"{pos_ret:+.2f}%",
                    "P&L Contribution":   f"{contrib:+.2f}%",
                    "Verdict":            "Helped" if contrib >= 0 else "Hurt",
                })
            st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)

            # ── Correlation culprit analysis ──────────────────────────────────
            if len(avail) >= 2:
                st.markdown("""
                <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;
                            color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                            margin-bottom:1rem;margin-top:1.25rem">Correlation Culprits</div>
                """, unsafe_allow_html=True)
                st.markdown(
                    "<div style='font-size:0.82rem;color:#6b7a8d;margin-bottom:0.75rem'>"
                    "When your losers move together, drawdowns get amplified — "
                    "high correlation between red positions is the hidden risk.</div>",
                    unsafe_allow_html=True,
                )

                corr    = returns_df[avail].corr()
                losers  = [t for t in avail if pos_returns.get(t, 0) < 0]

                if len(losers) >= 2:
                    culprits = []
                    for i, t1 in enumerate(losers):
                        for t2 in losers[i + 1:]:
                            if t1 in corr.index and t2 in corr.columns:
                                c = float(corr.loc[t1, t2])
                                if c > 0.5:
                                    culprits.append((t1, t2, round(c, 2)))
                    culprits.sort(key=lambda x: -x[2])

                    if culprits:
                        chips = " · ".join(
                            f"<span style='font-family:\"JetBrains Mono\",monospace;font-size:0.76rem'>"
                            f"<span style='color:#dc2626'>{p[0]} ↔ {p[1]}</span> "
                            f"<span style='color:#7f1d1d;font-weight:500'>ρ={p[2]}</span></span>"
                            for p in culprits[:6]
                        )
                        st.markdown(
                            f"<div style='background:#fef2f2;border:1px solid #fca5a5;border-radius:6px;"
                            f"padding:0.75rem 1rem;font-size:0.82rem;margin-bottom:1rem'>"
                            f"Highly correlated losing pairs: {chips}</div>",
                            unsafe_allow_html=True,
                        )
                    else:
                        st.success("No highly correlated loser pairs — your losers moved independently.")

                # Heatmap
                fig_corr = go.Figure(go.Heatmap(
                    z=corr.values,
                    x=corr.columns.tolist(),
                    y=corr.index.tolist(),
                    colorscale=ct.color.diverging,
                    zmin=-1, zmax=1,
                    text=[[f"{v:.2f}" for v in row] for row in corr.values],
                    texttemplate="%{text}",
                    textfont=dict(size=ct.font.size.axis, family=ct.font.data),
                    colorbar=dict(thickness=12, len=0.8),
                    hovertemplate="%{x} ↔ %{y}: %{z:.2f}<extra></extra>",
                ))
                ct.style(
                    fig_corr,
                    height=max(300, len(avail) * 42 + 80),
                    x=ct.category_axis(automargin=True),
                    y=ct.category_axis(automargin=True),
                    grid=False,
                    legend=None,
                    crosshair=False,
                )
                st.plotly_chart(fig_corr, use_container_width=True)

        except Exception as e:
            # st.stop() and st.rerun() raise control-flow exceptions that subclass
            # Exception, so a bare `except Exception` swallows them. Every
            # st.stop() above (missing Ticker column, failed fetch, no valid
            # prices) was being reported as "check the columns" instead of the
            # specific message it had just written. Matched by class name rather
            # than by import: the module path moved between Streamlit versions.
            if type(e).__name__ in ("StopException", "RerunException"):
                raise
            st.error(f"Couldn't process that CSV — check the columns and try again. ({e})")
            import traceback as _tb; print(_tb.format_exc())   # server log, not UI

    st.markdown(render_section("Stress Test Methodology", _disc.STRESS_TEST),
                unsafe_allow_html=True)
    st.markdown(render_inline(_disc.SHORT), unsafe_allow_html=True)
