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


def _border():
    t = Side(style="thin")
    return Border(left=t, right=t, top=t, bottom=t)


def _hdr_cell(cell, bg=DARK_BLUE, fg=WHITE):
    cell.font      = Font(bold=True, color=fg, name="Arial", size=10)
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
def _build_cover(wb, ticker, period, sheetnames):
    ws = wb.create_sheet("Cover", 0)
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 44
    ws.column_dimensions["C"].width = 28

    ws.merge_cells("B2:C3")
    c = ws["B2"]
    c.value     = f"{ticker}  —  Stock Analysis Report"
    c.font      = Font(size=22, bold=True, color=MID_BLUE, name="Arial")
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[2].height = 30

    ws.merge_cells("B4:C4")
    c = ws["B4"]
    c.value     = f"Period: {period}   |   Generated: {datetime.now().strftime('%B %d, %Y %H:%M')}   |   Data: Polygon.io"
    c.font      = Font(size=9, italic=True, color="888888", name="Arial")
    c.alignment = Alignment(horizontal="left")

    ws["B6"] = "TABLE OF CONTENTS"
    ws["B6"].font = Font(bold=True, size=12, color=DARK_BLUE, name="Arial")

    for i, name in enumerate(sheetnames, 7):
        cell = ws.cell(row=i, column=2, value=name.replace("_", " "))
        cell.font      = Font(name="Arial", size=10, color=MID_BLUE, underline="single")
        cell.hyperlink = f"#{name}!A1"
        ws.row_dimensions[i].height = 16

    ws["B2"].fill = PatternFill("solid", fgColor="F0F7FF")


# ── Dashboard ─────────────────────────────────────────────────────────────────
def _build_dashboard(wb, ticker, df, company_details, mc_summary,
                     resistance_levels, support_levels, summary_text):
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
    sharpe   = ann_ret / ann_std  if ann_std  else np.nan
    sortino  = ann_ret / downside if downside else np.nan

    try:
        rsi_val = float(latest.get("RSI14", np.nan))
    except Exception:
        rsi_val = np.nan

    ws.merge_cells("A1:D1")
    ws["A1"] = f"{ticker} — Professional Stock Analysis"
    ws["A1"].font      = Font(size=18, bold=True, color=DARK_BLUE, name="Arial")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 34

    ws.merge_cells("A2:D2")
    ws["A2"] = f"Generated: {datetime.now().strftime('%B %d, %Y %H:%M')}  |  Data source: Polygon.io"
    ws["A2"].font      = Font(italic=True, color="888888", name="Arial", size=9)
    ws["A2"].alignment = Alignment(horizontal="center")

    def sec_hdr(row, label, col_end="D"):
        ws.merge_cells(f"A{row}:{col_end}{row}")
        c = ws.cell(row=row, column=1, value=label)
        c.font      = Font(bold=True, color=WHITE, name="Arial", size=11)
        c.fill      = PatternFill("solid", fgColor=DARK_BLUE)
        c.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[row].height = 20

    def kv(row, label, value, fmt=None, rag=None):
        cl = ws.cell(row=row, column=1, value=label)
        cv = ws.cell(row=row, column=2, value=value)
        cl.font      = Font(name="Arial", size=10)
        cv.font      = Font(name="Arial", size=10, bold=True)
        cv.alignment = Alignment(horizontal="right")
        cl.border    = cv.border = _border()
        if fmt and isinstance(value, (int, float)):
            cv.number_format = fmt
        if rag and isinstance(value, (int, float)):
            direction, thresh = rag
            colour = (GREEN_OK if value > thresh else RED_BAD) if direction == "gt" \
                     else (RED_BAD if value < thresh else GREEN_OK)
            cv.fill = PatternFill("solid", fgColor=colour)
            cv.font = Font(name="Arial", size=10, bold=True, color=WHITE)
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
        row_cursor += 1

    if company_details:
        sec_hdr(row_cursor, "Company Information")
        row_cursor += 1
        for k, v in company_details.items():
            if k != "Description":
                kv(row_cursor, k, str(v))
                row_cursor += 1
        row_cursor += 1

    sec_hdr(row_cursor, "Automated Analysis Summary", col_end="D")
    row_cursor += 1
    ws.merge_cells(f"A{row_cursor}:D{row_cursor + 4}")
    sc = ws.cell(row=row_cursor, column=1, value=summary_text)
    sc.font      = Font(name="Arial", size=10, italic=True)
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
        ws.cell(row=4, column=5, value="SPARKLINES").font = Font(bold=True, color=WHITE, name="Arial")
        ws.cell(row=4, column=5).fill = PatternFill("solid", fgColor=MID_BLUE)
        for i, (label, vals, col) in enumerate(spark_data):
            row = 5 + i * 3
            ws.cell(row=row, column=5, value=label).font = Font(name="Arial", size=9, bold=True)
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
    ws_a["A1"].font      = Font(size=14, bold=True, color=DARK_BLUE, name="Arial")
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
            c.font   = Font(name="Arial", size=10)
            c.border = _border()
            c.fill   = PatternFill("solid", fgColor=bg)
            if ci == 1: c.number_format = "0"
            elif ci in (2, 3, 4):
                c.number_format = "0.00%"
                if isinstance(val, float):
                    good = val > 0 if ci == 2 else val > -0.15
                    c.fill = PatternFill("solid", fgColor=GREEN_OK if good else "FFAAAA")
                    c.font = Font(name="Arial", size=10, bold=True)

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
        nc.font      = Font(name="Arial", size=9, italic=True, color="1F4E79")
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
            c.font = Font(name="Arial", size=10)
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
            cell.font   = Font(name="Arial", size=10, bold=(ri == 2))
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
    ws_corr.cell(row=1, column=1).font = Font(bold=True, size=12, color=DARK_BLUE, name="Arial")
    ws_corr.merge_cells(f"A1:{get_column_letter(len(labels)+1)}1")
    for ci, lbl in enumerate(labels, 2):
        _hdr_cell(ws_corr.cell(row=2, column=ci, value=lbl), bg=MID_BLUE)
    for ri, lbl in enumerate(labels, 3):
        _hdr_cell(ws_corr.cell(row=ri, column=1, value=lbl), bg=MID_BLUE)
        for ci, col_lbl in enumerate(labels, 2):
            val  = corr_matrix.loc[lbl, col_lbl]
            cell = ws_corr.cell(row=ri, column=ci, value=round(float(val), 4))
            cell.number_format = "0.0000"
            cell.font          = Font(name="Arial", size=10)
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
    ws_mc["A1"].font = Font(bold=True, size=13, color=DARK_BLUE, name="Arial")
    ws_mc["A2"] = "Field"
    ws_mc["B2"] = "Value"
    for cell in ws_mc[2]:
        _hdr_cell(cell, bg=MID_BLUE)
    for i, (k, v) in enumerate(mc_summary.items(), 3):
        ws_mc.cell(row=i, column=1, value=k).font      = Font(name="Arial", size=10)
        ws_mc.cell(row=i, column=2, value=str(v)).font = Font(name="Arial", size=10, bold=True)
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
def _build_charts_sheet(wb, ticker, ws_p, export_df, ws_s, ws_mc_data):
    ws_ch = wb.create_sheet("Charts")
    ws_ch.sheet_view.showGridLines = False

    if not MPL_AVAILABLE:
        ws_ch["A1"] = "Charts unavailable — matplotlib not installed."
        return

    dates = pd.to_datetime(export_df["Date"]) if "Date" in export_df.columns else None

    # ── Chart 1: Price + Moving Averages ──────────────────────────────────────
    fig, ax = plt.subplots(figsize=(13, 5))
    ax.plot(dates, export_df["Close"],  color="#1F4E79", linewidth=1.8, label="Close",  zorder=3)
    for ma, col, lw in [("MA20","#E8A838",1.2), ("MA50","#2ECC71",1.2), ("MA200","#E74C3C",1.2)]:
        if ma in export_df.columns:
            ax.plot(dates, export_df[ma], color=col, linewidth=lw, linestyle="--", label=ma, zorder=2)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.2f}"))
    _chart_style(ax, f"{ticker} — Price & Moving Averages")
    _add_mpl_image(ws_ch, _mpl_chart(fig), "A1")

    # ── Chart 2: Volume (relative — uses Vol_MA20 proxy) ─────────────────────
    if "Vol_MA20" in export_df.columns:
        fig, ax = plt.subplots(figsize=(13, 3.5))
        colors = ["#2ECC71" if r >= 0 else "#E74C3C"
                  for r in export_df.get("Daily_Return", pd.Series([0]*len(export_df))).fillna(0)]
        ax.bar(dates, export_df["Vol_MA20"], color=colors, width=1.5, alpha=0.75)
        ax.plot(dates, export_df["Vol_MA20"], color="#1F4E79", linewidth=1.2,
                linestyle="--", label="20-Day Avg Volume")
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x/1e6:.1f}M"))
        _chart_style(ax, f"{ticker} — Volume (20-Day Moving Average)", ylabel="Volume")
        _add_mpl_image(ws_ch, _mpl_chart(fig), "A22", height_px=280)

    # ── Chart 3: Bollinger Bands ──────────────────────────────────────────────
    if "BB_Upper" in export_df.columns:
        fig, ax = plt.subplots(figsize=(13, 5))
        ax.plot(dates, export_df["Close"],     color="#1F4E79", linewidth=1.8, label="Close",    zorder=3)
        ax.plot(dates, export_df["BB_Upper"],  color="#E74C3C", linewidth=1.0, linestyle="--", label="BB Upper")
        ax.plot(dates, export_df["BB_Middle"], color="#888888", linewidth=1.0, linestyle="--", label="BB Mid")
        ax.plot(dates, export_df["BB_Lower"],  color="#2ECC71", linewidth=1.0, linestyle="--", label="BB Lower")
        ax.fill_between(dates, export_df["BB_Upper"], export_df["BB_Lower"], alpha=0.07, color="#2E75B6")
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.2f}"))
        _chart_style(ax, f"{ticker} — Bollinger Bands (20-day, 2σ)")
        _add_mpl_image(ws_ch, _mpl_chart(fig), "A35")

    # ── Chart 4: RSI ──────────────────────────────────────────────────────────
    if "RSI14" in export_df.columns:
        fig, ax = plt.subplots(figsize=(13, 3))
        ax.plot(dates, export_df["RSI14"], color="#6C3483", linewidth=1.4, label="RSI (14)")
        ax.axhline(70, color="#E74C3C", linewidth=0.8, linestyle="--", alpha=0.7, label="Overbought (70)")
        ax.axhline(30, color="#2ECC71", linewidth=0.8, linestyle="--", alpha=0.7, label="Oversold (30)")
        ax.fill_between(dates, export_df["RSI14"], 70,
                        where=export_df["RSI14"] >= 70, alpha=0.15, color="#E74C3C")
        ax.fill_between(dates, export_df["RSI14"], 30,
                        where=export_df["RSI14"] <= 30, alpha=0.15, color="#2ECC71")
        ax.set_ylim(0, 100)
        _chart_style(ax, f"{ticker} — RSI (14)", ylabel="RSI")
        _add_mpl_image(ws_ch, _mpl_chart(fig), "A56", height_px=240)

    # ── Chart 5: Cumulative Return vs Benchmarks ──────────────────────────────
    cum_cols = [c for c in export_df.columns if c.endswith("_Cumulative")]
    if "Cumulative_Index" in export_df.columns:
        fig, ax = plt.subplots(figsize=(13, 5))
        ax.plot(dates, export_df["Cumulative_Index"], color="#1F4E79", linewidth=2.0,
                label=ticker, zorder=3)
        bench_colors = ["#E74C3C", "#2ECC71", "#F39C12", "#8E44AD"]
        for i, col in enumerate(cum_cols):
            label = col.replace("_Cumulative", "")
            ax.plot(dates, export_df[col], color=bench_colors[i % len(bench_colors)],
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
def build_excel(ticker, df, period,
                company_details=None, sector_df=None,
                mc_sim_df=None, mc_summary=None,
                news_list=None, peer_df=None,
                corr_matrix=None,
                resistance_levels=None, support_levels=None,
                summary_text="", bar_size="day"):

    wb = Workbook()
    wb.remove(wb.active)

    ws_dash = _build_dashboard(wb, ticker, df, company_details, mc_summary,
                                resistance_levels, support_levels, summary_text)
    ws_p, export_df = _build_price_sheet(wb, df, bar_size=bar_size)
    _build_annual_summary(wb, df)
    _build_news_sheet(wb, news_list)
    _build_peer_sheet(wb, peer_df)
    ws_s       = _build_sector_sheet(wb, ticker, df, sector_df)
    _build_correlation_sheet(wb, corr_matrix)
    ws_mc_data = _build_monte_carlo_sheet(wb, mc_sim_df, mc_summary)
    _build_charts_sheet(wb, ticker, ws_p, export_df, ws_s, ws_mc_data)

    # Cover last so it knows all sheet names
    sheets_so_far = [s for s in wb.sheetnames]
    _build_cover(wb, ticker, period, sheets_so_far)

    desired = ["Cover","Dashboard","Annual_Summary","Price_Indicators","News_Headlines",
               "Peer_Comparison","Sector_Comparison","Correlation_Matrix",
               "Monte_Carlo","Charts"]
    existing = wb.sheetnames
    ordered  = [s for s in desired if s in existing]
    extras   = [s for s in existing if s not in ordered]
    for i, name in enumerate(ordered + extras):
        wb.move_sheet(name, offset=wb.sheetnames.index(name) - i)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
