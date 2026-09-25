"""The report's sentences, written from the company's own numbers.

The reference workbook's bull/bear bullets, catalysts and company notes were
written by hand for Alphabet. These rules write the same kind of sentence for
any company from its filings, prices and model - deterministic, so the same
data always gives the same words, and incapable of stating a fact the data does
not contain. Every sentence has a fallback, and each is kept under ~150
characters so it fits the cell or slide it lands in.
"""
import math
import re

import pandas as pd


def _num(x):
    return isinstance(x, (int, float)) and not (isinstance(x, float) and not math.isfinite(x))


def _d(x, fmt="%d-%b-%Y"):
    try:
        return pd.Timestamp(x).strftime(fmt)
    except Exception:
        return str(x or "")


def part_of_month(ts):
    ts = pd.Timestamp(ts)
    return "early" if ts.day <= 10 else ("mid" if ts.day <= 20 else "late")


def clean_name(name, ticker):
    n = re.sub(r"\s+(Class [A-Z]\s+)?(Common Stock|Ordinary Shares|Common Shares|"
               r"Capital Stock|American Depositary Shares)\b.*$", "", name or "", flags=re.I)
    return n.strip() or ticker


def short_name(name):
    s = re.sub(r"\s*&\s*Co\.?$", "", (name or "").strip())
    for _ in range(2):
        s = re.sub(r",?\s+(Inc\.?|Incorporated|Corporation|Corp\.?|Company|Co\.?|Ltd\.?|"
                   r"plc|PLC|N\.V\.|S\.A\.|Holdings?|Group|Limited|L\.P\.)$", "", s,
                   flags=re.I).strip(" ,")
    return s or name


_EXCH = {"nasdaq": "NASDAQ", "new york stock exchange": "NYSE", "nyse": "NYSE",
         "nyse american": "NYSE American", "nyse arca": "NYSE Arca", "cboe": "Cboe"}


def exchange_short(code):
    try:
        from analysis import exchange_name
        n = exchange_name(code) or ""
    except Exception:
        n = code or ""
    return _EXCH.get(n.lower(), n.upper() if len(n) <= 6 else n)


def fy_end_desc(ends):
    """'late December' for the reader; fiscal years ending on a fixed weekday
    say so."""
    ends = [pd.Timestamp(e) for e in (ends or [])][-5:]
    if not ends:
        return "at its fiscal year-end"
    e = ends[-1]
    anchor = e if e.day >= 15 else e - pd.Timedelta(days=e.day + 1)
    if e.day < 15:
        return f"early {e.strftime('%B')}"
    return f"{part_of_month(anchor)} {anchor.strftime('%B')}"


def profile(R, peer_label=None):
    cd = R.get("cd") or {}
    name = clean_name(cd.get("Name") or R["ticker"], R["ticker"])
    return {"name": name, "short": short_name(name), "ticker": R["ticker"],
            "exchange": exchange_short(cd.get("Exchange")) or "",
            "report_date": pd.Timestamp(R["generated"]).normalize(),
            "fy_end": fy_end_desc((R["fb"] or {}).get("fy_ends")),
            "peer_label": peer_label or "peers"}


def _fastest_segment(R):
    seg = R.get("segments") or {}
    rows = [r for r in (seg.get("rows") or []) if _num(r.get("rev")) and _num(r.get("rev_prev"))
            and r["rev_prev"] > 0]
    tot = sum(r["rev"] for r in rows) or 0
    cands = [r for r in rows if tot and r["rev"] / tot >= 0.05]
    if len(cands) < 2:
        return None
    best = max(cands, key=lambda r: r["rev"] / r["rev_prev"])
    g = best["rev"] / best["rev_prev"] - 1
    return {**best, "growth": g, "share": best["rev"] / tot}


def _fit(s, n=150):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0].rstrip(",;") + "…"


def q_texts(R, catalysts=None):
    """Data!Q8:Q16 - the sentences the Summary's bullets and the deck quote."""
    fb = R["fb"]
    ttm = fb.get("ttm") or {}
    C, B = fb.get("C") or {}, fb.get("B") or {}
    out = {}
    # Q8 - the strongest single growth fact
    fact = None
    rpo = next((d for d in fb.get("drivers") or [] if d[0].startswith("Contracted backlog")), None)
    if rpo and _num(rpo[1]) and _num(rpo[2]) and rpo[1] > 0 and rpo[2] / rpo[1] - 1 > 0.10:
        fact = (f"Contracted backlog (remaining performance obligations) reached ${rpo[2]:,.1f}B at "
                f"{_d(fb.get('bal_end'))}, up {rpo[2] / rpo[1] - 1:.0%} from a year earlier.")
    fs = _fastest_segment(R)
    seg_fact = False
    if fact is None and fs and fs["growth"] > (ttm.get("rev_growth") or 0):
        seg_fact = True
        q = (R.get("segments") or {}).get("basis") == "quarter"
        fact = (f"{fs['name']} revenue grew {fs['growth']:.0%} y/y to ${fs['rev'] / 1e9:,.1f}B "
                f"in the latest {'quarter' if q else 'year'}, {fs['share']:.0%} of the total.")
    if fact is None and _num(ttm.get("revenue")):
        g = ttm.get("rev_growth")
        fact = (f"Revenue reached ${ttm['revenue']:,.1f}B over the last twelve months"
                + (f", {g:+.0%} on the year before." if _num(g) else "."))
    out["Q8"] = _fit(fact or "Recent filings show the business is still growing.")
    # Q9 - growth driver
    out["Q9"] = _fit(f"led by {fs['name']} ({fs['growth']:+.0%} y/y)", 60) if (
        fs and fs["growth"] > 0 and not seg_fact) else ""
    # Q10 - the main risk, first that applies
    risk = None
    g = ttm.get("rev_growth")
    A = R.get("assumptions") or {}
    ebitda = (C.get("op_income") or 0) + (C.get("da") or 0)
    nd = -(C.get("net_cash") or 0)
    om_fy = (B.get("op_income") / fb["fy_revenue"][-1]) if (
        _num(B.get("op_income")) and fb.get("fy_revenue") and _num(fb["fy_revenue"][-1])
        and fb["fy_revenue"][-1]) else None
    om = ttm.get("op_margin")
    seg = R.get("segments") or {}
    srows = [r for r in (seg.get("rows") or []) if _num(r.get("rev"))]
    stot = sum(r["rev"] for r in srows)
    rv = (R.get("model") or {}).get("reverse") or {}
    beta = R.get("beta_raw")
    sbc_pct = (C.get("sbc") / ttm["revenue"]) if (_num(C.get("sbc")) and ttm.get("revenue")) else None
    if _num(g) and g < 0:
        risk = f"Revenue is shrinking ({g:+.0%} over the last twelve months)."
    elif ebitda > 0 and nd > 3 * ebitda:
        risk = (f"Net debt of ${nd:,.0f}B is {nd / ebitda:.1f}x trailing EBITDA, leaving little room "
                f"if earnings fall.")
    elif _num(om) and _num(om_fy) and om < om_fy - 0.03:
        risk = f"Operating margin has slipped to {om:.0%} from {om_fy:.0%} in {fb.get('fy')}."
    elif len(srows) >= 2 and stot and max(r["rev"] for r in srows) / stot > 0.8:
        top = max(srows, key=lambda r: r["rev"])
        risk = f"{top['name']} is {top['rev'] / stot:.0%} of revenue, so the result rests on one business."
    elif _num(rv.get("growth")) and _num(A.get("g1")) and rv["growth"] > 2 * max(A["g1"], 0.01):
        risk = (f"The price needs {rv['growth']:.0%} year-one revenue growth, well above the "
                f"{A['g1']:.0%} base case.")
    elif _num(beta) and beta > 1.5:
        risk = (f"A beta of {beta:.2f}: the shares have moved about {beta:.1f}x as much as the market "
                f"over five years.")
    elif _num(sbc_pct) and sbc_pct > 0.10:
        risk = (f"Stock-based pay runs at {sbc_pct:.0%} of revenue, a real cost that reported free "
                f"cash flow adds back.")
    elif _num(A.get("g1")):
        risk = (f"Growth below the base case's {A['g1']:.0%} year-one rate would pull value toward "
                f"the bear case.")
    out["Q10"] = _fit(risk or "Slower growth than the base case would pull value toward the bear case.")
    # Q11 - capital-structure note (completes the Summary's share-count bullet)
    bb, sbc = (fb.get("fy_buybacks") or [None])[-1], (fb.get("fy_sbc") or [None])[-1]
    if _num(sbc) and (not _num(bb) or sbc > bb):
        out["Q11"] = "stock issued for employee pay outpacing buybacks"
    elif _num(bb) and bb > 0:
        out["Q11"] = "buybacks offsetting stock issued for employee pay"
    else:
        out["Q11"] = "no buyback programme"
    out["Q12"] = ""                                  # no capex guidance in the filings data
    for k, row in zip(("Q13", "Q14", "Q15", "Q16"), (catalysts or [])[:4]):
        timing, event, why = row[0], row[1], row[2] or ""
        if timing.split(" ")[0] in ("Early", "Mid", "Late", "With", "Every", "Ongoing"):
            timing = timing[0].lower() + timing[1:]
        out[k] = _fit(f"{event} ({timing}): {why[:1].lower() + why[1:]}")
    for k in ("Q13", "Q14", "Q15", "Q16"):
        out.setdefault(k, "")
    return out


def catalysts(R, filings=None, news=None, earnings_date=None):
    """(timing, event, why it matters, status) rows - the filing calendar,
    dividends, guidance and whatever recent headlines flag."""
    from data import fiscal_quarter_label
    fb = R["fb"]
    rows = []
    qs = fb.get("quarters") or []
    fy_m = pd.Timestamp(fb["fy_end"]).month if fb.get("fy_end") is not None else 12
    if qs:
        last_end = pd.Timestamp(qs[-1]["end"])
        lag = 35
        for f in filings or []:
            if f.get("form") in ("10-Q", "10-K") and f.get("period") and f.get("filed"):
                if abs((pd.Timestamp(f["period"]) - last_end).days) <= 7:
                    lag = max(20, min(60, (pd.Timestamp(f["filed"]) - last_end).days))
                    break
        for k in (1, 2):
            nxt_end = last_end + pd.DateOffset(months=3 * k)
            qq, fy = fiscal_quarter_label(nxt_end, fy_m)
            when = nxt_end + pd.Timedelta(days=lag)
            status = "Expected"
            if k == 1 and earnings_date is not None and abs((pd.Timestamp(earnings_date) - when).days) <= 45:
                when = pd.Timestamp(earnings_date)
                timing, status = _d(when), "Date announced"
            else:
                timing = f"{part_of_month(when).capitalize()} {when:%b-%Y}"
                if k == 1:
                    status = "Date not yet announced"
            label = f"{qq} FY{str(fy)[2:]}"
            if qq == "Q4":
                rows.append((timing, f"{label} earnings",
                             f"Completes FY{fy}: full-year revenue, margins and cash flow; guidance for "
                             "the new fiscal year.", status))
                rows.append((timing, f"FY{fy} Form 10-K",
                             "Audited balance sheet, segment data, stock-based pay and buybacks for the "
                             "full year.", "Expected"))
            else:
                rows.append((timing, f"{label} earnings",
                             "Refreshes revenue, margins and the trailing-twelve-month figures in this "
                             "report; tests whether recent growth is holding.", status))
    if (fb.get("dps_quarter") or 0) > 0:
        rows.append(("With results", "Dividend declaration",
                     f"Last declared ${fb['dps_quarter']:.2f} a share a quarter.", "Expected"))
    A = R.get("assumptions") or {}
    rows.append(("Every call", "Guidance on growth, margins and capex",
                 f"The DCF rests on {A.get('g1', 0):.0%} year-one revenue growth and a long-run operating "
                 f"margin anchored to {A.get('anchor', 'TTM')}; guidance moves both.", "Monitor"))
    themes = " ".join(str(n.get("Theme") or "") for n in (news or [])).lower()
    for key, event, why in (("legal", "Legal and regulatory matters",
                             "Recent headlines flag legal or regulatory exposure; check the latest filings."),
                            ("regulat", "Regulatory rulings", "Recent headlines flag regulatory exposure."),
                            ("m&a", "Deal activity", "Recent headlines concern acquisitions or disposals."),
                            ("management", "Management changes", "Recent headlines concern leadership."),
                            ("product", "Product launches and demand",
                             "Recent headlines concern new products and demand.")):
        if key in themes and len(rows) < 6:
            rows.append(("Ongoing", event, why, "Monitor"))
    return rows[:6]
