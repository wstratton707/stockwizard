"""valuation.py — the "Valuation Lens": price vs. earnings-justified fair value.

Our own take on price-vs-earnings valuation charting. Deep annual EPS and
dividends come from SEC EDGAR (free, ~15 years); long monthly price comes from
yfinance. Both are expressed on TODAY's split basis so the implied P/E stays
continuous across stock splits (otherwise as-reported EPS and split-adjusted
price disagree at every split boundary).

    from valuation import get_valuation_data, build_valuation_figure
    data = get_valuation_data("AAPL")
    fig  = build_valuation_figure(data)      # a Plotly figure, or None
"""
import numpy as np
import pandas as pd
import requests

from data import _sec_load_cik_map, SEC_HEADERS, _sec_fy_series, _SEC_TAGS_EPS

_DIV_TAGS = ["CommonStockDividendsPerShareDeclared",
             "CommonStockDividendsPerShareCashPaid"]

# House palette (kept consistent with the app's Plotly charts)
INK   = "#0f172a"
FAIR  = "#e0871a"     # earnings-justified value line
GREEN = "#4ca66a"     # earnings value fill
GOLD  = "#caa11e"     # dividend-implied line
GRID  = "#e2e8f0"
MUTED = "#94a3b8"


def _cum_split_factor(splits, when):
    """Product of split ratios that occurred AFTER `when`. Divide an as-of-then
    per-share figure by this to express it on today's share basis."""
    if splits is None or len(splits) == 0:
        return 1.0
    f = 1.0
    ref = pd.Timestamp(when)
    for dt, ratio in splits.items():
        try:
            d = pd.Timestamp(dt).tz_localize(None)
            if d > ref and ratio:
                f *= float(ratio)
        except Exception:
            continue
    return f or 1.0


def get_valuation_data(ticker, min_years=6):
    """Return the valuation series for `ticker`, or None if unavailable
    (ETF/crypto/foreign filer, thin EPS history, or a data hiccup)."""
    import yfinance as yf
    tk = (ticker or "").upper()

    # 1) EDGAR annual EPS + dividends (as-reported per fiscal year)
    cik = _sec_load_cik_map(log=lambda m: None).get(tk)
    if not cik:
        return None
    try:
        facts = requests.get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
            headers=SEC_HEADERS, timeout=25).json()
    except Exception:
        return None
    eps_raw = _sec_fy_series(facts, _SEC_TAGS_EPS, "USD/shares")
    div_raw = _sec_fy_series(facts, _DIV_TAGS, "USD/shares")
    if len(eps_raw) < min_years:
        return None

    # 2) yfinance: long monthly UNADJUSTED close + split history
    try:
        t = yf.Ticker(tk)
        hist = t.history(period="max", interval="1mo", auto_adjust=False)
        splits = t.splits
    except Exception:
        return None
    if hist is None or hist.empty or "Close" not in hist.columns:
        return None
    px = hist["Close"].dropna()
    try:
        px.index = pd.to_datetime(px.index).tz_localize(None)
    except (TypeError, AttributeError):
        px.index = pd.to_datetime(px.index)

    # 3) put EPS / dividends / price on today's split basis
    years = sorted(eps_raw)
    eps, div = {}, {}
    for y in years:
        end = eps_raw[y][0]
        f = _cum_split_factor(splits, end)
        eps[y] = eps_raw[y][1] / f
        if y in div_raw:
            div[y] = div_raw[y][1] / f

    # yfinance's Close is already split-adjusted to today's basis, so the price
    # needs NO further adjustment — only the as-reported EDGAR EPS/dividends do.
    price_dates, price_vals = [], []
    for d, v in zip(px.index, px.values):
        if years[0] <= d.year <= years[-1] + 1:
            price_dates.append(d)
            price_vals.append(float(v))
    if len(price_vals) < 12:
        return None

    # 4) normal P/E = median of each year's (avg price / EPS), positive EPS only
    pe_samples = []
    for y in years:
        yr = [v for d, v in zip(price_dates, price_vals) if d.year == y]
        if yr and eps[y] and eps[y] > 0:
            pe_samples.append(float(np.mean(yr)) / eps[y])
    normal_pe = float(np.median(pe_samples)) if pe_samples else 15.0
    normal_pe = max(6.0, min(45.0, normal_pe))

    # "Core" EPS = 3-year rolling median — a transparent proxy for operating
    # earnings that strips single-year GAAP one-offs (tax-reform charges,
    # acquisition write-offs) so the fair-value line reflects sustainable
    # earning power rather than whipsawing on a non-recurring item.
    eps_list = [eps[y] for y in years]

    def _med3(a):
        out = []
        for i in range(len(a)):
            w = [a[j] for j in (i - 1, i, i + 1)
                 if 0 <= j < len(a) and a[j] is not None]
            out.append(float(np.median(w)) if w else a[i])
        return out
    eps_core = _med3(eps_list)

    cur = price_vals[-1]
    cur_eps = next((eps[y] for y in reversed(years) if eps[y] and eps[y] > 0), None)
    return {
        "ticker": tk,
        "years": years,
        "eps": eps_list,
        "eps_core": eps_core,
        "div": [div.get(y) for y in years],
        "price_dates": price_dates,
        "price_vals": price_vals,
        "normal_pe": round(normal_pe, 1),
        "current_price": round(cur, 2),
        "blended_pe": (round(cur / cur_eps, 1) if cur_eps else None),
    }


def build_valuation_figure(data):
    """Plotly figure for the valuation series, in the app's house style.
    Returns None if data is missing."""
    if not data or not data.get("years"):
        return None
    import plotly.graph_objects as go

    yrs   = data["years"]
    eps   = data["eps"]
    core  = data.get("eps_core") or eps
    npe   = data["normal_pe"]
    xyr   = [pd.Timestamp(f"{y}-06-30") for y in yrs]            # mid-year anchor
    fair  = [(e * npe) if (e and e > 0) else None for e in core]
    over  = [(f * 1.4) if f else None for f in fair]
    divln = [(d / 0.05) if d else None for d in data["div"]]     # ~5% yield-implied

    fig = go.Figure()

    # Earnings-justified value (dark green area under the fair-value line)
    fig.add_trace(go.Scatter(
        x=xyr, y=fair, mode="lines", line=dict(width=0),
        fill="tozeroy", fillcolor="rgba(76,166,106,.34)",
        name="Earnings value", hoverinfo="skip", showlegend=False))
    # Premium band (lighter green between fair and 1.4x fair)
    fig.add_trace(go.Scatter(
        x=xyr, y=over, mode="lines", line=dict(width=0),
        fill="tonexty", fillcolor="rgba(146,206,166,.22)",
        name="Premium band", hoverinfo="skip", showlegend=False))

    # Monthly price (black)
    fig.add_trace(go.Scatter(
        x=data["price_dates"], y=data["price_vals"], mode="lines",
        line=dict(color=INK, width=1.4), name="Price",
        hovertemplate="%{x|%b %Y}<br>$%{y:,.2f}<extra>Price</extra>"))

    # Dividend-implied value (gold)
    if any(v is not None for v in divln):
        fig.add_trace(go.Scatter(
            x=xyr, y=divln, mode="lines",
            line=dict(color=GOLD, width=1.6), name="Dividend value",
            hovertemplate="%{x|%Y}<br>$%{y:,.0f}<extra>Dividend value</extra>"))

    # Earnings-justified value line (orange) + year markers
    fig.add_trace(go.Scatter(
        x=xyr, y=fair, mode="lines+markers",
        line=dict(color=FAIR, width=2.4),
        marker=dict(symbol="triangle-up", size=7, color="#ffffff",
                    line=dict(color=FAIR, width=1.4)),
        name=f"Fair value (EPS &times; {npe:g})",
        customdata=core,
        hovertemplate="%{x|%Y}<br>Fair value $%{y:,.0f}"
                      "<br>Core EPS $%{customdata:.2f}<extra></extra>"))

    # US recession shading (COVID 2020; 2008 predates the EPS window)
    fig.add_vrect(x0="2020-02-01", x1="2020-04-30",
                  fillcolor="rgba(100,116,139,.12)", line_width=0, layer="below")

    fig.update_layout(
        height=440, template=None,
        plot_bgcolor="#ffffff", paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=16, t=30, b=30),
        hovermode="x unified",
        font=dict(family="DM Sans, system-ui, sans-serif"),
        hoverlabel=dict(bgcolor=INK, bordercolor="#334155",
                        font=dict(color="white", size=12, family="DM Sans")),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    font=dict(size=11, family="DM Sans", color="#64748b"),
                    bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(type="date", tickformat="%Y", dtick="M24",
                   tickfont=dict(size=11, color=MUTED, family="DM Sans"),
                   gridcolor=GRID, showline=True, linecolor=GRID, zeroline=False),
        yaxis=dict(tickprefix="$", tickformat=",.0f", side="right",
                   tickfont=dict(size=11, color=MUTED, family="DM Sans"),
                   gridcolor=GRID, showline=False, zeroline=False, rangemode="tozero"),
    )
    return fig
