"""One set of numbers behind the site's valuation and every report.

The report workbook (see excel_valuation.py) is a live model: the site writes
its input cells and Excel computes the rest. This module derives those inputs
ONCE - the filings figures in $ billions, the five-year price window, beta,
the discount rate and the DCF's house assumptions - and runs the same model in
Python (valuation_model.py). The site, the PowerPoint, the Word memo and the
workbook all read the result, so a number can only differ between them if the
workbook's formulas and valuation_model disagree, which the tests forbid.

Every rule that decides an input is here and nowhere else:
  - Data window: the last 1,255 trading days (five years), whatever window the
    reader picked on the site, so a stock's fair value never depends on a
    chart setting. Fewer when the stock is younger.
  - Beta: Excel's SLOPE of the stock's daily returns on SPY's over that window.
  - Clean EPS: GAAP EPS less the after-tax per-share effect of gains on equity
    securities (taxed at the 21% US federal rate), applied only when the gains
    move trailing net income by more than 5%.
  - Year-1 growth: the five-year revenue CAGR, held between -10% and +30%.
  - Long-run margin: today's (TTM) operating margin; the peer median when the
    company is loss-making; no DCF when neither exists.
  - Long-run capex: the company's own median capex share of revenue over its
    filed years, to the nearest half point.
  - Cost of debt: the 10-year Treasury plus the leverage-stepped spread used
    elsewhere on the site; tax: the trailing effective rate.
"""
import math
from datetime import datetime

import numpy as np
import pandas as pd

import valuation_model as V

DATA_ROWS = 1255
GAIN_TAX = 0.21
GAIN_MATERIAL = 0.05
G1_RANGE = (-0.10, 0.30)
ERP = 0.05
TERMINAL_GROWTH = 0.025
FINANCIAL_WORDS = ("bank", "broker", "dealer", "insurance", "capital market", "financial",
                   "investment banking", "asset management", "credit services")


# ── small helpers ─────────────────────────────────────────────────────────────
def _num(x):
    return isinstance(x, (int, float, np.integer, np.floating)) and not (
        isinstance(x, float) and not math.isfinite(x))


def _bn(x):
    return float(x) / 1e9 if _num(x) else None


def _val(frame, field, i=0):
    if frame is None or len(frame) <= i or field not in frame.columns:
        return None
    v = frame.iloc[i][field]
    return float(v) if _num(v) else None


def _sum(xs):
    xs = [x for x in xs if _num(x)]
    return float(sum(xs)) if xs else None


def fiscal_year_of(end):
    """FY named for the calendar year it ends in; a 52/53-week year ending in
    the first days of a month belongs to the month before."""
    end = pd.Timestamp(end)
    anchor = end if end.day >= 15 else end - pd.Timedelta(days=end.day + 1)
    return anchor.year


def fy_label(end):
    return f"FY{fiscal_year_of(end)}"


def _median(xs):
    xs = sorted(x for x in xs if _num(x))
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


# ── prices ────────────────────────────────────────────────────────────────────
def ensure_spy_returns(df, log=None):
    """`df` with a SPY_Return column aligned to its dates, fetching SPY when the
    reader did not include it as a benchmark (beta needs it)."""
    if "SPY_Return" in df.columns and df["SPY_Return"].notna().sum() > 20:
        return df
    try:
        import market_data
        d0 = pd.to_datetime(df["Date"]).min() - pd.Timedelta(days=10)
        d1 = pd.to_datetime(df["Date"]).max()
        spy = market_data.get_bars("SPY", str(d0.date()), str(d1.date()))
        if spy is None or not len(spy):
            return df
        s = spy.assign(Date=pd.to_datetime(spy["Date"]).dt.normalize()).set_index("Date")["Close"]
        s = s[~s.index.duplicated()].sort_index()
        dates = pd.to_datetime(df["Date"]).dt.normalize()
        aligned = s.reindex(dates.values)
        out = df.copy()
        out["SPY_Return"] = aligned.pct_change().values
        return out
    except Exception as e:
        if log:
            log(f"   SPY returns unavailable: {type(e).__name__}")
        return df


def price_window(df, rows=DATA_ROWS):
    """The Data tab: the last `rows` trading days, oldest first."""
    d = df.copy()
    dt = pd.to_datetime(d["Date"])
    if dt.dt.tz is not None:
        dt = dt.dt.tz_localize(None)
    d["Date"] = dt.dt.normalize()
    d = d.sort_values("Date").tail(rows).reset_index(drop=True)
    keep = [c for c in ("Date", "Open", "High", "Low", "Close", "Volume", "SPY_Return")
            if c in d.columns]
    return d[keep]


def beta_raw(win):
    """Excel's SLOPE(Data!H3:Hn, Data!G3:Gn): the stock's daily return (from
    closes) on SPY's, pairs with a blank on either side dropped."""
    if win is None or "SPY_Return" not in win.columns or len(win) < 30:
        return None
    close = win["Close"].astype(float).to_numpy()
    y = close[1:] / close[:-1] - 1
    x = pd.to_numeric(win["SPY_Return"], errors="coerce").to_numpy(dtype=float)[1:]
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 30:
        return None
    xm, ym = x.mean(), y.mean()
    den = float(((x - xm) ** 2).sum())
    return float(((x - xm) * (y - ym)).sum() / den) if den > 0 else None


# ── filings ───────────────────────────────────────────────────────────────────
def _debt(row):
    """(short-term, total) debt the way compute_fundamentals counts it."""
    get = (lambda k: (float(row[k]) if (k in row and _num(row[k])) else None)) if row is not None \
        else (lambda k: None)
    ltd = get("long_term_debt")
    cur = get("debt_current_total")
    if cur is None:
        parts = [get(k) for k in ("debt_current", "commercial_paper", "short_term_borrowings")]
        parts = [p for p in parts if p is not None]
        cur = sum(parts) if parts else None
    total = get("debt_total_incl_current")
    if total is None and (ltd is not None or cur is not None):
        total = (ltd or 0.0) + (cur or 0.0)
    if total is None:
        return None, None
    st = min(cur or 0.0, total)
    return st, total


def filings_block(financials, fundamentals=None, price=None):
    """Everything the Financials, Multiples, Capital_Returns and Segments tabs
    read from the filings, in $ billions (per-share in $, shares in billions)."""
    fin = financials or {}
    inc, bal, cf = (fin.get("income_statement"), fin.get("balance_sheet"),
                    fin.get("cash_flow_statement"))
    inc = inc if inc is not None else pd.DataFrame()
    bal = bal if bal is not None else pd.DataFrame()
    cf = cf if cf is not None else pd.DataFrame()
    ttm = fin.get("ttm") or {}
    f = fundamentals or {}
    out = {"ok": len(inc) > 0}
    if not out["ok"]:
        # No US-GAAP filings (a foreign 20-F filer, a fund): the workbook still
        # builds - prices, risk and news - with the filings rows blank.
        empty = {k: None for k in ("cash", "st_sec", "lt_sec", "st_debt", "lt_debt", "equity", "assets",
                                   "cfo", "capex", "sbc", "da", "op_income")}
        empty.update({"preferred": 0.0, "nonmkt": 0.0, "cash_total": 0.0, "debt_total": 0.0,
                      "net_cash": 0.0})
        sh = (f["market_cap"] / price) if (_num(f.get("market_cap")) and price) else None
        out.update({"fy_ends": [], "fy_labels": [], "fy_revenue": [], "fy_net_income": [], "fy_fcf": [],
                    "fy_capex_pct": [], "fy_eps": [], "fy_diluted_shares": [], "fy_buybacks": [],
                    "fy_dividends": [], "fy_sbc": [], "fy_end": None, "fy": "FY", "rev_cagr5": None,
                    "B": dict(empty), "C": dict(empty), "bal_end": None, "flows_end": None,
                    "has_ttm": False, "quarters": [], "annual_only": True, "gains_material": False,
                    "ttm": {"revenue": None, "op_income": None, "op_margin": None, "eps": None,
                            "eps_clean": None, "net_income": None, "gain_eps": 0.0, "rev_growth": None},
                    "shares_now": _bn(sh), "shares_fy": None, "shares_basis": None, "shares_date": None,
                    "dps_quarter": 0.0, "gross_margin_fy": None, "gross_margin_ttm": None,
                    "tax_ttm": None, "drivers": []})
        return out

    n = min(10, len(inc))
    order = list(range(n - 1, -1, -1))                     # oldest first
    ends = [pd.Timestamp(inc.iloc[i]["Period"]) for i in order]
    out["fy_ends"] = ends
    out["fy_labels"] = [fy_label(e) for e in ends]
    out["fy_revenue"] = [_bn(_val(inc, "revenues", i)) for i in order]
    out["fy_net_income"] = [_bn(_val(inc, "net_income_loss", i)) for i in order]
    fcf = []
    capex_pct = []
    for i in order:
        o, c = _val(cf, "net_cash_flow_from_operating_activities", i), _val(cf, "capex", i)
        fcf.append(_bn(o - c) if (o is not None and c is not None) else None)
        r = _val(inc, "revenues", i)
        if c is not None and r and r > 0:
            capex_pct.append(c / r)
    out["fy_fcf"] = fcf
    out["fy_capex_pct"] = capex_pct
    out["fy_eps"] = [_val(inc, "diluted_earnings_per_share", i) for i in order]
    out["fy_diluted_shares"] = [_bn(_val(inc, "diluted_shares", i)) for i in order]
    out["fy_buybacks"] = [_bn(_val(cf, "buybacks", i)) for i in order]
    out["fy_dividends"] = [_bn(_val(cf, "dividends_paid", i)) for i in order]
    out["fy_sbc"] = [_bn(_val(cf, "sbc", i)) for i in order]
    out["fy_end"] = ends[-1]
    out["fy"] = out["fy_labels"][-1]

    # Five-year revenue CAGR exactly as Financials!M6 computes it.
    rv = out["fy_revenue"]
    out["rev_cagr5"] = ((rv[-1] / rv[-6]) ** (1 / 5) - 1
                        if len(rv) >= 6 and _num(rv[-1]) and _num(rv[-6]) and rv[-6] > 0
                        and rv[-1] > 0 else None)

    # ── balance sheet & flows: B = last fiscal year, C = latest / TTM ──────────
    fy_bal = bal.iloc[0].to_dict() if len(bal) else {}
    fy_end = ends[-1]
    bal_end = pd.Timestamp(ttm["balance_end"]) if ttm.get("balance_end") else None
    flows_end = pd.Timestamp(ttm["flows_end"]) if ttm.get("flows_end") else None
    bal_new = bool(bal_end is not None and bal_end > fy_end and ttm.get("balance"))
    flows_new = bool(flows_end is not None and flows_end > fy_end)
    now_bal = ttm["balance"] if bal_new else fy_bal

    def bal_items(row):
        g = (lambda k: _bn(row.get(k)) if row and _num(row.get(k)) else None)
        st, total = _debt(row)
        return {"cash": g("cash") or 0.0, "st_sec": g("short_term_investments") or 0.0,
                "lt_sec": g("lt_securities") or 0.0,
                "st_debt": _bn(st) if st is not None else 0.0,
                "lt_debt": (_bn(total) - _bn(st)) if total is not None else 0.0,
                "equity": g("equity"), "assets": g("assets"),
                "preferred": g("preferred") or 0.0,
                "nonmkt": (_sum([g("nonmkt_securities"), g("equity_method")]) or 0.0)}
    B, C = bal_items(fy_bal), bal_items(now_bal)
    B.update({"cfo": _bn(_val(cf, "net_cash_flow_from_operating_activities")),
              "sbc": _bn(_val(cf, "sbc")),
              "da": _bn(_val(inc, "depreciation_amortization")),
              "op_income": _bn(_val(inc, "operating_income_loss"))})
    ti, tc = ttm.get("income") or {}, ttm.get("cash_flow") or {}
    if flows_new and ti:
        C.update({"cfo": _bn(tc.get("net_cash_flow_from_operating_activities")),
                  "capex": _bn(tc.get("capex")), "sbc": _bn(tc.get("sbc")),
                  "da": _bn(ti.get("depreciation_amortization")),
                  "op_income": _bn(ti.get("operating_income_loss"))})
    else:
        C.update({"cfo": B["cfo"], "capex": _bn(_val(cf, "capex")), "sbc": B["sbc"],
                  "da": B["da"], "op_income": B["op_income"]})
    for d in (B, C):
        d["cash_total"] = d["cash"] + d["st_sec"] + d["lt_sec"]
        d["debt_total"] = d["st_debt"] + d["lt_debt"]
        d["net_cash"] = d["cash_total"] - d["debt_total"]
    out["B"], out["C"] = B, C
    out["bal_end"] = bal_end if bal_new else fy_end
    out["flows_end"] = flows_end if flows_new else fy_end
    out["has_ttm"] = flows_new

    # ── the latest four quarters ──────────────────────────────────────────────
    qs = [q for q in (ttm.get("quarters") or []) if _num(q.get("revenue"))]
    annual = len(qs) < 4
    quarters = []
    if not annual:
        for q in qs[-4:]:
            quarters.append({
                "label": q["label"], "end": pd.Timestamp(q["end"]),
                "revenue": _bn(q["revenue"]), "rev_yoy": q.get("rev_yoy"),
                "eps": round(q["eps"], 4) if _num(q.get("eps")) else None,
                "eps_yoy": q.get("eps_yoy"),
                "net_income": _bn(q.get("net_income")), "op_income": _bn(q.get("operating_income")),
                "other_income": _bn(q.get("other_income")), "gains": _bn(q.get("equity_gains")),
                "rev_prev": _bn(q.get("rev_prev")), "rnd": _bn(q.get("rnd")),
                "rnd_prev": _bn(q.get("rnd_prev")),
                "eps_exact": q.get("eps_exact", True)})
    else:
        i0 = 0
        r_prev = _val(inc, "revenues", 1)
        e_prev = _val(inc, "diluted_earnings_per_share", 1)
        rv0, e0 = _val(inc, "revenues", i0), _val(inc, "diluted_earnings_per_share", i0)
        quarters.append({
            "label": f"{out['fy']} (annual)", "end": fy_end, "revenue": _bn(rv0),
            "rev_yoy": (rv0 / r_prev - 1) if (rv0 and r_prev) else None,
            "eps": round(e0, 4) if _num(e0) else None,
            "eps_yoy": (e0 / e_prev - 1) if (_num(e0) and e_prev and e_prev > 0) else None,
            "net_income": _bn(_val(inc, "net_income_loss", i0)),
            "op_income": _bn(_val(inc, "operating_income_loss", i0)),
            "other_income": None, "gains": None, "rev_prev": _bn(r_prev),
            "rnd": _bn(_val(inc, "research_and_development", i0)),
            "rnd_prev": _bn(_val(inc, "research_and_development", 1)), "eps_exact": True})
    out["quarters"], out["annual_only"] = quarters, annual

    # Clean EPS: the after-tax per-share effect of equity-security gains, only
    # when material to trailing earnings.
    g_ni = [(q["gains"] * (1 - GAIN_TAX)) if _num(q["gains"]) else 0.0 for q in quarters]
    ni_ttm = _sum(q["net_income"] for q in quarters)
    material = bool(ni_ttm and abs(sum(g_ni)) > GAIN_MATERIAL * abs(ni_ttm))
    for q, gn in zip(quarters, g_ni):
        if material and gn and _num(q["eps"]) and q["net_income"]:
            q["gain_ni"], q["gain_eps"] = gn, gn * q["eps"] / q["net_income"]
        else:
            q["gain_ni"], q["gain_eps"] = 0.0, 0.0
    out["gains_material"] = material

    ttm_rev = _sum(q["revenue"] for q in quarters)
    ttm_oi = _sum(q["op_income"] for q in quarters) if all(
        _num(q["op_income"]) for q in quarters) else None
    out["ttm"] = {"revenue": ttm_rev, "op_income": ttm_oi,
                  "op_margin": (ttm_oi / ttm_rev) if (ttm_oi is not None and ttm_rev) else None,
                  "eps": _sum(q["eps"] for q in quarters),
                  "eps_clean": ((_sum(q["eps"] for q in quarters) or 0)
                                - sum(q["gain_eps"] for q in quarters)),
                  "net_income": ni_ttm,
                  "gain_eps": sum(q["gain_eps"] for q in quarters)}
    prev = _sum(q.get("rev_prev") for q in quarters) if all(
        _num(q.get("rev_prev")) for q in quarters) else None
    out["ttm"]["rev_growth"] = (ttm_rev / prev - 1) if (ttm_rev and prev) else (
        (f.get("growth") or {}).get("revenue_yoy") / 100
        if _num((f.get("growth") or {}).get("revenue_yoy")) else None)

    # Capital structure
    sh_now = ttm.get("shares_outstanding") or f.get("shares_outstanding")
    shares_basis = ttm.get("shares_source") or ("filing cover" if sh_now else None)
    if not sh_now and _num(f.get("market_cap")) and price:
        sh_now, shares_basis = f["market_cap"] / price, "market cap ÷ price (no cover count filed)"
    sh_fy = ttm.get("shares_fy") or _val(inc, "diluted_shares")
    out["shares_now"], out["shares_fy"] = _bn(sh_now), _bn(sh_fy)
    out["shares_basis"], out["shares_date"] = shares_basis, ttm.get("shares_date")

    # Per-share and margin inputs
    dps = ttm.get("dps_quarter")
    out["dps_quarter"] = float(dps) if _num(dps) else 0.0
    gp, rv0 = _val(inc, "gross_profit"), _val(inc, "revenues")
    out["gross_margin_fy"] = (gp / rv0) if (gp is not None and rv0) else None
    tgp, trv = ti.get("gross_profit"), ti.get("revenues")
    out["gross_margin_ttm"] = ((tgp / trv) if (_num(tgp) and _num(trv) and trv and flows_new)
                               else out["gross_margin_fy"])
    pre, tax = ti.get("pretax_income"), ti.get("income_tax")
    if not flows_new:
        pre, tax = _val(inc, "pretax_income"), _val(inc, "income_tax")
    out["tax_ttm"] = (tax / pre) if (_num(pre) and pre > 0 and _num(tax)) else None

    # Operating drivers: latest vs a year earlier
    drv = []
    lq = quarters[-1]
    if _num(lq.get("revenue")):
        drv.append(("Revenue, " + ("quarter" if not annual else "year") + " ($B)",
                    lq.get("rev_prev"), lq["revenue"]))
    if _num(lq.get("rnd")):
        drv.append(("R&D expense, " + ("quarter" if not annual else "year") + " ($B)",
                    lq.get("rnd_prev"), lq["rnd"]))
    names = {"rpo": "Contracted backlog, RPO ($B)", "deferred_revenue": "Deferred revenue, current ($B)",
             "inventory": "Inventory ($B)"}
    for k in ("rpo", "deferred_revenue", "inventory"):
        d = (ttm.get("drivers_bal") or {}).get(k)
        if d and _num(d.get("now")):
            drv.append((names[k], _bn(d.get("ago")), _bn(d["now"])))
    out["drivers"] = drv[:5]
    return out


# ── the whole context ─────────────────────────────────────────────────────────
def build(ticker, df, financials, fundamentals=None, company_details=None, peer_rows=None,
          latest_xbrl=None, consensus=None, rf=None, rf10=None, sector=None, log=None):
    """Every number the reports and the site's valuation need, derived once.

    `df` is the full price history (at least five years when available) with
    Date/Open/High/Low/Close/Volume and, ideally, SPY_Return. `peer_rows` are
    analysis.peer_metrics rows (the subject's own row is skipped)."""
    from constants import get_risk_free_rate, get_long_risk_free_rate
    t = ticker.upper()
    df = ensure_spy_returns(df, log=log)
    win = price_window(df)
    price = float(win["Close"].iloc[-1])
    fb = filings_block(financials, fundamentals, price=price)
    R = {"ticker": t, "win": win, "price": price, "price_date": pd.Timestamp(win["Date"].iloc[-1]),
         "fb": fb, "cd": company_details or {}, "generated": datetime.now()}
    try:
        R["rf"] = float(rf if rf is not None else get_risk_free_rate())
    except Exception:
        R["rf"] = 0.04
    try:
        R["rf10"] = float(rf10 if rf10 is not None else get_long_risk_free_rate())
    except Exception:
        R["rf10"] = 0.045
    R["beta_raw"] = beta_raw(win)

    # Peers (decimal units), at most four.
    peers = []
    for p in (peer_rows or []):
        if not p or str(p.get("ticker", "")).upper() == t:
            continue
        pct = (lambda k: p[k] / 100 if _num(p.get(k)) else None)
        peers.append({"ticker": p["ticker"], "market_cap": _bn(p.get("market_cap")),
                      "pe": p.get("pe") if _num(p.get("pe")) else None,
                      "ev_ebitda": p.get("ev_ebitda") if _num(p.get("ev_ebitda")) else None,
                      "ps": p.get("ps") if _num(p.get("ps")) else None,
                      "rev_growth": pct("rev_growth"), "gross_margin": pct("gross_margin"),
                      "op_margin": pct("op_margin"), "net_margin": pct("net_margin"),
                      "fcf_yield": pct("fcf_yield"), "div_yield": pct("div_yield"),
                      "basis": p.get("basis") or ""})
        if len(peers) == 4:
            break
    R["peers"] = peers
    R["peer_median_op_margin"] = _median([p["op_margin"] for p in peers])
    R["peer_median_ev_ebitda"] = _median([p["ev_ebitda"] for p in peers])
    R["segments"] = (latest_xbrl or {}).get("segments")
    R["latest_filing"] = latest_xbrl or {}
    R["consensus"] = _consensus(consensus, fb) if consensus else None

    _valuation(R, fundamentals, sector or (company_details or {}).get("Sector"))
    return R


def _consensus(c, fb):
    """Next-twelve-month and year-2 revenue from fiscal-year estimates,
    aligned to the DCF's year 1 (the twelve months after the TTM end)."""
    try:
        fy0, fy1 = c.get("fy0"), c.get("fy1")
        if not (_num(fy0) and _num(fy1)):
            return None
        qs = fb.get("quarters") or []
        last = qs[-1]["label"] if qs else ""
        qn = int(last[1]) if (last.startswith("Q") and last[1].isdigit()) else 4
        n_rep = 0 if qn == 4 else qn                       # quarters of FY0 already reported
        ytd = sum((q["revenue"] or 0) for q in qs[-n_rep:]) if n_rep else 0.0
        fy0b, fy1b = fy0 / 1e9, fy1 / 1e9
        ntm = (fy0b - ytd) + fy1b * n_rep / 4
        g1 = fy1b / fy0b - 1 if fy0b else 0.0
        y2 = fy1b * (4 - n_rep) / 4 + fy1b * (1 + g1) * n_rep / 4
        ttm_rev = (fb.get("ttm") or {}).get("revenue")
        return {"ntm_revenue": ntm, "y2_revenue": y2,
                "ntm_growth": (ntm / ttm_rev - 1) if ttm_rev else None,
                "op_margin": None, "analysts": c.get("analysts"), "as_of": c.get("as_of"),
                "source": c.get("source", "Yahoo Finance analyst estimates")}
    except Exception:
        return None


def _valuation(R, fundamentals, sector):
    fb = R["fb"]
    R["dcf_ok"], R["dcf_reason"] = False, None
    sec = " ".join([str(sector or ""), str((fundamentals or {}).get("sector") or ""),
                    str((fundamentals or {}).get("industry") or "")]).lower()
    ttm = fb.get("ttm") or {}
    C = fb.get("C") or {}
    reason = None
    if not fb.get("ok"):
        reason = "no filed financial statements"
    elif any(w in sec for w in FINANCIAL_WORDS):
        reason = ("a financial company: lending and trading flows dominate its cash flow, "
                  "so a revenue-and-margin DCF does not describe it")
    elif not ttm.get("revenue") or ttm["revenue"] <= 0:
        reason = "no revenue to project"
    elif ttm.get("op_income") is None:
        reason = "operating income is not reported"
    elif not fb.get("shares_now"):
        reason = "no share count"

    # House assumptions (DCF!B8:B22, B25:B31)
    tax = fb.get("tax_ttm")
    tax = min(0.35, max(0.05, tax)) if _num(tax) else 0.21
    from analysis import _credit_spread
    mcap = R["price"] * (fb.get("shares_now") or 0) * 1e9
    kd = R["rf10"] + _credit_spread((C.get("debt_total") or 0) * 1e9, mcap)
    g_raw = fb.get("rev_cagr5")
    g_basis = "5-yr revenue CAGR"
    if not _num(g_raw):
        g_raw, g_basis = ttm.get("rev_growth"), "trailing-twelve-month revenue growth"
    g1 = min(G1_RANGE[1], max(G1_RANGE[0], g_raw)) if _num(g_raw) else 0.05
    if _num(g_raw) and g1 != g_raw:
        g_basis += f" ({g_raw:.1%}), held within {G1_RANGE[0]:.0%} to +{G1_RANGE[1]:.0%}"
    anchor, margin = "TTM", ttm.get("op_margin")
    if not reason and (margin is None or margin <= 0):
        if _num(R.get("peer_median_op_margin")) and R["peer_median_op_margin"] > 0:
            anchor, margin = "Peer median", R["peer_median_op_margin"]
        else:
            reason = "operating losses and no profitable peers to anchor a long-run margin"
    cps = fb.get("fy_capex_pct") or []
    lr = _median(cps)
    if lr is None and C.get("capex") and ttm.get("revenue"):
        lr = C["capex"] / ttm["revenue"]
    lr = round(min(0.5, max(0.005, lr)) * 200) / 200 if _num(lr) else 0.05
    tg = min(TERMINAL_GROWTH, R["rf10"])
    A = {"tg": tg, "years_norm": 5, "deduct_sbc": False, "include_nonmkt": True, "haircut": 0.2,
         "mid_year": True, "anchor": anchor, "custom_margin": 0.35, "lr_capex": lr,
         "da_of_capex": 0.85, "nwc": 0.0, "capex_guide": None, "erp": ERP, "kd": kd, "tax": tax,
         "g1": g1, "g1_basis": g_basis, "g1_formula": _num(fb.get("rev_cagr5"))}
    R["assumptions"] = A
    beta = R.get("beta_raw")
    if beta is None and not reason:
        beta = 1.0
        A["beta_note"] = "beta unavailable - 1.0 assumed"
    R["wacc"] = V.wacc_build(R["rf10"], beta if beta is not None else 1.0, ERP, kd, tax,
                             C.get("debt_total") or 0.0, R["price"], fb.get("shares_now") or 1.0)
    if reason:
        R["dcf_reason"] = reason
        return
    rev = ttm["revenue"]
    inputs = V.Inputs(
        price=R["price"], shares=fb["shares_now"], wacc=R["wacc"]["wacc"], tg=tg, g1=g1,
        margin=margin, years_norm=5, deduct_sbc=False, include_nonmkt=True, haircut=0.2,
        mid_year=True, lr_capex=lr, da_of_capex=0.85, nwc=0.0, capex_guide=0.0, tax=tax,
        revenue=rev, margin0=ttm["op_margin"], capex0=(C.get("capex") or 0.0) / rev,
        da0=(C.get("da") or 0.0) / rev, sbc0=(C.get("sbc") or 0.0) / rev,
        net_cash=C["net_cash"], preferred=C.get("preferred") or 0.0, nonmkt=C.get("nonmkt") or 0.0)
    if inputs.wacc <= inputs.tg:
        R["dcf_reason"] = "the discount rate is at or below terminal growth"
        return
    A["spreads"] = V.scaled_spreads(g1, margin)
    model = V.run(inputs, spreads=A["spreads"])
    fv = model["bridge"]["fair_value"]
    if not _num(fv) or fv <= 0:
        R["dcf_reason"] = ("the model gives no positive value - projected cash burn outweighs "
                           "the terminal value")
        return
    R["inputs"], R["model"], R["dcf_ok"] = inputs, model, True
    R["fair_value_after_sbc"] = V.fair_value(inputs, deduct_sbc=True)


def snapshot(R):
    """The Valuation_Log row for today's model (None without a DCF)."""
    if not R.get("dcf_ok"):
        return None
    m = R["model"]
    sc = m["scenarios"]
    return {"ticker": R["ticker"], "report_date": str(pd.Timestamp(R["generated"]).date()),
            "price": R["price"], "bear": sc["bear"]["fair_value"], "base": sc["base"]["fair_value"],
            "bull": sc["bull"]["fair_value"], "pw": sc["prob_weighted"],
            "upside": sc["prob_weighted_upside"], "verdict": m["verdict"],
            "implied_cagr": m["reverse"]["cagr"], "integrity": m["checks"]["integrity"],
            "model_version": "v2"}


# ── the site's view of the same model ─────────────────────────────────────────
def site_dcf(R):
    """The dict the site and the deck/memo builders read (the keys the old
    FCF DCF returned, with the revenue model's numbers), or {"ok": False}."""
    if not R.get("dcf_ok"):
        return {"ok": False, "reason": R.get("dcf_reason") or "no model"}
    i, m = R["inputs"], R["model"]
    br, sc, rv = m["bridge"], m["scenarios"], m["reverse"]
    w = R["wacc"]
    sens = m["sensitivity"]
    proj = [{"year": r["year"], "growth": r["growth"], "revenue": r["revenue"] * 1e9,
             "op_margin": r["op_margin"], "fcf": r["fcf"] * 1e9, "pv": r["pv"] * 1e9}
            for r in m["projection"]]
    scen = {k: {"growth": v["g1"], "margin": v["margin"], "wacc": v["wacc"],
                "terminal_growth": v["tg"], "probability": v["probability"],
                "fair_value": v["fair_value"], "upside": v["upside"]}
            for k, v in sc.items() if k in ("bear", "base", "bull")}
    fv_sbc = R.get("fair_value_after_sbc")
    return {
        "ok": True, "model": "revenue", "price": i.price,
        "fair_value": br["fair_value"], "upside": br["upside"],
        "wacc": i.wacc,
        "wacc_basis": {"rf": w["rf10"], "beta": w["beta_raw"], "beta_adj": w["beta_adj"],
                       "erp": w["erp"], "cost_of_equity": w["cost_of_equity"],
                       "cost_of_debt": w["cost_of_debt"], "tax": w["tax"],
                       "weight_debt": w["weight_debt"], "weight_equity": w["weight_equity"]},
        "terminal_growth": i.tg, "years": V.YEARS, "mid_year": i.mid_year,
        "base_growth": i.g1, "base_growth_basis": R["assumptions"]["g1_basis"],
        "target_margin": i.margin, "margin_anchor": R["assumptions"]["anchor"],
        "current_margin": i.margin0, "lr_capex": i.lr_capex, "capex_now": i.capex0,
        "revenue_ttm": i.revenue * 1e9,
        "net_cash": i.net_cash * 1e9, "net_debt": -i.net_cash * 1e9 + i.preferred * 1e9,
        "preferred": i.preferred * 1e9, "nonmkt": i.nonmkt_after_haircut * 1e9,
        "shares": i.shares * 1e9,
        "enterprise_value": br["enterprise_value"] * 1e9, "equity_value": br["equity_value"] * 1e9,
        "pv_explicit": br["pv_explicit"] * 1e9, "terminal_value": br["terminal_value"] * 1e9,
        "pv_terminal": br["pv_terminal"] * 1e9, "tv_share": br["tv_share"],
        "projection": proj, "scenarios": scen,
        "prob_weighted": sc["prob_weighted"], "prob_weighted_upside": sc["prob_weighted_upside"],
        "fair_value_after_sbc": fv_sbc,
        "upside_after_sbc": (fv_sbc / i.price - 1) if fv_sbc else None,
        "sbc_share": i.sbc0,
        # What today's price implies - revenue growth and long-run margin now.
        "market_implied_growth": rv["growth"], "market_implied_cagr": rv["cagr"],
        "implied_margin": rv["margin"], "implied_capex": rv["capex"],
        "implied_capex_note": rv["capex_note"], "implied_year10_revenue":
            (rv["year10_revenue"] * 1e9) if rv["year10_revenue"] else None,
        "base_cagr": _fade_cagr(i.g1, i.tg),
        "sensitivity": {"wacc_axis": sens["wacc_tg"]["rows"], "tg_axis": sens["wacc_tg"]["cols"],
                        "grid": sens["wacc_tg"]["grid"]},
        "sensitivity_growth": {"growth_axis": sens["growth_margin"]["rows"],
                               "margin_axis": sens["growth_margin"]["cols"],
                               "grid": sens["growth_margin"]["grid"]},
        "sensitivity_capex": sens["capex_years"],
        "checks": m["checks"]["items"], "integrity": m["checks"]["integrity"],
        "verdict": m["verdict"], "verdict_detail": m["verdict_detail"],
        "caveats": [], "base_fcf": m["projection"][0]["fcf"] * 1e9,
        "base_fcf_basis": "year-1 projection",
    }


def _fade_cagr(g1, tg):
    s = sum(math.log(1 + (g1 - (g1 - tg) * (t - 1) / 9)) for t in range(1, V.YEARS + 1))
    return math.exp(s) ** (1 / V.YEARS) - 1
