"""QuantWizard's Excel valuation report.

Reproduces the reference workbook ('GOOGL_5Y_Analysis UPDATED GOOD
TEMPLATE.xlsx', our Alphabet export rebuilt by Claude in Excel) for any
company. assets/valuation_template.xlsx - derived from the reference by
tools/build_valuation_template.py - carries every formula, style, dropdown and
conditional format; this module writes only the input cells, as the
reference's own build manual prescribes, plus the per-company text and notes.
Excel recalculates everything else when the file opens.

The numbers written here come from report_inputs.build(), which also runs the
same model in Python (valuation_model.py) for the site - so the file and the
site agree to the cent. Two things the template cannot carry are restored after
saving: the reference's two Summary charts and Excel 365's dynamic-array flag on
the LET / SEQUENCE / FILTER formulas.

Shapes the reference never had, handled here: fewer than five years of prices
(every fixed Data-row reference is rewritten), fewer than ten fiscal years or
four peers or six segments (rows left blank, formulas guarded), fiscal years
that are not calendar years, and companies with no DCF (banks, loss-makers
without a margin anchor) - the model cells read n/a and the verdict says why.
"""
import copy
import io
import math
import os
import re
import zipfile
from datetime import datetime

import numpy as np
import pandas as pd
from openpyxl import load_workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.comments import Comment
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor

import report_inputs as RI
import report_text as RT

_HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(_HERE, "assets", "valuation_template.xlsx")
CHART_DIR = os.path.join(_HERE, "assets", "valuation_charts")
AUTHOR = "QuantWizard"
TEMPLATE_LAST_ROW = 1256          # Data rows 2..1256 in the reference
SHEETS = ["Summary", "Scenarios", "DCF", "Multiples", "Peers", "Valuation_History", "Financials",
          "Segments", "Capital_Returns", "Risk", "Catalysts_News", "Methodology", "Sources",
          "Valuation_Log", "Data"]


# ── helpers ───────────────────────────────────────────────────────────────────
def _num(x):
    return isinstance(x, (int, float, np.integer, np.floating)) and not (
        isinstance(x, float) and not math.isfinite(x))


def _d(x, fmt="%d-%b-%Y"):
    try:
        return pd.Timestamp(x).strftime(fmt)
    except Exception:
        return str(x or "")


def _dt(x):
    try:
        return pd.Timestamp(x).to_pydatetime().replace(tzinfo=None)
    except Exception:
        return None


def _v(x):
    """A number for a cell, or None (blank) - never NaN."""
    if _num(x):
        return float(x)
    return None


def _note(cell, text):
    if not text:
        return
    c = Comment(text, AUTHOR)
    c.width, c.height = 340, 120
    cell.comment = c


def _put(ws, addr, value, note=None):
    c = ws[addr]
    if type(c).__name__ == "MergedCell":
        return c
    c.value = value
    if note:
        _note(c, note)
    return c


def _link(cell, url):
    if url and str(url).startswith("http"):
        cell.hyperlink = url


def _copy_row_style(ws, src_row, dst_row, cols):
    for col in cols:
        ws[f"{col}{dst_row}"]._style = copy.copy(ws[f"{col}{src_row}"]._style)


class _Filings:
    """Which filing a period's figures came from, for source notes."""

    def __init__(self, name, filings, cik=None):
        self.name = name
        self.list = filings or []
        self.by_period = {}
        for f in self.list:
            if f.get("form") in ("10-Q", "10-K") and f.get("period"):
                self.by_period.setdefault(f["period"], f)
        self.tenk = next((f for f in self.list if f.get("form") == "10-K"), None)
        self.tenq = next((f for f in self.list if f.get("form") == "10-Q"), None)
        self.index = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
                      f"&type=10-&dateb=&owner=include&count=40" if cik
                      else "https://www.sec.gov/edgar/search/")

    def for_period(self, end):
        if end is None:
            return None
        key = _d(end, "%Y-%m-%d")
        if key in self.by_period:
            return self.by_period[key]
        for p, f in self.by_period.items():
            try:
                if abs((pd.Timestamp(p) - pd.Timestamp(end)).days) <= 7:
                    return f
            except Exception:
                continue
        return None

    def note(self, what, end=None, form=None):
        f = self.for_period(end) if end is not None else None
        f = f or (self.tenq if form == "10-Q" else self.tenk if form == "10-K" else None)
        if f:
            return (f"Source: {self.name} Form {f.get('form')} for the period ended {_d(f.get('period'))} "
                    f"(filed {_d(f.get('filed'))}), {what}, SEC EDGAR XBRL, {f.get('url')}")
        return f"Source: SEC EDGAR XBRL company facts, {what}, {self.index}"


# ── Data ──────────────────────────────────────────────────────────────────────
def _data(wb, R, P, Q, price_source):
    ws = wb["Data"]
    win = R["win"]
    n = len(win)
    for i in range(n):
        r = i + 2
        row = win.iloc[i]
        ws.cell(row=r, column=1, value=_dt(row["Date"]))
        for j, col in enumerate(("Open", "High", "Low", "Close"), 2):
            ws.cell(row=r, column=j, value=_v(row.get(col)))
        vol = row.get("Volume")
        ws.cell(row=r, column=6, value=int(vol) if _num(vol) and vol > 0 else None)
        spy = row.get("SPY_Return") if "SPY_Return" in win.columns else None
        ws.cell(row=r, column=7, value=_v(spy) if i > 0 else None)
    last = n + 1
    for r in range(last + 1, TEMPLATE_LAST_ROW + 1):
        for col in range(1, 15):
            ws.cell(row=r, column=col).value = None
    _note(ws["A1"], f"INPUT BLOCK (A:G) — written by QuantWizard: the last {n:,} trading days, oldest "
                    f"first, rows 2–{last}. Close = split- and dividend-adjusted close; SPY_Return = SPY "
                    f"adjusted-close daily return. Source: {price_source or 'market data feed'} via "
                    f"QuantWizard. Columns H:N are formulas.")
    vals = {2: P["name"], 3: P["ticker"], 4: P["exchange"], 5: _dt(P["report_date"]),
            6: P["fy_end"], 7: P["peer_label"]}
    for k in range(8, 17):
        vals[k] = Q.get(f"Q{k}", "")
    for r, v in vals.items():
        ws.cell(row=r, column=17, value=v)
    return last


def _rewrite_rows(wb, last):
    """Point every fixed reference to Data rows 1256 / 1004 (the reference's
    five-year window and its 52-week start) at this report's last row."""
    if last == TEMPLATE_LAST_ROW:
        return
    w52 = max(2, last - 252)
    rng = re.compile(r"(Data!\$?[A-Z]{1,3}\$?)(\d+)(:\$?[A-Z]{1,3}\$?)(\d+)")
    one = re.compile(r"(Data!\$?[A-Z]{1,3}\$?)(\d+)(?![\d:])")

    def fix(n):
        n = int(n)
        return str(last if n == TEMPLATE_LAST_ROW else w52 if n == TEMPLATE_LAST_ROW - 252 else n)

    for ws in wb.worksheets:
        if ws.title == "Data":
            continue
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                text = getattr(v, "text", v)
                if not (isinstance(text, str) and text.startswith("=") and "Data!" in text):
                    continue
                new = rng.sub(lambda m: m.group(1) + fix(m.group(2)) + m.group(3) + fix(m.group(4)), text)
                new = one.sub(lambda m: m.group(1) + fix(m.group(2)), new)
                if new != text:
                    if hasattr(v, "text"):
                        v.text = new
                    else:
                        c.value = new


# ── Financials ────────────────────────────────────────────────────────────────
_FY_COLS = "BCDEFGHIJK"


def _financials(wb, R, F):
    ws = wb["Financials"]
    fb = R["fb"]
    if not fb.get("ok"):
        ws["A4"].value = ("No US-GAAP filings are available for this company in SEC XBRL (foreign "
                          "filers report under IFRS on Form 20-F), so this tab and the model are empty.")
        for addr in ("A28", "B31", "C31", "B53", "C53"):
            ws[addr].value = None
        return
    labels = fb.get("fy_labels") or []
    pad = 10 - len(labels)

    def series(key):
        xs = fb.get(key) or []
        return [None] * pad + list(xs)
    for j, col in enumerate(_FY_COLS):
        lab = ([None] * pad + labels)[j]
        _put(ws, f"{col}5", lab)
        _put(ws, f"{col}6", _v(series("fy_revenue")[j]))
        _put(ws, f"{col}8", _v(series("fy_net_income")[j]))
        _put(ws, f"{col}10", _v(series("fy_fcf")[j]))
        if j > 0:
            prev = _FY_COLS[j - 1]
            _put(ws, f"{col}7", f'=IFERROR({col}6/{prev}6-1,"")')
        _put(ws, f"{col}9", f'=IFERROR({col}8/{col}6,"")')
        _put(ws, f"{col}11", f'=IFERROR({col}10/{col}6,"")')
        _put(ws, f"{col}12", f'=IFERROR({col}10/{col}8,"")')
    for r in (6, 8, 10):
        _put(ws, f"L{r}", f'=IFERROR((K{r}/B{r})^(1/9)-1,"n/a")')
        _put(ws, f"M{r}", f'=IFERROR((K{r}/F{r})^(1/5)-1,"n/a")')
    first_fy = labels[0] if labels else "FY"
    _note(ws["A6"], f"Source: QuantWizard export of SEC XBRL company facts ({R['fb'].get('name', '')}"
                    f"10-K filings {first_fy}–{fb.get('fy')}), rows Revenue (6), Net income (8), Free "
                    f"cash flow (10). {F.index}")

    # latest four quarters
    qs = fb.get("quarters") or []
    cols = "BCDE"[-len(qs):] if qs else ""
    for col in "BCDE":
        for r in (15, 16, 17, 18, 19, 20, 21, 22, 24, 25, 26):
            _put(ws, f"{col}{r}", None)
    for q, col in zip(qs, cols):
        what = f"quarter ended {_d(q['end'])}"
        note = F.note(what, q["end"])
        _put(ws, f"{col}15", q["label"])
        _put(ws, f"{col}16", _dt(q["end"]))
        _put(ws, f"{col}17", _v(q["revenue"]), note)
        _put(ws, f"{col}18", _v(q["rev_yoy"]))
        _put(ws, f"{col}19", _v(q["eps"]), note)
        _put(ws, f"{col}20", _v(q["eps_yoy"]))
        _put(ws, f"{col}21", _v(q["net_income"]))
        _put(ws, f"{col}22", _v(q["op_income"]))
        _put(ws, f"{col}24", _v(q["other_income"]))
        gn = q.get("gain_ni") or 0.0
        ge = q.get("gain_eps") or 0.0
        _put(ws, f"{col}25", round(gn, 4))
        _put(ws, f"{col}26", round(ge, 4),
             ("Estimated: pre-tax gains on equity securities (XBRL "
              f"${(q.get('gains') or 0):,.3f}B) × (1 − 21% US federal rate) ÷ diluted shares."
              if fb.get("gains_material") and ge else None))
    _put(ws, "F16", _dt(qs[-1]["end"]) if qs else None)
    if fb.get("annual_only"):
        a28 = ("No quarterly filings are available; the latest fiscal year stands in for the trailing "
               "twelve months.")
    elif any(q["label"].startswith("Q4") for q in qs):
        q4 = next(q for q in qs if q["label"].startswith("Q4"))
        a28 = (f"{q4['label']} EPS is the full year less the first nine months (the 10-K reports the "
               "year, not the quarter).")
    else:
        a28 = "Quarterly figures from each 10-Q; growth compares with the same quarter a year earlier."
    if not fb.get("gains_material"):
        a28 += (" Equity-security gains are immaterial (< 5% of trailing net income), so clean EPS "
                "equals GAAP EPS.")
    _put(ws, "A28", a28)

    # balance sheet & flows
    B, C = fb["B"], fb["C"]
    fy_end = fb["fy_end"]
    _put(ws, "B31", f"{fb.get('fy')} · {_d(fy_end)} (10-K)")
    tail = "(10-Q)" if fb.get("has_ttm") else "(10-K)"
    _put(ws, "C31", (f"TTM · {_d(fb.get('flows_end'))} {tail}" if fb.get("has_ttm")
                     else f"Latest · {_d(fb.get('bal_end'))} {tail}"))
    _put(ws, "A36", "Short-term debt & current maturities")
    _put(ws, "A37", "Long-term debt (carrying amount)")
    nb = F.note("balance sheet and cash flow", fy_end, "10-K")
    nc = F.note("balance sheet; trailing-twelve-month flows", fb.get("bal_end"), "10-Q")
    for col, d, note in (("B", B, nb), ("C", C, nc)):
        _put(ws, f"{col}32", _v(d.get("cash")), note)
        _put(ws, f"{col}33", _v(d.get("st_sec")))
        _put(ws, f"{col}34", _v(d.get("lt_sec")))
        _put(ws, f"{col}36", _v(d.get("st_debt")))
        _put(ws, f"{col}37", _v(d.get("lt_debt")))
        _put(ws, f"{col}40", _v(d.get("equity")))
        _put(ws, f"{col}41", _v(d.get("assets")))
        _put(ws, f"{col}42", _v(d.get("cfo")))
        _put(ws, f"{col}44", _v(d.get("sbc")))
        _put(ws, f"{col}46", _v(d.get("da")))
        _put(ws, f"{col}47", _v(d.get("op_income")))
    _put(ws, "C43", _v(C.get("capex")))
    _put(ws, "A50", "Annual figures are SEC XBRL company-facts values (10-K filings) supplied by QuantWizard; "
                    "quarterly, balance-sheet and TTM figures are from the XBRL in each 10-Q / 10-K (hover for "
                    "the filing). Blue = hard-coded source input · Green = link to another tab · Black = formula.")
    _put(ws, "B53", f"{fb.get('fy')} · {_d(fy_end)}")
    _put(ws, "C53", f"Latest · {_d(fb.get('bal_end'))}")
    _put(ws, "A55", "Preferred stock (carrying value)")
    _put(ws, "A56", "Shares outstanding (B) — all classes, filing cover")
    _put(ws, "B54", _v(B.get("nonmkt")))
    _put(ws, "C54", _v(C.get("nonmkt")),
         F.note("equity securities without readily determinable fair value plus equity-method "
                "investments", fb.get("bal_end"), "10-Q"))
    _put(ws, "B55", _v(B.get("preferred")))
    _put(ws, "C55", _v(C.get("preferred")), F.note("preferred stock carrying value", fb.get("bal_end"), "10-Q"))
    _put(ws, "B56", _v(fb.get("shares_fy")))
    _put(ws, "C56", _v(fb.get("shares_now")),
         f"Shares outstanding on the latest filing cover ({_d(fb.get('shares_date'))}), all share "
         f"classes summed; basis: {fb.get('shares_basis') or 'n/a'}.")


# ── Multiples, Peers, history, capital returns ────────────────────────────────
def _multiples(wb, R, F, price_source):
    ws = wb["Multiples"]
    fb = R["fb"]
    q_end = fb.get("flows_end")
    if not fb.get("ok"):
        _put(ws, "A2", "No US-GAAP filings available: multiples need filed statements.")
        _put(ws, "C5", f"Report price, {_d(R['price_date'])}")
        _put(ws, "B10", 0.0)
        return
    _put(ws, "A2", f"{fb.get('fy')} = last audited fiscal year (ended {_d(fb['fy_end'])}) · TTM = four "
                   f"quarters to {_d(q_end)} · $ in billions except per share")
    _put(ws, "C5", f"Report price, {_d(R['price_date'])}")
    _put(ws, "C6", f"All share classes, filing cover ({_d(fb.get('shares_date'))})")
    pref = fb["C"].get("preferred") or 0
    _put(ws, "C8", f"Net cash at {_d(fb.get('bal_end'))}" + (
        f", plus ${pref:,.1f}B of preferred stock (a senior claim)" if pref else ""))
    dps = fb.get("dps_quarter") or 0.0
    _put(ws, "B10", dps, F.note("dividends declared per share", q_end, "10-Q") if dps else
         "No common dividend declared in the latest quarter.")
    _put(ws, "C10", f"Declared for the quarter to {_d(q_end, '%b-%Y')}" if dps else "No dividend")
    _put(ws, "B14", fb.get("fy"))
    _put(ws, "C14", f"TTM ({_d(q_end, '%b-%y')})")
    _put(ws, "D16", "Price ÷ TTM diluted EPS less the after-tax per-share effect of gains on equity "
                    "securities (estimated at the 21% US federal rate). The clean earnings multiple."
         if fb.get("gains_material") else
         "Price ÷ TTM diluted EPS; equity-security gains are immaterial, so this equals the GAAP P/E.")
    _put(ws, "A27", f"Profitability, cash quality & balance sheet ({fb.get('fy')} unless stated)")
    _put(ws, "C27", f"TTM ({_d(q_end, '%b-%y')})")
    _put(ws, "B28", _v(fb.get("gross_margin_fy")),
         F.note("gross profit ÷ revenue for the fiscal year", fb["fy_end"], "10-K"))


def _peers(wb, R, P):
    ws = wb["Peers"]
    fb = R["fb"]
    _put(ws, "A2", f"Peer group: {P['peer_label']} · each company on its own latest filings (TTM where a "
                   f"10-Q is newer than the 10-K) and its own market cap")
    _put(ws, "F6", _v((fb.get("ttm") or {}).get("rev_growth")))
    _put(ws, "G6", _v(fb.get("gross_margin_ttm")))
    _put(ws, "L6", f"TTM to {_d(fb.get('flows_end'))}"
         + (" · P/E & net margin excl. equity-security gains" if fb.get("gains_material") else ""))
    for k in range(4):
        r = 7 + k
        p = R["peers"][k] if k < len(R["peers"]) else None
        vals = ([p["ticker"], p["market_cap"], p["pe"], p["ev_ebitda"], p["ps"], p["rev_growth"],
                 p["gross_margin"], p["op_margin"], p["net_margin"], p["fcf_yield"], p["div_yield"],
                 p["basis"]] if p else [None] * 12)
        for col, v in zip("ABCDEFGHIJKL", vals):
            _put(ws, f"{col}{r}", v if isinstance(v, str) else _v(v))


def _valuation_history(wb, R, vhist):
    ws = wb["Valuation_History"]
    rows = ((vhist or {}).get("rows") or [])[-9:]
    fb = R["fb"]
    for k in range(9):
        r = 6 + k
        h = rows[k] if k < len(rows) else None
        vals = ([h["year"], h["eps"], h["pe_low"], h["pe_avg"], h["pe_high"], h["fcf_yield"]]
                if h else [None] * 6)
        for col, v in zip("ABCDEF", vals):
            _put(ws, f"{col}{r}", int(v) if (col == "A" and _num(v)) else _v(v))
    last = 5 + len(rows)
    _put(ws, "A15", "Today — TTM excl. gains" if fb.get("gains_material") else "Today — TTM EPS")
    _put(ws, "A16", f"Today — {fb.get('fy')} GAAP EPS")
    if rows:
        _put(ws, "B16", f"=B{last}")
        _put(ws, "D16", "=IFERROR(Multiples!B5/B16,\"n/a\")")
        _put(ws, "A17", f"Average P/E, FY{rows[0]['year']}–FY{rows[-1]['year']}")
        _note(ws["A6"], "EPS per fiscal year restated onto today's share basis (splits removed); price "
                        "low / average / high per calendar year from daily closes. Source: SEC XBRL EPS and "
                        "Yahoo Finance prices via QuantWizard.")
    else:
        _put(ws, "A6", "Not enough price and earnings history for a P/E comparison.")
        _put(ws, "B16", None)
        _put(ws, "D16", None)
        _put(ws, "A17", "Average P/E")
    return bool(rows)


def _capital_returns(wb, R):
    ws = wb["Capital_Returns"]
    fb = R["fb"]
    labels = fb.get("fy_labels") or []
    pad = 10 - len(labels)

    def ser(key):
        return [None] * pad + list(fb.get(key) or [])
    bb, dv, sbc, sh = ser("fy_buybacks"), ser("fy_dividends"), ser("fy_sbc"), ser("fy_diluted_shares")
    for k in range(10):
        r = 6 + k
        lab = ([None] * pad + labels)[k]
        _put(ws, f"A{r}", lab)
        if lab is None:
            for col in "CDGH":
                _put(ws, f"{col}{r}", None)
            continue
        _put(ws, f"C{r}", _v(bb[k]) if _num(bb[k]) else 0.0)
        _put(ws, f"D{r}", _v(dv[k]) if _num(dv[k]) else 0.0)
        _put(ws, f"G{r}", _v(sbc[k]))
        _put(ws, f"H{r}", _v(sh[k]))
        _put(ws, f"F{r}", f'=IFERROR(E{r}/B{r},"")')
    if labels:
        _put(ws, "A16", f"{labels[0].replace('FY20', 'FY')}–{labels[-1].replace('FY20', 'FY')} total")
    _note(ws["C5"], "Source: SEC XBRL company facts (10-K cash-flow statements): payments for "
                    "repurchase of common stock, dividends paid, share-based compensation; diluted "
                    "weighted-average shares. 0 = none paid that year.")


# ── Segments ──────────────────────────────────────────────────────────────────
def _segments(wb, R, F):
    ws = wb["Segments"]
    seg = R.get("segments") or {}
    rows_in = [r for r in (seg.get("rows") or []) if _num(r.get("rev"))]
    ok = len(rows_in) >= 1 and _num(seg.get("total_rev"))
    basis = seg.get("basis") or "quarter"
    mult = R.get("peer_median_ev_ebitda") or 15.0
    mult = float(round(min(max(mult, 6.0), 40.0)))
    for r in range(6, 12):
        for col in "ABCEFI":
            _put(ws, f"{col}{r}", None)
    if ok:
        main = rows_in[:5] if len(rows_in) <= 5 else rows_in[:4]
        rest = [] if len(rows_in) <= 5 else rows_in[4:]
        out = []
        for s in main:
            out.append((s["name"], s.get("rev_prev"), s["rev"], s.get("oi_prev"), s.get("oi")))
        if rest:
            out.append(("Other segments", _sum_opt(r.get("rev_prev") for r in rest),
                        sum(r["rev"] for r in rest), _sum_opt(r.get("oi_prev") for r in rest),
                        _sum_opt(r.get("oi") for r in rest)))
        # Corporate and reconciling items so the totals tie to reported figures.
        tr, trp = seg.get("total_rev"), seg.get("total_rev_prev")
        to, top = seg.get("total_oi"), seg.get("total_oi_prev")
        srev = sum(x[2] or 0 for x in out)
        srevp = sum(x[1] or 0 for x in out)
        has_oi = any(_num(x[4]) for x in out)
        soi = sum(x[4] or 0 for x in out)
        soip = sum(x[3] or 0 for x in out)
        c_rev = (tr - srev) if _num(tr) else 0.0
        c_revp = (trp - srevp) if _num(trp) else 0.0
        c_oi = (to - soi) if (_num(to) and has_oi) else 0.0
        c_oip = (top - soip) if (_num(top) and has_oi) else 0.0
        if abs(c_rev) > 0.001 * abs(tr or 1) or abs(c_oi) > 0.001 * abs(tr or 1):
            out.append(("Corporate & reconciling items", c_revp, c_rev, c_oip, c_oi))
        out = out[:6]
        note = F.note(f"segment revenue and operating income ({'three months' if basis == 'quarter' else 'fiscal year'} "
                      f"to {_d(seg.get('cur_end'))} and a year earlier)", seg.get("cur_end"))
        for k, (name, rp, rc, op, oc) in enumerate(out):
            r = 6 + k
            _put(ws, f"A{r}", name, note if k == 0 else None)
            _put(ws, f"B{r}", _v(rp / 1e9) if _num(rp) else None)
            _put(ws, f"C{r}", _v(rc / 1e9) if _num(rc) else None)
            _put(ws, f"E{r}", _v(op / 1e9) if _num(op) else None)
            _put(ws, f"F{r}", _v(oc / 1e9) if _num(oc) else None)
            profitable = _num(oc) and oc > 0
            corp = name.startswith("Corporate")
            _put(ws, f"I{r}", mult if (profitable or corp) and _num(oc) else 0.0)
        _note(ws["I6"], f"House assumption: {mult:.0f}x — the peer-median EV/EBITDA used as each profitable "
                        "segment's EV/EBIT multiple (conservative, since EV/EBIT exceeds EV/EBITDA). "
                        "Loss-making segments at 0x; corporate costs at the same multiple. Edit freely.")
    else:
        _put(ws, "A6", "No segment disclosure in the latest filing's XBRL.")
    if basis == "year":
        _put(ws, "B4", '="Fiscal year: "&Financials!K5')
        _put(ws, "H5", "Operating income, full year")
        for r in range(6, 12):
            _put(ws, f"H{r}", f"=N(F{r})")
        _put(ws, "A13", '=IF(ABS(C12-Financials!K6)<0.05,"✓ segment revenue ties to reported annual '
                        'revenue","⚠ segment revenue differs from Financials!K6 by $"&TEXT(C12-Financials!K6,'
                        '"0.00")&"B")')
    else:
        _put(ws, "H5", "Annualised op. income (×4)")
    # operating drivers
    drv = R["fb"].get("drivers") or []
    for k in range(5):
        r = 25 + k
        d = drv[k] if k < len(drv) else None
        _put(ws, f"A{r}", d[0] if d else None)
        _put(ws, f"B{r}", _v(d[1]) if d else None)
        _put(ws, f"C{r}", _v(d[2]) if d else None)
    labels = [d[0] for d in drv[:5]]
    ann = R["fb"].get("annual_only")
    _put(ws, "B24", "Prior year" if ann else "Yr-ago qtr")
    _put(ws, "C24", "Latest year" if ann else "Latest qtr")
    rv = next((25 + i for i, lab in enumerate(labels) if lab.startswith("Revenue")), None)
    rd = next((25 + i for i, lab in enumerate(labels) if lab.startswith("R&D")), None)
    if rv and rd:
        _put(ws, "A30", "R&D as % of revenue")
        _put(ws, "B30", f'=IFERROR(B{rd}/B{rv},"–")')
        _put(ws, "C30", f'=IFERROR(C{rd}/C{rv},"–")')
        _put(ws, "D30", '=IFERROR(C30-B30,"–")')
    else:
        for col in "ABCD":
            _put(ws, f"{col}30", None)
    _note(ws["A24"], "Source: the company's XBRL (latest 10-Q / 10-K): revenue, R&D expense, remaining "
                     "performance obligations, current contract liabilities and inventory - whichever "
                     "the company reports.")
    # street estimates
    cs = R.get("consensus") or {}
    _put(ws, "B34", _v(cs.get("ntm_revenue")))
    _put(ws, "B35", _v(cs.get("y2_revenue")))
    _put(ws, "B36", _v(cs.get("op_margin")))
    _put(ws, "B37", _v(cs.get("ntm_growth")))
    if cs:
        _note(ws["B33"], f"{cs.get('source')}, retrieved {_d(cs.get('as_of'))}"
                         + (f" ({cs['analysts']} analysts)" if cs.get("analysts") else "")
                         + ". Next-12-month revenue = the rest of the current fiscal year plus the matching "
                           "share of next year's estimate; year 2 extends next year's estimate at its own "
                           "growth rate. Unofficial.")
    _put(ws, "A38", "Consensus is optional and unofficial: when blank the comparison shows 'awaiting "
                    "input'. Model year 1 = the twelve months after the latest reported quarter.")
    return ok


def _sum_opt(xs):
    xs = [x for x in xs if _num(x)]
    return sum(xs) if xs else None


# ── DCF inputs ────────────────────────────────────────────────────────────────
def _dcf(wb, R, price_source):
    ws = wb["DCF"]
    fb, A, W = R["fb"], R["assumptions"], R["wacc"]
    qs = fb.get("quarters") or []
    _put(ws, "C5", f"Report price: close of {_d(R['price_date'])} ({price_source or 'market data feed'}).")
    _put(ws, "C6", f"All share classes, filing cover ({_d(fb.get('shares_date'))}).")
    _put(ws, "B8", A["tg"])
    if A.get("g1_formula"):
        _put(ws, "B9", f"=IFERROR(MAX({RI.G1_RANGE[0]},MIN({RI.G1_RANGE[1]},Financials!M6)),{A['g1']})")
    else:
        _put(ws, "B9", A["g1"])
    labels = fb.get("fy_labels") or []
    span = f"{labels[-6]}–{labels[-1]}" if len(labels) >= 6 else "available years"
    _put(ws, "C9", f"Defaults to the 5-yr revenue CAGR ({span}), held between {RI.G1_RANGE[0]:.0%} and "
                   f"+{RI.G1_RANGE[1]:.0%}; fades linearly to terminal growth by year 10. Overwrite to test."
         if A.get("g1_formula") else
         f"Defaults to {A['g1_basis']}; fades linearly to terminal growth by year 10. Overwrite to test.")
    for addr, v in (("B11", A["years_norm"]), ("B12", "Yes" if A["deduct_sbc"] else "No"),
                    ("B13", "Yes" if A["include_nonmkt"] else "No"), ("B14", R["rf"]),
                    ("B15", A["haircut"]), ("B16", "Yes" if A["mid_year"] else "No"),
                    ("B17", A["anchor"]), ("B18", A["custom_margin"]), ("B19", A["lr_capex"]),
                    ("B20", A["da_of_capex"]), ("B21", A["nwc"]), ("B22", None),
                    ("B25", R["rf10"]), ("B28", A["erp"]), ("B30", A["kd"]), ("B31", A["tax"])):
        _put(ws, addr, v)
    n_cx = len(fb.get("fy_capex_pct") or [])
    _put(ws, "C19", f"The company's median capex share of revenue over {n_cx} filed fiscal years, to the "
                    "nearest half point. Assumption; edit to test." if n_cx else
                    "Trailing capex share of revenue. Assumption; edit to test.")
    _put(ws, "C38", "(CFO − capex) ÷ revenue, trailing twelve months.")
    _put(ws, "C41", "D&A share of revenue, trailing twelve months.")
    _put(ws, "C44", f"Cash + marketable securities − debt, {_d(fb.get('bal_end'))}.")
    _put(ws, "A37", f"Base revenue — TTM to {_d(fb.get('flows_end'), '%b-%Y')} ($B)")
    _put(ws, "A45", "Preferred stock ($B)")
    _put(ws, "C45", "A senior claim ahead of common shareholders; 0 when none is outstanding.")
    _put(ws, "A69", "Less: preferred stock ($B)")
    _put(ws, "A83", f"Historical 5-yr revenue CAGR ({span})")
    if len(qs) >= 3:
        _put(ws, "C84", f"{qs[-3]['label']}–{qs[-1]['label']} as reported.")
    spread = A["kd"] - R["rf10"]
    _put(ws, "C30", f"10-year Treasury + {spread:.2%} credit spread (stepped by leverage).")
    _put(ws, "C31", "Effective tax rate, trailing twelve months (held between 5% and 35%)."
         if fb.get("tax_ttm") is not None else "US federal statutory rate (no usable effective rate).")
    _note(ws["B25"], f"FRED DGS10 on {_d(R['generated'])}: {R['rf10']:.2%}. https://fred.stlouisfed.org/series/DGS10")
    _note(ws["B14"], f"FRED DGS3MO on {_d(R['generated'])}: {R['rf']:.2%}. https://fred.stlouisfed.org/series/DGS3MO")
    if R.get("beta_raw") is None:
        _put(ws, "B26", 1.0)
        _put(ws, "C26", "Beta could not be estimated (no benchmark series); 1.0 assumed.")
    sp = A.get("spreads")
    if sp:
        sc = wb["Scenarios"]
        for addr, k in (("F6", "growth"), ("F7", "margin"), ("F8", "wacc"), ("F9", "tg")):
            sc[addr].value = sp[k]


# ── Risk ──────────────────────────────────────────────────────────────────────
def _risk(wb, R, last, period_label):
    ws = wb["Risk"]
    win = R["win"]
    d0, d1 = pd.Timestamp(win["Date"].iloc[0]), pd.Timestamp(win["Date"].iloc[-1])
    years = (d1 - d0).days / 365.25
    _put(ws, "C7", f"Report period: {period_label}")
    # monthly table: one row per month from the first full month to the last
    m0 = (d0 + pd.offsets.MonthEnd(0)) + pd.offsets.MonthEnd(1)
    m1 = d1 + pd.offsets.MonthEnd(0)
    n_months = (m1.year - m0.year) * 12 + (m1.month - m0.month) + 1
    n_months = max(1, min(n_months, 61))
    L = last
    for k in range(61):
        r = 6 + k
        if k >= n_months:
            for col in "FGH":
                _put(ws, f"{col}{r}", None)
            continue
        if k == 0:
            continue                                   # template row 6 stays
        _put(ws, f"F{r}", f"=EOMONTH(F{r - 1},1)")
        _put(ws, f"G{r}", f"=_xlfn.XLOOKUP(F{r}+0.99,Data!$A$2:$A${L},Data!$E$2:$E${L},,-1)")
        _put(ws, f"H{r}", f"=G{r}/G{r - 1}-1")
        if r == 66:
            _copy_row_style(ws, 65, 66, "FGH")
    end = 5 + n_months
    for addr, f in (("H67", f"=MAX(H6:H{end})"), ("H68", f"=MIN(H6:H{end})"),
                    ("H69", f'=COUNTIF(H6:H{end},">0")&" of "&COUNT(H6:H{end})')):
        _put(ws, addr, f)
    _put(ws, "F71", f"First month is partial (starts {_d(d0)} → {m0:%b} uses the full month); the last "
                    f"row ({m1:%b-%Y}) is month-to-date to {_d(d1, '%d-%b')}.")
    # calendar-year table
    y0, y1 = d0.year, d1.year
    k = max(1, min(6, y1 - y0 + 1))
    base = f"_xlfn.XLOOKUP(DATE({{y}},12,31)+0.99,Data!$A$2:$A${L},Data!$E$2:$E${L},,-1)"
    vol = f"=_xlfn.STDEV.S(_xlfn._xlws.FILTER(Data!$H$3:$H${L},YEAR(Data!$A$3:$A${L})=A{{r}}))*SQRT(252)"
    from openpyxl.worksheet.formula import ArrayFormula
    for i in range(6):
        r = 38 + i
        if i >= k:
            for col in "ABCD":
                _put(ws, f"{col}{r}", None)
            continue
        _put(ws, f"A{r}", "=YEAR(B5)" if i == 0 else f"=A{r - 1}+1")
        last_row = (i == k - 1)
        if last_row:
            _put(ws, f"B{r}", '="YTD to "&TEXT(B6,"d-mmm")' if i > 0 else
                 '="Partial: "&TEXT(B5,"d-mmm")&" to "&TEXT(B6,"d-mmm")')
            prev = (base.format(y=f"A{r - 1}") if i > 0 else f"Data!E2")
            _put(ws, f"C{r}", f"=B9/{prev}-1")
        else:
            _put(ws, f"B{r}", '="Partial: from "&TEXT(B5,"d-mmm")' if i == 0 else "Full year")
            cur = base.format(y=f"A{r}")
            prev = "Data!E2" if i == 0 else base.format(y=f"A{r - 1}")
            _put(ws, f"C{r}", f"={cur}/{prev}-1")
        ws[f"D{r}"].value = ArrayFormula(f"D{r}", vol.format(r=r))
    ytd_row = 38 + k - 1
    wb["Summary"]["B14"].value = f"=Risk!C{ytd_row}"
    return years


# ── Catalysts & news ──────────────────────────────────────────────────────────
def _catalysts_news(wb, cats, news):
    ws = wb["Catalysts_News"]
    for k in range(6):
        r = 6 + k
        row = cats[k] if k < len(cats) else (None, None, None, None)
        for col, v in zip("ABCD", row):
            _put(ws, f"{col}{r}", v)
    items = (news or [])[:9]
    for k in range(9):
        r = 15 + k
        n = items[k] if k < len(items) else None
        if not n:
            for col in "ABCDEF":
                _put(ws, f"{col}{r}", None)
            continue
        dt = n.get("Date")
        try:
            dt = pd.Timestamp(str(dt)[:10]).to_pydatetime()
        except Exception:
            pass
        note = "SEC filing" if "SEC" in str(n.get("Publisher") or "") else None
        vals = (dt, n.get("Headline"), n.get("Publisher"), n.get("Theme") or "General",
                n.get("Sentiment") or "Neutral", note)
        for col, v in zip("ABCDEF", vals):
            _put(ws, f"{col}{r}", v)
        _link(ws[f"B{r}"], n.get("URL"))
    if not items:
        _put(ws, "B15", "No recent headlines passed the relevance filter.")


# ── Methodology & Sources ─────────────────────────────────────────────────────
def _methodology(wb, R, P, price_source):
    ws = wb["Methodology"]
    fb, A, W = R["fb"], R["assumptions"], R["wacc"]
    win = R["win"]
    d0, d1 = pd.Timestamp(win["Date"].iloc[0]), pd.Timestamp(win["Date"].iloc[-1])
    yrs = (d1 - d0).days / 365.25
    ttm_txt = (f" TTM = the four quarters to {_d(fb.get('flows_end'))}, rebuilt from the cumulative "
               "figures in each 10-Q / 10-K." if not fb.get("annual_only") else
               " No quarterly filings: the latest fiscal year stands in for TTM.")
    _put(ws, "B6", f"FY = {P['short']}'s fiscal year, ending {P['fy_end']}.{ttm_txt}")
    if fb.get("gains_material"):
        _put(ws, "B8", "TTM diluted EPS less the after-tax per-share effect of gains on equity securities, "
                       f"estimated from the XBRL gain line at the 21% US federal rate (${fb['ttm']['gain_eps']:.2f} "
                       "over the last four quarters). Used for P/E, earnings yield and peer comparison.")
        _put(ws, "C8", "An estimate: companies that disclose the per-share effect in their earnings release "
                       "may differ slightly. FY P/E is GAAP.")
    else:
        _put(ws, "B8", "TTM diluted EPS. Gains on equity securities were checked and are immaterial "
                       "(under 5% of trailing net income), so no adjustment is made.")
        _put(ws, "C8", "Other one-off items (tax, litigation, impairments) are not adjusted.")
    _put(ws, "B11", f"10-year revenue × operating-margin model. Revenue starts from TTM "
                    f"(${(fb.get('ttm') or {}).get('revenue') or 0:,.0f}B) and year-1 growth (default: "
                    f"{A['g1_basis']}) fades linearly to terminal growth. Operating margin, capex and D&A move "
                    f"in a straight line from today's level to their long-run level over "
                    f"{A['years_norm']} years; FCF = NOPAT + D&A − capex − working capital (+ SBC unless "
                    "deducted).")
    _put(ws, "C11", "Paths are linear; the model does not time an investment cycle explicitly.")
    _put(ws, "C12", f"Valuation date ≈ {_d(fb.get('flows_end'))}, the TTM end.")
    _put(ws, "B13", f"CAPM: 10-year Treasury ({R['rf10']:.2%}) + Blume-adjusted beta (⅔ × live raw beta "
                    f"from the Risk tab + ⅓) × {A['erp']:.1%} ERP, blended with an after-tax cost of debt "
                    "at market weights. Built line by line on the DCF tab.")
    pref = fb["C"].get("preferred") or 0
    _put(ws, "B14", "Enterprise value + net cash (cash + marketable securities − debt)"
                    + (f" − ${pref:,.1f}B preferred stock" if pref else "")
                    + " + non-marketable equity stakes after a 20% haircut (toggle).")
    _put(ws, "C14", f"Balance sheet at {_d(fb.get('bal_end'))}. Private stakes are carried at the "
                    "company's own valuation marks.")
    _put(ws, "B15", f"All share classes outstanding from the latest filing cover "
                    f"({(fb.get('shares_now') or 0):,.3f}B at {_d(fb.get('shares_date'))}).")
    _put(ws, "C15", "Basic count; options and convertibles are not converted into shares.")
    _put(ws, "B16", "Solves the year-1 revenue growth (margin and capex held), the long-run operating "
                    "margin (growth held) and the long-run capex share (growth and margin held) at which "
                    "the DCF equals the price.")
    _put(ws, "C16", "Linear interpolation on 161-point grids (DCF columns Q:V).")
    _put(ws, "B19", f"Daily closes {_d(d0)} to {_d(d1)} ({yrs:.1f} years), {price_source or 'market data feed'}.")
    _put(ws, "B21", f"(Annualised arithmetic mean return − {R['rf']:.2%} risk-free) ÷ annualised volatility "
                    "(downside deviation for Sortino).")
    _put(ws, "C21", f"A {yrs:.1f}-year window; a single sample, highly period-dependent.")
    _put(ws, "A31", f"Model v2 ({_d(R['generated'])})")
    _put(ws, "B31", "DCF on explicit drivers: revenue → operating margin → tax → + D&A − capex − working "
                    "capital (+ SBC unless deducted). Long-run operating margin chosen by anchor (DCF!B17); "
                    "capex and D&A normalise over DCF!B11 years. Replaces the earlier free-cash-flow growth "
                    "model, so base values differ from reports before this date.")
    _put(ws, "B33", f"Prices from {price_source or 'Polygon / Yahoo Finance'}; fundamentals from SEC filings "
                    "(XBRL, 10-K/10-Q); consensus (where shown) from Yahoo Finance — unofficial. Figures "
                    "may contain errors; check the Sources tab before relying on them.")


def _sources(wb, R, F, P, price_source, cs_used):
    ws = wb["Sources"]
    fb = R["fb"]
    rows = []
    for q in (fb.get("quarters") or [])[-4:]:
        f = F.for_period(q["end"])
        rows.append((f"{q['label']} revenue / diluted EPS", "Financials quarters",
                     f"${q['revenue']:,.1f}B / ${q['eps']:.2f}" if _num(q.get("eps")) else
                     f"${q['revenue']:,.1f}B",
                     f"Form {f['form']} for the period ended {_d(f['period'])}" if f else "SEC XBRL",
                     "SEC filing", f["url"] if f else F.index))
    if F.tenk:
        rows.append((f"{fb.get('fy')} balance sheet, cash flow, SBC, D&A", "Financials column B",
                     f"FY ended {_d(fb['fy_end'])}", f"Form 10-K filed {_d(F.tenk.get('filed'))}",
                     "SEC filing", F.tenk.get("url")))
    if F.tenq and fb.get("has_ttm"):
        rows.append(("Latest balance sheet & TTM cash flow", "Financials column C; DCF net cash",
                     f"At {_d(fb.get('bal_end'))}", f"Form 10-Q filed {_d(F.tenq.get('filed'))}",
                     "SEC filing", F.tenq.get("url")))
    if fb.get("dps_quarter"):
        rows.append(("Quarterly dividend", "Multiples B10", f"${fb['dps_quarter']:.2f}",
                     "Dividends declared per share", "SEC filing",
                     (F.for_period(fb.get("flows_end")) or F.tenq or {}).get("url")))
    labels = fb.get("fy_labels") or []
    if labels:
        rows.append((f"{labels[0]}–{labels[-1]} revenue, net income, FCF", "Financials B6:K12", "Various",
                     "SEC EDGAR XBRL company facts (10-K)", "SEC filing", F.index))
    rows.append(("Price history & current price", "Data; Risk; DCF B5",
                 f"${R['price']:,.2f} ({_d(R['price_date'])})", price_source or "Market data feed",
                 "Feed", f"https://finance.yahoo.com/quote/{R['ticker']}/history/"))
    rows.append(("Risk-free rate", "DCF B14; Risk", f"{R['rf']:.2%}", "US Treasury 3-month (FRED DGS3MO)",
                 "Government data", "https://fred.stlouisfed.org/series/DGS3MO"))
    rows.append(("10-year Treasury (WACC)", "DCF B25", f"{R['rf10']:.2%}", "US Treasury 10-year (FRED DGS10)",
                 "Government data", "https://fred.stlouisfed.org/series/DGS10"))
    if R["peers"]:
        rows.append(("Peer multiples", "Peers; Multiples peer table",
                     ", ".join([R["ticker"]] + [p["ticker"] for p in R["peers"]]),
                     "Each peer's own 10-Q / 10-K XBRL; market caps from the data feed",
                     "SEC filing / feed", None))
    rows.append(("Headlines", "Catalysts_News", "—", "QuantWizard news feed (aggregators + SEC 8-Ks)",
                 "Third-party", None))
    rows.append(("Daily prices & SPY returns", "Data (A:G)", f"Last {len(R['win']):,} trading days",
                 f"{price_source or 'Market data feed'} via QuantWizard", "Adjusted close", None))
    rows.append(("Quarterly operating income, other income, net income, gain effects",
                 "Financials rows 21–27", f"{(fb.get('quarters') or [{}])[0].get('label', '')}–"
                                          f"{(fb.get('quarters') or [{}])[-1].get('label', '')}",
                 "XBRL in each 10-Q / 10-K (gain effect estimated)", "SEC filing", F.index))
    lx = R.get("latest_filing") or {}
    rows.append(("Shares outstanding, non-marketable securities, preferred stock",
                 "Financials rows 54–56; DCF B6",
                 f"{(fb.get('shares_now') or 0):,.3f}B shares · ${fb['C'].get('nonmkt') or 0:,.1f}B · "
                 f"${fb['C'].get('preferred') or 0:,.1f}B",
                 f"{lx.get('form') or 'Latest'} filing cover & balance sheet", "SEC filing",
                 lx.get("url") or (F.tenq or F.tenk or {}).get("url")))
    if (R.get("segments") or {}).get("rows"):
        rows.append(("Segment revenue & operating income", "Segments rows 6–11",
                     f"Period to {_d(R['segments'].get('cur_end'))}", "Latest filing's XBRL instance",
                     "SEC filing", lx.get("url")))
    if cs_used:
        cs = R.get("consensus") or {}
        rows.append(("Revenue consensus", "Segments B34:B37", "—", cs.get("source") or "Yahoo Finance",
                     "Third-party (unofficial)", f"https://finance.yahoo.com/quote/{R['ticker']}/analysis/"))
    rows = rows[:20]
    for k in range(20):
        r = 5 + k
        rec = rows[k] if k < len(rows) else (None,) * 6
        for col, v in zip("ABCDEF", rec):
            val = "Open source" if (col == "F" and v) else v
            _put(ws, f"{col}{r}", val)
            if col == "F":
                ws[f"F{r}"].hyperlink = None
                _link(ws[f"F{r}"], v)


# ── Valuation log ─────────────────────────────────────────────────────────────
def _valuation_log(wb, history):
    ws = wb["Valuation_Log"]
    hist = [h for h in (history or []) if h][:20]
    note = ws["A7"].value
    note_style = copy.copy(ws["A7"]._style)
    for k, h in enumerate(hist):
        r = 6 + k
        if r > 6:
            _copy_row_style(ws, 6, r, "ABCDEFGHIJ")
        vals = (_dt(h.get("report_date")), h.get("price"), h.get("bear"), h.get("base"), h.get("bull"),
                h.get("pw"), h.get("upside"), h.get("verdict"), h.get("implied_cagr"), h.get("integrity"))
        for col, v in zip("ABCDEFGHIJ", vals):
            ws[f"{col}{r}"].value = v if (isinstance(v, str) or v is None or hasattr(v, "year")) else _v(v)
    if hist:
        nr = 6 + len(hist) + 1
        ws["A7"].value = None if nr != 7 else note
        ws[f"A{nr}"].value = note
        ws[f"A{nr}"]._style = note_style


# ── when there is no DCF ──────────────────────────────────────────────────────
def _no_dcf(wb, R):
    why = R.get("dcf_reason") or "the model does not apply"
    na = "n/a"
    d = wb["DCF"]
    for r in range(52, 62):
        for col in "BCDEFGHIJKLMNO":
            d[f"{col}{r}"].value = None
    for r in range(52, 213):
        for col in "QRSTUV":
            d[f"{col}{r}"].value = None
    for r in list(range(64, 77)) + list(range(79, 87)) + list(range(92, 101)):
        if d[f"A{r}"].value is not None:
            d[f"B{r}"].value = na
    for r in range(101, 109):
        d[f"B{r}"].value = "–"
    d["B109"].value = "n/a — no DCF"
    d["A50"].value = f"No DCF for {R['ticker']}: {why}. The report values it on multiples only."
    s = wb["Scenarios"]
    for addr in ("B11", "C11", "D11", "B12", "C12", "D12", "B13", "B14"):
        s[addr].value = na
    for addr in ("B15", "C15", "D15"):
        s[addr].value = None
    s["A35"].value = f"No DCF for this company: {why}."
    for rng in ("C21:G25", "C29:G33", "K29:O33"):
        for row in s[rng]:
            for c in row:
                c.value = na
    for r in (39, 40, 41, 42):
        s[f"B{r}"].value = na
    for r in (50, 51, 52):
        for col in "BCD":
            s[f"{col}{r}"].value = None
    su = wb["Summary"]
    su["A5"].value = "No DCF"
    su["A6"].value = f"Valuation on multiples only: {why}."
    su["E5"].value, su["E6"].value = na, None
    su["F5"].value, su["F6"].value = na, None
    for rng in ("F10:H14",):
        for row in su[rng]:
            for c in row:
                c.value = na
    for addr in ("F17", "F18", "F20", "G20"):
        su[addr].value = na
    su["E23"].value = (f'="At "&TEXT(B9,"$0")&", "&Data!$Q$2&" is valued here on multiples only — no DCF '
                       f'({why})."')
    su["E31"].value = "Stance: valuation view, no rating. No DCF for this company. Not investment advice."
    su["D42"].value = "• No DCF: the verdict rests on the multiples and peer comparison."
    su["D43"].value = None
    su["D44"].value = None
    seg = wb["Segments"]
    for r in range(34, 38):
        seg[f"C{r}"].value = na
        seg[f"D{r}"].value = na
    log = wb["Valuation_Log"]
    for col in "CDEFGIJ":
        log[f"{col}5"].value = na
    if not (R.get("fb") or {}).get("ok"):
        # No filings at all: the model's derived inputs have nothing to read.
        for addr in ("B7", "B9", "B10", "B27", "B29", "B32", "B33", "B34") + tuple(
                f"B{r}" for r in range(37, 48)):
            d[addr].value = na
        for rng in ("B6:D9", "C28:G28", "B21:B25", "B29:B33", "J29:J33"):
            for row in s[rng]:
                for c in row:
                    c.value = na
        su["A36"].value = ("• No US-GAAP financial statements are filed in SEC XBRL for this company, so "
                           "the bull and bear evidence rests on prices and news.")
        for addr in ("A37", "A38", "A39", "D36", "D37", "D38", "D39"):
            su[addr].value = None


def _blank_football(wb, has_peers_pe, has_hist, has_segments):
    """A football-field row with nothing behind it would draw a bar at $0."""
    s = wb["Scenarios"]
    for r, ok, label in ((53, has_peers_pe, "Peer P/E range × clean EPS"),
                         (54, has_hist, "Own P/E history × clean EPS"),
                         (55, has_segments, "Sum-of-the-parts (segment multiples ±20%)")):
        if not ok:
            for col in "BCD":
                s[f"{col}{r}"].value = None
            s[f"A{r}"].value = f"{label}  —  not available"
    # multiples rows need positive clean EPS
    s["B53"].value = s["B53"].value and '=IF(Financials!F27>0,MIN(Peers!C7:C10)*Financials!F27,"")'
    s["C53"].value = s["C53"].value and '=IF(Financials!F27>0,MAX(Peers!C7:C10)*Financials!F27,"")'
    s["B54"].value = s["B54"].value and '=IF(Financials!F27>0,MIN(Valuation_History!D6:D14)*Financials!F27,"")'
    s["C54"].value = s["C54"].value and '=IF(Financials!F27>0,MAX(Valuation_History!D6:D14)*Financials!F27,"")'
    for r in (53, 54, 55):
        if s[f"D{r}"].value:
            s[f"D{r}"].value = f'=IFERROR(C{r}-B{r},"")'


# ── charts & post-processing ─────────────────────────────────────────────────
def _charts(wb, last):
    ws = wb["Summary"]
    data = wb["Data"]
    line = LineChart()
    line.add_data(Reference(data, min_col=5, min_row=2, max_row=last))
    line.set_categories(Reference(data, min_col=1, min_row=2, max_row=last))
    line.anchor = TwoCellAnchor(_from=AnchorMarker(col=8, colOff=76200, row=27, rowOff=114300),
                                to=AnchorMarker(col=17, colOff=431800, row=42, rowOff=177800))
    ws.add_chart(line)
    sc = wb["Scenarios"]
    bar = BarChart()
    bar.add_data(Reference(sc, min_col=2, min_row=49, max_row=57))
    bar.set_categories(Reference(sc, min_col=1, min_row=49, max_row=57))
    bar.anchor = TwoCellAnchor(_from=AnchorMarker(col=8, colOff=76200, row=7, rowOff=0),
                               to=AnchorMarker(col=17, colOff=431800, row=28, rowOff=57150))
    ws.add_chart(bar)


_METADATA_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<metadata xmlns="http://schemas.'
    'openxmlformats.org/spreadsheetml/2006/main" xmlns:xda="http://schemas.microsoft.com/office/'
    'spreadsheetml/2017/dynamicarray"><metadataTypes count="1"><metadataType name="XLDAPR" '
    'minSupportedVersion="120000" copy="1" pasteAll="1" pasteValues="1" merge="1" splitFirst="1" '
    'rowColShift="1" clearFormats="1" clearComments="1" assign="1" coerce="1" cellMeta="1"/>'
    '</metadataTypes><futureMetadata name="XLDAPR" count="1"><bk><extLst><ext uri="{bdbb8cdc-fa1e-'
    '496e-a857-3c3f30c029c3}"><xda:dynamicArrayProperties fDynamic="1" fCollapsed="0"/></ext>'
    '</extLst></bk></futureMetadata><cellMetadata count="1"><bk><rc t="1" v="0"/></bk></cellMetadata>'
    '</metadata>')


def _postprocess(raw, ticker, last, years, price_source):
    zin = zipfile.ZipFile(io.BytesIO(raw))
    out = io.BytesIO()
    zout = zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED)
    with open(os.path.join(CHART_DIR, "price_line.xml"), encoding="utf-8") as fh:
        line_xml = fh.read()
    with open(os.path.join(CHART_DIR, "football.xml"), encoding="utf-8") as fh:
        bar_xml = fh.read()
    win = f"{years:.0f} years" if years >= 1.5 else f"{years * 12:.0f} months"
    basis = "total return, $" if price_source in (None, "Yahoo Finance") else "price-only, $"
    names = zin.namelist()
    for name in names:
        data = zin.read(name)
        if name == "[Content_Types].xml":
            s = data.decode("utf-8")
            if "/xl/metadata.xml" not in s:
                s = s.replace("</Types>", '<Override PartName="/xl/metadata.xml" ContentType="application/'
                                          'vnd.openxmlformats-officedocument.spreadsheetml.sheetMetadata+xml"/></Types>')
            data = s.encode("utf-8")
        elif name == "xl/_rels/workbook.xml.rels":
            s = data.decode("utf-8")
            if "metadata.xml" not in s:
                s = s.replace("</Relationships>", '<Relationship Id="rIdQWmeta" Type="http://schemas.openxmlformats'
                                                  '.org/officeDocument/2006/relationships/sheetMetadata" '
                                                  'Target="metadata.xml"/></Relationships>')
            data = s.encode("utf-8")
        elif name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
            s = data.decode("utf-8")
            s = re.sub(r'<c r="([A-Z]+\d+)"([^>]*)><f t="array" ref="\1"',
                       lambda m: (f'<c r="{m.group(1)}"{m.group(2)} cm="1"><f t="array" ref="{m.group(1)}"'
                                  if "cm=" not in m.group(2) else m.group(0)), s)
            data = s.encode("utf-8")
        elif name == "xl/charts/chart1.xml":
            s = line_xml.replace("{TITLE}", f"{ticker} price, {win} ({basis})")
            if last != TEMPLATE_LAST_ROW:
                s = s.replace(f"${TEMPLATE_LAST_ROW}", f"${last}")
            data = s.encode("utf-8")
        elif name == "xl/charts/chart2.xml":
            data = bar_xml.encode("utf-8")
        zout.writestr(name, data)
    if "xl/metadata.xml" not in names:
        zout.writestr("xl/metadata.xml", _METADATA_XML)
    zout.close()
    return out.getvalue()


# ── publish checks (the reference's section 3, in Python) ─────────────────────
def publish_checks(R):
    """[(check, status, detail)] - Excel is not available on the server, so the
    reference's publish checks run on the same numbers here."""
    out = []
    fb = R["fb"]
    if R.get("dcf_ok"):
        ck = R["model"]["checks"]
        out.append(("Model integrity", ck["integrity"][:1], ck["integrity"]))
        out.append(("Terminal growth < WACC", ck["items"][0][1][:1], ""))
    else:
        out.append(("Model integrity", "–", f"no DCF: {R.get('dcf_reason')}"))
    age = (pd.Timestamp(R["generated"]).normalize() - pd.Timestamp(R["price_date"]).normalize()).days
    out.append(("Price freshness", "✓" if age <= 4 else "⚠", f"last price {_d(R['price_date'])}"))
    q_end = (fb.get("quarters") or [{}])[-1].get("end")
    stale = (pd.Timestamp(R["generated"]) - pd.Timestamp(q_end)).days if q_end is not None else 999
    out.append(("Filing freshness", "✓" if stale <= 135 else "⚠",
                f"latest period {_d(q_end)} ({stale} days)"))
    ttm = fb.get("ttm") or {}
    if fb.get("gains_material"):
        out.append(("Clean EPS", "✓", f"TTM EPS excludes ${ttm.get('gain_eps', 0):.2f} of gains"))
    return out


# ── entry point ───────────────────────────────────────────────────────────────
def build_report(ticker, df, financials=None, fundamentals=None, company_details=None,
                 news_rows=None, peer_fund=None, peer_group=None, valuation_data=None,
                 filings=None, price_source=None, latest_xbrl=None, street=None,
                 log_history=None, R=None, period_label="5Y", log=None):
    """The workbook as BytesIO. Pass `R` (report_inputs.build) to reuse the
    numbers the site already computed; otherwise they are built here."""
    from analysis import valuation_history
    t = ticker.upper()
    if R is None:
        R = RI.build(t, df, financials, fundamentals, company_details=company_details,
                     peer_rows=peer_fund, latest_xbrl=latest_xbrl, consensus=street, log=log)
    P = RT.profile(R, peer_group)
    cats = RT.catalysts(R, filings, news_rows, (street or {}).get("earnings_date"))
    Q = RT.q_texts(R, cats)
    cik = None
    try:
        from data import _sec_cik_for
        cik = _sec_cik_for(t, log=lambda *a, **k: None)
    except Exception:
        pass
    F = _Filings(P["name"], filings, cik)
    R["fb"]["name"] = P["name"] + " "
    vhist = valuation_history(valuation_data, fundamentals) if valuation_data else None

    wb = load_workbook(TEMPLATE)
    last = _data(wb, R, P, Q, price_source)
    _rewrite_rows(wb, last)
    _financials(wb, R, F)
    _multiples(wb, R, F, price_source)
    _peers(wb, R, P)
    has_hist = _valuation_history(wb, R, vhist)
    _capital_returns(wb, R)
    has_seg = _segments(wb, R, F)
    _dcf(wb, R, price_source)
    years = _risk(wb, R, last, period_label)
    _catalysts_news(wb, cats, news_rows)
    _methodology(wb, R, P, price_source)
    _sources(wb, R, F, P, price_source, bool(R.get("consensus")))
    _valuation_log(wb, log_history)
    has_peer_pe = any(_num(p.get("pe")) for p in R["peers"])
    _blank_football(wb, has_peer_pe, has_hist, has_seg)
    if not R.get("dcf_ok"):
        _no_dcf(wb, R)
    for ws in wb.worksheets:
        for part in ("oddFooter", "evenFooter", "firstFooter"):
            side = getattr(ws, part).center
            if side.text and "{COMPANY}" in side.text:
                side.text = side.text.replace("{COMPANY}", f"{P['short']} ({t})")
    _charts(wb, last)
    wb.active = 0
    try:
        wb.calculation.fullCalcOnLoad = True
    except Exception:
        pass
    buf = io.BytesIO()
    __import__("doc_props").stamp(wb)
    wb.save(buf)
    return io.BytesIO(_postprocess(buf.getvalue(), t, last, years, price_source))


def filename(ticker, when=None):
    return f"{ticker.upper()}_Valuation_{pd.Timestamp(when or datetime.now()):%Y-%m-%d}.xlsx"
