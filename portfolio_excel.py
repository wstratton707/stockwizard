import io
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import LineChart, BarChart, Reference
from openpyxl.formatting.rule import ColorScaleRule, CellIsRule
from datetime import datetime

DARK_BLUE = "1F4E79"
MID_BLUE  = "2E75B6"
GREEN_OK  = "70AD47"
RED_BAD   = "FF0000"
AMBER     = "FFC000"
WHITE     = "FFFFFF"
GREY_ROW  = "F2F2F2"
LIGHT_BG  = "EBF5FB"


def _border():
    t = Side(style="thin")
    return Border(left=t, right=t, top=t, bottom=t)


def _hdr(cell, bg=DARK_BLUE, fg=WHITE, size=10):
    cell.font      = Font(bold=True, color=fg, name="Arial", size=size)
    cell.fill      = PatternFill("solid", fgColor=bg)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border    = _border()


def _kv(ws, row, label, value, fmt=None, rag=None, label_col=1, val_col=2):
    cl = ws.cell(row=row, column=label_col, value=label)
    cv = ws.cell(row=row, column=val_col,   value=value)
    cl.font      = Font(name="Arial", size=10)
    cv.font      = Font(name="Arial", size=10, bold=True)
    cv.alignment = Alignment(horizontal="right")
    cl.border    = cv.border = _border()
    if fmt and isinstance(value, (int, float)):
        cv.number_format = fmt
    if rag and isinstance(value, (int, float)):
        d, thresh = rag
        colour = (GREEN_OK if value > thresh else RED_BAD) if d == "gt" \
                 else (RED_BAD if value < thresh else GREEN_OK)
        cv.fill = PatternFill("solid", fgColor=colour)
        cv.font = Font(name="Arial", size=10, bold=True, color=WHITE)
    bg = GREY_ROW if row % 2 == 0 else WHITE
    for c in [label_col, val_col]:
        cell = ws.cell(row=row, column=c)
        if not cell.fill or cell.fill.fgColor.rgb in ("00000000","FFFFFFFF",WHITE):
            cell.fill = PatternFill("solid", fgColor=bg)


def _sec_hdr(ws, row, label, col_start=1, col_end=4):
    ws.merge_cells(f"{get_column_letter(col_start)}{row}:{get_column_letter(col_end)}{row}")
    c = ws.cell(row=row, column=col_start, value=label)
    c.font      = Font(bold=True, color=WHITE, name="Arial", size=11)
    c.fill      = PatternFill("solid", fgColor=DARK_BLUE)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[row].height = 20


def _auto_width(ws, max_w=30):
    from openpyxl.cell.cell import Cell
    for col in ws.columns:
        real = [c for c in col if isinstance(c, Cell)]
        if not real:
            continue
        best = max((len(str(c.value or "")) for c in real), default=10)
        ws.column_dimensions[real[0].column_letter].width = min(best + 3, max_w)


def _style_header_row(ws, bg=DARK_BLUE):
    for cell in ws[1]:
        _hdr(cell, bg=bg)


# ── Cover ─────────────────────────────────────────────────────────────────────
def _build_cover(wb, preferences, final_weights, backtest_metrics, mc_summary, sheetnames):
    ws = wb.create_sheet("Cover", 0)
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 44
    ws.column_dimensions["C"].width = 28

    ws.merge_cells("B2:C3")
    c = ws["B2"]
    c.value     = "◈  StockWizard — Portfolio Analysis Report"
    c.font      = Font(size=20, bold=True, color=MID_BLUE, name="Arial")
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[2].height = 28

    ws.merge_cells("B4:C4")
    ws["B4"].value = f"Generated: {datetime.now().strftime('%B %d, %Y %H:%M')}  |  Data: Polygon.io"
    ws["B4"].font  = Font(size=9, italic=True, color="888888", name="Arial")

    ws["B6"] = "TABLE OF CONTENTS"
    ws["B6"].font = Font(bold=True, size=12, color=DARK_BLUE, name="Arial")
    for i, name in enumerate([s for s in sheetnames if s != "Cover"], 7):
        cell = ws.cell(row=i, column=2, value=name.replace("_", " "))
        cell.font      = Font(name="Arial", size=10, color=MID_BLUE, underline="single")
        cell.hyperlink = f"#{name}!A1"
        ws.row_dimensions[i].height = 16

    # Key stats block
    snap_row = 7 + len(sheetnames) + 2
    stats = [
        ("Risk Tolerance",      f"{preferences.get('risk_tolerance',5)}/10"),
        ("Investment Horizon",  preferences.get("horizon","5 years")),
        ("Starting Capital",    f"${preferences.get('starting_capital',10000):,.0f}"),
        ("Monthly Contribution",f"${preferences.get('monthly_contribution',500):,.0f}"),
        ("Holdings",            len(final_weights)),
        ("Final Portfolio Value", f"${backtest_metrics.get('Final Value',0):,.2f}"),
        ("Total Return",        f"{backtest_metrics.get('Total Return',0):.2f}%"),
        ("Sharpe Ratio",        backtest_metrics.get("Sharpe Ratio","N/A")),
    ]
    ws.cell(row=snap_row, column=2, value="Portfolio Snapshot").font = Font(
        bold=True, size=11, color=DARK_BLUE, name="Arial")
    for j, (k, v) in enumerate(stats, snap_row+1):
        ws.cell(row=j, column=2, value=k).font  = Font(name="Arial", size=9, color="555555")
        ws.cell(row=j, column=3, value=str(v)).font = Font(name="Arial", size=9, bold=True)

    ws["B2"].fill = PatternFill("solid", fgColor="EBF5FB")


# ── Dashboard ─────────────────────────────────────────────────────────────────
def _build_dashboard(wb, preferences, final_weights, stock_metrics,
                     backtest_metrics, mc_summary, diversification_score):
    ws = wb.create_sheet("Dashboard")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 32
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 18
    ws.column_dimensions["D"].width = 10

    ws.merge_cells("A1:D1")
    ws["A1"] = "◈ StockWizard — Portfolio Dashboard"
    ws["A1"].font      = Font(size=18, bold=True, color=DARK_BLUE, name="Arial")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 34

    ws.merge_cells("A2:D2")
    ws["A2"] = f"Generated: {datetime.now().strftime('%B %d, %Y %H:%M')}  |  Powered by StockWizard"
    ws["A2"].font      = Font(italic=True, color="888888", name="Arial", size=9)
    ws["A2"].alignment = Alignment(horizontal="center")

    row = 4
    _sec_hdr(ws, row, "Portfolio Preferences")
    row += 1
    prefs_display = [
        ("Risk Tolerance",       f"{preferences.get('risk_tolerance',5)}/10"),
        ("Investment Horizon",   preferences.get("horizon","5 years")),
        ("Starting Capital",     preferences.get("starting_capital",10000)),
        ("Monthly Contribution", preferences.get("monthly_contribution",500)),
        ("Target Goal",          preferences.get("target_value","Not set")),
    ]
    for label, val in prefs_display:
        _kv(ws, row, label, val,
            fmt='_($* #,##0.00_)' if isinstance(val,(int,float)) else None)
        row += 1

    row += 1
    _sec_hdr(ws, row, "Portfolio Performance")
    row += 1
    perf_items = [
        ("Final Portfolio Value",  backtest_metrics.get("Final Value",0),         '_($* #,##0.00_)', ("gt",0)),
        ("Total Gain / Loss",      backtest_metrics.get("Total Gain/Loss",0),      '_($* #,##0.00_)', ("gt",0)),
        ("Total Return %",         backtest_metrics.get("Total Return",0),         '0.00%',           ("gt",0)),
        ("Annualised Return %",    backtest_metrics.get("Ann. Return",0),          '0.00%',           ("gt",0)),
        ("vs S&P 500",             backtest_metrics.get("vs S&P 500","N/A"),       None,              None),
        ("Sharpe Ratio",           backtest_metrics.get("Sharpe Ratio",0),         '0.000',           ("gt",1)),
        ("Sortino Ratio",          backtest_metrics.get("Sortino Ratio",0),        '0.000',           ("gt",1)),
        ("Max Drawdown",           backtest_metrics.get("Max Drawdown",0),         '0.00%',           ("gt",-20)),
        ("Ann. Volatility",        backtest_metrics.get("Ann. Volatility",0),      '0.00%',           None),
        ("Best Month",             backtest_metrics.get("Best Month",0),           '0.00%',           ("gt",0)),
        ("Worst Month",            backtest_metrics.get("Worst Month",0),          '0.00%',           ("gt",-5)),
        ("% Months Positive",      backtest_metrics.get("% Months Positive",0),   '0.0%',            ("gt",50)),
        ("Diversification Score",  diversification_score,                          '0.0',             ("gt",6)),
    ]
    for label, val, fmt, rag in perf_items:
        if isinstance(val, (int, float)):
            # Convert percentage display values
            display_val = val/100 if fmt == '0.00%' or fmt == '0.0%' else val
            _kv(ws, row, label, display_val, fmt=fmt, rag=rag)
        else:
            _kv(ws, row, label, str(val))
        row += 1

    row += 1
    _sec_hdr(ws, row, "Monte Carlo Forecast Summary")
    row += 1
    if mc_summary:
        for k, v in mc_summary.items():
            _kv(ws, row, k, str(v))
            row += 1

    return ws


# ── Holdings sheet ────────────────────────────────────────────────────────────
def _build_holdings_sheet(wb, final_weights, stock_metrics, ticker_info):
    ws = wb.create_sheet("Holdings_Breakdown")
    headers = ["Ticker","Company","Weight %","Ann. Return %","Ann. Volatility %",
               "Sharpe Ratio","Sortino Ratio","Max Drawdown %","Total Return %"]
    ws.append(headers)
    _style_header_row(ws, bg=MID_BLUE)

    sorted_holdings = sorted(final_weights.items(), key=lambda x: x[1], reverse=True)
    for ri, (ticker, weight) in enumerate(sorted_holdings, 2):
        m    = stock_metrics.get(ticker, {})
        info = ticker_info.get(ticker, {})
        row  = [
            ticker,
            info.get("name", ticker)[:30],
            round(weight * 100, 2),
            m.get("ann_return", "N/A"),
            m.get("ann_vol",    "N/A"),
            m.get("sharpe",     "N/A"),
            m.get("sortino",    "N/A"),
            m.get("max_drawdown","N/A"),
            m.get("total_return","N/A"),
        ]
        ws.append(row)
        for ci, cell in enumerate(ws[ri], 1):
            cell.font   = Font(name="Arial", size=10)
            cell.border = _border()
            if ri % 2 == 0:
                cell.fill = PatternFill("solid", fgColor=GREY_ROW)
            if ci == 3:  # Weight %
                cell.number_format = "0.00%"
                cell.value = weight

    # Conditional formatting on Sharpe
    sharpe_col = "D"
    ws.conditional_formatting.add(
        f"{sharpe_col}2:{sharpe_col}{ws.max_row}",
        ColorScaleRule(start_type="min", start_color="FF9999",
                       mid_type="num",  mid_value=1, mid_color="FFFFFF",
                       end_type="max",  end_color="99FF99"))

    _auto_width(ws)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}1"


# ── Backtest sheet ────────────────────────────────────────────────────────────
def _build_backtest_sheet(wb, backtest_df, backtest_metrics):
    ws = wb.create_sheet("Backtest_Results")

    # Metrics summary at top
    ws["A1"] = "Backtest Performance Metrics"
    ws["A1"].font = Font(bold=True, size=13, color=DARK_BLUE, name="Arial")
    ws.merge_cells("A1:D1")
    ws["A2"], ws["B2"] = "Metric", "Value"
    for cell in ws[2]:
        _hdr(cell, bg=MID_BLUE)

    row = 3
    for k, v in backtest_metrics.items():
        ws.cell(row=row, column=1, value=k).font  = Font(name="Arial", size=10)
        ws.cell(row=row, column=2, value=str(v)).font = Font(name="Arial", size=10, bold=True)
        row += 1

    # Daily values table
    data_start = row + 2
    ws.cell(row=data_start, column=1, value="Date").font = Font(bold=True, name="Arial", size=10)
    ws.cell(row=data_start, column=2, value="Portfolio ($)").font = Font(bold=True, name="Arial", size=10)
    ws.cell(row=data_start, column=3, value="Contributions ($)").font = Font(bold=True, name="Arial", size=10)
    ws.cell(row=data_start, column=4, value="S&P 500 ($)").font = Font(bold=True, name="Arial", size=10)
    for c in range(1, 5):
        _hdr(ws.cell(row=data_start, column=c), bg=MID_BLUE)

    # Sample every 5 days to keep file size manageable
    sample = backtest_df.iloc[::5]
    for ri, (date, row_data) in enumerate(sample.iterrows(), data_start+1):
        ws.cell(row=ri, column=1, value=date.strftime("%Y-%m-%d")).number_format = "yyyy-mm-dd"
        ws.cell(row=ri, column=2, value=round(row_data["Portfolio"],2)).number_format = '_($* #,##0.00_)'
        ws.cell(row=ri, column=3, value=round(row_data["Contrib"],2)).number_format  = '_($* #,##0.00_)'
        sp = row_data.get("SP500", None)
        if sp and not pd.isna(sp):
            ws.cell(row=ri, column=4, value=round(sp,2)).number_format = '_($* #,##0.00_)'
        if ri % 2 == 0:
            for c in range(1,5):
                ws.cell(row=ri, column=c).fill = PatternFill("solid", fgColor=GREY_ROW)

    # Chart
    chart = LineChart()
    chart.title          = "Portfolio vs S&P 500 vs Contributions"
    chart.y_axis.title   = "Value ($)"
    chart.height, chart.width, chart.style = 16, 32, 2
    max_r = data_start + len(sample)
    for ci, label in [(2,"Portfolio"),(3,"Contributions"),(4,"S&P 500")]:
        chart.add_data(Reference(ws, min_col=ci, min_row=data_start, max_row=max_r),
                       titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=data_start+1, max_row=max_r))
    ws.add_chart(chart, "F2")

    _auto_width(ws)
    ws.freeze_panes = f"A{data_start+1}"
    return ws, data_start


# ── Monthly heatmap sheet ─────────────────────────────────────────────────────
def _build_heatmap_sheet(wb, heatmap_df):
    if heatmap_df is None or heatmap_df.empty:
        return
    ws = wb.create_sheet("Monthly_Returns_Heatmap")
    ws["A1"] = "Monthly Returns Heatmap (%)"
    ws["A1"].font = Font(bold=True, size=13, color=DARK_BLUE, name="Arial")
    ws.merge_cells("A1:N1")

    months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec","Full Yr"]
    ws.cell(row=2, column=1, value="Year")
    for ci, m in enumerate(months[:12], 2):
        c = ws.cell(row=2, column=ci, value=m)
        _hdr(c, bg=MID_BLUE)
    _hdr(ws.cell(row=2, column=1), bg=MID_BLUE)

    for ri, (year, row_data) in enumerate(heatmap_df.iterrows(), 3):
        ws.cell(row=ri, column=1, value=year).font = Font(bold=True, name="Arial", size=10)
        annual = 1.0
        for ci, month in enumerate(range(1,13), 2):
            val = row_data.get(month, None)
            if val is not None and not pd.isna(val):
                cell = ws.cell(row=ri, column=ci, value=round(val,2))
                cell.number_format = '0.00"%"'
                cell.font          = Font(name="Arial", size=9)
                cell.alignment     = Alignment(horizontal="center")
                colour = "C6EFCE" if val >= 0 else "FFC7CE"
                cell.fill = PatternFill("solid", fgColor=colour)
                annual *= (1 + val/100)

        ann_ret = (annual - 1) * 100
        c = ws.cell(row=ri, column=14, value=round(ann_ret,2))
        c.number_format = '0.00"%"'
        c.font          = Font(name="Arial", size=9, bold=True)
        c.alignment     = Alignment(horizontal="center")
        c.fill          = PatternFill("solid", fgColor=("C6EFCE" if ann_ret >= 0 else "FFC7CE"))

    for ci in range(1,15):
        ws.column_dimensions[get_column_letter(ci)].width = 9
    ws.column_dimensions["A"].width = 7


# ── Monte Carlo sheet ─────────────────────────────────────────────────────────
def _build_mc_sheet(wb, mc_sim_df, mc_summary, milestones):
    if mc_sim_df is None:
        return
    ws_mc = wb.create_sheet("Monte_Carlo_Portfolio")
    ws_mc["A1"] = "Portfolio Monte Carlo Simulation"
    ws_mc["A1"].font = Font(bold=True, size=14, color=DARK_BLUE, name="Arial")
    ws_mc.merge_cells("A1:F1")

    # Summary
    ws_mc["A2"], ws_mc["B2"] = "Metric", "Value"
    for cell in ws_mc[2]:
        _hdr(cell, bg=MID_BLUE)
    for i, (k, v) in enumerate(mc_summary.items(), 3):
        ws_mc.cell(row=i, column=1, value=k).font      = Font(name="Arial", size=10)
        ws_mc.cell(row=i, column=2, value=str(v)).font = Font(name="Arial", size=10, bold=True)
    summary_end = 3 + len(mc_summary)

    # Milestone table
    ms_start = summary_end + 2
    ws_mc.cell(row=ms_start, column=1, value="Milestone Projections").font = Font(
        bold=True, size=12, color=DARK_BLUE, name="Arial")
    ws_mc.merge_cells(f"A{ms_start}:F{ms_start}")
    ms_hdr_row = ms_start + 1
    for ci, lbl in enumerate(["Horizon","Bear (P5)","Low (P25)","Median (P50)","Bull (P75)","Best (P95)"],1):
        _hdr(ws_mc.cell(row=ms_hdr_row, column=ci, value=lbl), bg=MID_BLUE)
    for ri, (horizon, pcts) in enumerate(milestones.items(), ms_hdr_row+1):
        ws_mc.cell(row=ri, column=1, value=horizon).font = Font(name="Arial", size=10, bold=True)
        for ci, key in enumerate(["P5","P25","P50","P75","P95"],2):
            c = ws_mc.cell(row=ri, column=ci, value=pcts[key])
            c.number_format = '_($* #,##0.00_)'
            c.font          = Font(name="Arial", size=10)
            c.border        = _border()
        if ri % 2 == 0:
            for ci in range(1,7):
                ws_mc.cell(row=ri,column=ci).fill = PatternFill("solid", fgColor=GREY_ROW)

    # Percentile paths
    pct_col = 10
    pct_start = ms_hdr_row + len(milestones) + 3
    pct_labels = ["P5 (Bear)","P25 (Low)","P50 (Median)","P75 (Bull)","P95 (Best)"]
    ws_mc.cell(row=pct_start, column=pct_col, value="Day")
    for j, lbl in enumerate(pct_labels):
        _hdr(ws_mc.cell(row=pct_start, column=pct_col+j+1, value=lbl), bg=MID_BLUE)
    for day_idx in range(0, len(mc_sim_df), 5):  # sample every 5 days
        row_prices = mc_sim_df.iloc[day_idx].values
        r = pct_start + 1 + day_idx // 5
        ws_mc.cell(row=r, column=pct_col, value=day_idx)
        for j, pct in enumerate([5,25,50,75,95]):
            ws_mc.cell(row=r, column=pct_col+j+1,
                       value=round(np.percentile(row_prices, pct),2)).number_format = '_($* #,##0.00_)'

    # Chart
    n_rows   = len(mc_sim_df) // 5 + 1
    chart_mc = LineChart()
    chart_mc.title = "Portfolio Monte Carlo — Percentile Forecast"
    chart_mc.y_axis.title = "Portfolio Value ($)"
    chart_mc.height, chart_mc.width, chart_mc.style = 16, 32, 10
    for j in range(5):
        chart_mc.add_data(Reference(ws_mc, min_col=pct_col+j+1,
                                    min_row=pct_start, max_row=pct_start+n_rows),
                          titles_from_data=True)
    chart_mc.set_categories(Reference(ws_mc, min_col=pct_col,
                                       min_row=pct_start+1, max_row=pct_start+n_rows))
    ws_mc.add_chart(chart_mc, "A" + str(pct_start + 2))
    ws_mc.freeze_panes = f"A{ms_hdr_row+1}"
    _auto_width(ws_mc)


# ── Correlation sheet ─────────────────────────────────────────────────────────
def _build_correlation_sheet(wb, corr_matrix):
    if corr_matrix is None or corr_matrix.empty:
        return
    ws   = wb.create_sheet("Correlation_Matrix")
    labs = list(corr_matrix.columns)
    ws.cell(row=1,column=1,value="Correlation Matrix (Daily Returns)").font = Font(
        bold=True, size=12, color=DARK_BLUE, name="Arial")
    ws.merge_cells(f"A1:{get_column_letter(len(labs)+1)}1")
    for ci, lbl in enumerate(labs,2):
        _hdr(ws.cell(row=2,column=ci,value=lbl), bg=MID_BLUE)
    for ri, lbl in enumerate(labs,3):
        _hdr(ws.cell(row=ri,column=1,value=lbl), bg=MID_BLUE)
        for ci, col_lbl in enumerate(labs,2):
            val  = corr_matrix.loc[lbl,col_lbl]
            cell = ws.cell(row=ri,column=ci,value=round(float(val),4))
            cell.number_format = "0.0000"
            cell.font          = Font(name="Arial", size=10)
            cell.border        = _border()
            cell.alignment     = Alignment(horizontal="center")
    data_range = f"B3:{get_column_letter(len(labs)+1)}{len(labs)+2}"
    ws.conditional_formatting.add(data_range, ColorScaleRule(
        start_type="num",start_value=-1,start_color="FF9999",
        mid_type="num",mid_value=0,mid_color="FFFFFF",
        end_type="num",end_value=1,end_color="99CCFF"))
    _auto_width(ws)


# ── Master builder ────────────────────────────────────────────────────────────
def build_portfolio_excel(preferences, final_weights, stock_metrics,
                           backtest_df, backtest_metrics, heatmap_df,
                           mc_sim_df, mc_summary, milestones,
                           corr_matrix, diversification_score,
                           ticker_info=None):
    if ticker_info is None:
        ticker_info = {}

    wb = Workbook()
    wb.remove(wb.active)

    _build_dashboard(wb, preferences, final_weights, stock_metrics,
                     backtest_metrics, mc_summary, diversification_score)
    _build_holdings_sheet(wb, final_weights, stock_metrics, ticker_info)
    _build_backtest_sheet(wb, backtest_df, backtest_metrics)
    _build_heatmap_sheet(wb, heatmap_df)
    _build_mc_sheet(wb, mc_sim_df, mc_summary, milestones)
    _build_correlation_sheet(wb, corr_matrix)

    sheets_so_far = list(wb.sheetnames)
    _build_cover(wb, preferences, final_weights, backtest_metrics, mc_summary, sheets_so_far)

    desired = ["Cover","Dashboard","Holdings_Breakdown","Backtest_Results",
               "Monthly_Returns_Heatmap","Monte_Carlo_Portfolio","Correlation_Matrix"]
    existing = wb.sheetnames
    ordered  = [s for s in desired if s in existing]
    extras   = [s for s in existing if s not in ordered]
    for i, name in enumerate(ordered + extras):
        wb.move_sheet(name, offset=wb.sheetnames.index(name) - i)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
