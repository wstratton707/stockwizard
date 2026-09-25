"""The report's DCF, formula for formula.

The workbook (assets/report_template.xlsx, built from the reference
`GOOGL_5Y_Analysis UPDATED GOOD TEMPLATE.xlsx`) values a company by projecting
revenue, not free cash flow: revenue growth fades from year one to the terminal
rate by year ten, the operating margin, capex and D&A glide in a straight line
from today's level to a long-run level, and free cash flow is what is left -
NOPAT + D&A - capex - working capital (+ stock pay, unless it is treated as a
cash cost). Everything here mirrors a cell or a LET formula on the DCF and
Scenarios tabs, so the site, the deck and the downloaded file show the same
numbers to the cent. Change the workbook, change this - tests/test_valuation_model
checks both against the reference file's own calculated results.

Pure functions, no network. Money in any consistent unit (the workbook uses $B);
per-share results come out in the price's unit.
"""
from dataclasses import dataclass, replace
import math

YEARS = 10                     # the workbook's horizon; the growth fade divides by 9

# The reverse-DCF helper grids on the DCF tab (Q/R, S/T, U/V, rows 52-212).
GROWTH_GRID = [-0.10 + 0.005 * k for k in range(161)]
MARGIN_GRID = [0.05 + 0.005 * k for k in range(161)]
CAPEX_GRID = [0.40 - 0.0025 * k for k in range(161)]

# Scenarios F6:F9 and B10:D10.
SPREADS = {"growth": 0.06, "margin": 0.05, "wacc": 0.01, "tg": 0.005}
PROBS = (0.25, 0.5, 0.25)


@dataclass(frozen=True)
class Inputs:
    """The DCF tab's inputs (B5:B22) and derived inputs (B37:B48).

    Fractions as decimals; money in one unit (the workbook: $B); `shares` in the
    unit that turns that money into the price's unit (the workbook: billions)."""
    price: float                     # B5
    shares: float                    # B6
    wacc: float                      # B7 (= B34, the CAPM build)
    tg: float                        # B8 terminal growth
    g1: float                        # B9 year-1 revenue growth
    margin: float                    # B10 long-run operating margin
    years_norm: float = 5            # B11
    deduct_sbc: bool = False         # B12
    include_nonmkt: bool = True      # B13
    haircut: float = 0.2             # B15
    mid_year: bool = True            # B16
    lr_capex: float = 0.12           # B19 long-run capex % of revenue
    da_of_capex: float = 0.85        # B20 long-run D&A % of capex
    nwc: float = 0.0                 # B21 working capital per $ of new revenue
    capex_guide: float = 0.0         # B22 next-year capex ($), 0 = none
    tax: float = 0.21                # B31
    revenue: float = 0.0             # B37 TTM revenue
    margin0: float = 0.0             # B39 TTM operating margin
    capex0: float = 0.0              # B40 TTM capex % of revenue
    da0: float = 0.0                 # B41 TTM D&A % of revenue
    sbc0: float = 0.0                # B42 TTM SBC % of revenue
    net_cash: float = 0.0            # B44
    preferred: float = 0.0           # B45
    nonmkt: float = 0.0              # Financials!C54, before the haircut

    @property
    def sbc_addback(self):           # B43
        return 0.0 if self.deduct_sbc else self.sbc0

    @property
    def nonmkt_after_haircut(self):  # B46
        return self.nonmkt * (1 - self.haircut) if self.include_nonmkt else 0.0

    @property
    def non_operating(self):         # B47
        return self.net_cash - self.preferred + self.nonmkt_after_haircut

    @property
    def mid(self):                   # B48
        return 1 if self.mid_year else 0


def _glide(t, n):
    """How far along the straight line to the long-run level year t is."""
    return 1.0 if t >= n else t / n


def projection(i):
    """DCF!A52:O61 - the year-by-year table."""
    rows, prev = [], i.revenue
    for t in range(1, YEARS + 1):
        g = i.g1 - (i.g1 - i.tg) * (t - 1) / 9
        rev = prev * (1 + g)
        lp = _glide(t, i.years_norm)
        om = i.margin0 + (i.margin - i.margin0) * lp
        nopat = rev * om * (1 - i.tax)
        da = rev * (i.da0 + (i.lr_capex * i.da_of_capex - i.da0) * lp)
        capex = (i.capex_guide if (t == 1 and i.capex_guide > 0)
                 else rev * (i.capex0 + (i.lr_capex - i.capex0) * lp))
        wc = i.nwc * (rev - prev)
        sbc = rev * i.sbc_addback
        fcf = nopat + da - capex - wc + sbc
        period = t - 0.5 * i.mid
        factor = 1 / (1 + i.wacc) ** period
        rows.append({"year": t, "growth": g, "revenue": rev, "op_margin": om, "nopat": nopat,
                     "da": da, "capex": capex, "wc": wc, "sbc": sbc, "fcf": fcf,
                     "fcf_margin": fcf / rev if rev else None, "period": period,
                     "factor": factor, "pv": fcf * factor})
        prev = rev
    return rows


def fair_value(i, **kw):
    """The closed form every Scenarios / sensitivity / reverse-DCF cell uses
    (the LET formula): value per share for the inputs with `kw` overridden.
    None where the model is undefined (WACC at or below terminal growth)."""
    if kw:
        i = replace(i, **kw)
    if i.wacc <= i.tg or not i.shares:
        return None
    cum, pv, f = 0.0, 0.0, 0.0
    for t in range(1, YEARS + 1):
        g = i.g1 - (i.g1 - i.tg) * (t - 1) / 9
        if g <= -1:
            return None
        cum += math.log(1 + g)
        rev = i.revenue * math.exp(cum)
        prv = i.revenue * math.exp(cum - math.log(1 + g))
        lp = _glide(t, i.years_norm)
        omt = i.margin0 + (i.margin - i.margin0) * lp
        dat = i.da0 + (i.lr_capex * i.da_of_capex - i.da0) * lp
        cpx = (i.capex_guide / rev if (t == 1 and i.capex_guide > 0)
               else i.capex0 + (i.lr_capex - i.capex0) * lp)
        f = rev * (omt * (1 - i.tax) + dat - cpx + i.sbc_addback) - i.nwc * (rev - prv)
        pv += f / (1 + i.wacc) ** (t - 0.5 * i.mid)
    tv = f * (1 + i.tg) / (i.wacc - i.tg) / (1 + i.wacc) ** YEARS
    return (pv + tv + i.non_operating) / i.shares


def bridge(i, rows=None):
    """DCF!B64:B76 - enterprise value to value per share, from the table."""
    rows = rows or projection(i)
    pv = sum(r["pv"] for r in rows)
    last = rows[-1]
    tv = last["fcf"] * (1 + i.tg) / (i.wacc - i.tg) if i.wacc > i.tg else None
    pv_tv = tv / (1 + i.wacc) ** rows[-1]["year"] if tv is not None else None
    ev = pv + pv_tv if pv_tv is not None else None
    equity = ev + i.net_cash - i.preferred + i.nonmkt_after_haircut if ev is not None else None
    fv = equity / i.shares if (equity is not None and i.shares) else None
    closed = fair_value(i)
    return {"pv_explicit": pv, "terminal_value": tv, "pv_terminal": pv_tv,
            "enterprise_value": ev, "net_cash": i.net_cash, "preferred": i.preferred,
            "nonmkt_after_haircut": i.nonmkt_after_haircut, "equity_value": equity,
            "fair_value": fv, "upside": (fv / i.price - 1) if (fv and i.price) else None,
            "tv_share": (pv_tv / ev) if (ev and pv_tv is not None) else None,
            "model_check": (closed - fv) if (closed is not None and fv is not None) else None}


def scenarios(i, spreads=None, probs=PROBS):
    """Scenarios!B6:D14 - bear / base / bull move growth, margin, WACC and
    terminal growth together by the spreads in column F."""
    s = dict(SPREADS, **(spreads or {}))
    out = {}
    for name, k in (("bear", -1), ("base", 0), ("bull", 1)):
        kw = {"g1": i.g1 + k * s["growth"], "margin": i.margin + k * s["margin"],
              "wacc": i.wacc - k * s["wacc"], "tg": i.tg + k * s["tg"]}
        fv = fair_value(i, **kw)
        out[name] = dict(kw, fair_value=fv, upside=(fv / i.price - 1) if fv else None)
    for (name, p) in zip(("bear", "base", "bull"), probs):
        out[name]["probability"] = p
    fvs = [out[n]["fair_value"] for n in ("bear", "base", "bull")]
    pw = sum(p * v for p, v in zip(probs, fvs)) if all(v is not None for v in fvs) else None
    return {"bear": out["bear"], "base": out["base"], "bull": out["bull"],
            "prob_weighted": pw, "prob_weighted_upside": (pw / i.price - 1) if pw else None,
            "spreads": s, "probs": tuple(probs)}


def sensitivities(i):
    """The three Scenarios grids, each {"rows", "cols", "grid"}."""
    w_axis = [i.wacc + d for d in (-0.01, -0.005, 0.0, 0.005, 0.01)]
    tg_axis = [i.tg + d for d in (-0.01, -0.005, 0.0, 0.005, 0.01)]
    g_axis = [i.g1 + d for d in (-0.06, -0.03, 0.0, 0.03, 0.06)]
    m_axis = [i.margin + d for d in (-0.04, -0.02, 0.0, 0.02, 0.04)]
    cx_axis = [i.lr_capex + d for d in (-0.04, -0.02, 0.0, 0.02, 0.04)]
    n_axis = [max(1, i.years_norm - 2), max(1, i.years_norm - 1), i.years_norm,
              i.years_norm + 1, i.years_norm + 2]
    return {
        "wacc_tg": {"rows": w_axis, "cols": tg_axis,
                    "grid": [[fair_value(i, wacc=w, tg=g) for g in tg_axis] for w in w_axis]},
        "growth_margin": {"rows": g_axis, "cols": m_axis,
                          "grid": [[fair_value(i, g1=g, margin=m) for m in m_axis] for g in g_axis]},
        "capex_years": {"rows": cx_axis, "cols": n_axis,
                        "grid": [[fair_value(i, lr_capex=c, years_norm=n) for n in n_axis]
                                 for c in cx_axis]},
    }


def _match_le(arr, p):
    """Excel MATCH(p, arr, 1): binary search for the last value <= p on data
    assumed ascending. Returns a 0-based index, or None for #N/A."""
    lo, hi = 0, len(arr) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if arr[mid] is not None and arr[mid] <= p:
            lo = mid + 1
        else:
            hi = mid - 1
    return hi if hi >= 0 else None


def _solve_on_grid(xs, fs, p):
    """DCF!B79/B82/B85: linear interpolation between the grid points either
    side of the price, as the workbook does. None where Excel shows an error."""
    lo = _match_le(fs, p)
    if lo is None or lo + 1 >= len(xs):
        return None
    flo, fhi = fs[lo], fs[lo + 1]
    if flo is None or fhi is None or fhi == flo:
        return None
    return xs[lo] + (p - flo) / (fhi - flo) * (xs[lo + 1] - xs[lo])


def reverse(i):
    """What today's price implies (DCF!B79:B86), on the workbook's grids."""
    g_f = [fair_value(i, g1=x) for x in GROWTH_GRID]
    m_f = [fair_value(i, margin=x) for x in MARGIN_GRID]
    c_f = [fair_value(i, lr_capex=x) for x in CAPEX_GRID]
    g = _solve_on_grid(GROWTH_GRID, g_f, i.price)
    m = _solve_on_grid(MARGIN_GRID, m_f, i.price)
    c = _solve_on_grid(CAPEX_GRID, c_f, i.price)
    c_note = None
    if c is None:
        top = max((v for v in c_f if v is not None), default=None)
        c_note = ("Not reachable via capex alone" if (top is not None and i.price > top)
                  else "Above 40%")
    cagr = None
    if g is not None:
        s = sum(math.log(1 + (g - (g - i.tg) * (t - 1) / 9)) for t in range(1, YEARS + 1))
        cagr = math.exp(s) ** (1 / YEARS) - 1
    return {"growth": g, "cagr": cagr, "margin": m, "capex": c, "capex_note": c_note,
            "year10_revenue": (i.revenue * (1 + cagr) ** 10) if cagr is not None else None}


def checks(i, rows, br, extra=None):
    """DCF!B92:B109 - does the answer make sense, and the integrity roll-up.
    `extra` carries the data checks the workbook runs on other tabs (TTM =
    sum of quarters, net cash arithmetic, latest price = last price row):
    {"ttm_ties": bool, "net_cash_ties": bool, "price_is_latest": bool}."""
    last = rows[-1]
    tv = br["terminal_value"]
    ebitda10 = last["revenue"] * last["op_margin"] + last["da"]
    ev_ebitda = tv / ebitda10 if (tv is not None and ebitda10) else None
    reinvest = (last["capex"] - last["da"] + last["wc"]) / last["nopat"] if last["nopat"] else None
    ronic = (i.tg / reinvest) if reinvest else None
    extra = extra or {}
    items = [
        ("Terminal growth below WACC", "✓" if i.tg < i.wacc else "✗ fix"),
        ("TV share of EV under 75%",
         "✓" if (br["tv_share"] is not None and br["tv_share"] < 0.75) else "⚠ high"),
        ("Terminal EV/EBITDA in 8–25x",
         "✓" if (ev_ebitda is not None and 8 <= ev_ebitda <= 25) else "⚠ review"),
        ("Return on new capital between WACC and 50%",
         "✓" if (ronic is not None and i.wacc <= ronic <= 0.5) else "⚠ review"),
        ("Closed-form recompute = table",
         "✓" if (br["model_check"] is not None and abs(br["model_check"]) < 0.01) else "✗ fix"),
        ("TTM revenue = sum of quarters", "✓" if extra.get("ttm_ties", True) else "✗ fix"),
        ("Net cash = cash & securities − debt", "✓" if extra.get("net_cash_ties", True) else "✗ fix"),
        ("Latest price is the last Data row", "✓" if extra.get("price_is_latest", True) else "✗ fix"),
    ]
    fails = sum(1 for _, v in items if v.startswith("✗"))
    warns = sum(1 for _, v in items if v.startswith("⚠"))
    integrity = (f"✗ {fails} failed" if fails else
                 f"⚠ {warns} to review" if warns else "✓ all checks pass")
    return {"items": items, "integrity": integrity, "terminal_ev_ebitda": ev_ebitda,
            "terminal_ev_fcf": (tv / last["fcf"]) if (tv is not None and last["fcf"]) else None,
            "terminal_ev_sales": (tv / last["revenue"]) if (tv is not None and last["revenue"]) else None,
            "terminal_ev_nopat": (tv / last["nopat"]) if (tv is not None and last["nopat"]) else None,
            "reinvestment_rate": reinvest, "return_on_new_capital": ronic}


def verdict(pw_upside, sc=None):
    """Summary!A5:A6 - the model's view in words, never a rating."""
    if pw_upside is None:
        head = "No model value"
    elif pw_upside < -0.15:
        head = "Priced above model value"
    elif pw_upside > 0.15:
        head = "Priced below model value"
    else:
        head = "Near model value"
    sub = None
    if sc:
        if (sc["bull"]["upside"] or -1) > 0 and not ((sc["bear"]["upside"] or -1) > 0):
            sub = "Only the bull case clears today's price"
        elif (sc["bear"]["upside"] or -1) > 0:
            sub = "Even the bear case is above the price"
        else:
            sub = "No scenario reaches today's price"
    return head, sub


def wacc_build(rf10, beta_raw, erp, kd, tax, debt, price, shares):
    """DCF!B25:B34 - CAPM with a Blume-adjusted beta at market weights."""
    beta_adj = 2 / 3 * beta_raw + 1 / 3
    ke = rf10 + beta_adj * erp
    mcap = price * shares
    wd = debt / (debt + mcap) if (debt is not None and (debt + mcap) > 0) else 0.0
    wacc = (1 - wd) * ke + wd * kd * (1 - tax)
    return {"rf10": rf10, "beta_raw": beta_raw, "beta_adj": beta_adj, "erp": erp,
            "cost_of_equity": ke, "cost_of_debt": kd, "tax": tax,
            "weight_debt": wd, "weight_equity": 1 - wd, "wacc": wacc}


def scaled_spreads(g1, margin):
    """Bear/bull swings sized to the company: a quarter of the margin (1-5
    points) and 40% of year-1 growth (2-6 points), to the nearest half point.
    The reference's fixed 5-point margin swing is Alphabet-sized - on a 4%
    retail margin it takes the bear case into losses."""
    def half(x):
        return round(x * 200) / 200
    return {"growth": min(0.06, max(0.02, half(abs(g1) * 0.4))),
            "margin": min(0.05, max(0.01, half(abs(margin) * 0.25))),
            "wacc": 0.01, "tg": 0.005}


def run(i, extra=None, spreads=None):
    """Everything the reports show, from one set of inputs."""
    rows = projection(i)
    br = bridge(i, rows)
    sc = scenarios(i, spreads)
    rv = reverse(i)
    ck = checks(i, rows, br, extra)
    head, sub = verdict(sc["prob_weighted_upside"], sc)
    return {"inputs": i, "projection": rows, "bridge": br, "scenarios": sc,
            "sensitivity": sensitivities(i), "reverse": rv, "checks": ck,
            "verdict": head, "verdict_detail": sub}
