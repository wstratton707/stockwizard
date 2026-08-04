"""portfolio_pptx.py — the client-presentation deck for a tracked portfolio.

Ten slides in the shape an asset manager would present: cover, executive
summary, allocation, holdings, sector exposure, risk, performance and
attribution, strengths and opportunities, written commentary, and conclusions.

Shares its analytics with the Excel workbook (`_derive_portfolio_stats`) and its
prose with the Word report (`portfolio_narrative`), and borrows the slide chrome
from `pptx_builder`, so all three documents describe the portfolio identically.

    from portfolio_pptx import build_portfolio_review_pptx
    buf = build_portfolio_review_pptx(name, tracked, profiles)
"""

from __future__ import annotations

import io
import os
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pptx_builder import (PPTX_AVAILABLE, _new_prs, _blank_slide, _rect,
                          _text_box, _add_image, _slide_header, _slide_footer,
                          _kv_block, _fig_to_buf, _ASSET_LOGO,
                          C_NAVY, C_BLUE, C_ACCENT, C_GREEN, C_RED, C_WHITE,
                          C_LIGHT, C_DARK_TEXT, C_GREY_TEXT)
from portfolio_excel import _derive_portfolio_stats
from portfolio_narrative import build_narrative

try:
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN
except ImportError:                                    # pragma: no cover
    pass

TOTAL_SLIDES = 10
MPL_BLUE, MPL_LIGHT = "#3F6C9C", "#C3DDF5"
MPL_GREEN, MPL_RED, MPL_GREY = "#15803d", "#b91c1c", "#94a3b8"
PALETTE = ["#3F6C9C", "#6C8FB8", "#9BB4D2", "#C3DDF5", "#D98324",
           "#E0B341", "#7F9A6B", "#8E7CC3", "#B0BEC5", "#5D8AA8"]


def _money(v, dp=0):
    """Sign outside the symbol: -$2,074, never $-2,074. The table's red-for-
    negative rule keys off a leading '-', so this fixes the colour too."""
    if v is None:
        return "—"
    return f"-${abs(v):,.{dp}f}" if v < 0 else f"${v:,.{dp}f}"


def _pc(v, dp=1, sign=False):
    if v is None:
        return "—"
    return f"{v:+.{dp}f}%" if sign else f"{v:.{dp}f}%"


def _w(v):
    return "—" if v is None else f"{v:.1%}"


def _bullets(slide, items, l, t, w, h, size=12, gap=0.44, color=C_DARK_TEXT,
             max_items=6):
    """Bulleted list as individual boxes — keeps control of spacing per line."""
    for i, text in enumerate(items[:max_items]):
        _text_box(slide, "•  " + text, l, t + i * gap, w, gap, font_size=size,
                  color=color)


def _stat_tile(slide, l, t, w, h, label, value, accent=C_NAVY):
    _rect(slide, l, t, w, h, fill_rgb=C_LIGHT)
    _rect(slide, l, t, 0.06, h, fill_rgb=accent)
    _text_box(slide, label.upper(), l + 0.18, t + 0.10, w - 0.25, 0.26,
              font_size=9, color=C_GREY_TEXT)
    _text_box(slide, value, l + 0.18, t + 0.36, w - 0.25, 0.46,
              font_size=20, bold=True, color=accent)


# ── charts ────────────────────────────────────────────────────────────────────
def _donut(stats, key, title):
    items = stats.get(key) or []
    total = stats.get("total_value") or 1
    if not items:
        return None
    top, rest = items[:7], items[7:]
    labels = [k for k, _ in top]
    vals = [v for _, v in top]
    if rest:
        labels.append(f"Other ({len(rest)})")
        vals.append(sum(v for _, v in rest))
    try:
        fig, ax = plt.subplots(figsize=(6.2, 4.0))
        wedges, *_ = ax.pie(vals, startangle=90, counterclock=False,
                            colors=PALETTE[:len(vals)],
                            wedgeprops=dict(width=0.44, edgecolor="white",
                                            linewidth=1.6))
        ax.legend(wedges, [f"{l} — {v/total:.1%}" for l, v in zip(labels, vals)],
                  loc="center left", bbox_to_anchor=(0.98, 0.5), fontsize=10,
                  frameon=False)
        ax.set_title(title, fontsize=13, color="#1F4E79", loc="left",
                     fontweight="bold", pad=12)
        ax.axis("equal")
        return _fig_to_buf(fig, dpi=160)
    except Exception:
        plt.close("all")
        return None


def _weights_barh(stats, n=10):
    rows = (stats.get("rows") or [])[:n][::-1]
    if not rows:
        return None
    try:
        fig, ax = plt.subplots(figsize=(6.4, 4.2))
        ax.barh([r["ticker"] for r in rows], [r["w"] * 100 for r in rows],
                color=MPL_BLUE, height=0.66)
        for i, r in enumerate(rows):
            ax.text(r["w"] * 100 + 0.4, i, f"{r['w']:.1%}", va="center",
                    fontsize=9.5, color="#475569")
        ax.set_xlabel("Portfolio weight (%)", fontsize=10, color="#475569")
        ax.grid(axis="x", alpha=0.25); ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.tick_params(labelsize=10, colors="#475569")
        ax.set_title("Largest positions", fontsize=13, color="#1F4E79",
                     loc="left", fontweight="bold")
        return _fig_to_buf(fig, dpi=160)
    except Exception:
        plt.close("all")
        return None


def _sector_barh(stats):
    items = stats.get("sectors") or []
    total = stats.get("total_value") or 1
    if not items:
        return None
    items = items[:9][::-1]
    try:
        fig, ax = plt.subplots(figsize=(6.3, 4.2))
        ax.barh([s for s, _ in items], [v / total * 100 for _, v in items],
                color=MPL_BLUE, height=0.66)
        for i, (_, v) in enumerate(items):
            ax.text(v / total * 100 + 0.4, i, f"{v/total:.1%}", va="center",
                    fontsize=9.5, color="#475569")
        ax.axvline(30, color=MPL_RED, ls="--", lw=1.0, alpha=0.75)
        ax.text(30.4, -0.45, "30% concentration line", fontsize=8, color=MPL_RED)
        ax.set_xlabel("Weight (%)", fontsize=10, color="#475569")
        ax.grid(axis="x", alpha=0.25); ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.tick_params(labelsize=10, colors="#475569")
        ax.set_title("Sector exposure", fontsize=13, color="#1F4E79",
                     loc="left", fontweight="bold")
        return _fig_to_buf(fig, dpi=160)
    except Exception:
        plt.close("all")
        return None


def _value_curve(tracked):
    curve = tracked.get("curve")
    if curve is None or len(curve) < 2:
        return None
    try:
        fig, ax = plt.subplots(figsize=(7.6, 3.9))
        ax.plot(curve.index, curve["Portfolio"], color=MPL_BLUE, lw=2.1,
                label="Portfolio", zorder=3)
        if "SP500" in curve.columns and not curve["SP500"].isna().all():
            ax.plot(curve.index, curve["SP500"], color=MPL_GREY, lw=1.5, ls="--",
                    label="S&P 500 (same contributions)")
        if "Contrib" in curve.columns:
            ax.plot(curve.index, curve["Contrib"], color="#D98324", lw=1.3,
                    ls=":", label="Capital invested")
        ax.legend(fontsize=9.5, frameon=False, loc="upper left")
        ax.grid(alpha=0.22)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.tick_params(labelsize=9, colors="#64748b")
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
        ax.set_title("Value since inception", fontsize=13, color="#1F4E79",
                     loc="left", fontweight="bold")
        return _fig_to_buf(fig, dpi=160)
    except Exception:
        plt.close("all")
        return None


def _attrib_barh(narr):
    cs = (narr.get("winners") or [])[:5] + (narr.get("losers") or [])[:5]
    if not cs:
        return None
    cs = sorted(cs, key=lambda c: c["gain_usd"])
    try:
        fig, ax = plt.subplots(figsize=(6.2, 3.9))
        ax.barh([c["ticker"] for c in cs], [c["gain_usd"] for c in cs],
                color=[MPL_GREEN if c["gain_usd"] >= 0 else MPL_RED for c in cs],
                height=0.64)
        ax.axvline(0, color="#94a3b8", lw=0.9)
        ax.set_xlabel("Gain / loss vs cost ($)", fontsize=10, color="#475569")
        ax.grid(axis="x", alpha=0.25); ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.tick_params(labelsize=10, colors="#475569")
        ax.set_title("Contribution by holding", fontsize=13, color="#1F4E79",
                     loc="left", fontweight="bold")
        return _fig_to_buf(fig, dpi=160)
    except Exception:
        plt.close("all")
        return None


def _table_rows(slide, headers, rows, l, t, w, col_w, row_h=0.36, size=10.5):
    """Lightweight table — zebra rows, no borders. Cleaner than pptx tables."""
    _rect(slide, l, t, w, row_h, fill_rgb=C_NAVY)
    x = l
    for h, cw in zip(headers, col_w):
        _text_box(slide, h, x + 0.08, t + 0.05, cw, row_h - 0.06,
                  font_size=size - 1, bold=True, color=C_WHITE)
        x += cw
    for i, row in enumerate(rows):
        ry = t + row_h + i * row_h
        if i % 2 == 0:
            _rect(slide, l, ry, w, row_h, fill_rgb=C_LIGHT)
        x = l
        for j, (val, cw) in enumerate(zip(row, col_w)):
            col = C_DARK_TEXT
            sval = str(val)
            if sval.startswith("+"):
                col = C_GREEN
            elif sval.startswith("-") and j > 0:
                col = C_RED
            _text_box(slide, sval, x + 0.08, ry + 0.04, cw, row_h - 0.06,
                      font_size=size, bold=(j == 0), color=col)
            x += cw


# ── deck ──────────────────────────────────────────────────────────────────────
def build_portfolio_review_pptx(portfolio_name, tracked, profiles):
    """Return a BytesIO .pptx client review deck, or None if python-pptx is absent."""
    if not PPTX_AVAILABLE:
        return None

    stats = _derive_portfolio_stats(tracked, profiles)
    narr = build_narrative(portfolio_name, tracked, stats)
    m = tracked.get("metrics", {}) or {}
    today = date.today().strftime("%B %d, %Y")
    inception = tracked.get("inception_date", "—")
    tot = stats["total_value"] or 1

    prs = _new_prs()
    page = [0]

    def _slide(title, subtitle=None, cover=False):
        # The cover is deliberately outside the count: numbering the content
        # slides 1..10 of 10 is what a reader expects, and counting the cover
        # made the last slide read "11 / 10".
        s = _blank_slide(prs)
        if cover:
            return s
        page[0] += 1
        _slide_header(s, title, subtitle)
        _slide_footer(s, page[0], TOTAL_SLIDES)
        return s

    # ── 1. Cover ──────────────────────────────────────────────────────────────
    s = _slide("", cover=True)
    _rect(s, 0, 0, 13.33, 7.5, fill_rgb=C_NAVY)
    _rect(s, 0, 4.02, 13.33, 0.05, fill_rgb=C_ACCENT)
    if os.path.exists(_ASSET_LOGO):
        try:
            s.shapes.add_picture(_ASSET_LOGO, Inches(0.85), Inches(0.75),
                                 height=Inches(0.62))
        except Exception:
            pass
    _text_box(s, "PORTFOLIO REVIEW", 0.9, 2.5, 11, 0.5, font_size=15,
              bold=True, color=C_ACCENT)
    _text_box(s, portfolio_name, 0.9, 3.0, 11.5, 1.0, font_size=42, bold=True,
              color=C_WHITE)
    _text_box(s, narr["objective"], 0.9, 4.35, 10.6, 0.9, font_size=14,
              color=C_GREY_TEXT)
    _text_box(s, f"Prepared {today}   ·   Tracked since {inception}",
              0.9, 6.4, 8, 0.4, font_size=11, italic=True, color=C_GREY_TEXT)
    _text_box(s, "QuantWizard", 11.1, 6.4, 1.6, 0.4, font_size=12, bold=True,
              color=C_ACCENT, align=PP_ALIGN.RIGHT)

    # ── 2. Executive summary ──────────────────────────────────────────────────
    s = _slide("Executive Summary", portfolio_name)
    _tr = m.get("Total Return")
    tiles = [
        ("Market value", _money(m.get("Final Value"))),
        ("Total return", _pc(_tr, sign=True)),
        ("Holdings", str(stats["n"])),
        ("Portfolio beta", "—" if stats["beta"] is None else f"{stats['beta']:.2f}"),
    ]
    for i, (lbl, val) in enumerate(tiles):
        acc = C_NAVY
        if lbl == "Total return" and _tr is not None:
            acc = C_GREEN if _tr >= 0 else C_RED
        _stat_tile(s, 0.45 + i * 3.18, 1.5, 3.0, 1.0, lbl, val, acc)
    _text_box(s, "Assessment", 0.45, 2.75, 6.0, 0.35, font_size=14, bold=True,
              color=C_NAVY)
    _text_box(s, narr["assessment"], 0.45, 3.15, 6.2, 3.4, font_size=12,
              color=C_DARK_TEXT)
    _kv_block(s, [
        ("Capital invested", _money(m.get("Total Contributed"))),
        ("Gain / loss", _money(m.get("Total Gain/Loss")),
         (m.get("Total Gain/Loss") or 0) >= 0),
        ("vs S&P 500", (f"{m['vs S&P 500']:+.1f} pts"
                        if isinstance(m.get("vs S&P 500"), (int, float)) else "—"),
         (m.get("vs S&P 500") or 0) >= 0 if isinstance(m.get("vs S&P 500"), (int, float)) else None),
        ("Volatility", _pc(stats.get("vol"))),
        ("Max drawdown", _pc(m.get("Max Drawdown"))),
        ("Sharpe ratio", m.get("Sharpe Ratio", "—")),
        ("Dividend yield", _w(stats.get("div_yield"))),
    ], 7.0, 2.75, 5.85, col_w=2.6, row_h=0.55)

    # ── 3. Asset allocation ───────────────────────────────────────────────────
    s = _slide("Asset Allocation", "Where the money actually sits")
    img = _donut(stats, "sectors", "By sector")
    if img:
        _add_image(s, img, 0.4, 1.45, 6.3, 4.1)
    _text_box(s, "Concentration", 7.1, 1.5, 5.7, 0.35, font_size=14, bold=True,
              color=C_NAVY)
    _kv_block(s, [
        ("Largest position", (f"{stats['largest']['ticker']} · {_w(stats['largest']['w'])}"
                              if stats.get("largest") else "—")),
        ("Top 5 holdings", _w(stats["top5"])),
        ("Top 10 holdings", _w(stats["top10"])),
        ("Herfindahl (HHI)", f"{stats['hhi']:.3f}"),
        ("Effective holdings",
         "—" if not stats.get("eff_n") else f"{stats['eff_n']:.1f}"),
        ("Diversification", f"{narr['diversification_score']}/10 · {narr['diversification_band']}"),
    ], 7.1, 1.95, 5.7, col_w=2.5, row_h=0.55)
    _text_box(s, narr["diversification_text"], 7.1, 5.4, 5.7, 1.5,
              font_size=11, color=C_GREY_TEXT)

    # ── 4. Holdings overview ──────────────────────────────────────────────────
    s = _slide("Holdings Overview", f"{stats['n']} positions · "
                                    f"{_money(stats['total_value'])}")
    img = _weights_barh(stats)
    if img:
        _add_image(s, img, 0.4, 1.45, 6.1, 4.0)
    top = stats["rows"][:9]
    _table_rows(s, ["Ticker", "Weight", "Value", "Return"],
                [[r["ticker"], _w(r["w"]), _money(r["value"]),
                  _pc(r.get("gain_pct"), sign=True)] for r in top],
                6.85, 1.5, 6.0, [1.7, 1.3, 1.6, 1.4], row_h=0.42)

    # ── 5. Sector exposure ────────────────────────────────────────────────────
    s = _slide("Sector Exposure", "Concentration versus the 30% line")
    img = _sector_barh(stats)
    if img:
        _add_image(s, img, 0.4, 1.45, 6.3, 4.2)
    _text_box(s, "Commentary", 7.1, 1.5, 5.7, 0.35, font_size=14, bold=True,
              color=C_NAVY)
    _text_box(s, narr["sector_commentary"], 7.1, 1.95, 5.75, 3.0,
              font_size=12, color=C_DARK_TEXT)
    _text_box(s, "Themes", 7.1, 4.55, 5.7, 0.35, font_size=14, bold=True,
              color=C_NAVY)
    _bullets(s, narr["themes"], 7.1, 4.95, 5.75, 1.9, size=10.5, gap=0.42,
             max_items=4)

    # ── 6. Risk ───────────────────────────────────────────────────────────────
    s = _slide("Risk Analysis", f"Measured over {stats['n_days']} trading days")
    risk_tiles = [
        ("Beta", "—" if stats["beta"] is None else f"{stats['beta']:.2f}"),
        ("Volatility", _pc(stats.get("vol"))),
        ("Max drawdown", _pc(m.get("Max Drawdown"))),
        ("Sharpe", str(m.get("Sharpe Ratio", "—"))),
    ]
    for i, (lbl, val) in enumerate(risk_tiles):
        _stat_tile(s, 0.45 + i * 3.18, 1.5, 3.0, 1.0, lbl, val)
    _text_box(s, "What this means", 0.45, 2.8, 12.4, 0.35, font_size=14,
              bold=True, color=C_NAVY)
    _text_box(s, narr["posture_text"], 0.45, 3.2, 12.4, 0.9, font_size=12,
              color=C_DARK_TEXT)
    _text_box(s, "Observations", 0.45, 4.15, 12.4, 0.35, font_size=14,
              bold=True, color=C_NAVY)
    _bullets(s, narr["concerns"], 0.45, 4.55, 12.4, 2.4, size=11.5, gap=0.45,
             max_items=5)

    # ── 7. Performance ────────────────────────────────────────────────────────
    s = _slide("Performance", "Against an S&P 500 position funded the same way")
    img = _value_curve(tracked)
    if img:
        _add_image(s, img, 0.4, 1.4, 7.5, 3.85)
    _kv_block(s, [
        ("Total return", _pc(m.get("Total Return"), sign=True),
         (m.get("Total Return") or 0) >= 0),
        ("S&P 500", _pc(m.get("S&P 500 Return"), sign=True)
         if isinstance(m.get("S&P 500 Return"), (int, float)) else "—"),
        ("Relative", (f"{m['vs S&P 500']:+.1f} pts"
                      if isinstance(m.get("vs S&P 500"), (int, float)) else "—"),
         (m.get("vs S&P 500") or 0) >= 0 if isinstance(m.get("vs S&P 500"), (int, float)) else None),
        ("Best month", _pc(m.get("Best Month"), sign=True)),
        ("Worst month", _pc(m.get("Worst Month"), sign=True)),
        ("Positive months", _pc(m.get("% Months Positive"))),
    ], 8.15, 1.5, 4.75, col_w=2.2, row_h=0.55)
    _text_box(s, narr["performance_lines"][0] if narr["performance_lines"] else "",
              0.45, 5.45, 12.4, 1.5, font_size=11.5, color=C_DARK_TEXT)

    # ── 8. Attribution ────────────────────────────────────────────────────────
    s = _slide("Attribution", "What moved the portfolio, in dollars")
    img = _attrib_barh(narr)
    if img:
        _add_image(s, img, 0.4, 1.45, 6.1, 3.9)
    rows = [[c["ticker"], _money(c["gain_usd"]), _pc(c.get("gain_pct"), sign=True),
             _w(c.get("weight"))] for c in (narr["winners"][:4] + narr["losers"][:4])]
    _table_rows(s, ["Ticker", "Gain / loss", "Return", "Weight"], rows,
                6.85, 1.5, 6.0, [1.6, 1.7, 1.4, 1.3], row_h=0.42)
    if len(narr["performance_lines"]) > 1:
        _text_box(s, narr["performance_lines"][1], 0.45, 5.6, 12.4, 1.3,
                  font_size=11.5, color=C_DARK_TEXT)

    # ── 9. Strengths & opportunities ──────────────────────────────────────────
    s = _slide("Strengths & Opportunities", "What is working, and what to watch")
    _rect(s, 0.45, 1.45, 6.1, 0.42, fill_rgb=C_GREEN)
    _text_box(s, "STRENGTHS", 0.6, 1.5, 5.8, 0.32, font_size=12, bold=True,
              color=C_WHITE)
    _bullets(s, narr["strengths"], 0.5, 2.0, 6.0, 4.5, size=11, gap=0.72,
             max_items=5)
    _rect(s, 6.85, 1.45, 6.0, 0.42, fill_rgb=C_RED)
    _text_box(s, "AREAS TO WATCH", 7.0, 1.5, 5.7, 0.32, font_size=12, bold=True,
              color=C_WHITE)
    _bullets(s, narr["concerns"], 6.9, 2.0, 5.9, 4.5, size=11, gap=0.72,
             max_items=5)

    # ── 10a. Commentary ───────────────────────────────────────────────────────
    s = _slide("Investment Commentary", "Positioning and market sensitivity")
    _text_box(s, f"Style — {narr['style_label']}", 0.45, 1.45, 6.0, 0.35,
              font_size=13, bold=True, color=C_NAVY)
    _text_box(s, narr["style_text"], 0.45, 1.85, 6.0, 1.8, font_size=11.5,
              color=C_DARK_TEXT)
    _text_box(s, f"Positioning — {narr['posture_label']}", 0.45, 3.7, 6.0, 0.35,
              font_size=13, bold=True, color=C_NAVY)
    _text_box(s, narr["posture_text"], 0.45, 4.1, 6.0, 2.0, font_size=11.5,
              color=C_DARK_TEXT)
    _text_box(s, "Should do well in", 6.85, 1.45, 6.0, 0.35, font_size=13,
              bold=True, color=C_GREEN)
    _bullets(s, [e[0].upper() + e[1:] for e in narr["environments_favourable"]],
             6.85, 1.85, 5.9, 2.0, size=11, gap=0.56, max_items=3)
    _text_box(s, "Likely to struggle in", 6.85, 3.9, 6.0, 0.35, font_size=13,
              bold=True, color=C_RED)
    _bullets(s, [e[0].upper() + e[1:] for e in narr["environments_adverse"]],
             6.85, 4.3, 5.9, 2.0, size=11, gap=0.56, max_items=3)

    # ── 10b. Conclusion ───────────────────────────────────────────────────────
    s = _slide("Considerations & Conclusion", "Points a reviewer would raise")
    _recs = narr["recommendations"][:5]
    _bullets(s, _recs, 0.5, 1.5, 12.3, 3.6, size=12, gap=0.62, max_items=5)
    # Follow the bullets rather than sitting at a fixed height — with three
    # recommendations a pinned box left a third of the slide empty.
    _sum_t = min(max(1.5 + len(_recs) * 0.62 + 0.35, 3.5), 5.35)
    _rect(s, 0.45, _sum_t, 12.4, 1.35, fill_rgb=C_LIGHT)
    _rect(s, 0.45, _sum_t, 0.07, 1.35, fill_rgb=C_NAVY)
    _text_box(s, "IN SUMMARY", 0.7, _sum_t + 0.10, 11.9, 0.3, font_size=10,
              bold=True, color=C_GREY_TEXT)
    _text_box(s, narr["assessment"], 0.7, _sum_t + 0.40, 11.9, 0.9,
              font_size=11.5, color=C_DARK_TEXT)
    _text_box(s, "These are observations derived from the portfolio's own metrics, "
                 "not recommendations to buy or sell. Not investment advice.",
              0.45, 6.85, 12.4, 0.3, font_size=9, italic=True, color=C_GREY_TEXT)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf
