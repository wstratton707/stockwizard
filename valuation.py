"""valuation.py — the "Valuation Lens": price vs. earnings-justified fair value.

Our own take on price-vs-earnings valuation charting. Deep annual EPS and
dividends come from SEC EDGAR (free, ~15 years); long monthly price comes from
yfinance. Both are expressed on TODAY's split basis so the implied P/E stays
continuous across stock splits (otherwise as-reported EPS and split-adjusted
price disagree at every split boundary).

    from valuation import get_valuation_data, build_valuation_figure
    data = get_valuation_data("AAPL")
    fig  = build_valuation_figure(data)      # a Plotly figure, or None

Chart anatomy
-------------
`build_valuation_figure` returns a single figure of three stacked, x-linked
bands:

    ┌────────────────────────────────────────┐
    │ RANGE TABLE     Year / High / Low      │   row 1
    ├────────────────────────────────────────┤
    │ PLOT AREA       price vs fair value    │   row 2
    ├────────────────────────────────────────┤
    │ FUNDAMENTALS    FY / EPS / Chg / Div   │   row 3
    └────────────────────────────────────────┘

The two data grids are subplots sharing the plot's x-axis rather than HTML
tables beside it. That is deliberate: the columns must line up with the year
ticks exactly, and any layout computed independently of Plotly's internal
margins will drift. Sharing the axis makes the alignment structural.

Data density is the point. A valuation chart surrounded by numbers reads as
research; the same chart alone reads as decoration.
"""
import numpy as np
import pandas as pd
import requests

from data import _sec_load_cik_map, SEC_HEADERS, _sec_fy_series, _SEC_TAGS_EPS
import chart_theme as ct
from chart_tokens import color, stroke, marker, font, layout

_DIV_TAGS = ["CommonStockDividendsPerShareDeclared",
             "CommonStockDividendsPerShareCashPaid"]

# Premium band ceiling, as a multiple of the earnings-justified line.
PREMIUM_MULTIPLE = 1.4
# Yield used to convert a dividend into an implied value (~5%).
DIVIDEND_YIELD_BASIS = 0.05

# US recessions that can fall inside a ~15-year EPS window.
RECESSIONS = [("2020-02-01", "2020-04-30")]


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


def _sec_quarterly_eps(facts, tags, unit="USD/shares"):
    """{period_end -> EPS} for single quarters, as filed.

    Selected by period DURATION rather than by `fp`, because a Q3 10-Q reports
    both the three-month and the nine-month figure and labels both "Q3" — going
    by the label silently mixes quarters with year-to-date and inflates TTM.

    Q4 is almost never filed on its own (it lands inside the 10-K), so it is
    derived per fiscal year as FY minus the three quarters that fall inside it.
    Later filings win on a restatement, since EDGAR lists entries oldest-first.
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    quarters, annuals = {}, {}
    for tag in tags:                                  # priority order
        node = gaap.get(tag)
        if not node:
            continue
        q_tag, a_tag = {}, {}
        for e in node.get("units", {}).get(unit) or []:
            start, end, val = e.get("start"), e.get("end"), e.get("val")
            if not start or not end or val is None:
                continue
            try:
                s, d = pd.Timestamp(start), pd.Timestamp(end)
            except Exception:
                continue
            days = (d - s).days
            # Entries are oldest-first, so a later one is a restatement and wins.
            if 80 <= days <= 100:
                q_tag[d] = float(val)
            elif 350 <= days <= 380:
                a_tag[d] = float(val)
        for d, v in q_tag.items():
            quarters.setdefault(d, v)                 # higher-priority tag wins
        for d, v in a_tag.items():
            annuals.setdefault(d, v)

    # Derive the missing Q4 from the annual total where three quarters are known.
    for fy_end, fy_val in annuals.items():
        inside = [q for q in quarters if fy_end - pd.Timedelta(days=360) < q < fy_end]
        if len(inside) == 3 and fy_end not in quarters:
            quarters[fy_end] = fy_val - sum(quarters[q] for q in inside)
    return quarters


def _ttm_series(quarters, splits):
    """(dates, TTM EPS) on today's split basis, one point per quarter.

    TTM is the sum of four consecutive quarters, which is what lets the
    fair-value line move within a fiscal year instead of stepping once a year.
    A gap in the filings breaks the window rather than summing across it.
    """
    if not quarters:
        return [], []
    ends = sorted(quarters)
    dates, vals = [], []
    for i in range(3, len(ends)):
        window = ends[i - 3:i + 1]
        # Four consecutive quarter-END dates span ~three quarters (~270 days),
        # not a year — the first quarter's start sits 90 days before its end.
        # Anything outside this band means a missing filing, so skip rather than
        # sum across the gap and under-report a year of earnings.
        if not (240 <= (window[-1] - window[0]).days <= 310):
            continue
        total = sum(quarters[q] for q in window)
        f = _cum_split_factor(splits, window[-1])
        dates.append(window[-1])
        vals.append(total / f)
    return dates, vals


def _naive_index(idx):
    """Drop timezone so EDGAR fiscal years and yfinance bars compare cleanly."""
    try:
        return pd.to_datetime(idx).tz_localize(None)
    except (TypeError, AttributeError):
        return pd.to_datetime(idx)


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

    # 2) yfinance: long monthly UNADJUSTED OHLC + split history
    try:
        t = yf.Ticker(tk)
        hist = t.history(period="max", interval="1mo", auto_adjust=False)
        splits = t.splits
    except Exception:
        return None
    if hist is None or hist.empty or "Close" not in hist.columns:
        return None
    hist = hist.copy()
    hist.index = _naive_index(hist.index)
    px = hist["Close"].dropna()

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

    # 3b) annual high/low for the range table. The OHLC frame is already in
    # hand — previously only Close survived and the rest was discarded, so this
    # costs no extra network call. Monthly bars carry intra-month extremes, so
    # a yearly max/min over them is a true annual high/low.
    high_by_y, low_by_y = {}, {}
    if {"High", "Low"}.issubset(hist.columns):
        try:
            gh = hist["High"].dropna().groupby(hist["High"].dropna().index.year).max()
            gl = hist["Low"].dropna().groupby(hist["Low"].dropna().index.year).min()
            high_by_y = {int(k): float(v) for k, v in gh.items()}
            low_by_y = {int(k): float(v) for k, v in gl.items()}
        except Exception:
            high_by_y, low_by_y = {}, {}

    # 4) normal P/E = median of each year's (avg price / EPS), positive EPS only.
    #    The per-year samples are kept this time — they are what makes the
    #    fundamentals grid worth reading.
    pe_samples, pe_by_year = [], {}
    for y in years:
        yr = [v for d, v in zip(price_dates, price_vals) if d.year == y]
        if yr and eps[y] and eps[y] > 0:
            p = float(np.mean(yr)) / eps[y]
            pe_samples.append(p)
            pe_by_year[y] = p
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

    # Year-over-year EPS change. Undefined off a non-positive base: a swing from
    # -$1.00 to +$0.50 is not "+150% growth", and printing it as such is worse
    # than printing nothing.
    eps_chg = [None]
    for i in range(1, len(eps_list)):
        prev, cur_e = eps_list[i - 1], eps_list[i]
        eps_chg.append((cur_e / prev - 1) * 100
                       if (prev and prev > 0 and cur_e is not None) else None)

    # Quarterly trailing-twelve-month EPS. Optional: every consumer falls back
    # to the annual anchors when a filer doesn't give us clean quarters.
    try:
        ttm_dates, ttm_eps = _ttm_series(
            _sec_quarterly_eps(facts, _SEC_TAGS_EPS), splits)
    except Exception:
        ttm_dates, ttm_eps = [], []
    if ttm_dates:
        _lo, _hi = years[0], years[-1] + 1
        _keep = [(d, v) for d, v in zip(ttm_dates, ttm_eps) if _lo <= d.year <= _hi]
        ttm_dates = [d for d, _ in _keep]
        ttm_eps = [v for _, v in _keep]

    cur = price_vals[-1]
    cur_eps = next((eps[y] for y in reversed(years) if eps[y] and eps[y] > 0), None)
    return {
        "ticker": tk,
        "years": years,
        "ttm_dates": ttm_dates,
        "ttm_eps": ttm_eps,
        "eps": eps_list,
        "eps_core": eps_core,
        "eps_chg": eps_chg,
        "div": [div.get(y) for y in years],
        "high": [high_by_y.get(y) for y in years],
        "low": [low_by_y.get(y) for y in years],
        "pe_by_year": [pe_by_year.get(y) for y in years],
        "price_dates": price_dates,
        "price_vals": price_vals,
        "normal_pe": round(normal_pe, 1),
        "current_price": round(cur, 2),
        "blended_pe": (round(cur / cur_eps, 1) if cur_eps else None),
    }


# ── Figure construction ──────────────────────────────────────────────────────

def _fy_anchors(years):
    """Mid-year timestamps — annual figures belong at the centre of their year,
    not at a boundary where they would appear to belong to either side."""
    return [pd.Timestamp(f"{y}-06-30") for y in years]


def _grid_font_size(n_years):
    """Shrink grid type rather than dropping columns when history is long.
    Losing years would defeat the density the grid exists to provide."""
    return font.size.grid if n_years <= 16 else 9


def _text_row(fig, xs, labels, y, row, size, color_):
    """One row of a data grid, positioned on the shared x-axis."""
    import plotly.graph_objects as go
    fig.add_trace(go.Scatter(
        x=xs, y=[y] * len(xs), mode="text", text=labels,
        textposition="middle center",
        textfont=dict(size=size, color=color_, family=font.data),
        hoverinfo="skip", showlegend=False), row=row, col=1)


def _row_label(fig, text, y, yref, size):
    """Left-gutter label for a grid row."""
    fig.add_annotation(
        x=0, xref="paper", xanchor="right", xshift=-8,
        y=y, yref=yref, yanchor="middle",
        text=text, showarrow=False,
        font=dict(size=size, color=color.ink_muted, family=font.data))


def _fmt(v, dp=2, dash="—"):
    return dash if v is None else f"{v:,.{dp}f}"


def _fmt_pct(v, dash="—"):
    if v is None:
        return dash
    v = max(-999, min(999, v))          # keep a blowout from widening the column
    return f"{v:+.0f}%"


def window_data(data, years_back=None):
    """Trim the series to the last `years_back` fiscal years for display.

    Display-only. `normal_pe` is deliberately NOT recomputed: it is the stock's
    own long-run multiple, and rebasing it to a 3-year window would redefine
    "fair value" every time the user changed the zoom.
    """
    if not data or not years_back:
        return data
    yrs = data["years"]
    if years_back >= len(yrs):
        return data
    keep = yrs[-years_back:]
    first = keep[0]
    out = dict(data)
    idx = len(yrs) - years_back
    for k in ("years", "eps", "eps_core", "eps_chg", "div", "high", "low",
              "pe_by_year"):
        if isinstance(data.get(k), list):
            out[k] = data[k][idx:]
    pd_, pv_ = [], []
    for d, v in zip(data["price_dates"], data["price_vals"]):
        if d.year >= first:
            pd_.append(d)
            pv_.append(v)
    if pd_:
        out["price_dates"], out["price_vals"] = pd_, pv_
    # The TTM series is trimmed to the same window, but one quarter earlier so
    # the fair-value line is already drawn where price starts rather than
    # beginning a quarter late.
    if data.get("ttm_dates"):
        _edge = pd_[0] if pd_ else pd.Timestamp(f"{first}-01-01")
        _edge = _edge - pd.Timedelta(days=95)
        _k = [(d, v) for d, v in zip(data["ttm_dates"], data["ttm_eps"]) if d >= _edge]
        out["ttm_dates"] = [d for d, _ in _k]
        out["ttm_eps"] = [v for _, v in _k]
    return out


def _hold_flat(xs, ys, x0, x1):
    """Extend a fiscal-year series flat out to the price window's edges.

    Annual figures are anchored at fiscal-year midpoints, but price runs from
    January of the first kept year to today. Over a long window that mismatch is
    invisible; over three years the corridor started six months late and stopped
    fourteen months early, leaving price hanging outside the band at both ends.
    Holding the end values flat says the honest thing — the last published
    fiscal year's fair value is the most recent one that exists — and it is what
    lets a 1-year window (a single anchor) draw a band at all.
    """
    if not xs:
        return list(xs), list(ys)
    out_x, out_y = list(xs), list(ys)
    if x0 is not None and x0 < out_x[0]:
        out_x.insert(0, x0)
        out_y.insert(0, out_y[0])
    if x1 is not None and x1 > out_x[-1]:
        out_x.append(x1)
        out_y.append(out_y[-1])
    return out_x, out_y


def _fit_to_window(xs, ys, x0, x1):
    """Trim and extend a series so it spans exactly [x0, x1].

    Used for the quarterly corridor. A point outside the window is replaced by
    the interpolated value at the boundary, so the band starts and ends with
    price rather than a quarter early or late; where the series stops short of
    the window, the last value is held flat.
    """
    if not xs:
        return list(xs), list(ys)
    xs, ys = list(xs), list(ys)

    def _at(xa, ya, xb, yb, xt):
        if ya is None or yb is None or xb == xa:
            return ya if ya is not None else yb
        return ya + (yb - ya) * ((xt - xa) / (xb - xa))

    if x0 is not None and xs[0] > x0:
        xs.insert(0, x0)
        ys.insert(0, ys[0])
    elif x0 is not None and xs[0] < x0:
        i = 0
        while i + 1 < len(xs) and xs[i + 1] <= x0:
            i += 1
        y0 = _at(xs[i], ys[i], xs[i + 1], ys[i + 1], x0) if i + 1 < len(xs) else ys[-1]
        xs, ys = [x0] + xs[i + 1:], [y0] + ys[i + 1:]

    if x1 is not None and xs[-1] < x1:
        xs.append(x1)
        ys.append(ys[-1])
    elif x1 is not None and xs[-1] > x1:
        j = len(xs) - 1
        while j > 0 and xs[j - 1] >= x1:
            j -= 1
        y1 = _at(xs[j - 1], ys[j - 1], xs[j], ys[j], x1) if j > 0 else ys[0]
        xs, ys = xs[:j] + [x1], ys[:j] + [y1]
    return xs, ys


def _flat_stubs(xs, ys, x0, x1):
    """Just the extension segments, as one trace with a None separator.

    Drawn separately from the anchored line so the fiscal-year markers, hover
    text and legend entry stay on real data points.
    """
    sx, sy = [], []
    if not xs:
        return sx, sy
    if x0 is not None and x0 < xs[0] and ys[0] is not None:
        sx += [x0, xs[0], None]
        sy += [ys[0], ys[0], None]
    if x1 is not None and x1 > xs[-1] and ys[-1] is not None:
        sx += [xs[-1], x1, None]
        sy += [ys[-1], ys[-1], None]
    return sx, sy


def build_valuation_figure(data, years_back=None):
    """Price vs. earnings-justified fair value, with the range table above and
    the fundamentals grid below. Returns None if data is missing."""
    if not data or not data.get("years"):
        return None
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    data = window_data(data, years_back)
    yrs = data["years"]
    core = data.get("eps_core") or data["eps"]
    npe = data["normal_pe"]
    xyr = _fy_anchors(yrs)

    # The corridor rides trailing-twelve-month EPS where the filer gives us
    # clean quarters, so it moves through the year instead of stepping once a
    # year — which is what made short windows read as a flat bar. Annual core
    # EPS stays the fallback, and still drives the fundamentals grid below.
    _ttm_d = list(data.get("ttm_dates") or [])
    _ttm_e = list(data.get("ttm_eps") or [])
    use_ttm = len(_ttm_d) >= 2
    x_fv = _ttm_d if use_ttm else xyr
    eps_fv = _ttm_e if use_ttm else core

    fair = [(e * npe) if (e and e > 0) else None for e in eps_fv]
    over = [(f * PREMIUM_MULTIPLE) if f else None for f in fair]
    divln = [(d / DIVIDEND_YIELD_BASIS) if d else None for d in data["div"]]

    fs = _grid_font_size(len(yrs))
    # Row 1 holds Year/High/Low, row 3 holds FY/EPS/Chg/Div.
    h_top, h_bot = 3 * layout.grid_row_h, 4 * layout.grid_row_h
    h_plot = ct.plot_height()
    total = h_top + h_plot + h_bot

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.012,
        row_heights=[h_top / total, h_plot / total, h_bot / total])

    # Pad the x range by half a column each side. The grid labels are centred on
    # their year anchor and the final anchor sits on the last price date, so
    # without this half the label falls outside the axis and Plotly clips it
    # there ("2026" rendered as "202"). A wider figure margin does not help: the
    # clip is at the axis boundary, not the figure edge.
    #
    # Pad the x range 10% each side so the outermost grid labels clear the clip.
    #
    # This is derived, not guessed. Plotly positions these text traces against
    # `xaxis._length` but draws and clips the plot area a CONSTANT ~33px
    # narrower (measured 767 vs 734 at 1440px, and 733 vs 700 after changing the
    # margin — the gap does not move with the margin, so margin is not the
    # lever). A centred label also needs ~11px of its own. So the last anchor
    # must land within `1 - 44/_length` of the range: 0.940 at 1440px, 0.922 at
    # 768px. With pad p on a span s the anchor sits at (s+p)/(s+2p), and p = 0.1s
    # gives 0.917 — inside both.
    #
    # Do not shrink this without re-checking in a browser at BOTH widths. 2%
    # dropped the final column, and half- and full-column pads (geometrically
    # more generous) rendered worse, not better.
    _x_lo = min(data["price_dates"][0], xyr[0])
    _x_hi = max(data["price_dates"][-1], xyr[-1])
    _pad = (_x_hi - _x_lo) * 0.10
    _x_range = [_x_lo - _pad, _x_hi + _pad]

    # ── Band 3: range table ─────────────────────────────────────────────────
    _text_row(fig, xyr, [str(y) for y in yrs], 2.5, 1, fs, color.ink_muted)
    _text_row(fig, xyr, [_fmt(v, 1) for v in data.get("high") or []], 1.5, 1, fs, color.ink)
    _text_row(fig, xyr, [_fmt(v, 1) for v in data.get("low") or []], 0.5, 1, fs, color.ink)
    for lbl, yv in (("Year", 2.5), ("High", 1.5), ("Low", 0.5)):
        _row_label(fig, lbl, yv, "y", fs)

    # ── Band 4: the plot ────────────────────────────────────────────────────
    # z-order runs bottom to top: context, then bands, then lines.
    if data["price_dates"]:
        ct.add_no_coverage(fig, data["price_dates"][0], xyr[0], label=None,
                           row=2, col=1)
    ct.add_recession(fig, RECESSIONS, row=2, col=1)
    ct.add_fy_hairlines(fig, [pd.Timestamp(f"{y}-01-01") for y in yrs],
                        row=2, col=1)

    # The bands (and the lines on them) run the full width of the price series,
    # holding the first/last fiscal year's value flat past the anchors.
    _px0 = data["price_dates"][0] if data.get("price_dates") else None
    _px1 = data["price_dates"][-1] if data.get("price_dates") else None
    _fit = _fit_to_window if use_ttm else _hold_flat
    x_band, fair_band = _fit(x_fv, fair, _px0, _px1)
    _,      over_band = _fit(x_fv, over, _px0, _px1)

    # Base band: zero to the normal-multiple line.
    fig.add_trace(go.Scatter(
        x=x_band, y=fair_band, mode="lines", line=dict(width=0, shape="linear"),
        fill="tozeroy", fillcolor=color.corridor_base_fill,
        hoverinfo="skip", showlegend=False), row=2, col=1)
    # Upper band: normal multiple to the premium ceiling, lighter, no stroke.
    fig.add_trace(go.Scatter(
        x=x_band, y=over_band, mode="lines", line=dict(width=0, shape="linear"),
        fill="tonexty", fillcolor=color.corridor_high_fill,
        hoverinfo="skip", showlegend=False), row=2, col=1)
    # The edge between them — this is what separates two bands from one blob.
    # Dashed rather than a hard rule: the boundary is an estimate, and drawing
    # it as a solid rail overstates how precise the multiple is.
    fig.add_trace(go.Scatter(
        x=x_band, y=fair_band, mode="lines",
        line=dict(color=color.corridor_edge, width=1, shape="linear", dash="2px,3px"),
        hoverinfo="skip", showlegend=False), row=2, col=1)

    if any(v is not None for v in divln):
        _dsx, _dsy = _flat_stubs(xyr, divln, _px0, _px1)
        if _dsx:
            fig.add_trace(go.Scatter(
                x=_dsx, y=_dsy, mode="lines",
                line=dict(color=color.income_line, width=stroke.income, shape="linear"),
                hoverinfo="skip", showlegend=False), row=2, col=1)
        fig.add_trace(go.Scatter(
            x=xyr, y=divln, mode="lines+markers", name="Dividend value",
            line=dict(color=color.income_line, width=stroke.income, shape="linear"),
            marker=dict(size=3, color=color.income_line),
            hovertemplate="%{x|%Y}<br>$%{y:,.0f}<extra>Dividend value</extra>"),
            row=2, col=1)

    if use_ttm:
        # Quarterly points are dense enough that a marker on each one turns the
        # line into a bead chain, so the TTM line runs bare and follows the band.
        fig.add_trace(go.Scatter(
            x=x_band, y=fair_band, mode="lines",
            name=f"Fair value (TTM EPS &times; {npe:g})",
            line=dict(color=color.value_line, width=stroke.value, shape="linear"),
            customdata=[(f / npe if f else None) for f in fair_band],
            hovertemplate="%{x|%b %Y}<br>Fair value $%{y:,.0f}"
                          "<br>TTM EPS $%{customdata:.2f}<extra></extra>"),
            row=2, col=1)
    else:
        _fsx, _fsy = _flat_stubs(x_fv, fair, _px0, _px1)
        if _fsx:
            fig.add_trace(go.Scatter(
                x=_fsx, y=_fsy, mode="lines",
                line=dict(color=color.value_line, width=stroke.value, shape="linear"),
                hoverinfo="skip", showlegend=False), row=2, col=1)
        fig.add_trace(go.Scatter(
            x=x_fv, y=fair, mode="lines+markers",
            name=f"Fair value (EPS &times; {npe:g})",
            line=dict(color=color.value_line, width=stroke.value, shape="linear"),
            marker=dict(symbol="diamond", size=marker.size, color=marker.fill,
                        line=dict(color=color.value_line, width=marker.stroke_width)),
            customdata=eps_fv,
            hovertemplate="%{x|%Y}<br>Fair value $%{y:,.0f}"
                          "<br>Core EPS $%{customdata:.2f}<extra></extra>"),
            row=2, col=1)

    # Price last, on top, unsmoothed and marker-free. The jaggedness is the
    # point — smoothing a price series is the clearest tell of a chart built by
    # someone who does not work with market data.
    fig.add_trace(go.Scatter(
        x=data["price_dates"], y=data["price_vals"], mode="lines", name="Price",
        line=dict(color=color.ink, width=stroke.price, shape="linear"),
        hovertemplate="%{x|%b %Y}<br>$%{y:,.2f}<extra>Price</extra>"),
        row=2, col=1)

    # ── Band 5: fundamentals grid ───────────────────────────────────────────
    _text_row(fig, xyr, [f"FY{str(y)[-2:]}" for y in yrs], 3.5, 3, fs, color.ink_muted)
    _text_row(fig, xyr, [_fmt(v) for v in data["eps"]], 2.5, 3, fs, color.ink)
    _text_row(fig, xyr, [_fmt_pct(v) for v in data.get("eps_chg") or []], 1.5, 3, fs, color.ink_muted)
    _text_row(fig, xyr, [_fmt(v) for v in data["div"]], 0.5, 3, fs, color.ink)
    for lbl, yv in (("FY", 3.5), ("EPS", 2.5), ("Chg/Yr", 1.5), ("Div", 0.5)):
        _row_label(fig, lbl, yv, "y3", fs)

    # ── Chrome ──────────────────────────────────────────────────────────────
    ct.style(
        fig,
        height=total + 52,
        y=ct.value_axis(),
        legend="bottom",
        # Margin is back to the token default: widening it does NOT fix the
        # label clipping (the position/clip gap is a constant 33px that does not
        # move with the margin) — the x-range padding above is what handles it.
        margin=dict(l=layout.plot_padding["left"], r=layout.plot_padding["right"],
                    t=layout.plot_padding["top"], b=42),
    )
    # `legend_inline("bottom")` offsets by 16% of the plot area, which is tuned
    # for a single plot. Here the area spans all three bands, so that becomes a
    # ~100px dead gap. Sit the legend just under the fundamentals grid instead.
    fig.layout.legend.update(y=-0.045)
    # Grid rows: no axes, no interaction, fixed scale so the text stays put.
    for ax in ("yaxis", "yaxis3"):
        fig.layout[ax].update(visible=False, fixedrange=True,
                              range=[0, 3] if ax == "yaxis" else [0, 4])
    for ax in ("xaxis", "xaxis3"):
        fig.layout[ax].update(showgrid=False, showline=False, zeroline=False,
                              showticklabels=False, fixedrange=True, ticks="",
                              range=_x_range)
    # The plot's own x labels are redundant — both grids carry the years. The
    # crosshair belongs here, not on row 1, which is where `style()` puts it.
    fig.layout.xaxis2.update(showticklabels=False, showgrid=False, showline=False,
                             range=_x_range, **ct.spike_config())
    fig.layout.yaxis2.update(ct.value_axis())

    # Hairline rules separating the three bands.
    for y_pos in (fig.layout.yaxis.domain[0], fig.layout.yaxis3.domain[1]):
        fig.add_shape(type="line", xref="paper", yref="paper",
                      x0=0, x1=1, y0=y_pos, y1=y_pos,
                      line=dict(color=color.rule, width=stroke.rule), layer="below")
    return fig


def build_eps_figure(data):
    """Annual EPS bars (reported) with the core-EPS trend line."""
    if not data or not data.get("years"):
        return None
    import plotly.graph_objects as go
    yrs = [f"FY{str(y)[-2:]}" for y in data["years"]]
    eps = data["eps"]
    core = data.get("eps_core") or eps
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=yrs, y=eps, name="Reported EPS", marker_color=color.corridor_base,
        marker_line_width=0,
        hovertemplate="%{x}<br>EPS $%{y:.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=yrs, y=core, name="Core EPS (3-yr median)", mode="lines+markers",
        line=dict(color=color.value_line, width=stroke.value, shape="linear"),
        marker=dict(size=marker.size, color=marker.fill,
                    line=dict(color=color.value_line, width=marker.stroke_width)),
        hovertemplate="%{x}<br>Core EPS $%{y:.2f}<extra></extra>"))
    ct.style(fig, height=ct.plot_height(), x=ct.category_axis(),
             y=ct.value_axis(tick_format=",.2f", zero=False), legend="bottom",
             crosshair=False)
    return fig


def build_dividend_figure(data):
    """Annual dividends-per-share bars. Returns None if the name pays no dividend."""
    if not data or not data.get("years"):
        return None
    div = data.get("div") or []
    if not any(v for v in div):
        return None
    import plotly.graph_objects as go
    yrs = [f"FY{str(y)[-2:]}" for y in data["years"]]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=yrs, y=[v or 0 for v in div], name="Dividends / share",
        marker_color=color.income_line, marker_line_width=0,
        hovertemplate="%{x}<br>Dividend $%{y:.2f}<extra></extra>"))
    ct.style(fig, height=ct.plot_height(), x=ct.category_axis(),
             y=ct.value_axis(tick_format=",.2f"), legend=None, crosshair=False)
    return fig
