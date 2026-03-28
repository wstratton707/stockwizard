"""
pptx_builder.py — Professional PowerPoint report builder for StockWizard
Generates a polished stock analysis or portfolio deck using python-pptx + matplotlib.
"""

import io
import math
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from datetime import datetime

try:
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt
    PPTX_AVAILABLE = True
except ImportError:
    PPTX_AVAILABLE = False


# ── Brand colours ─────────────────────────────────────────────────────────────
C_NAVY      = RGBColor(0x1F, 0x4E, 0x79)   # dark navy — headers, accents
C_BLUE      = RGBColor(0x2E, 0x75, 0xB6)   # mid blue — sub-headers
C_ACCENT    = RGBColor(0x00, 0xB0, 0xF0)   # bright cyan — highlights
C_GREEN     = RGBColor(0x70, 0xAD, 0x47)   # positive values
C_RED       = RGBColor(0xC0, 0x00, 0x00)   # negative values
C_WHITE     = RGBColor(0xFF, 0xFF, 0xFF)
C_LIGHT     = RGBColor(0xF0, 0xF7, 0xFF)   # light blue bg
C_DARK_TEXT = RGBColor(0x1E, 0x29, 0x3B)
C_GREY_TEXT = RGBColor(0x64, 0x74, 0x8B)

SLIDE_W = Inches(13.33)
SLIDE_H = Inches(7.5)

MPL_COLORS = {
    "navy":   "#1F4E79",
    "blue":   "#2E75B6",
    "cyan":   "#00B0F0",
    "green":  "#70AD47",
    "red":    "#C00000",
    "orange": "#E8A838",
    "purple": "#8E44AD",
    "grey":   "#94A3B8",
}


# ── Slide helpers ─────────────────────────────────────────────────────────────

def _new_prs():
    prs = Presentation()
    prs.slide_width  = SLIDE_W
    prs.slide_height = SLIDE_H
    return prs


def _blank_slide(prs):
    blank_layout = prs.slide_layouts[6]  # completely blank
    return prs.slides.add_slide(blank_layout)


def _rect(slide, l, t, w, h, fill_rgb=None, line_rgb=None, line_width=None):
    shape = slide.shapes.add_shape(
        1,  # MSO_SHAPE_TYPE.RECTANGLE
        Inches(l), Inches(t), Inches(w), Inches(h)
    )
    if fill_rgb:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill_rgb
    else:
        shape.fill.background()
    if line_rgb:
        shape.line.color.rgb = line_rgb
        if line_width:
            shape.line.width = Pt(line_width)
    else:
        shape.line.fill.background()
    return shape


def _text_box(slide, text, l, t, w, h,
              font_size=12, bold=False, italic=False,
              color=C_DARK_TEXT, align=PP_ALIGN.LEFT,
              font_name="Arial"):
    txb = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    tf  = txb.text_frame
    tf.word_wrap = True
    p   = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = str(text)
    run.font.size    = Pt(font_size)
    run.font.bold    = bold
    run.font.italic  = italic
    run.font.color.rgb = color
    run.font.name    = font_name
    return txb


def _add_image(slide, buf, l, t, w, h):
    buf.seek(0)
    slide.shapes.add_picture(buf, Inches(l), Inches(t), Inches(w), Inches(h))


def _slide_header(slide, title, subtitle=None):
    """Dark navy top bar with title."""
    _rect(slide, 0, 0, 13.33, 1.15, fill_rgb=C_NAVY)
    _text_box(slide, title, 0.35, 0.12, 11.5, 0.65,
              font_size=28, bold=True, color=C_WHITE, align=PP_ALIGN.LEFT)
    if subtitle:
        _text_box(slide, subtitle, 0.35, 0.72, 11.5, 0.35,
                  font_size=11, italic=True, color=C_ACCENT, align=PP_ALIGN.LEFT)
    # thin accent line below header
    _rect(slide, 0, 1.15, 13.33, 0.04, fill_rgb=C_ACCENT)


def _slide_footer(slide, page_num, total_pages):
    _rect(slide, 0, 7.2, 13.33, 0.3, fill_rgb=C_NAVY)
    _text_box(slide, "StockWizard  |  For informational purposes only. Not investment advice.",
              0.3, 7.22, 10, 0.25, font_size=8, color=C_GREY_TEXT)
    _text_box(slide, f"{page_num} / {total_pages}",
              12.5, 7.22, 0.7, 0.25, font_size=8, color=C_GREY_TEXT, align=PP_ALIGN.RIGHT)


def _kv_block(slide, pairs, l, t, w, col_w=1.8, row_h=0.52, bg=True):
    """Render a list of (label, value, positive?) tuples as a metric table."""
    for i, item in enumerate(pairs):
        label, value = item[0], item[1]
        positive     = item[2] if len(item) > 2 else None
        row_t = t + i * row_h
        if bg:
            bg_col = C_LIGHT if i % 2 == 0 else C_WHITE
            _rect(slide, l, row_t, w, row_h - 0.02, fill_rgb=bg_col)
        _text_box(slide, label, l + 0.1, row_t + 0.07, col_w, row_h - 0.1,
                  font_size=10, color=C_GREY_TEXT)
        val_color = C_DARK_TEXT
        if positive is True:
            val_color = C_GREEN
        elif positive is False:
            val_color = C_RED
        _text_box(slide, str(value), l + col_w, row_t + 0.07, w - col_w - 0.1, row_h - 0.1,
                  font_size=11, bold=True, color=val_color, align=PP_ALIGN.RIGHT)


# ── Matplotlib chart helpers ──────────────────────────────────────────────────

def _chart_style(ax, title, xlabel="Date", ylabel=""):
    ax.set_title(title, fontsize=12, fontweight="bold", color="#1F4E79", pad=8)
    ax.set_xlabel(xlabel, fontsize=9, color="#475569")
    ax.set_ylabel(ylabel, fontsize=9, color="#475569")
    ax.tick_params(axis="x", rotation=30, labelsize=7.5)
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(axis="y", linestyle="--", alpha=0.35, color="#CBD5E1")
    ax.grid(axis="x", linestyle=":",  alpha=0.2,  color="#CBD5E1")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#CBD5E1")
    ax.spines["bottom"].set_color("#CBD5E1")
    ax.set_facecolor("#F8FAFC")
    ax.figure.patch.set_facecolor("#FFFFFF")
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=8.5, framealpha=0.8, edgecolor="#E2E8F0")


def _fig_to_buf(fig, dpi=140):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def _price_ma_chart(df, ticker, w=11, h=4.2):
    dates = pd.to_datetime(df["Date"])
    fig, ax = plt.subplots(figsize=(w, h))
    ax.plot(dates, df["Close"], color=MPL_COLORS["navy"], linewidth=1.8,
            label="Close", zorder=3)
    for ma, col, lw in [("MA20", MPL_COLORS["orange"], 1.1),
                         ("MA50", MPL_COLORS["green"],  1.1),
                         ("MA200", MPL_COLORS["red"],   1.1)]:
        if ma in df.columns and df[ma].notna().any():
            ax.plot(dates, df[ma], color=col, linewidth=lw,
                    linestyle="--", label=ma, zorder=2)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.2f}"))
    _chart_style(ax, f"{ticker} — Price & Moving Averages", ylabel="Price ($)")
    fig.tight_layout()
    return _fig_to_buf(fig)


def _volume_chart(df, ticker, w=11, h=2.8):
    dates  = pd.to_datetime(df["Date"])
    fig, ax = plt.subplots(figsize=(w, h))
    if "Volume" in df.columns:
        daily_ret = df.get("Daily_Return", pd.Series([0]*len(df))).fillna(0)
        colors = [MPL_COLORS["green"] if r >= 0 else MPL_COLORS["red"] for r in daily_ret]
        ax.bar(dates, df["Volume"], color=colors, width=1.2, alpha=0.7)
        if "Vol_MA20" in df.columns:
            ax.plot(dates, df["Vol_MA20"], color=MPL_COLORS["navy"],
                    linewidth=1.3, linestyle="--", label="20-Day Avg")
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x/1e6:.1f}M"))
    elif "Vol_MA20" in df.columns:
        ax.plot(dates, df["Vol_MA20"], color=MPL_COLORS["navy"],
                linewidth=1.4, label="20-Day Avg Volume")
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x/1e6:.1f}M"))
    _chart_style(ax, f"{ticker} — Volume", ylabel="Volume")
    fig.tight_layout()
    return _fig_to_buf(fig)


def _bollinger_chart(df, ticker, w=11, h=4.2):
    if "BB_Upper" not in df.columns:
        return None
    dates = pd.to_datetime(df["Date"])
    fig, ax = plt.subplots(figsize=(w, h))
    ax.plot(dates, df["Close"],     color=MPL_COLORS["navy"],  linewidth=1.8, label="Close",    zorder=3)
    ax.plot(dates, df["BB_Upper"],  color=MPL_COLORS["red"],   linewidth=1.0, linestyle="--", label="BB Upper")
    ax.plot(dates, df["BB_Middle"], color=MPL_COLORS["grey"],  linewidth=1.0, linestyle="--", label="BB Middle")
    ax.plot(dates, df["BB_Lower"],  color=MPL_COLORS["green"], linewidth=1.0, linestyle="--", label="BB Lower")
    ax.fill_between(dates, df["BB_Upper"], df["BB_Lower"],
                    alpha=0.07, color=MPL_COLORS["blue"])
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.2f}"))
    _chart_style(ax, f"{ticker} — Bollinger Bands (20-day, 2σ)", ylabel="Price ($)")
    fig.tight_layout()
    return _fig_to_buf(fig)


def _rsi_chart(df, ticker, w=11, h=2.8):
    if "RSI14" not in df.columns:
        return None
    dates = pd.to_datetime(df["Date"])
    fig, ax = plt.subplots(figsize=(w, h))
    ax.plot(dates, df["RSI14"], color=MPL_COLORS["purple"], linewidth=1.4, label="RSI (14)")
    ax.axhline(70, color=MPL_COLORS["red"],   linewidth=0.8, linestyle="--", alpha=0.7, label="Overbought (70)")
    ax.axhline(30, color=MPL_COLORS["green"], linewidth=0.8, linestyle="--", alpha=0.7, label="Oversold (30)")
    ax.fill_between(dates, df["RSI14"], 70,
                    where=df["RSI14"] >= 70, alpha=0.15, color=MPL_COLORS["red"])
    ax.fill_between(dates, df["RSI14"], 30,
                    where=df["RSI14"] <= 30, alpha=0.15, color=MPL_COLORS["green"])
    ax.set_ylim(0, 100)
    _chart_style(ax, f"{ticker} — RSI (14)", ylabel="RSI")
    fig.tight_layout()
    return _fig_to_buf(fig)


def _cumulative_chart(df, ticker, w=11, h=4.2):
    if "Cumulative_Index" not in df.columns:
        return None
    dates = pd.to_datetime(df["Date"])
    fig, ax = plt.subplots(figsize=(w, h))
    ax.plot(dates, df["Cumulative_Index"], color=MPL_COLORS["navy"],
            linewidth=2.0, label=ticker, zorder=3)
    bench_colors = [MPL_COLORS["red"], MPL_COLORS["green"],
                    MPL_COLORS["orange"], MPL_COLORS["purple"]]
    cum_cols = [c for c in df.columns if c.endswith("_Cumulative")]
    for i, col in enumerate(cum_cols):
        label = col.replace("_Cumulative", "")
        ax.plot(dates, df[col], color=bench_colors[i % len(bench_colors)],
                linewidth=1.3, linestyle="--", label=label)
    ax.axhline(100, color="#CBD5E1", linewidth=0.7, linestyle=":")
    ax.fill_between(dates, df["Cumulative_Index"], 100,
                    where=df["Cumulative_Index"] >= 100,
                    alpha=0.08, color=MPL_COLORS["green"])
    ax.fill_between(dates, df["Cumulative_Index"], 100,
                    where=df["Cumulative_Index"] < 100,
                    alpha=0.08, color=MPL_COLORS["red"])
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}"))
    _chart_style(ax, f"{ticker} — Cumulative Return vs Benchmarks",
                 ylabel="Index (100 = start)")
    fig.tight_layout()
    return _fig_to_buf(fig)


def _monte_carlo_chart(mc_sim_df, mc_summary, ticker, w=11, h=4.2):
    if mc_sim_df is None:
        return None
    pct_labels = ["P5 (Bear)", "P25 (Low)", "P50 (Median)", "P75 (Bull)", "P95 (Best)"]
    pct_colors = [MPL_COLORS["red"], MPL_COLORS["orange"], MPL_COLORS["navy"],
                  MPL_COLORS["green"], "#27AE60"]
    n = min(252, len(mc_sim_df))
    days = list(range(n))
    arr  = mc_sim_df.values[:n]
    pcts = np.percentile(arr, [5, 25, 50, 75, 95], axis=1)
    fig, ax = plt.subplots(figsize=(w, h))
    ax.fill_between(days, pcts[0], pcts[4], alpha=0.10, color=MPL_COLORS["blue"], label="P5–P95 range")
    ax.fill_between(days, pcts[1], pcts[3], alpha=0.18, color=MPL_COLORS["blue"], label="P25–P75 range")
    for j, (lbl, col) in enumerate(zip(pct_labels, pct_colors)):
        lw = 2.2 if "Median" in lbl else 1.0
        ls = "-"  if "Median" in lbl else "--"
        ax.plot(days, pcts[j], color=col, linewidth=lw, linestyle=ls, label=lbl)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.2f}"))
    _chart_style(ax, f"{ticker} — Monte Carlo Forecast ({n} Trading Days)",
                 xlabel="Trading Days Forward", ylabel="Price ($)")
    fig.tight_layout()
    return _fig_to_buf(fig)


def _drawdown_chart(df, ticker, w=11, h=2.8):
    if "Drawdown_60d" not in df.columns:
        return None
    dates = pd.to_datetime(df["Date"])
    fig, ax = plt.subplots(figsize=(w, h))
    dd = df["Drawdown_60d"] * 100
    ax.fill_between(dates, dd, 0, alpha=0.5, color=MPL_COLORS["red"], label="Drawdown (%)")
    ax.plot(dates, dd, color=MPL_COLORS["red"], linewidth=0.8)
    ax.axhline(-20, color="#888", linewidth=0.7, linestyle="--", alpha=0.5, label="-20% threshold")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.1f}%"))
    _chart_style(ax, f"{ticker} — 60-Day Rolling Drawdown", ylabel="Drawdown (%)")
    fig.tight_layout()
    return _fig_to_buf(fig)


# ── Portfolio chart helpers ───────────────────────────────────────────────────

def _alloc_pie_chart(weights, ticker_info, w=6, h=4.5):
    labels = []
    sizes  = []
    for tk, wt in sorted(weights.items(), key=lambda x: -x[1]):
        name = (ticker_info or {}).get(tk, {}).get("name", tk)
        short = name[:18] + "…" if len(name) > 18 else name
        labels.append(f"{tk}\n{short}")
        sizes.append(wt * 100)
    colors = plt.cm.Blues(np.linspace(0.35, 0.85, len(sizes)))
    fig, ax = plt.subplots(figsize=(w, h))
    wedges, texts, autotexts = ax.pie(
        sizes, labels=None, colors=colors,
        autopct=lambda p: f"{p:.1f}%" if p > 3 else "",
        startangle=140, pctdistance=0.78,
        wedgeprops={"linewidth": 1.2, "edgecolor": "white"}
    )
    for at in autotexts:
        at.set_fontsize(8)
        at.set_color("white")
        at.set_fontweight("bold")
    ax.legend(wedges, [f"{tk} ({wt:.1f}%)" for tk, wt in zip(weights.keys(), sizes)],
              loc="center left", bbox_to_anchor=(1, 0, 0.5, 1),
              fontsize=8, framealpha=0.7)
    ax.set_title("Portfolio Allocation", fontsize=12, fontweight="bold",
                 color="#1F4E79", pad=6)
    fig.patch.set_facecolor("#FFFFFF")
    fig.tight_layout()
    return _fig_to_buf(fig)


def _portfolio_performance_chart(backtest_df, w=11, h=4.0):
    if backtest_df is None or backtest_df.empty:
        return None
    dates = pd.to_datetime(backtest_df.index if backtest_df.index.name else backtest_df.iloc[:, 0])
    cols  = [c for c in backtest_df.columns if "cumul" in c.lower() or "value" in c.lower() or "index" in c.lower()]
    if not cols:
        cols = [backtest_df.columns[1]] if len(backtest_df.columns) > 1 else []
    if not cols:
        return None
    fig, ax = plt.subplots(figsize=(w, h))
    bench_colors = [MPL_COLORS["red"], MPL_COLORS["green"], MPL_COLORS["orange"]]
    for i, col in enumerate(cols):
        lw = 2.2 if i == 0 else 1.3
        ls = "-"  if i == 0 else "--"
        label = col.replace("_Cumulative", "").replace("_cumulative", "")
        clr = MPL_COLORS["navy"] if i == 0 else bench_colors[i % len(bench_colors)]
        ax.plot(dates, backtest_df[col], color=clr, linewidth=lw, linestyle=ls, label=label)
    ax.axhline(100, color="#CBD5E1", linewidth=0.7, linestyle=":")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}"))
    _chart_style(ax, "Portfolio vs Benchmark — Cumulative Return",
                 ylabel="Index (100 = start)")
    fig.tight_layout()
    return _fig_to_buf(fig)


def _holdings_bar_chart(stock_metrics, w=9, h=4.0):
    if not stock_metrics:
        return None
    tickers = list(stock_metrics.keys())
    returns = [stock_metrics[t].get("ann_return", 0) * 100 for t in tickers]
    colors  = [MPL_COLORS["green"] if r >= 0 else MPL_COLORS["red"] for r in returns]
    fig, ax = plt.subplots(figsize=(w, h))
    bars = ax.bar(tickers, returns, color=colors, alpha=0.85, edgecolor="white", linewidth=1.2)
    for bar, val in zip(bars, returns):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + (0.3 if val >= 0 else -1.2),
                f"{val:+.1f}%", ha="center", va="bottom", fontsize=8.5, fontweight="bold",
                color="#1E293B")
    ax.axhline(0, color="#94A3B8", linewidth=0.8)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    _chart_style(ax, "Annualized Return by Holding", ylabel="Ann. Return (%)")
    fig.tight_layout()
    return _fig_to_buf(fig)


# ── Stock Deck ────────────────────────────────────────────────────────────────

def build_stock_pptx(ticker, df, period_label,
                     company_details=None, mc_sim_df=None, mc_summary=None,
                     news_list=None, summary_text=""):
    """Build a professional stock analysis PowerPoint. Returns BytesIO."""
    if not PPTX_AVAILABLE:
        raise RuntimeError("python-pptx is not installed.")

    prs        = _new_prs()
    cd         = company_details or {}
    latest     = df.iloc[-1]
    first      = df.iloc[0]
    period_ret = (latest["Close"] / first["Close"] - 1) * 100
    ret        = df["Daily_Return"].dropna()
    ann_ret    = ret.mean() * 252
    ann_std    = ret.std() * np.sqrt(252)
    downside   = ret[ret < 0].std() * np.sqrt(252)
    sharpe     = ann_ret / ann_std  if ann_std  else float("nan")
    sortino    = ann_ret / downside if downside else float("nan")
    max_dd     = df["Drawdown_60d"].min() * 100 if "Drawdown_60d" in df.columns else float("nan")

    def _fmt_pct(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "N/A"
        return f"{v:+.2f}%"

    def _fmt_ratio(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "N/A"
        return f"{v:.2f}"

    total_slides = 10
    page = [0]

    def _next_page():
        page[0] += 1
        return page[0]

    # ── Slide 1: Cover ────────────────────────────────────────────────────────
    sl = _blank_slide(prs)
    _rect(sl, 0, 0, 13.33, 7.5, fill_rgb=C_NAVY)
    _rect(sl, 0, 5.8, 13.33, 1.7, fill_rgb=RGBColor(0x0F, 0x17, 0x2A))
    _rect(sl, 0.35, 2.2, 0.08, 2.6, fill_rgb=C_ACCENT)  # left accent bar

    company_name = cd.get("Name", ticker)
    _text_box(sl, "STOCKWIZARD", 0.6, 1.0, 12, 0.5,
              font_size=13, bold=True, color=C_ACCENT, align=PP_ALIGN.LEFT)
    _text_box(sl, ticker, 0.6, 1.5, 12, 1.0,
              font_size=60, bold=True, color=C_WHITE, align=PP_ALIGN.LEFT)
    _text_box(sl, company_name, 0.6, 2.55, 12, 0.6,
              font_size=20, bold=False, color=RGBColor(0xB0, 0xC4, 0xDE), align=PP_ALIGN.LEFT)
    _text_box(sl, "Professional Stock Analysis Report", 0.6, 3.2, 12, 0.45,
              font_size=14, italic=True, color=C_ACCENT, align=PP_ALIGN.LEFT)

    sector   = cd.get("Sector", "")
    exchange = cd.get("Exchange", "")
    meta_str = "  ·  ".join(filter(None, [sector, exchange, f"Period: {period_label}"]))
    _text_box(sl, meta_str, 0.6, 3.75, 12, 0.35,
              font_size=10, color=RGBColor(0x94, 0xA3, 0xB8), align=PP_ALIGN.LEFT)

    _text_box(sl, f"Generated {datetime.now().strftime('%B %d, %Y')}  ·  Data: Polygon.io",
              0.6, 6.1, 10, 0.3, font_size=9, color=C_GREY_TEXT)
    _text_box(sl, "For informational purposes only. Not financial advice.",
              0.6, 6.45, 12, 0.3, font_size=8, italic=True,
              color=RGBColor(0x64, 0x74, 0x8B))
    _text_box(sl, "1", 12.9, 7.15, 0.4, 0.25, font_size=8,
              color=C_GREY_TEXT, align=PP_ALIGN.RIGHT)

    # ── Slide 2: Company Snapshot ─────────────────────────────────────────────
    sl = _blank_slide(prs)
    _slide_header(sl, "Company Snapshot", ticker)
    _slide_footer(sl, _next_page(), total_slides)

    desc = cd.get("Description", "No description available.")
    if len(desc) > 420:
        desc = desc[:420].rsplit(" ", 1)[0] + "…"

    _rect(sl, 0.3, 1.3, 8.5, 3.2, fill_rgb=C_LIGHT)
    _text_box(sl, "About", 0.5, 1.35, 8, 0.3, font_size=10, bold=True, color=C_NAVY)
    _text_box(sl, desc, 0.5, 1.7, 8.1, 2.7, font_size=9.5, color=C_DARK_TEXT)

    mc_raw  = cd.get("Market Cap")
    mc_str  = f"${mc_raw/1e9:.1f}B" if isinstance(mc_raw, (int, float)) and mc_raw > 1e9 \
              else (f"${mc_raw/1e6:.0f}M" if isinstance(mc_raw, (int, float)) else "N/A")
    emp_raw = cd.get("Employees")
    emp_str = f"{int(emp_raw):,}" if isinstance(emp_raw, (int, float)) else "N/A"

    info_pairs = [
        ("Ticker",    ticker),
        ("Company",   cd.get("Name",     "N/A")),
        ("Sector",    cd.get("Sector",   "N/A")),
        ("Exchange",  cd.get("Exchange", "N/A")),
        ("Market Cap", mc_str),
        ("Employees", emp_str),
        ("Country",   cd.get("Country",  "N/A")),
        ("Website",   cd.get("Website",  "N/A")),
    ]
    _kv_block(sl, info_pairs, 9.15, 1.3, 3.85, col_w=1.6, row_h=0.49)

    _rect(sl, 0.3, 4.65, 12.7, 2.2, fill_rgb=C_LIGHT)
    _text_box(sl, "Analysis Summary", 0.5, 4.7, 12, 0.3,
              font_size=10, bold=True, color=C_NAVY)
    summary = summary_text or f"{ticker} professional stock analysis generated by StockWizard."
    if len(summary) > 500:
        summary = summary[:500].rsplit(" ", 1)[0] + "…"
    _text_box(sl, summary, 0.5, 5.05, 12.3, 1.7, font_size=9.5, italic=True, color=C_DARK_TEXT)

    # ── Slide 3: Key Metrics ──────────────────────────────────────────────────
    sl = _blank_slide(prs)
    _slide_header(sl, "Key Performance Metrics", f"{ticker}  ·  {period_label}")
    _slide_footer(sl, _next_page(), total_slides)

    rsi_val = latest.get("RSI14")
    try:
        rsi_val = float(rsi_val) if rsi_val is not None and not (isinstance(rsi_val, float) and math.isnan(rsi_val)) else None
    except Exception:
        rsi_val = None

    rsi_str = f"{rsi_val:.1f}" if rsi_val is not None else "N/A"
    rsi_pos = (rsi_val < 70) if rsi_val is not None else None

    col1 = [
        ("Current Price",       f"${latest['Close']:,.2f}"),
        ("Period Return",       _fmt_pct(period_ret),    period_ret >= 0),
        ("52-Week High",        f"${latest.get('52W_High', 0):,.2f}" if latest.get('52W_High') else "N/A"),
        ("52-Week Low",         f"${latest.get('52W_Low', 0):,.2f}"  if latest.get('52W_Low')  else "N/A"),
        ("% From 52W High",     _fmt_pct((latest.get("Pct_From_52W_High") or 0) * 100),
                                (latest.get("Pct_From_52W_High") or -1) > -0.05),
    ]
    col2 = [
        ("Sharpe Ratio",        _fmt_ratio(sharpe),  sharpe > 1 if not math.isnan(sharpe) else None),
        ("Sortino Ratio",       _fmt_ratio(sortino), sortino > 1 if not math.isnan(sortino) else None),
        ("Ann. Return",         _fmt_pct(ann_ret * 100), ann_ret >= 0),
        ("Ann. Volatility",     _fmt_pct(ann_std * 100)),
        ("Max Drawdown (60d)",  _fmt_pct(max_dd),    max_dd > -20),
    ]
    col3 = [
        ("20-Day MA",   f"${latest.get('MA20', 0):,.2f}"  if latest.get('MA20')  else "N/A"),
        ("50-Day MA",   f"${latest.get('MA50', 0):,.2f}"  if latest.get('MA50')  else "N/A"),
        ("200-Day MA",  f"${latest.get('MA200', 0):,.2f}" if latest.get('MA200') else "N/A"),
        ("RSI (14)",    rsi_str, rsi_pos),
        ("BB %B",       f"{latest.get('BB_Pct', 0):.2f}"  if latest.get('BB_Pct') is not None else "N/A"),
    ]

    _text_box(sl, "Price & Returns",   0.3,  1.25, 4.1, 0.28, font_size=10, bold=True, color=C_NAVY)
    _text_box(sl, "Risk Metrics",      4.65, 1.25, 4.1, 0.28, font_size=10, bold=True, color=C_NAVY)
    _text_box(sl, "Technical Levels",  9.0,  1.25, 4.1, 0.28, font_size=10, bold=True, color=C_NAVY)

    _kv_block(sl, col1, 0.3,  1.55, 4.2, col_w=1.9, row_h=0.52)
    _kv_block(sl, col2, 4.65, 1.55, 4.2, col_w=1.9, row_h=0.52)
    _kv_block(sl, col3, 9.0,  1.55, 4.2, col_w=1.9, row_h=0.52)

    # ── Slide 4: Price & Moving Averages ──────────────────────────────────────
    sl = _blank_slide(prs)
    _slide_header(sl, "Price & Moving Averages", f"{ticker}  ·  {period_label}")
    _slide_footer(sl, _next_page(), total_slides)

    buf = _price_ma_chart(df, ticker, w=12, h=5.0)
    _add_image(sl, buf, 0.6, 1.25, 12.0, 5.0)

    # ── Slide 5: Volume ───────────────────────────────────────────────────────
    sl = _blank_slide(prs)
    _slide_header(sl, "Volume Analysis", f"{ticker}  ·  {period_label}")
    _slide_footer(sl, _next_page(), total_slides)

    buf_vol = _volume_chart(df, ticker, w=12, h=3.2)
    _add_image(sl, buf_vol, 0.6, 1.25, 12.0, 3.2)

    buf_dd = _drawdown_chart(df, ticker, w=12, h=2.5)
    if buf_dd:
        _add_image(sl, buf_dd, 0.6, 4.55, 12.0, 2.5)

    # ── Slide 6: Bollinger Bands ──────────────────────────────────────────────
    sl = _blank_slide(prs)
    _slide_header(sl, "Bollinger Bands  ·  RSI", f"{ticker}  ·  {period_label}")
    _slide_footer(sl, _next_page(), total_slides)

    buf_bb = _bollinger_chart(df, ticker, w=12, h=3.5)
    if buf_bb:
        _add_image(sl, buf_bb, 0.6, 1.25, 12.0, 3.5)

    buf_rsi = _rsi_chart(df, ticker, w=12, h=2.4)
    if buf_rsi:
        _add_image(sl, buf_rsi, 0.6, 4.8, 12.0, 2.4)

    # ── Slide 7: Cumulative Return vs Benchmarks ──────────────────────────────
    sl = _blank_slide(prs)
    _slide_header(sl, "Cumulative Return vs Benchmarks", f"{ticker}  ·  {period_label}")
    _slide_footer(sl, _next_page(), total_slides)

    buf_cum = _cumulative_chart(df, ticker, w=12, h=5.0)
    if buf_cum:
        _add_image(sl, buf_cum, 0.6, 1.25, 12.0, 5.0)

    # ── Slide 8: Monte Carlo Forecast ─────────────────────────────────────────
    sl = _blank_slide(prs)
    _slide_header(sl, "Monte Carlo Forecast", f"{ticker}  ·  1,000 Simulations")
    _slide_footer(sl, _next_page(), total_slides)

    if mc_sim_df is not None and mc_summary:
        buf_mc = _monte_carlo_chart(mc_sim_df, mc_summary, ticker, w=8.5, h=5.0)
        if buf_mc:
            _add_image(sl, buf_mc, 0.6, 1.25, 8.5, 5.0)

        mc_pairs = [
            ("Last Price",      mc_summary.get("Last Price", "N/A")),
            ("Median (P50)",    mc_summary.get("Median (P50)", "N/A")),
            ("Bear (P5)",       mc_summary.get("Bear Case (P5)", "N/A")),
            ("Bull (P75)",      mc_summary.get("Bull Case (P75)", "N/A")),
            ("Best (P95)",      mc_summary.get("Best Case (P95)", "N/A")),
            ("Prob. of Gain",   mc_summary.get("Prob. of Gain", "N/A")),
            ("Ann. Volatility", mc_summary.get("Ann. Volatility", "N/A")),
        ]
        _text_box(sl, "Scenario Summary", 9.35, 1.25, 3.6, 0.3,
                  font_size=10, bold=True, color=C_NAVY)
        _kv_block(sl, mc_pairs, 9.35, 1.6, 3.6, col_w=1.4, row_h=0.52)
    else:
        _text_box(sl, "Monte Carlo simulation was not run for this analysis.\n\n"
                  "Enable it in the sidebar options to generate probabilistic forecasts.",
                  0.6, 2.5, 12, 1.5, font_size=13, color=C_GREY_TEXT, align=PP_ALIGN.CENTER)

    # ── Slide 9: News Headlines ───────────────────────────────────────────────
    sl = _blank_slide(prs)
    _slide_header(sl, "Recent News Headlines", ticker)
    _slide_footer(sl, _next_page(), total_slides)

    if news_list:
        shown = news_list[:8]
        for i, item in enumerate(shown):
            row_t = 1.3 + i * 0.7
            bg = C_LIGHT if i % 2 == 0 else C_WHITE
            _rect(sl, 0.3, row_t, 12.7, 0.66, fill_rgb=bg)
            date_str = item.get("Date", "")[:10]
            pub      = item.get("Publisher", "")
            headline = item.get("Headline", "")
            if len(headline) > 110:
                headline = headline[:110] + "…"
            _text_box(sl, f"{date_str}  ·  {pub}", 0.45, row_t + 0.04, 3.5, 0.26,
                      font_size=8, color=C_GREY_TEXT)
            _text_box(sl, headline, 0.45, row_t + 0.3, 12.3, 0.32,
                      font_size=9.5, bold=True, color=C_DARK_TEXT)
    else:
        _text_box(sl, "No news headlines available for this ticker.",
                  0.6, 3.0, 12, 0.5, font_size=13, color=C_GREY_TEXT, align=PP_ALIGN.CENTER)

    # ── Slide 10: Disclaimer ──────────────────────────────────────────────────
    sl = _blank_slide(prs)
    _rect(sl, 0, 0, 13.33, 7.5, fill_rgb=RGBColor(0x0F, 0x17, 0x2A))
    _rect(sl, 0, 0, 13.33, 0.06, fill_rgb=C_ACCENT)
    _rect(sl, 0, 7.44, 13.33, 0.06, fill_rgb=C_ACCENT)

    _text_box(sl, "STOCKWIZARD", 0.6, 0.7, 12, 0.4,
              font_size=12, bold=True, color=C_ACCENT, align=PP_ALIGN.CENTER)
    _text_box(sl, "Important Disclaimer", 0.6, 1.2, 12, 0.55,
              font_size=26, bold=True, color=C_WHITE, align=PP_ALIGN.CENTER)

    disclaimer = (
        "This report has been generated by StockWizard for informational and educational purposes "
        "only. It does not constitute financial, investment, legal, or tax advice. The information "
        "presented is derived from third-party data sources (Polygon.io) and is believed to be "
        "accurate but is not guaranteed.\n\n"
        "Past performance is not indicative of future results. All investments involve risk, "
        "including the possible loss of principal. You should not make any investment decision "
        "based solely on the information in this report.\n\n"
        "StockWizard is not a registered investment adviser, broker-dealer, or financial planner. "
        "Always consult a qualified financial professional before making investment decisions."
    )
    _text_box(sl, disclaimer, 1.0, 2.0, 11.3, 4.2,
              font_size=10, color=RGBColor(0xB0, 0xC4, 0xDE), align=PP_ALIGN.LEFT)

    _text_box(sl, f"© {datetime.now().year} StockWizard  ·  stockwizard.app",
              0.6, 6.6, 12, 0.3, font_size=9, color=C_GREY_TEXT, align=PP_ALIGN.CENTER)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf


# ── Portfolio Deck ────────────────────────────────────────────────────────────

def build_portfolio_pptx(preferences, final_weights, stock_metrics,
                          backtest_df=None, backtest_metrics=None,
                          mc_sim_df=None, mc_summary=None, milestones=None,
                          corr_matrix=None, diversification_score=None,
                          ticker_info=None):
    """Build a professional portfolio analysis PowerPoint. Returns BytesIO."""
    if not PPTX_AVAILABLE:
        raise RuntimeError("python-pptx is not installed.")

    prs   = _new_prs()
    prefs = preferences or {}
    ti    = ticker_info or {}
    bm    = backtest_metrics or {}
    tickers = list(final_weights.keys()) if final_weights else []

    total_slides = 9
    page = [0]

    def _next_page():
        page[0] += 1
        return page[0]

    inv_amount  = prefs.get("investment_amount", 10000)
    risk_label  = prefs.get("risk_label", "Moderate")
    horizon_yrs = prefs.get("horizon_years", 5)

    # ── Slide 1: Cover ────────────────────────────────────────────────────────
    sl = _blank_slide(prs)
    _rect(sl, 0, 0, 13.33, 7.5, fill_rgb=C_NAVY)
    _rect(sl, 0, 5.8, 13.33, 1.7, fill_rgb=RGBColor(0x0F, 0x17, 0x2A))
    _rect(sl, 0.35, 2.1, 0.08, 2.8, fill_rgb=C_ACCENT)

    _text_box(sl, "STOCKWIZARD", 0.6, 0.95, 12, 0.5,
              font_size=13, bold=True, color=C_ACCENT)
    _text_box(sl, "Portfolio Analysis", 0.6, 1.5, 12, 0.85,
              font_size=48, bold=True, color=C_WHITE)
    _text_box(sl, "Professional Report", 0.6, 2.4, 12, 0.45,
              font_size=16, italic=True, color=C_ACCENT)

    holdings_str = "  ·  ".join(tickers[:10])
    _text_box(sl, holdings_str, 0.6, 3.0, 12, 0.35,
              font_size=10, color=RGBColor(0xB0, 0xC4, 0xDE))

    meta_parts = [
        f"Risk Profile: {risk_label}",
        f"Horizon: {horizon_yrs}yr",
        f"Amount: ${inv_amount:,.0f}",
    ]
    _text_box(sl, "  ·  ".join(meta_parts), 0.6, 3.45, 12, 0.3,
              font_size=10, color=RGBColor(0x94, 0xA3, 0xB8))

    _text_box(sl, f"Generated {datetime.now().strftime('%B %d, %Y')}  ·  Data: Polygon.io",
              0.6, 6.1, 10, 0.3, font_size=9, color=C_GREY_TEXT)
    _text_box(sl, "For informational purposes only. Not financial advice.",
              0.6, 6.45, 12, 0.3, font_size=8, italic=True, color=C_GREY_TEXT)

    # ── Slide 2: Portfolio Allocation ─────────────────────────────────────────
    sl = _blank_slide(prs)
    _slide_header(sl, "Portfolio Allocation", f"{len(tickers)} Holdings  ·  {risk_label} Risk")
    _slide_footer(sl, _next_page(), total_slides)

    if final_weights:
        buf_pie = _alloc_pie_chart(final_weights, ti, w=6.5, h=5.0)
        _add_image(sl, buf_pie, 0.3, 1.25, 6.5, 5.0)

    alloc_pairs = [(tk, f"{wt*100:.1f}%") for tk, wt in
                   sorted(final_weights.items(), key=lambda x: -x[1])]
    _text_box(sl, "Weights", 7.2, 1.25, 5.8, 0.28, font_size=10, bold=True, color=C_NAVY)
    _kv_block(sl, alloc_pairs, 7.2, 1.6, 5.8, col_w=2.5,
              row_h=min(0.52, 5.0 / max(len(alloc_pairs), 1)))

    # ── Slide 3: Holdings Breakdown ───────────────────────────────────────────
    sl = _blank_slide(prs)
    _slide_header(sl, "Holdings Breakdown", "Individual Performance Metrics")
    _slide_footer(sl, _next_page(), total_slides)

    buf_bar = _holdings_bar_chart(stock_metrics, w=12, h=3.8)
    if buf_bar:
        _add_image(sl, buf_bar, 0.6, 1.25, 12.0, 3.8)

    if stock_metrics:
        metrics_pairs = []
        for tk in tickers[:8]:
            m = stock_metrics.get(tk, {})
            ann_r = m.get("ann_return", 0) * 100
            sharpe_v = m.get("sharpe", float("nan"))
            sharpe_s = f"{sharpe_v:.2f}" if not (isinstance(sharpe_v, float) and math.isnan(sharpe_v)) else "N/A"
            metrics_pairs.append((tk, f"Ret: {ann_r:+.1f}%  ·  Sharpe: {sharpe_s}",
                                   ann_r >= 0))
        _text_box(sl, "Per-Holding Summary", 0.3, 5.2, 12, 0.28,
                  font_size=10, bold=True, color=C_NAVY)
        _kv_block(sl, metrics_pairs, 0.3, 5.5, 12.7, col_w=1.0, row_h=0.3)

    # ── Slide 4: Portfolio Metrics ────────────────────────────────────────────
    sl = _blank_slide(prs)
    _slide_header(sl, "Portfolio Performance Metrics", "Backtest Results")
    _slide_footer(sl, _next_page(), total_slides)

    def _bv(key, default="N/A"):
        v = bm.get(key, default)
        return v if v is not None else default

    col1 = [
        ("Total Return",         _bv("Total Return")),
        ("Ann. Return",          _bv("Ann. Return")),
        ("vs S&P 500",           _bv("vs S&P 500")),
        ("Final Portfolio Value", f"${float(str(_bv('Final Value')).replace('$','').replace(',','').replace('%','')):,.0f}"
                                  if _bv("Final Value") != "N/A" else "N/A"),
    ]
    col2 = [
        ("Sharpe Ratio",         _bv("Sharpe Ratio")),
        ("Max Drawdown",         _bv("Max Drawdown")),
        ("Best Month",           _bv("Best Month")),
        ("% Months Positive",    _bv("% Months Positive")),
    ]
    col3 = [
        ("Risk Profile",         risk_label),
        ("Horizon",              f"{horizon_yrs} years"),
        ("Investment Amount",    f"${inv_amount:,.0f}"),
        ("Diversification",      f"{diversification_score:.1f}/10" if diversification_score else "N/A"),
    ]

    _text_box(sl, "Return Metrics",    0.3,  1.25, 4.2, 0.28, font_size=10, bold=True, color=C_NAVY)
    _text_box(sl, "Risk Metrics",      4.75, 1.25, 4.2, 0.28, font_size=10, bold=True, color=C_NAVY)
    _text_box(sl, "Portfolio Profile", 9.2,  1.25, 4.2, 0.28, font_size=10, bold=True, color=C_NAVY)
    _kv_block(sl, col1, 0.3,  1.55, 4.3, col_w=2.0, row_h=0.55)
    _kv_block(sl, col2, 4.75, 1.55, 4.3, col_w=2.0, row_h=0.55)
    _kv_block(sl, col3, 9.2,  1.55, 4.1, col_w=2.0, row_h=0.55)

    # ── Slide 5: Backtest Performance Chart ───────────────────────────────────
    sl = _blank_slide(prs)
    _slide_header(sl, "Backtest — Cumulative Return", "Portfolio vs Benchmark")
    _slide_footer(sl, _next_page(), total_slides)

    buf_bt = _portfolio_performance_chart(backtest_df, w=12, h=5.0)
    if buf_bt:
        _add_image(sl, buf_bt, 0.6, 1.25, 12.0, 5.0)
    else:
        _text_box(sl, "Backtest chart unavailable.", 0.6, 3.5, 12, 0.5,
                  font_size=13, color=C_GREY_TEXT, align=PP_ALIGN.CENTER)

    # ── Slide 6: Monte Carlo ──────────────────────────────────────────────────
    sl = _blank_slide(prs)
    _slide_header(sl, "Monte Carlo Portfolio Forecast", "Probabilistic Return Scenarios")
    _slide_footer(sl, _next_page(), total_slides)

    if mc_sim_df is not None:
        buf_mc = _monte_carlo_chart(mc_sim_df, mc_summary or {}, "Portfolio", w=8.5, h=5.0)
        if buf_mc:
            _add_image(sl, buf_mc, 0.6, 1.25, 8.5, 5.0)

        if milestones:
            ms_pairs = [(k, v) for k, v in milestones.items()]
            _text_box(sl, "Value Milestones", 9.35, 1.25, 3.6, 0.3,
                      font_size=10, bold=True, color=C_NAVY)
            _kv_block(sl, ms_pairs, 9.35, 1.6, 3.6, col_w=1.5, row_h=0.52)
    else:
        _text_box(sl, "Monte Carlo simulation not available.",
                  0.6, 3.5, 12, 0.5, font_size=13, color=C_GREY_TEXT, align=PP_ALIGN.CENTER)

    # ── Slide 7: Correlation Matrix ───────────────────────────────────────────
    sl = _blank_slide(prs)
    _slide_header(sl, "Correlation Matrix", "Daily Return Correlation Between Holdings")
    _slide_footer(sl, _next_page(), total_slides)

    if corr_matrix is not None and not corr_matrix.empty:
        labels = list(corr_matrix.columns)
        n      = len(labels)
        fig, ax = plt.subplots(figsize=(10, 5))
        data    = corr_matrix.values.astype(float)
        im      = ax.imshow(data, cmap="RdYlGn", vmin=-1, vmax=1, aspect="auto")
        ax.set_xticks(range(n)); ax.set_xticklabels(labels, rotation=40, ha="right", fontsize=8)
        ax.set_yticks(range(n)); ax.set_yticklabels(labels, fontsize=8)
        for i in range(n):
            for j in range(n):
                val = data[i, j]
                ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                        fontsize=7.5, color="black" if abs(val) < 0.7 else "white",
                        fontweight="bold")
        plt.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
        ax.set_title("Correlation Matrix (Daily Returns)", fontsize=11,
                     fontweight="bold", color="#1F4E79", pad=8)
        fig.patch.set_facecolor("#FFFFFF")
        fig.tight_layout()
        buf_corr = _fig_to_buf(fig)
        _add_image(sl, buf_corr, 1.0, 1.3, 11.3, 5.5)
    else:
        _text_box(sl, "Correlation data unavailable.", 0.6, 3.5, 12, 0.5,
                  font_size=13, color=C_GREY_TEXT, align=PP_ALIGN.CENTER)

    # ── Slide 8: Rebalancing & Notes ──────────────────────────────────────────
    sl = _blank_slide(prs)
    _slide_header(sl, "Rebalancing Guide", "Suggested Target Weights & Notes")
    _slide_footer(sl, _next_page(), total_slides)

    _rect(sl, 0.3, 1.3, 12.7, 0.38, fill_rgb=C_NAVY)
    _text_box(sl, "Target Allocation",    0.5,  1.35, 3.0, 0.3, font_size=9, bold=True, color=C_WHITE)
    _text_box(sl, "Ticker",               3.6,  1.35, 1.5, 0.3, font_size=9, bold=True, color=C_WHITE)
    _text_box(sl, "Weight",               5.2,  1.35, 1.5, 0.3, font_size=9, bold=True, color=C_WHITE)
    _text_box(sl, "$ Amount",             6.8,  1.35, 2.0, 0.3, font_size=9, bold=True, color=C_WHITE)
    _text_box(sl, "Ann. Return",          8.9,  1.35, 1.8, 0.3, font_size=9, bold=True, color=C_WHITE)
    _text_box(sl, "Sharpe",              10.8,  1.35, 1.5, 0.3, font_size=9, bold=True, color=C_WHITE)

    for i, (tk, wt) in enumerate(sorted(final_weights.items(), key=lambda x: -x[1])):
        row_t  = 1.72 + i * 0.43
        bg     = C_LIGHT if i % 2 == 0 else C_WHITE
        _rect(sl, 0.3, row_t, 12.7, 0.41, fill_rgb=bg)
        m      = stock_metrics.get(tk, {})
        name   = ti.get(tk, {}).get("name", "")
        if len(name) > 28:
            name = name[:28] + "…"
        ann_r  = m.get("ann_return", 0) * 100
        sh_v   = m.get("sharpe", float("nan"))
        sh_s   = f"{sh_v:.2f}" if not (isinstance(sh_v, float) and math.isnan(sh_v)) else "N/A"
        _text_box(sl, name,                0.45, row_t + 0.06, 3.1, 0.3, font_size=9, color=C_DARK_TEXT)
        _text_box(sl, tk,                  3.6,  row_t + 0.06, 1.5, 0.3, font_size=9, bold=True, color=C_NAVY)
        _text_box(sl, f"{wt*100:.1f}%",    5.2,  row_t + 0.06, 1.5, 0.3, font_size=9, color=C_DARK_TEXT)
        _text_box(sl, f"${wt*inv_amount:,.0f}", 6.8, row_t + 0.06, 2.0, 0.3, font_size=9, color=C_DARK_TEXT)
        clr = C_GREEN if ann_r >= 0 else C_RED
        _text_box(sl, f"{ann_r:+.1f}%",   8.9,  row_t + 0.06, 1.8, 0.3, font_size=9, bold=True, color=clr)
        _text_box(sl, sh_s,               10.8,  row_t + 0.06, 1.5, 0.3, font_size=9, color=C_DARK_TEXT)

    note = ("Target weights reflect the optimized portfolio based on your risk profile and historical data. "
            "Rebalance when individual positions drift more than 5% from target weights. "
            "Past performance does not guarantee future results.")
    _rect(sl, 0.3, 6.55, 12.7, 0.6, fill_rgb=RGBColor(0xFF, 0xFB, 0xEB))
    _text_box(sl, f"⚠  {note}", 0.5, 6.6, 12.3, 0.5, font_size=8.5,
              italic=True, color=RGBColor(0x92, 0x40, 0x0E))

    # ── Slide 9: Disclaimer ───────────────────────────────────────────────────
    sl = _blank_slide(prs)
    _rect(sl, 0, 0, 13.33, 7.5, fill_rgb=RGBColor(0x0F, 0x17, 0x2A))
    _rect(sl, 0, 0,    13.33, 0.06, fill_rgb=C_ACCENT)
    _rect(sl, 0, 7.44, 13.33, 0.06, fill_rgb=C_ACCENT)

    _text_box(sl, "STOCKWIZARD", 0.6, 0.7, 12, 0.4,
              font_size=12, bold=True, color=C_ACCENT, align=PP_ALIGN.CENTER)
    _text_box(sl, "Important Disclaimer", 0.6, 1.2, 12, 0.55,
              font_size=26, bold=True, color=C_WHITE, align=PP_ALIGN.CENTER)

    disclaimer = (
        "This portfolio analysis has been generated by StockWizard for informational and "
        "educational purposes only. It does not constitute financial, investment, legal, or "
        "tax advice.\n\n"
        "Optimized portfolio weights are based on historical data and mathematical models. "
        "They are not guarantees of future performance. All investments involve risk, "
        "including the possible loss of principal.\n\n"
        "The rebalancing suggestions in this report are informational targets based on "
        "your stated preferences — not personalized investment advice. Always consult a "
        "qualified financial professional before making investment decisions.\n\n"
        "StockWizard is not a registered investment adviser or broker-dealer."
    )
    _text_box(sl, disclaimer, 1.0, 2.0, 11.3, 4.5,
              font_size=10, color=RGBColor(0xB0, 0xC4, 0xDE))

    _text_box(sl, f"© {datetime.now().year} StockWizard  ·  stockwizard.app",
              0.6, 6.6, 12, 0.3, font_size=9, color=C_GREY_TEXT, align=PP_ALIGN.CENTER)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf
