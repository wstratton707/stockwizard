import io
import math
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import LineChart, Reference
from openpyxl.formatting.rule import ColorScaleRule, CellIsRule
from openpyxl.drawing.image import Image as XLImage
from datetime import datetime
from constants import get_risk_free_rate
from disclaimers import SHORT as DISCLAIMER_SHORT
from market_data import consensus_from_recommendation
from analysis import compute_scorecard

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    MPL_AVAILABLE = True
except ImportError:
    MPL_AVAILABLE = False

# ── Colours ───────────────────────────────────────────────────────────────────
DARK_BLUE  = "1F4E79"
MID_BLUE   = "2E75B6"
GREEN_OK   = "70AD47"
RED_BAD    = "FF0000"
WHITE      = "FFFFFF"
GREY_ROW   = "F2F2F2"
TILE_BG    = "F0F5FB"   # pale blue KPI-tile fill
BAD_FILL   = "FFC7CE"   # Excel-classic light red
BAD_TEXT   = "9C0006"   # Excel-classic dark red

# Kept in sync with the PowerPoint deck's data-source line (pptx_builder.py).
DATA_SOURCE_LINE = "Polygon · Yahoo Finance · Finnhub · SEC EDGAR"


def _border():
    t = Side(style="thin")
    return Border(left=t, right=t, top=t, bottom=t)


def _hdr_cell(cell, bg=DARK_BLUE, fg=WHITE):
    cell.font      = Font(bold=True, color=fg, name="Calibri", size=10)
    cell.fill      = PatternFill("solid", fgColor=bg)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border    = _border()


def style_header_row(ws, bg=DARK_BLUE):
    for cell in ws[1]:
        _hdr_cell(cell, bg=bg)


def auto_col_width(ws, max_w=28):
    from openpyxl.cell.cell import Cell
    for col in ws.columns:
        real_cells = [c for c in col if isinstance(c, Cell)]
        if not real_cells:
            continue
        best = max((len(str(c.value or "")) for c in real_cells), default=10)
        ws.column_dimensions[real_cells[0].column_letter].width = min(best + 3, max_w)


def make_sparkline(values, color="#2E75B6", width=2.2, height=0.45):
    if not MPL_AVAILABLE:
        return None
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(vals) < 2:
        return None
    fig, ax = plt.subplots(figsize=(width, height))
    ax.plot(vals, color=color, linewidth=1.2)
    ax.fill_between(range(len(vals)), vals, min(vals), alpha=0.15, color=color)
    ax.set_axis_off()
    fig.patch.set_alpha(0)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=72, bbox_inches="tight", transparent=True, pad_inches=0)
    plt.close(fig)
    buf.seek(0)
    return buf


# ── Cover page ────────────────────────────────────────────────────────────────
def _build_cover(wb, ticker, period, sheetnames, df=None):
    ws = wb.create_sheet("Cover", 0)
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 4
    for col in ("B", "C", "D", "E"):
        ws.column_dimensions[col].width = 21

    # Title band — solid navy with white text, matching the deck cover.
    ws.merge_cells("B2:E3")
    c = ws["B2"]
    c.value     = f"{ticker}  —  Equity Research Report"
    c.font      = Font(size=22, bold=True, color=WHITE, name="Calibri")
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    c.fill      = PatternFill("solid", fgColor=DARK_BLUE)
    ws.row_dimensions[2].height = 30
    ws.row_dimensions[3].height = 14

    ws.merge_cells("B4:E4")
    c = ws["B4"]
    c.value     = (f"Period: {period}    |    Generated: {datetime.now().strftime('%B %d, %Y %H:%M')}"
                   f"    |    Multi-source data: {DATA_SOURCE_LINE}")
    c.font      = Font(size=9, italic=True, color="888888", name="Calibri")
    c.alignment = Alignment(horizontal="left", indent=1)

    # KPI band — four headline stats so the cover reads like a tear-sheet, not a TOC.
    toc_start = 6
    if df is not None and len(df) > 1:
        latest, firstrow = df.iloc[-1], df.iloc[0]
        period_ret = latest["Close"] / firstrow["Close"] - 1
        ret     = df["Daily_Return"].dropna()
        ann_vol = ret.std() * np.sqrt(252)
        ann_ret = ret.mean() * 252
        sharpe  = (ann_ret - get_risk_free_rate()) / ann_vol if ann_vol else float("nan")
        tiles = [
            ("Current Price",   f"${latest['Close']:,.2f}",                    DARK_BLUE),
            ("Period Return",   f"{period_ret * 100:+.1f}%",
                                GREEN_OK if period_ret >= 0 else RED_BAD),
            ("Sharpe Ratio",    f"{sharpe:.2f}" if pd.notna(sharpe) else "N/A",
                                GREEN_OK if pd.notna(sharpe) and sharpe >= 1 else DARK_BLUE),
            ("Ann. Volatility", f"{ann_vol * 100:.1f}%",                       DARK_BLUE),
        ]
        for i, (label, value, accent) in enumerate(tiles):
            col   = 2 + i   # B, C, D, E
            vcell = ws.cell(row=6, column=col, value=value)
            vcell.font      = Font(size=16, bold=True, name="Calibri", color=accent)
            vcell.alignment = Alignment(horizontal="center", vertical="center")
            vcell.fill      = PatternFill("solid", fgColor=TILE_BG)
            vcell.border    = _border()
            lcell = ws.cell(row=7, column=col, value=label.upper())
            lcell.font      = Font(size=8, bold=True, name="Calibri", color="808080")
            lcell.alignment = Alignment(horizontal="center", vertical="center")
            lcell.fill      = PatternFill("solid", fgColor=TILE_BG)
            lcell.border    = _border()
        ws.row_dimensions[6].height = 30
        ws.row_dimensions[7].height = 16
        toc_start = 9

    ws.cell(row=toc_start, column=2, value="TABLE OF CONTENTS").font = \
        Font(bold=True, size=12, color=DARK_BLUE, name="Calibri")
    for i, name in enumerate(sheetnames, toc_start + 1):
        cell = ws.cell(row=i, column=2, value=name.replace("_", " "))
        cell.font      = Font(name="Calibri", size=10, color=MID_BLUE, underline="single")
        cell.hyperlink = f"#{name}!A1"
        ws.row_dimensions[i].height = 16


# ── Dashboard ─────────────────────────────────────────────────────────────────
def _narrative_box(ws, row, text, height=60, italic=True, bold=False, bg=None):
    """Wrapped, full-width (A:D) text box on one tall row. Returns the next row.

    Used for the plain-English blocks (takeaway, what-changed, disclaimers) that
    answer the "dense numbers, little narrative guidance" critique."""
    ws.merge_cells(f"A{row}:D{row}")
    c = ws.cell(row=row, column=1, value=text)
    c.font      = Font(name="Calibri", size=10, italic=italic, bold=bold)
    c.alignment = Alignment(wrap_text=True, vertical="top")
    c.border    = _border()
    if bg:
        c.fill = PatternFill("solid", fgColor=bg)
    ws.row_dimensions[row].height = height
    return row + 1


def _relative_performance_rows(df):
    """(name, ticker_ret, bench_ret, diff) per benchmark present in df.

    Uses the *_Cumulative columns fetch_stock_data already merges in (indexed to
    100 at the period start), so no extra data fetch is needed."""
    rows = []
    if "Cumulative_Index" not in df.columns:
        return rows
    tcum = df["Cumulative_Index"].dropna()
    if len(tcum) < 2:
        return rows
    t_ret = tcum.iloc[-1] / tcum.iloc[0] - 1
    for b, name in [("SPY", "S&P 500 (SPY)"), ("QQQ", "NASDAQ 100 (QQQ)")]:
        col = f"{b}_Cumulative"
        if col in df.columns:
            bcum = df[col].dropna()
            if len(bcum) >= 2:
                b_ret = bcum.iloc[-1] / bcum.iloc[0] - 1
                rows.append((name, t_ret, b_ret, t_ret - b_ret))
    return rows


def _technical_posture(df):
    """Descriptive read of the current technical indicators — NOT a recommendation.

    Returns {score:0-100, label, color, signals:[str,...]} or None. The label
    ("Bullish/Mixed/Bearish technicals") describes what the indicators say today;
    it is deliberately framed as a signal, not advice."""
    if df is None or len(df) < 2:
        return None
    latest = df.iloc[-1]

    def _num(col):
        v = latest.get(col)
        try:
            return float(v) if v is not None and pd.notna(v) else None
        except Exception:
            return None

    close, ma50, ma200 = _num("Close"), _num("MA50"), _num("MA200")
    rsi, macd_h, pct_hi = _num("RSI14"), _num("MACD_Hist"), _num("Pct_From_52W_High")
    comps = []   # (points 0..1, descriptive text)

    if close is not None and ma50 is not None:
        comps.append((1.0 if close > ma50 else 0.0,
                      f"Price is {'above' if close > ma50 else 'below'} the 50-day moving average"))
    if close is not None and ma200 is not None:
        comps.append((1.0 if close > ma200 else 0.0,
                      f"Price is {'above' if close > ma200 else 'below'} the 200-day moving average"))
    if ma50 is not None and ma200 is not None:
        up = ma50 > ma200
        comps.append((1.0 if up else 0.0,
                      f"50-day MA is {'above' if up else 'below'} the 200-day MA "
                      f"({'uptrend' if up else 'downtrend'} structure)"))
    if rsi is not None:
        zone = ("overbought" if rsi > 70 else "oversold" if rsi < 30
                else "positive momentum" if rsi >= 50 else "soft momentum")
        comps.append((max(0.0, min(1.0, (rsi - 30) / 40.0)), f"RSI is {rsi:.0f} — {zone}"))
    if macd_h is not None:
        up = macd_h > 0
        comps.append((1.0 if up else 0.0,
                      f"MACD is {'above' if up else 'below'} its signal line "
                      f"({'bullish' if up else 'bearish'} momentum)"))
    if pct_hi is not None:
        comps.append((max(0.0, min(1.0, 1.0 + pct_hi / 0.5)),
                      f"Trading {abs(pct_hi)*100:.0f}% "
                      f"{'below' if pct_hi < 0 else 'above'} the 52-week high"))
    rel = _relative_performance_rows(df)
    if rel:
        name, _t, _b, diff = rel[0]
        short = name.split(" (")[0]
        comps.append((1.0 if diff >= 0 else 0.0,
                      f"{'Outperforming' if diff >= 0 else 'Lagging'} {short} by "
                      f"{abs(diff)*100:.1f} pts over the period"))

    if not comps:
        return None
    score = round(sum(p for p, _ in comps) / len(comps) * 100)
    label, color = (("Bullish technicals", GREEN_OK) if score >= 66 else
                    ("Mixed technicals",   DARK_BLUE) if score >= 40 else
                    ("Bearish technicals",  RED_BAD))
    return {"score": score, "label": label, "color": color,
            "signals": [t for _, t in comps]}


def _recent_changes(df, ticker, lookback=5):
    """Plain-English 'what changed recently' bullets from the last few sessions."""
    if df is None or len(df) < 2:
        return []
    lookback = min(lookback, len(df) - 1)
    latest   = df.iloc[-1]
    bullets  = []

    c_now, c_prev = float(latest["Close"]), float(df["Close"].iloc[-1 - lookback])
    if c_prev:
        bullets.append(f"Price {(c_now / c_prev - 1) * 100:+.1f}% over the last "
                       f"{lookback} sessions (${c_prev:,.2f} -> ${c_now:,.2f}).")

    last_rets = df["Daily_Return"].dropna().tail(lookback)
    if len(last_rets):
        bullets.append(f"Best day {last_rets.max() * 100:+.1f}%, worst day "
                       f"{last_rets.min() * 100:+.1f}% in that window.")

    vv = latest.get("Volume_vs_Avg")
    if vv is not None and pd.notna(vv):
        vv = float(vv)
        if vv >= 1.15 or vv <= 0.85:
            bullets.append(f"Latest volume ran {vv:.1f}x its 20-day average "
                           f"({(vv - 1) * 100:+.0f}%) — {'heavier' if vv >= 1 else 'lighter'} trading.")

    if "RSI14" in df.columns:
        r = df["RSI14"].dropna()
        if len(r) > lookback:
            r_now, r_prev = float(r.iloc[-1]), float(r.iloc[-1 - lookback])
            note = (" — now overbought" if r_now > 70 else
                    " — now oversold" if r_now < 30 else "")
            bullets.append(f"RSI moved {r_prev:.0f} -> {r_now:.0f}{note}.")

    if "Close_vs_MA50" in df.columns:
        cvm = df["Close_vs_MA50"].dropna()
        if len(cvm) > lookback and (cvm.iloc[-1] > 0) != (cvm.iloc[-1 - lookback] > 0):
            above = cvm.iloc[-1] > 0
            bullets.append(f"Price crossed {'above' if above else 'below'} its 50-day "
                           f"moving average in the last {lookback} sessions.")

    if "52W_High" in df.columns:
        hs = df["52W_High"].dropna()
        if len(hs) > lookback and hs.iloc[-1] > hs.iloc[-1 - lookback]:
            bullets.append("Notched a new 52-week high in the last week.")
    if "52W_Low" in df.columns:
        ls = df["52W_Low"].dropna()
        if len(ls) > lookback and ls.iloc[-1] < ls.iloc[-1 - lookback]:
            bullets.append("Made a new 52-week low in the last week.")

    if "SPY_Cumulative" in df.columns:
        s = df["SPY_Cumulative"].dropna()
        t = df["Cumulative_Index"].dropna()
        if len(s) > lookback and len(t) > lookback:
            diff = (t.iloc[-1] / t.iloc[-1 - lookback] - 1) - (s.iloc[-1] / s.iloc[-1 - lookback] - 1)
            bullets.append(f"{'Outpaced' if diff >= 0 else 'Trailed'} the S&P 500 by "
                           f"{abs(diff) * 100:.1f} pts over the last {lookback} sessions.")

    return bullets[:5]


def _mc_plain_language(mc_summary):
    """Turn the Monte Carlo summary into plain-English downside/upside lines."""
    if not mc_summary:
        return []
    last    = mc_summary.get("Last Price")
    horizon = mc_summary.get("Forecast Horizon (days)")
    lines   = []
    if last:
        lines.append(f"From today's ${last:,.2f}"
                     + (f" over ~{horizon} trading days:" if horizon else ":"))

    def _line(label, key):
        v = mc_summary.get(key)
        if v is None:
            return
        chg = f" ({(v / last - 1) * 100:+.1f}% vs today)" if last else ""
        lines.append(f"{label}: ${v:,.2f}{chg}")

    _line("Median outcome (P50)",         "Median (P50)")
    _line("Bear case (P5, ~5% chance)",   "Bear Case (P5)")
    _line("Low case (P25, ~25% chance)",  "Low Case (P25)")
    _line("Bull case (P75, ~25% chance)", "Bull Case (P75)")
    _line("Best case (P95, ~5% chance)",  "Best Case (P95)")
    prob = mc_summary.get("Prob. of Gain")
    if prob is not None:
        lines.append(f"Probability of finishing above today's price: {prob}.")
    return lines


def _humanize_company_value(key, value):
    """Format company_details values for display instead of dumping raw floats.

    Fixes the "Market Cap 3288094076962.04" issue flagged in review — large money
    magnitudes render as $T/$B/$M and plain counts get thousands separators.
    Non-numeric values (already-formatted strings) pass through untouched."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    k = key.lower()
    if any(w in k for w in ("cap", "value", "revenue", "assets", "debt", "cash")):
        a = abs(value)
        if a >= 1e12: return f"${value / 1e12:,.2f}T"
        if a >= 1e9:  return f"${value / 1e9:,.2f}B"
        if a >= 1e6:  return f"${value / 1e6:,.2f}M"
        return f"${value:,.0f}"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _build_dashboard(wb, ticker, df, company_details, mc_summary,
                     resistance_levels, support_levels, summary_text,
                     analyst_data=None, dcf=None, fundamentals=None):
    ws = wb.create_sheet("Dashboard")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 24
    ws.column_dimensions["C"].width = 18
    ws.column_dimensions["D"].width = 10

    latest     = df.iloc[-1]
    first      = df.iloc[0]
    period_ret = (latest["Close"] / first["Close"] - 1) * 100

    ret      = df["Daily_Return"].dropna()
    ann_ret  = ret.mean() * 252
    ann_std  = ret.std() * np.sqrt(252)
    downside = ret[ret < 0].std() * np.sqrt(252)
    # Excess-return Sharpe/Sortino (subtract the risk-free rate) so the exported
    # report matches the on-screen metric cards and the portfolio engine.
    rfr      = get_risk_free_rate()
    sharpe   = (ann_ret - rfr) / ann_std  if ann_std  else np.nan
    sortino  = (ann_ret - rfr) / downside if downside else np.nan

    try:
        rsi_val = float(latest.get("RSI14", np.nan))
    except Exception:
        rsi_val = np.nan

    ws.merge_cells("A1:D1")
    ws["A1"] = f"{ticker} — Equity Research Dashboard"
    ws["A1"].font      = Font(size=18, bold=True, color=DARK_BLUE, name="Calibri")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 34

    ws.merge_cells("A2:D2")
    ws["A2"] = (f"Generated: {datetime.now().strftime('%B %d, %Y %H:%M')}"
                f"  |  Multi-source data: {DATA_SOURCE_LINE}")
    ws["A2"].font      = Font(italic=True, color="888888", name="Calibri", size=9)
    ws["A2"].alignment = Alignment(horizontal="center")

    def sec_hdr(row, label, col_end="D"):
        ws.merge_cells(f"A{row}:{col_end}{row}")
        c = ws.cell(row=row, column=1, value=label)
        c.font      = Font(bold=True, color=WHITE, name="Calibri", size=11)
        c.fill      = PatternFill("solid", fgColor=DARK_BLUE)
        c.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[row].height = 20

    def kv(row, label, value, fmt=None, rag=None):
        cl = ws.cell(row=row, column=1, value=label)
        cv = ws.cell(row=row, column=2, value=value)
        cl.font      = Font(name="Calibri", size=10)
        cv.font      = Font(name="Calibri", size=10, bold=True)
        cv.alignment = Alignment(horizontal="right")
        cl.border    = cv.border = _border()
        if fmt and isinstance(value, (int, float)):
            cv.number_format = fmt
        if rag and isinstance(value, (int, float)):
            direction, thresh = rag
            colour = (GREEN_OK if value > thresh else RED_BAD) if direction == "gt" \
                     else (RED_BAD if value < thresh else GREEN_OK)
            cv.fill = PatternFill("solid", fgColor=colour)
            cv.font = Font(name="Calibri", size=10, bold=True, color=WHITE)
        bg = GREY_ROW if row % 2 == 0 else WHITE
        for col in [1, 2]:
            c = ws.cell(row=row, column=col)
            if not c.fill or c.fill.fgColor.rgb in ("00000000", "FFFFFFFF", WHITE):
                c.fill = PatternFill("solid", fgColor=bg)

    sec_hdr(4, "Price & Performance")
    kv(5,  "Current Price ($)",        latest["Close"],              fmt='_($* #,##0.00_)')
    kv(6,  "Period Return",            period_ret / 100,             fmt="0.00%", rag=("gt", 0))
    kv(7,  "52-Week High ($)",         latest.get("52W_High"),       fmt='_($* #,##0.00_)')
    kv(8,  "52-Week Low ($)",          latest.get("52W_Low"),        fmt='_($* #,##0.00_)')
    kv(9,  "% from 52W High",          latest.get("Pct_From_52W_High"), fmt="0.00%", rag=("gt", -0.10))
    kv(10, "20-Day MA ($)",            latest.get("MA20"),           fmt='_($* #,##0.00_)')
    kv(11, "50-Day MA ($)",            latest.get("MA50"),           fmt='_($* #,##0.00_)')
    kv(12, "200-Day MA ($)",           latest.get("MA200"),          fmt='_($* #,##0.00_)')
    kv(13, "Price vs 50-Day MA",       latest.get("Close_vs_MA50"),  fmt="0.00%", rag=("gt", 0))

    if "BB_Upper" in df.columns:
        sec_hdr(15, "Bollinger Bands (20-day, 2σ)")
        kv(16, "BB Upper ($)",  latest.get("BB_Upper"),  fmt='_($* #,##0.00_)')
        kv(17, "BB Middle ($)", latest.get("BB_Middle"), fmt='_($* #,##0.00_)')
        kv(18, "BB Lower ($)",  latest.get("BB_Lower"),  fmt='_($* #,##0.00_)')
        kv(19, "BB Width",      latest.get("BB_Width"),  fmt="0.0000")
        kv(20, "BB %B",         latest.get("BB_Pct"),    fmt="0.00%")
        risk_start = 22
    else:
        risk_start = 15

    sec_hdr(risk_start, "Risk & Return Metrics")
    kv(risk_start+1, "20-Day Ann. Volatility",  latest.get("Volatility_20d"), fmt="0.00%")
    kv(risk_start+2, "60-Day Max Drawdown",      df["Drawdown_60d"].min(),     fmt="0.00%", rag=("gt", -0.20))
    kv(risk_start+3, "RSI (14)",
       round(rsi_val, 1) if pd.notna(rsi_val) else "N/A",
       rag=("lt", 70) if pd.notna(rsi_val) and rsi_val > 70 else
           ("gt", 30) if pd.notna(rsi_val) and rsi_val < 30 else None)
    kv(risk_start+4, "Sharpe Ratio",  round(sharpe, 2)  if pd.notna(sharpe)  else "N/A", rag=("gt", 1))
    kv(risk_start+5, "Sortino Ratio", round(sortino, 2) if pd.notna(sortino) else "N/A", rag=("gt", 1))

    row_cursor = risk_start + 7

    # ── Investor Takeaway — a plain-English lead so the page opens with a view,
    #    not a wall of numbers (the "dense, little narrative guidance" critique).
    posture   = _technical_posture(df)
    consensus = consensus_from_recommendation((analyst_data or {}).get("recommendation"))
    changes   = _recent_changes(df, ticker)

    sec_hdr(row_cursor, "Investor Takeaway")
    row_cursor += 1
    takeaway = [f"{ticker} is {period_ret:+.1f}% over the period, last ${latest['Close']:,.2f}."]
    if posture:
        takeaway.append(f"Technical posture: {posture['label']} ({posture['score']}/100).")
    if consensus:
        takeaway.append(f"Wall-Street consensus: {consensus['verdict']} "
                        f"({consensus['total']} analysts).")
    if dcf and dcf.get("ok") and dcf.get("upside") is not None:
        takeaway.append(f"DCF fair value ${dcf['fair_value']:,.0f} "
                        f"({dcf['upside']*100:+.0f}% vs price).")
    row_cursor = _narrative_box(ws, row_cursor, "  ".join(takeaway),
                                height=58, italic=False, bold=True, bg=TILE_BG)

    # ── Stock Scorecard — the "investment snapshot" that turns a dozen metrics
    #    into a graded profile. Descriptive (quality/attractiveness), not buy/sell.
    _sc_risk = {
        "sharpe": float(sharpe)  if pd.notna(sharpe)  else None,
        "vol":    float(ann_std) if pd.notna(ann_std) else None,
        "max_dd": (float(df["Drawdown_60d"].min())
                   if pd.notna(df["Drawdown_60d"].min()) else None),
    }
    scorecard = compute_scorecard(
        fundamentals=fundamentals, dcf=dcf,
        momentum_score=(posture["score"] if posture else None),
        risk=_sc_risk, consensus=consensus)
    if scorecard and scorecard.get("ok"):
        _grade_bg = {"Strong": "548235", "Above-avg": GREEN_OK, "Average": "BF8F00",
                     "Below-avg": "C55A11", "Weak": RED_BAD}
        sec_hdr(row_cursor, "Stock Scorecard")
        row_cursor += 1
        comp  = scorecard["composite"]
        ccolor = GREEN_OK if comp >= 65 else "BF8F00" if comp >= 45 else RED_BAD
        cl = ws.cell(row=row_cursor, column=1, value="Composite Score (0–100)")
        cv = ws.cell(row=row_cursor, column=2, value=comp)
        cl.font = Font(name="Calibri", size=10, bold=True)
        cv.font = Font(name="Calibri", size=12, bold=True, color=WHITE)
        cv.fill = PatternFill("solid", fgColor=ccolor)
        cv.alignment = Alignment(horizontal="right")
        cl.border = cv.border = _border()
        row_cursor += 1
        ll = ws.cell(row=row_cursor, column=1, value="Overall Profile")
        ws.merge_cells(f"B{row_cursor}:C{row_cursor}")
        lv = ws.cell(row=row_cursor, column=2, value=scorecard["label"])
        ll.font = Font(name="Calibri", size=10)
        lv.font = Font(name="Calibri", size=10, bold=True, color=WHITE)
        lv.fill = PatternFill("solid", fgColor=ccolor)
        lv.alignment = Alignment(horizontal="right")
        ll.border = lv.border = _border()
        row_cursor += 1
        for ci, h in enumerate(["Factor", "Score", "Grade"], 1):
            _hdr_cell(ws.cell(row=row_cursor, column=ci, value=h), bg=MID_BLUE)
        row_cursor += 1
        for fac in scorecard["factors"]:
            a = ws.cell(row=row_cursor, column=1, value=fac["name"])
            b = ws.cell(row=row_cursor, column=2, value=fac["score"])
            c = ws.cell(row=row_cursor, column=3, value=fac["grade"])
            a.font = Font(name="Calibri", size=10)
            b.font = Font(name="Calibri", size=10, bold=True)
            b.alignment = Alignment(horizontal="right")
            c.font = Font(name="Calibri", size=10, bold=True, color=WHITE)
            c.fill = PatternFill("solid", fgColor=_grade_bg.get(fac["grade"], MID_BLUE))
            c.alignment = Alignment(horizontal="center")
            a.border = b.border = c.border = _border()
            row_cursor += 1
        _details = "   ·   ".join(f"{fac['name']}: {fac['detail']}"
                                   for fac in scorecard["factors"] if fac["detail"])
        row_cursor = _narrative_box(
            ws, row_cursor,
            "Weighted blend of valuation, growth, profitability, financial health, "
            "momentum, risk and sentiment — a descriptive profile of the stock's "
            f"characteristics, not a recommendation.\n{_details}\n" + DISCLAIMER_SHORT,
            height=96, italic=True, bg="FFF8E1")
        row_cursor += 1

    if changes:
        c = ws.cell(row=row_cursor, column=1, value="What changed recently")
        c.font = Font(name="Calibri", size=10, bold=True, color=DARK_BLUE)
        row_cursor += 1
        bullet_text = "\n".join(f"•  {b}" for b in changes)
        est_lines   = sum(max(1, math.ceil(len(b) / 82)) for b in changes)
        row_cursor  = _narrative_box(ws, row_cursor, bullet_text,
                                     height=16 * est_lines + 8, italic=False)
    row_cursor += 1

    # ── Relative Performance vs benchmarks (uses *_Cumulative already in df) ────
    rel_rows = _relative_performance_rows(df)
    if rel_rows:
        sec_hdr(row_cursor, "Relative Performance")
        row_cursor += 1
        t_ret = rel_rows[0][1]
        for name, _t, _b, diff in rel_rows:
            kv(row_cursor, f"vs {name}", diff, fmt="+0.0%;-0.0%", rag=("gt", 0))
            row_cursor += 1
        abs_bits = [f"{ticker} {t_ret*100:+.1f}%"] + \
                   [f"{n.split(' (')[0]} {b*100:+.1f}%" for n, _t, b, _d in rel_rows]
        row_cursor = _narrative_box(ws, row_cursor, "Period return:    " + "      |      ".join(abs_bits),
                                    height=26, italic=False)
        row_cursor += 1

    # ── Technical Posture — descriptive read of the indicators, NOT advice ──────
    if posture:
        sec_hdr(row_cursor, "Technical Posture")
        row_cursor += 1
        kv(row_cursor, "Technical Score (0–100)", posture["score"])
        row_cursor += 1
        lc = ws.cell(row=row_cursor, column=1, value="Reading")
        vc = ws.cell(row=row_cursor, column=2, value=posture["label"])
        lc.font = Font(name="Calibri", size=10)
        vc.font = Font(name="Calibri", size=10, bold=True, color=WHITE)
        vc.fill = PatternFill("solid", fgColor=posture["color"])
        vc.alignment = Alignment(horizontal="right")
        lc.border = vc.border = _border()
        row_cursor += 1
        for sig in posture["signals"]:
            sc = ws.cell(row=row_cursor, column=1, value=f"•  {sig}")
            ws.merge_cells(f"A{row_cursor}:D{row_cursor}")
            sc.font = Font(name="Calibri", size=9)
            sc.alignment = Alignment(wrap_text=True, vertical="center")
            row_cursor += 1
        row_cursor = _narrative_box(
            ws, row_cursor,
            "Describes what the technical indicators say today — a signal, not a "
            "recommendation. " + DISCLAIMER_SHORT,
            height=40, italic=True, bg="FFF8E1")
        row_cursor += 1

    # ── Analyst Consensus — Wall Street's view, clearly attributed (not ours) ───
    if consensus:
        sec_hdr(row_cursor, "Analyst Consensus")
        row_cursor += 1
        lc = ws.cell(row=row_cursor, column=1, value="Wall-Street Rating")
        vc = ws.cell(row=row_cursor, column=2, value=consensus["verdict"])
        lc.font = Font(name="Calibri", size=10)
        vc.font = Font(name="Calibri", size=10, bold=True, color=WHITE)
        vc.fill = PatternFill("solid", fgColor=consensus["color"].lstrip("#"))
        vc.alignment = Alignment(horizontal="right")
        lc.border = vc.border = _border()
        row_cursor += 1
        kv(row_cursor, "Buy (Strong Buy + Buy)", consensus["strong_buy"] + consensus["buy"])
        row_cursor += 1
        kv(row_cursor, "Hold", consensus["hold"])
        row_cursor += 1
        kv(row_cursor, "Sell (Sell + Strong Sell)", consensus["sell"] + consensus["strong_sell"])
        row_cursor += 1
        kv(row_cursor, "Analysts covering", consensus["total"])
        row_cursor += 1
        row_cursor = _narrative_box(
            ws, row_cursor,
            f"Source: {consensus['total']} Wall-Street analyst ratings aggregated by "
            f"Finnhub (period {consensus['period']}). This is analysts' consensus, "
            f"not QuantWizard's opinion. " + DISCLAIMER_SHORT,
            height=40, italic=True, bg="FFF8E1")
        row_cursor += 1

    # ── Fair Value (DCF) — a valuation conclusion, honestly assumption-driven ───
    if dcf and dcf.get("ok"):
        sec_hdr(row_cursor, "Fair Value (DCF)")
        row_cursor += 1
        up = dcf.get("upside")
        verdict, vcolor = (("Undervalued vs DCF", GREEN_OK) if up is not None and up > 0.15 else
                           ("Overvalued vs DCF",  RED_BAD)  if up is not None and up < -0.15 else
                           ("Fairly valued vs DCF", DARK_BLUE))
        kv(row_cursor, "DCF Fair Value / Share", dcf["fair_value"], fmt='_($* #,##0.00_)')
        row_cursor += 1
        if up is not None:
            kv(row_cursor, "Upside / Downside", up, fmt="+0.0%;-0.0%", rag=("gt", 0))
            row_cursor += 1
        lc = ws.cell(row=row_cursor, column=1, value="DCF Verdict")
        vc = ws.cell(row=row_cursor, column=2, value=verdict)
        lc.font = Font(name="Calibri", size=10)
        vc.font = Font(name="Calibri", size=10, bold=True, color=WHITE)
        vc.fill = PatternFill("solid", fgColor=vcolor)
        vc.alignment = Alignment(horizontal="right")
        lc.border = vc.border = _border()
        row_cursor += 1
        scn  = dcf.get("scenarios", {})
        bear, bull = scn.get("bear", {}), scn.get("bull", {})
        imp  = dcf.get("market_implied_growth")
        note = (f"2-stage FCF DCF: {dcf['wacc']*100:.0f}% WACC, "
                f"{dcf['terminal_growth']*100:.1f}% terminal growth, {dcf['years']}y, "
                f"base FCF growth {dcf['base_growth']*100:.1f}%. ")
        if bear.get("fair_value") and bull.get("fair_value"):
            note += f"Bear ${bear['fair_value']:,.0f} / Bull ${bull['fair_value']:,.0f}. "
        if imp is not None:
            note += f"Today's price implies ~{imp*100:.0f}% FCF growth for a decade. "
        note += "Full workings on the Valuation sheet. " + DISCLAIMER_SHORT
        row_cursor = _narrative_box(ws, row_cursor, note, height=56, italic=True, bg="FFF8E1")
        row_cursor += 1

    if resistance_levels or support_levels:
        sec_hdr(row_cursor, "Support & Resistance Levels")
        row_cursor += 1
        kv(row_cursor,   "Resistance", "  |  ".join([f"${r:,.2f}" for r in (resistance_levels or [])]))
        kv(row_cursor+1, "Support",    "  |  ".join([f"${s:,.2f}" for s in (support_levels or [])]))
        row_cursor += 3

    if mc_summary:
        sec_hdr(row_cursor, "Monte Carlo Forecast")
        row_cursor += 1
        for k, v in mc_summary.items():
            kv(row_cursor, k, str(v))
            row_cursor += 1
        mc_lines = _mc_plain_language(mc_summary)
        if mc_lines:
            body = ("Monte Carlo in plain English\n" + "\n".join(mc_lines)
                    + "\nSimulated from the historical return distribution — outcomes "
                      "are probabilities, not guarantees.")
            row_cursor = _narrative_box(ws, row_cursor, body,
                                        height=16 * (len(mc_lines) + 3) + 6,
                                        italic=False, bg=TILE_BG)
        row_cursor += 1

    if company_details:
        sec_hdr(row_cursor, "Company Information")
        row_cursor += 1
        for k, v in company_details.items():
            if k != "Description":
                kv(row_cursor, k, _humanize_company_value(k, v))
                row_cursor += 1
        row_cursor += 1

    sec_hdr(row_cursor, "Automated Analysis Summary", col_end="D")
    row_cursor += 1
    ws.merge_cells(f"A{row_cursor}:D{row_cursor + 4}")
    sc = ws.cell(row=row_cursor, column=1, value=summary_text)
    sc.font      = Font(name="Calibri", size=10, italic=True)
    sc.alignment = Alignment(wrap_text=True, vertical="top")
    sc.border    = _border()
    ws.row_dimensions[row_cursor].height = 90
    row_cursor += 6

    if MPL_AVAILABLE:
        ws.column_dimensions["E"].width = 18
        spark_data = [
            ("Price",      df["Close"].tolist(),          "#2E75B6"),
            ("Volume",     df["Volume"].tolist(),         "#70AD47"),
            ("Daily Ret",  df["Daily_Return"].tolist(),   "#FF6B35"),
            ("Volatility", df["Volatility_20d"].tolist(), "#7030A0"),
            ("Drawdown",   df["Drawdown_60d"].tolist(),   "#C00000"),
        ]
        ws.cell(row=4, column=5, value="SPARKLINES").font = Font(bold=True, color=WHITE, name="Calibri")
        ws.cell(row=4, column=5).fill = PatternFill("solid", fgColor=MID_BLUE)
        for i, (label, vals, col) in enumerate(spark_data):
            row = 5 + i * 3
            ws.cell(row=row, column=5, value=label).font = Font(name="Calibri", size=9, bold=True)
            buf = make_sparkline(vals, color=col)
            if buf:
                img = XLImage(buf)
                img.width, img.height = 130, 35
                ws.add_image(img, f"E{row+1}")
            ws.row_dimensions[row+1].height = 28

    return ws


# ── Annual summary sheet ──────────────────────────────────────────────────────
def _build_annual_summary(wb, df):
    """Year-by-year performance table — always included, especially useful for long ranges."""
    ws_a = wb.create_sheet("Annual_Summary")
    ws_a.sheet_view.showGridLines = False

    ws_a.merge_cells("A1:H1")
    ws_a["A1"] = "Annual Performance Summary"
    ws_a["A1"].font      = Font(size=14, bold=True, color=DARK_BLUE, name="Calibri")
    ws_a["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws_a.row_dimensions[1].height = 26

    headers = ["Year","Annual Return","Max Drawdown","Ann. Volatility","Sharpe (approx)"]
    for ci, h in enumerate(headers, 1):
        _hdr_cell(ws_a.cell(row=2, column=ci, value=h), bg=MID_BLUE)

    tmp = df[["Date","Daily_Return","Drawdown_60d"]].copy()
    tmp["Year"] = pd.to_datetime(tmp["Date"]).dt.year

    for ri, (year, grp) in enumerate(tmp.groupby("Year"), 3):
        ret         = grp["Daily_Return"].dropna()
        yr_return   = (1 + ret).prod() - 1
        yr_drawdown = grp["Drawdown_60d"].min()
        yr_vol      = ret.std() * np.sqrt(252)
        yr_sharpe   = (ret.mean() * 252) / yr_vol if yr_vol else np.nan

        row_vals = [year, yr_return, yr_drawdown, yr_vol, round(yr_sharpe, 2) if pd.notna(yr_sharpe) else "N/A"]
        bg = GREY_ROW if ri % 2 == 0 else WHITE
        for ci, val in enumerate(row_vals, 1):
            c = ws_a.cell(row=ri, column=ci, value=val)
            c.font   = Font(name="Calibri", size=10)
            c.border = _border()
            c.fill   = PatternFill("solid", fgColor=bg)
            if ci == 1: c.number_format = "0"
            elif ci in (2, 3, 4):
                c.number_format = "0.00%"
                # RAG only for return (col 2) and drawdown (col 3); volatility (col 4)
                # is not "good/bad" on its own, so it stays neutral.
                if isinstance(val, float) and ci in (2, 3):
                    good = val > 0 if ci == 2 else val > -0.15
                    c.fill = PatternFill("solid", fgColor=GREEN_OK if good else BAD_FILL)
                    c.font = Font(name="Calibri", size=10, bold=True,
                                  color=WHITE if good else BAD_TEXT)

    auto_col_width(ws_a)
    ws_a.freeze_panes = "A3"
    ws_a.auto_filter.ref = f"A2:{get_column_letter(len(headers))}2"
    return ws_a


# ── Price & Indicators sheet ──────────────────────────────────────────────────
def _build_price_sheet(wb, df, bar_size="day"):
    price_cols = ["Date",
                  "Daily_Return","Cumulative_Index","MA20","MA50","MA200",
                  "Close_vs_MA20","Close_vs_MA50","Close_vs_MA200",
                  "Vol_MA20","Volume_vs_Avg",
                  "Volatility_20d","Drawdown_20d","Drawdown_60d",
                  "52W_High","52W_Low","Pct_From_52W_High","Pct_From_52W_Low"]
    if "RSI14" in df.columns:
        price_cols += ["RSI14","MACD","MACD_Signal","MACD_Hist"]
    if "BB_Upper" in df.columns:
        price_cols += ["BB_Upper","BB_Middle","BB_Lower","BB_Width","BB_Pct"]
    if "Rolling_Beta_60d" in df.columns:
        price_cols += ["Rolling_Beta_60d"]
    price_cols += [c for c in df.columns if c.endswith("_Cumulative")]

    full_df   = df[[c for c in price_cols if c in df.columns]].copy()
    # Cap raw data sheet at 1,300 rows (~5yr daily). All calculations use the full dataset.
    ROW_CAP   = 1300
    truncated = len(full_df) > ROW_CAP
    export_df = full_df.tail(ROW_CAP).copy() if truncated else full_df

    ws_p = wb.create_sheet("Price_Indicators")

    # Info banner when data is capped
    if truncated:
        ws_p.insert_rows(1)
        note = (f"Note: Showing most recent {ROW_CAP} bars ({bar_size} data). "
                f"Full {len(full_df)}-bar history used for all calculations & charts. "
                f"See Annual_Summary sheet for full year-by-year breakdown.")
        ws_p.merge_cells(f"A1:{get_column_letter(len(export_df.columns))}1")
        nc = ws_p["A1"]
        nc.value     = note
        nc.font      = Font(name="Calibri", size=9, italic=True, color="1F4E79")
        nc.fill      = PatternFill("solid", fgColor="D6E4F0")
        nc.alignment = Alignment(wrap_text=True, vertical="center")
        ws_p.row_dimensions[1].height = 30

    for r in dataframe_to_rows(export_df, index=False, header=True):
        ws_p.append(r)

    # Header row is row 2 if banner exists, else row 1
    hdr_row = 2 if truncated else 1
    data_start = hdr_row + 1
    for cell in ws_p[hdr_row]:
        _hdr_cell(cell)
    auto_col_width(ws_p)
    ws_p.freeze_panes = f"A{data_start}"
    ws_p.auto_filter.ref = f"A{hdr_row}:{get_column_letter(ws_p.max_column)}{hdr_row}"

    col_map    = {c[0].column_letter: c[0].value
                  for c in ws_p.iter_cols(1, ws_p.max_column, hdr_row, hdr_row)}
    price_hdrs = {"MA20","MA50","MA200","BB_Upper","BB_Middle","BB_Lower","52W_High","52W_Low"}
    pct_hdrs   = {"Daily_Return","Cumulative_Index","Close_vs_MA20","Close_vs_MA50","Close_vs_MA200",
                  "Volatility_20d","Drawdown_20d","Drawdown_60d","BB_Pct","Vol_MA20","Volume_vs_Avg",
                  "Pct_From_52W_High","Pct_From_52W_Low"}

    for row in ws_p.iter_rows(min_row=data_start):
        for cell in row:
            h = col_map.get(cell.column_letter)
            if   h == "Date":         cell.number_format = "yyyy-mm-dd"
            elif h == "Volume":       cell.number_format = "#,##0"
            elif h in price_hdrs:     cell.number_format = '_($* #,##0.00_)'
            elif h in pct_hdrs:       cell.number_format = "0.00%"

    dr_col = next((l for l, h in col_map.items() if h == "Daily_Return"), None)
    if dr_col:
        ws_p.conditional_formatting.add(
            f"{dr_col}{data_start}:{dr_col}{ws_p.max_row}",
            ColorScaleRule(start_type="num", start_value=-0.05, start_color="FFAAAA",
                           mid_type="num",   mid_value=0,        mid_color="FFFFFF",
                           end_type="num",   end_value=0.05,     end_color="AAFFAA"))
    rsi_col = next((l for l, h in col_map.items() if h == "RSI14"), None)
    if rsi_col:
        rng = f"{rsi_col}{data_start}:{rsi_col}{ws_p.max_row}"
        ws_p.conditional_formatting.add(rng, CellIsRule(operator="greaterThan", formula=["70"],
            fill=PatternFill("solid", fgColor="FF9999")))
        ws_p.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=["30"],
            fill=PatternFill("solid", fgColor="99FF99")))
    return ws_p, export_df


# ── News sheet ────────────────────────────────────────────────────────────────
def _build_news_sheet(wb, news_list):
    if not news_list:
        return
    ws_n = wb.create_sheet("News_Headlines")
    ws_n.append(["Date","Headline","Publisher","URL"])
    style_header_row(ws_n)
    for ni, item in enumerate(news_list, 2):
        for ci, key in enumerate(["Date","Headline","Publisher","URL"], 1):
            c = ws_n.cell(row=ni, column=ci, value=item.get(key,""))
            c.font = Font(name="Calibri", size=10)
            c.border = _border()
            if ni % 2 == 0:
                c.fill = PatternFill("solid", fgColor=GREY_ROW)
    ws_n.column_dimensions["A"].width = 18
    ws_n.column_dimensions["B"].width = 80
    ws_n.column_dimensions["C"].width = 22
    ws_n.column_dimensions["D"].width = 60
    ws_n.freeze_panes = "A2"
    ws_n.auto_filter.ref = "A1:D1"


# ── Peer comparison sheet ─────────────────────────────────────────────────────
def _build_peer_sheet(wb, peer_df):
    if peer_df is None or peer_df.empty:
        return
    ws_peer = wb.create_sheet("Peer_Comparison")
    for r in dataframe_to_rows(peer_df, index=False, header=True):
        ws_peer.append(r)
    style_header_row(ws_peer, bg=MID_BLUE)
    auto_col_width(ws_peer)
    ws_peer.freeze_panes = "A2"
    ws_peer.auto_filter.ref = f"A1:{get_column_letter(ws_peer.max_column)}1"
    for ri, row in enumerate(ws_peer.iter_rows(min_row=2), 2):
        bg = "D6E4F0" if ri == 2 else (GREY_ROW if ri % 2 == 0 else WHITE)
        for cell in row:
            cell.font   = Font(name="Calibri", size=10, bold=(ri == 2))
            cell.border = _border()
            cell.fill   = PatternFill("solid", fgColor=bg)


# ── Sector comparison sheet ───────────────────────────────────────────────────
def _build_sector_sheet(wb, ticker, df, sector_df):
    if sector_df is None:
        return None
    merged = pd.merge(df[["Date","Cumulative_Index"]], sector_df, on="Date", how="inner")
    merged = merged.rename(columns={"Cumulative_Index": f"{ticker}_Cumulative"})
    ws_s = wb.create_sheet("Sector_Comparison")
    for r in dataframe_to_rows(merged, index=False, header=True):
        ws_s.append(r)
    style_header_row(ws_s)
    auto_col_width(ws_s)
    ws_s.freeze_panes = "A2"
    ws_s.auto_filter.ref = f"A1:{get_column_letter(ws_s.max_column)}1"
    for row in ws_s.iter_rows(min_row=2):
        for cell in row:
            cell.number_format = "yyyy-mm-dd" if cell.column == 1 else "0.00"
    return ws_s


# ── Correlation matrix sheet ──────────────────────────────────────────────────
def _build_correlation_sheet(wb, corr_matrix):
    if corr_matrix is None:
        return
    ws_corr = wb.create_sheet("Correlation_Matrix")
    labels  = list(corr_matrix.columns)
    ws_corr.cell(row=1, column=1, value="Correlation Matrix (Daily Returns)")
    ws_corr.cell(row=1, column=1).font = Font(bold=True, size=12, color=DARK_BLUE, name="Calibri")
    ws_corr.merge_cells(f"A1:{get_column_letter(len(labels)+1)}1")
    for ci, lbl in enumerate(labels, 2):
        _hdr_cell(ws_corr.cell(row=2, column=ci, value=lbl), bg=MID_BLUE)
    for ri, lbl in enumerate(labels, 3):
        _hdr_cell(ws_corr.cell(row=ri, column=1, value=lbl), bg=MID_BLUE)
        for ci, col_lbl in enumerate(labels, 2):
            val  = corr_matrix.loc[lbl, col_lbl]
            cell = ws_corr.cell(row=ri, column=ci, value=round(float(val), 4))
            cell.number_format = "0.0000"
            cell.font          = Font(name="Calibri", size=10)
            cell.border        = _border()
            cell.alignment     = Alignment(horizontal="center")
    ws_corr.conditional_formatting.add(
        f"B3:{get_column_letter(len(labels)+1)}{len(labels)+2}",
        ColorScaleRule(start_type="num", start_value=-1, start_color="FF9999",
                       mid_type="num",   mid_value=0,    mid_color="FFFFFF",
                       end_type="num",   end_value=1,    end_color="99CCFF"))
    auto_col_width(ws_corr)


# ── Monte Carlo sheet ─────────────────────────────────────────────────────────
def _build_monte_carlo_sheet(wb, mc_sim_df, mc_summary):
    if mc_sim_df is None:
        return None, None, None
    pct_col_start = 53
    ws_mc = wb.create_sheet("Monte_Carlo")
    ws_mc["A1"] = "Monte Carlo Simulation Summary"
    ws_mc["A1"].font = Font(bold=True, size=13, color=DARK_BLUE, name="Calibri")
    ws_mc["A2"] = "Field"
    ws_mc["B2"] = "Value"
    for cell in ws_mc[2]:
        _hdr_cell(cell, bg=MID_BLUE)
    for i, (k, v) in enumerate(mc_summary.items(), 3):
        ws_mc.cell(row=i, column=1, value=k).font      = Font(name="Calibri", size=10)
        ws_mc.cell(row=i, column=2, value=str(v)).font = Font(name="Calibri", size=10, bold=True)
    summary_end  = 3 + len(mc_summary)
    start_row_mc = summary_end + 2
    ws_mc.cell(row=start_row_mc, column=1, value="Day")
    for j in range(50):
        ws_mc.cell(row=start_row_mc, column=j+2, value=f"Sim {j+1}")
    for day_idx, row_data in enumerate(mc_sim_df.iloc[:, :50].itertuples(index=False)):
        r = start_row_mc + 1 + day_idx
        ws_mc.cell(row=r, column=1, value=day_idx)
        for j, price in enumerate(row_data):
            ws_mc.cell(row=r, column=j+2, value=round(price, 2)).number_format = '_($* #,##0.00_)'
    pct_labels = ["P5 (Bear)","P25 (Low)","P50 (Median)","P75 (Bull)","P95 (Best)"]
    ws_mc.cell(row=start_row_mc, column=pct_col_start, value="Day")
    for j, lbl in enumerate(pct_labels):
        _hdr_cell(ws_mc.cell(row=start_row_mc, column=pct_col_start+j+1, value=lbl), bg=MID_BLUE)
    for day_idx in range(len(mc_sim_df)):
        row_prices = mc_sim_df.iloc[day_idx].values
        ws_mc.cell(row=start_row_mc+1+day_idx, column=pct_col_start, value=day_idx)
        for j, pct in enumerate([5,25,50,75,95]):
            ws_mc.cell(row=start_row_mc+1+day_idx, column=pct_col_start+j+1,
                       value=round(np.percentile(row_prices, pct), 2)).number_format = '_($* #,##0.00_)'
    ws_mc.freeze_panes = f"A{start_row_mc+1}"
    return ws_mc, start_row_mc, pct_col_start


# ── Chart helpers (matplotlib) ────────────────────────────────────────────────
def _mpl_chart(fig):
    """Save a matplotlib figure to a BytesIO PNG buffer."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _chart_style(ax, title, xlabel="Date", ylabel="Price ($)"):
    ax.set_title(title, fontsize=13, fontweight="bold", color="#1F4E79", pad=10)
    ax.set_xlabel(xlabel, fontsize=10, color="#444444")
    ax.set_ylabel(ylabel, fontsize=10, color="#444444")
    ax.tick_params(axis="x", rotation=35, labelsize=8)
    ax.tick_params(axis="y", labelsize=9)
    ax.grid(axis="y", linestyle="--", alpha=0.4, color="#cccccc")
    ax.grid(axis="x", linestyle=":", alpha=0.25, color="#cccccc")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(fontsize=9, framealpha=0.7)


def _add_mpl_image(ws, buf, anchor, width_px=900, height_px=400):
    img = XLImage(buf)
    img.width  = width_px
    img.height = height_px
    ws.add_image(img, anchor)


# ── Charts sheet ──────────────────────────────────────────────────────────────
def _build_charts_sheet(wb, ticker, ws_p, export_df, ws_s, ws_mc_data, full_df=None):
    ws_ch = wb.create_sheet("Charts")
    ws_ch.sheet_view.showGridLines = False

    if not MPL_AVAILABLE:
        ws_ch["A1"] = "Charts unavailable — matplotlib not installed."
        return

    # Use full_df for charting (has Close/Volume); export_df only has derived metrics
    chart_df = full_df if full_df is not None else export_df
    dates = pd.to_datetime(chart_df["Date"]) if "Date" in chart_df.columns else None

    # ── Chart 1: Price + Moving Averages ──────────────────────────────────────
    if "Close" in chart_df.columns:
        fig, ax = plt.subplots(figsize=(13, 5))
        ax.plot(dates, chart_df["Close"], color="#1F4E79", linewidth=1.8, label="Close", zorder=3)
        for ma, col, lw in [("MA20","#E8A838",1.2), ("MA50","#2ECC71",1.2), ("MA200","#E74C3C",1.2)]:
            if ma in chart_df.columns:
                ax.plot(dates, chart_df[ma], color=col, linewidth=lw, linestyle="--", label=ma, zorder=2)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.2f}"))
        _chart_style(ax, f"{ticker} — Price & Moving Averages")
        _add_mpl_image(ws_ch, _mpl_chart(fig), "A1")

    # ── Chart 2: Volume ───────────────────────────────────────────────────────
    vol_col = "Volume" if "Volume" in chart_df.columns else ("Vol_MA20" if "Vol_MA20" in chart_df.columns else None)
    if vol_col:
        fig, ax = plt.subplots(figsize=(13, 3.5))
        colors = ["#2ECC71" if r >= 0 else "#E74C3C"
                  for r in chart_df.get("Daily_Return", pd.Series([0]*len(chart_df))).fillna(0)]
        ax.bar(dates, chart_df[vol_col], color=colors, width=1.5, alpha=0.75)
        if "Vol_MA20" in chart_df.columns and vol_col != "Vol_MA20":
            ax.plot(dates, chart_df["Vol_MA20"], color="#1F4E79", linewidth=1.2,
                    linestyle="--", label="20-Day Avg")
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x/1e6:.1f}M"))
        _chart_style(ax, f"{ticker} — Volume", ylabel="Volume")
        _add_mpl_image(ws_ch, _mpl_chart(fig), "A22", height_px=280)

    # ── Chart 3: Bollinger Bands ──────────────────────────────────────────────
    if "BB_Upper" in chart_df.columns and "Close" in chart_df.columns:
        fig, ax = plt.subplots(figsize=(13, 5))
        ax.plot(dates, chart_df["Close"],     color="#1F4E79", linewidth=1.8, label="Close",    zorder=3)
        ax.plot(dates, chart_df["BB_Upper"],  color="#E74C3C", linewidth=1.0, linestyle="--", label="BB Upper")
        ax.plot(dates, chart_df["BB_Middle"], color="#888888", linewidth=1.0, linestyle="--", label="BB Mid")
        ax.plot(dates, chart_df["BB_Lower"],  color="#2ECC71", linewidth=1.0, linestyle="--", label="BB Lower")
        ax.fill_between(dates, chart_df["BB_Upper"], chart_df["BB_Lower"], alpha=0.07, color="#2E75B6")
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.2f}"))
        _chart_style(ax, f"{ticker} — Bollinger Bands (20-day, 2σ)")
        _add_mpl_image(ws_ch, _mpl_chart(fig), "A35")

    # ── Chart 4: RSI ──────────────────────────────────────────────────────────
    if "RSI14" in chart_df.columns:
        fig, ax = plt.subplots(figsize=(13, 3))
        ax.plot(dates, chart_df["RSI14"], color="#6C3483", linewidth=1.4, label="RSI (14)")
        ax.axhline(70, color="#E74C3C", linewidth=0.8, linestyle="--", alpha=0.7, label="Overbought (70)")
        ax.axhline(30, color="#2ECC71", linewidth=0.8, linestyle="--", alpha=0.7, label="Oversold (30)")
        ax.fill_between(dates, chart_df["RSI14"], 70,
                        where=chart_df["RSI14"] >= 70, alpha=0.15, color="#E74C3C")
        ax.fill_between(dates, chart_df["RSI14"], 30,
                        where=chart_df["RSI14"] <= 30, alpha=0.15, color="#2ECC71")
        ax.set_ylim(0, 100)
        _chart_style(ax, f"{ticker} — RSI (14)", ylabel="RSI")
        _add_mpl_image(ws_ch, _mpl_chart(fig), "A56", height_px=240)

    # ── Chart 5: Cumulative Return vs Benchmarks ──────────────────────────────
    cum_cols = [c for c in chart_df.columns if c.endswith("_Cumulative")]
    if "Cumulative_Index" in chart_df.columns:
        fig, ax = plt.subplots(figsize=(13, 5))
        ax.plot(dates, chart_df["Cumulative_Index"], color="#1F4E79", linewidth=2.0,
                label=ticker, zorder=3)
        bench_colors = ["#E74C3C", "#2ECC71", "#F39C12", "#8E44AD"]
        for i, col in enumerate(cum_cols):
            label = col.replace("_Cumulative", "")
            ax.plot(dates, chart_df[col], color=bench_colors[i % len(bench_colors)],
                    linewidth=1.2, linestyle="--", label=label)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}"))
        ax.axhline(100, color="#aaaaaa", linewidth=0.7, linestyle=":")
        _chart_style(ax, f"{ticker} — Cumulative Return vs Benchmarks", ylabel="Index (100 = start)")
        _add_mpl_image(ws_ch, _mpl_chart(fig), "A68")

    # ── Chart 6: Sector comparison ────────────────────────────────────────────
    if ws_s is not None:
        sect_data = []
        for row in ws_s.iter_rows(min_row=2, values_only=True):
            sect_data.append(row)
        if sect_data:
            sect_df   = pd.DataFrame(sect_data, columns=[c[0].value for c in ws_s.iter_cols(1, ws_s.max_column, 1, 1)])
            sect_dates = pd.to_datetime(sect_df.iloc[:, 0])
            fig, ax = plt.subplots(figsize=(13, 5))
            ax.plot(sect_dates, sect_df.iloc[:, 1], color="#1F4E79", linewidth=2.0, label=ticker)
            ax.plot(sect_dates, sect_df.iloc[:, 2], color="#E74C3C", linewidth=1.4,
                    linestyle="--", label="Sector ETF")
            ax.axhline(100, color="#aaaaaa", linewidth=0.7, linestyle=":")
            _chart_style(ax, f"{ticker} vs Sector ETF — Cumulative Return", ylabel="Index (100 = start)")
            _add_mpl_image(ws_ch, _mpl_chart(fig), "A88")

    # ── Chart 7: Monte Carlo ──────────────────────────────────────────────────
    if ws_mc_data and ws_mc_data[0]:
        ws_mc, start_row_mc, pct_col_start = ws_mc_data
        n_rows = min(253, ws_mc.max_row - start_row_mc)
        pct_labels = ["P5 (Bear)","P25 (Low)","P50 (Median)","P75 (Bull)","P95 (Best)"]
        pct_colors = ["#E74C3C","#E8A838","#1F4E79","#2ECC71","#27AE60"]
        mc_rows = []
        for r in range(start_row_mc + 1, start_row_mc + 1 + n_rows):
            mc_rows.append([ws_mc.cell(row=r, column=pct_col_start + j + 1).value for j in range(5)])
        if mc_rows:
            mc_arr = np.array(mc_rows, dtype=float)
            days   = list(range(len(mc_arr)))
            fig, ax = plt.subplots(figsize=(13, 5))
            ax.fill_between(days, mc_arr[:, 0], mc_arr[:, 4], alpha=0.12, color="#2E75B6", label="P5–P95 range")
            ax.fill_between(days, mc_arr[:, 1], mc_arr[:, 3], alpha=0.2,  color="#2E75B6", label="P25–P75 range")
            for j, (lbl, col) in enumerate(zip(pct_labels, pct_colors)):
                lw = 2.2 if "Median" in lbl else 1.0
                ls = "-" if "Median" in lbl else "--"
                ax.plot(days, mc_arr[:, j], color=col, linewidth=lw, linestyle=ls, label=lbl)
            ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.2f}"))
            ax.set_xlabel("Trading Days Forward", fontsize=10)
            _chart_style(ax, f"{ticker} — Monte Carlo Forecast ({n_rows} days)")
            ax.set_xlabel("Trading Days Forward", fontsize=10)
            _add_mpl_image(ws_ch, _mpl_chart(fig), "A108")


# ── Master orchestrator ───────────────────────────────────────────────────────
# ── Fundamentals sheet (EDGAR-sourced statements + quality scores) ────────────
def _build_fundamentals_sheet(wb, fundamentals):
    if not fundamentals or not fundamentals.get("ok"):
        return
    ws = wb.create_sheet("Fundamentals")
    f = fundamentals
    v, m, r, l, g = f["valuation"], f["margins"], f["returns"], f["leverage"], f["growth"]
    q, fc = f.get("quality", {}), f.get("fcf", {})

    def _fmt(x, suffix=""):
        return f"{x}{suffix}" if x is not None else "N/A"

    def _bn(x):
        return f"${x/1e9:,.1f}B" if isinstance(x, (int, float)) else "N/A"

    row = 1
    tc = ws.cell(row=row, column=1,
                 value=f"Fundamentals & Valuation   ·   source: {f.get('source','—')}"
                       f"   ·   FY ending {f.get('as_of','—')}")
    tc.font = Font(size=14, bold=True, color=DARK_BLUE, name="Calibri")
    row += 2

    def section(title, pairs):
        nonlocal row
        for col in (1, 2):
            c = ws.cell(row=row, column=col, value=title if col == 1 else None)
            c.font = Font(bold=True, color=WHITE, name="Calibri", size=11)
            c.fill = PatternFill("solid", fgColor=DARK_BLUE)
        row += 1
        for lbl, val in pairs:
            cl = ws.cell(row=row, column=1, value=lbl)
            cv = ws.cell(row=row, column=2, value=val)
            cl.font, cv.font = Font(name="Calibri", size=10), Font(name="Calibri", size=10, bold=True)
            cl.border = cv.border = _border()
            if row % 2 == 0:
                cl.fill = cv.fill = PatternFill("solid", fgColor=GREY_ROW)
            row += 1
        row += 1

    _ig = f.get("implied_growth")
    section("Valuation", [
        ("P/E", _fmt(v["pe"], "x")), ("P/S", _fmt(v["ps"], "x")), ("P/B", _fmt(v["pb"], "x")),
        ("EV / EBITDA", _fmt(f.get("ev_ebitda"), "x")),
        ("Earnings Yield", _fmt(v["earnings_yield"], "%")),
        ("FCF Yield", _fmt(fc.get("fcf_yield"), "%")),
        ("Reverse-DCF implied growth", _fmt(round(_ig * 100, 1) if _ig is not None else None, "%")),
    ])
    section("Profitability & Returns", [
        ("Gross Margin", _fmt(m["gross"], "%")), ("Operating Margin", _fmt(m["operating"], "%")),
        ("Net Margin", _fmt(m["net"], "%")), ("Return on Equity", _fmt(r["roe"], "%")),
        ("Return on Assets", _fmt(r["roa"], "%")), ("Free Cash Flow", _bn(fc.get("fcf"))),
    ])
    section("Growth", [
        ("Revenue YoY", _fmt(g["revenue_yoy"], "%")), ("EPS YoY", _fmt(g["eps_yoy"], "%")),
        ("Revenue CAGR", _fmt(g["revenue_cagr"], "%")), ("EPS CAGR", _fmt(g["eps_cagr"], "%")),
    ])
    section("Balance Sheet & Quality", [
        ("Current Ratio", _fmt(l["current_ratio"])), ("Debt / Equity", _fmt(l["debt_to_equity"])),
        ("Piotroski F-Score", f"{q['f_score']} / 9" if q.get("f_score") is not None else "N/A"),
        ("Altman Z-Score", f"{q['z_score']} ({q['z_zone']})" if q.get("z_score") is not None else "N/A"),
    ])

    t = f.get("trend", {})
    periods = t.get("periods", [])
    if periods:
        for col in range(1, len(periods) + 2):
            hc = ws.cell(row=row, column=col,
                         value=("Fiscal Period" if col == 1 else periods[col - 2]))
            hc.font = Font(bold=True, color=WHITE, name="Calibri", size=10)
            hc.fill = PatternFill("solid", fgColor=MID_BLUE)
            hc.border = _border()
        row += 1
        for label, key in [("Revenue ($B)", "revenue"), ("Net Income ($B)", "net_income"),
                           ("Free Cash Flow ($B)", "fcf")]:
            ws.cell(row=row, column=1, value=label).font = Font(name="Calibri", size=10, bold=True)
            for ci, x in enumerate(t.get(key, []), 2):
                cc = ws.cell(row=row, column=ci,
                             value=(round(x / 1e9, 1) if isinstance(x, (int, float)) else "—"))
                cc.font = Font(name="Calibri", size=10)
                cc.border = _border()
            ws.cell(row=row, column=1).border = _border()
            row += 1

    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 16
    for col in "CDEFGHIJK":
        ws.column_dimensions[col].width = 12


# ── Valuation sheet (transparent DCF: conclusion, scenarios, projection, sensitivity)
def _build_valuation_sheet(wb, ticker, dcf, fundamentals=None):
    if not dcf or not dcf.get("ok"):
        return
    ws = wb.create_sheet("Valuation")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 32
    for col in ("B", "C", "D", "E", "F"):
        ws.column_dimensions[col].width = 15

    def _bn(x):
        return f"${x / 1e9:,.1f}B" if isinstance(x, (int, float)) else "—"

    ws.merge_cells("A1:F1")
    t = ws["A1"]
    t.value = f"{ticker} — Discounted Cash Flow Valuation"
    t.font  = Font(size=14, bold=True, color=DARK_BLUE, name="Calibri")
    ws.merge_cells("A2:F2")
    sub = ws["A2"]
    sub.value = ("Two-stage unlevered DCF on free cash flow.  Fair value = "
                 "(PV of projected FCF + PV of terminal value − net debt) ÷ shares.  "
                 "Assumption-driven — a valuation lens, not a price target.")
    sub.font      = Font(size=9, italic=True, color="888888", name="Calibri")
    sub.alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[2].height = 26

    def sec(row, label, span="F"):
        ws.merge_cells(f"A{row}:{span}{row}")
        c = ws.cell(row=row, column=1, value=label)
        c.font = Font(bold=True, color=WHITE, name="Calibri", size=11)
        c.fill = PatternFill("solid", fgColor=DARK_BLUE)
        ws.row_dimensions[row].height = 18

    def kv2(row, label, value, bold=True):
        cl = ws.cell(row=row, column=1, value=label)
        cv = ws.cell(row=row, column=2, value=value)
        cl.font = Font(name="Calibri", size=10)
        cv.font = Font(name="Calibri", size=10, bold=bold)
        cv.alignment = Alignment(horizontal="right")
        cl.border = cv.border = _border()

    row = 4
    # ── Conclusion ────────────────────────────────────────────────────────────
    sec(row, "Conclusion"); row += 1
    up = dcf.get("upside")
    verdict, vcol = (("Undervalued vs DCF", GREEN_OK) if up is not None and up > 0.15 else
                     ("Overvalued vs DCF",  RED_BAD)  if up is not None and up < -0.15 else
                     ("Fairly valued vs DCF", DARK_BLUE))
    kv2(row, "Base-case Fair Value / Share", f"${dcf['fair_value']:,.2f}"); row += 1
    kv2(row, "Current Price",                f"${dcf['price']:,.2f}");      row += 1
    kv2(row, "Upside / Downside", f"{up * 100:+.1f}%" if up is not None else "—"); row += 1
    ws.cell(row=row, column=1, value="Verdict").font = Font(name="Calibri", size=10)
    vc = ws.cell(row=row, column=2, value=verdict)
    vc.font = Font(name="Calibri", size=10, bold=True, color=WHITE)
    vc.fill = PatternFill("solid", fgColor=vcol)
    vc.alignment = Alignment(horizontal="right")
    ws.cell(row=row, column=1).border = vc.border = _border()
    row += 2

    # ── Scenarios ─────────────────────────────────────────────────────────────
    sec(row, "Scenarios"); row += 1
    for ci, h in enumerate(["Scenario", "Stage-1 FCF Growth", "Fair Value / Share",
                            "Upside / Downside"], 1):
        _hdr_cell(ws.cell(row=row, column=ci, value=h), bg=MID_BLUE)
    row += 1
    for name, key in [("Bear", "bear"), ("Base", "base"), ("Bull", "bull")]:
        s = dcf["scenarios"].get(key, {})
        vals = [name,
                f"{s.get('growth', 0) * 100:.1f}%",
                f"${s['fair_value']:,.2f}" if s.get("fair_value") else "—",
                f"{s['upside'] * 100:+.1f}%" if s.get("upside") is not None else "—"]
        for ci, v in enumerate(vals, 1):
            c = ws.cell(row=row, column=ci, value=v)
            c.font   = Font(name="Calibri", size=10, bold=(name == "Base"))
            c.border = _border()
            if name == "Base":
                c.fill = PatternFill("solid", fgColor="D6E4F0")
        row += 1
    row += 1

    # ── Key assumptions ───────────────────────────────────────────────────────
    sec(row, "Key Assumptions"); row += 1
    imp = dcf.get("market_implied_growth")
    for lbl, val in [
        ("Discount rate (WACC)",                f"{dcf['wacc'] * 100:.1f}%"),
        ("Terminal growth rate",                f"{dcf['terminal_growth'] * 100:.1f}%"),
        ("Explicit forecast horizon",           f"{dcf['years']} years"),
        ("Normalised base FCF",                 _bn(dcf['base_fcf'])),
        ("Base-case stage-1 growth",            f"{dcf['base_growth'] * 100:.1f}%"),
        ("Net debt (total debt − cash)",        _bn(dcf['net_debt'])),
        ("Shares (market cap ÷ price)",         f"{dcf['shares'] / 1e9:,.2f}B"),
        ("Market-implied FCF growth (at today's price)",
         f"{imp * 100:.1f}%" if imp is not None else "—"),
    ]:
        kv2(row, lbl, val); row += 1
    row += 1

    # ── Base-case projection + EV→equity bridge ───────────────────────────────
    sec(row, "Base-Case Projection & Value Bridge"); row += 1
    for ci, h in enumerate(["Year", "Growth", "Projected FCF", "PV of FCF"], 1):
        _hdr_cell(ws.cell(row=row, column=ci, value=h), bg=MID_BLUE)
    row += 1
    for p in dcf["projection"]:
        vals = [p["year"], f"{p['growth'] * 100:.1f}%", _bn(p["fcf"]), _bn(p["pv"])]
        for ci, v in enumerate(vals, 1):
            c = ws.cell(row=row, column=ci, value=v)
            c.font   = Font(name="Calibri", size=10)
            c.border = _border()
            if row % 2 == 0:
                c.fill = PatternFill("solid", fgColor=GREY_ROW)
        row += 1
    for lbl, val in [("PV of explicit FCF",   _bn(dcf["pv_explicit"])),
                     ("Terminal value",        _bn(dcf["terminal_value"])),
                     ("PV of terminal value",  _bn(dcf["pv_terminal"])),
                     ("Enterprise value",      _bn(dcf["enterprise_value"])),
                     ("Less: net debt",        _bn(dcf["net_debt"])),
                     ("Equity value",          _bn(dcf["equity_value"]))]:
        kv2(row, lbl, val); row += 1
    row += 1

    # ── Sensitivity grid (WACC × terminal growth) ─────────────────────────────
    sec(row, "Sensitivity — Fair Value / Share  (WACC × Terminal Growth)"); row += 1
    sens = dcf["sensitivity"]
    corner = ws.cell(row=row, column=1, value="WACC ╲ Term. g")
    corner.font   = Font(bold=True, size=9, name="Calibri")
    corner.border = _border()
    for cj, tg in enumerate(sens["tg_axis"], 2):
        _hdr_cell(ws.cell(row=row, column=cj, value=f"{tg * 100:.1f}%"), bg=MID_BLUE)
    row += 1
    grid_top = row
    for ri, w in enumerate(sens["wacc_axis"]):
        _hdr_cell(ws.cell(row=row, column=1, value=f"{w * 100:.1f}%"), bg=MID_BLUE)
        for cj, fv in enumerate(sens["grid"][ri], 2):
            c = ws.cell(row=row, column=cj, value=(round(fv, 2) if fv else None))
            c.number_format = '_($* #,##0.00_)'
            c.font          = Font(name="Calibri", size=10)
            c.border        = _border()
            c.alignment     = Alignment(horizontal="right")
        row += 1
    last_col = get_column_letter(1 + len(sens["tg_axis"]))
    ws.conditional_formatting.add(
        f"B{grid_top}:{last_col}{row - 1}",
        ColorScaleRule(start_type="min",        start_color="FFC7CE",
                       mid_type="percentile",   mid_value=50, mid_color="FFEB9C",
                       end_type="max",          end_color="C6EFCE"))
    row += 1

    _narrative_box(
        ws, row,
        "A DCF is a model, not a forecast. Small changes in WACC, terminal growth, or "
        "the FCF growth path move fair value materially — see the sensitivity grid. Base "
        "FCF is normalised over recent years; shares are derived from market cap ÷ price. "
        + DISCLAIMER_SHORT,
        height=52, italic=True, bg="FFF8E1")


def build_excel(ticker, df, period,
                company_details=None, sector_df=None,
                mc_sim_df=None, mc_summary=None,
                news_list=None, peer_df=None,
                corr_matrix=None,
                resistance_levels=None, support_levels=None,
                summary_text="", bar_size="day", fundamentals=None,
                analyst_data=None, dcf=None):

    wb = Workbook()
    wb.remove(wb.active)

    ws_dash = _build_dashboard(wb, ticker, df, company_details, mc_summary,
                                resistance_levels, support_levels, summary_text,
                                analyst_data=analyst_data, dcf=dcf, fundamentals=fundamentals)
    ws_p, export_df = _build_price_sheet(wb, df, bar_size=bar_size)
    _build_annual_summary(wb, df)
    _build_news_sheet(wb, news_list)
    _build_peer_sheet(wb, peer_df)
    ws_s       = _build_sector_sheet(wb, ticker, df, sector_df)
    _build_correlation_sheet(wb, corr_matrix)
    ws_mc_data = _build_monte_carlo_sheet(wb, mc_sim_df, mc_summary)
    _build_charts_sheet(wb, ticker, ws_p, export_df, ws_s, ws_mc_data, full_df=df)
    _build_valuation_sheet(wb, ticker, dcf, fundamentals)
    _build_fundamentals_sheet(wb, fundamentals)

    # Final tab order (Cover first, then Dashboard, then the rest). Valuation sits
    # right after the Dashboard — "what is it worth?" follows "what's the answer?".
    # The TOC is built from this same order so the cover links match the tab strip.
    desired = ["Cover","Dashboard","Valuation","Fundamentals","Annual_Summary","Price_Indicators","News_Headlines",
               "Peer_Comparison","Sector_Comparison","Correlation_Matrix",
               "Monte_Carlo","Charts"]
    built     = wb.sheetnames                                   # everything except Cover
    toc_order = [s for s in desired if s in built and s != "Cover"]
    extras    = [s for s in built if s not in desired]
    toc_order += extras

    # Cover last so it knows all sheet names; pass df for the KPI band.
    _build_cover(wb, ticker, period, toc_order, df=df)

    # Reorder tabs by sorting the internal sheet list directly. (openpyxl's
    # move_sheet offset is current_index + offset, so the old `index - i`
    # math moved sheets the wrong way and left the order untouched.)
    pos = {name: i for i, name in enumerate(["Cover"] + toc_order)}
    wb._sheets.sort(key=lambda s: pos.get(s.title, 999))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
