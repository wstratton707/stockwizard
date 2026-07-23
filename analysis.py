import numpy as np
import pandas as pd


# ── Fundamentals & valuation ──────────────────────────────────────────────────

def _fin_val(df, field, i=0):
    """Safely pull a numeric line item from a statement DataFrame (newest = row 0)."""
    try:
        if df is None or field not in df.columns or i >= len(df):
            return None
        v = df.iloc[i][field]
        return float(v) if v is not None and not pd.isna(v) else None
    except Exception:
        return None


def _reverse_dcf_growth(market_cap, base_earnings, years=10, discount=0.09,
                        terminal_growth=0.025):
    """
    Reverse DCF: the annual growth rate the market is pricing into `base_earnings`
    (a 2-stage model, `years` of explicit growth + a terminal value). Returns the
    implied growth as a decimal, or None if it can't be solved sensibly.
    Intentionally a *reverse* model — it states what's priced in rather than
    asserting a single "fair value", which is more honest about the assumptions.
    """
    if not market_cap or not base_earnings or base_earnings <= 0 or discount <= terminal_growth:
        return None

    def pv(g):
        total, e = 0.0, base_earnings
        for t in range(1, years + 1):
            e *= (1 + g)
            total += e / (1 + discount) ** t
        terminal = e * (1 + terminal_growth) / (discount - terminal_growth)
        return total + terminal / (1 + discount) ** years

    lo, hi = -0.50, 0.60
    if pv(lo) > market_cap or pv(hi) < market_cap:
        return None
    for _ in range(60):  # bisection
        mid = (lo + hi) / 2
        if pv(mid) < market_cap:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 4)


def _piotroski_f_score(inc, bal, cf):
    """
    Piotroski F-Score (0-9): a 9-point fundamental-quality test comparing the two
    most recent fiscal years (profitability, leverage/liquidity, efficiency).
    Returns (score, n_assessable). score is None when too few signals can be
    evaluated — e.g. banks that don't report current assets / gross profit.
    """
    def v(df, field, i): return _fin_val(df, field, i)
    points = {"n": 0, "ok": 0}

    def signal(cond):
        if cond is None:           # input unavailable — skip, don't penalise
            return
        points["n"] += 1
        if cond:
            points["ok"] += 1

    ni0,  ni1  = v(inc, "net_income_loss", 0), v(inc, "net_income_loss", 1)
    a0,   a1   = v(bal, "assets", 0),          v(bal, "assets", 1)
    ocf0       = v(cf,  "net_cash_flow_from_operating_activities", 0)
    ltd0, ltd1 = v(bal, "long_term_debt", 0),  v(bal, "long_term_debt", 1)
    ca0,  ca1  = v(bal, "current_assets", 0),  v(bal, "current_assets", 1)
    cl0,  cl1  = v(bal, "current_liabilities", 0), v(bal, "current_liabilities", 1)
    sh0,  sh1  = v(inc, "diluted_shares", 0),  v(inc, "diluted_shares", 1)
    gp0,  gp1  = v(inc, "gross_profit", 0),    v(inc, "gross_profit", 1)
    rv0,  rv1  = v(inc, "revenues", 0),        v(inc, "revenues", 1)

    def safediv(n, d): return (n / d) if (n is not None and d) else None
    roa0, roa1 = safediv(ni0, a0), safediv(ni1, a1)
    lev0, lev1 = safediv(ltd0, a0), safediv(ltd1, a1)
    cr0,  cr1  = safediv(ca0, cl0), safediv(ca1, cl1)
    gm0,  gm1  = safediv(gp0, rv0), safediv(gp1, rv1)
    at0,  at1  = safediv(rv0, a0),  safediv(rv1, a1)
    cmp = lambda x, y: (x > y) if (x is not None and y is not None) else None

    signal(ni0 > 0 if ni0 is not None else None)     # 1  positive net income
    signal(ocf0 > 0 if ocf0 is not None else None)   # 2  positive operating cash flow
    signal(cmp(roa0, roa1))                          # 3  rising ROA
    signal(ocf0 > ni0 if (ocf0 is not None and ni0 is not None) else None)  # 4  accruals (CFO > NI)
    signal(cmp(lev1, lev0))                          # 5  falling leverage
    signal(cmp(cr0, cr1))                            # 6  rising current ratio
    signal(sh0 <= sh1 * 1.01 if (sh0 is not None and sh1 is not None) else None)  # 7  no dilution
    signal(cmp(gm0, gm1))                            # 8  rising gross margin
    signal(cmp(at0, at1))                            # 9  rising asset turnover

    if points["n"] < 6:
        return None, points["n"]
    return points["ok"], points["n"]


def _altman_z(rev, ebit, ca, cl, assets, total_liab, retained, mcap):
    """
    Classic Altman Z-Score for non-financial public firms. Returns (z, zone) with
    zone in {safe (>2.99), grey (1.81-2.99), distress (<1.81)}, or (None, None)
    when inputs are missing (it isn't meaningful for banks/insurers).
    """
    if not assets or not total_liab or mcap is None:
        return None, None
    if any(x is None for x in (rev, ebit, ca, cl, retained)):
        return None, None
    x1 = (ca - cl) / assets      # working capital / assets
    x2 = retained / assets       # retained earnings / assets
    x3 = ebit / assets           # EBIT / assets
    x4 = mcap / total_liab       # market value of equity / total liabilities
    x5 = rev / assets            # asset turnover
    z  = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
    zone = "safe" if z > 2.99 else ("grey" if z >= 1.81 else "distress")
    return round(z, 2), zone


def compute_fundamentals(financials, market_cap=None, price=None):
    """
    Turn raw Polygon statements (from data.fetch_financials) + market cap into a
    structured analytics dict: margins, returns, leverage, growth, valuation
    multiples, a reverse-DCF implied growth, and 4-period trend arrays.

    Pure function (no network) so it is cheap to test. Returns {"ok": False} when
    the income statement is missing.
    """
    inc = financials.get("income_statement") if financials else None
    bal = financials.get("balance_sheet") if financials else None
    cf  = financials.get("cash_flow_statement") if financials else None
    if inc is None or len(inc) == 0:
        return {"ok": False}

    mcap = float(market_cap) if isinstance(market_cap, (int, float)) else None

    rev   = _fin_val(inc, "revenues")
    gp    = _fin_val(inc, "gross_profit")
    oi    = _fin_val(inc, "operating_income_loss")
    ni    = _fin_val(inc, "net_income_loss")
    eps   = _fin_val(inc, "diluted_earnings_per_share")
    rnd   = _fin_val(inc, "research_and_development")
    eq    = _fin_val(bal, "equity")
    asts  = _fin_val(bal, "assets")
    ca    = _fin_val(bal, "current_assets")
    cl    = _fin_val(bal, "current_liabilities")
    ltd   = _fin_val(bal, "long_term_debt")
    ocf   = _fin_val(cf, "net_cash_flow_from_operating_activities")

    def pct(n, d):   return round(n / d * 100, 1) if (n is not None and d) else None
    def ratio(n, d): return round(n / d, 2) if (n is not None and d) else None

    margins = {"gross": pct(gp, rev), "operating": pct(oi, rev), "net": pct(ni, rev)}
    returns = {"roe": pct(ni, eq), "roa": pct(ni, asts)}
    leverage = {"current_ratio": ratio(ca, cl), "debt_to_equity": ratio(ltd, eq)}

    # Growth — YoY (period 1) and CAGR over the available window
    def yoy(field):
        cur, prev = _fin_val(inc, field, 0), _fin_val(inc, field, 1)
        return round((cur / prev - 1) * 100, 1) if (cur and prev and prev > 0) else None

    def cagr(field):
        cur = _fin_val(inc, field, 0)
        n   = len(inc) - 1
        old = _fin_val(inc, field, n)
        if cur and old and old > 0 and n >= 1:
            return round(((cur / old) ** (1 / n) - 1) * 100, 1)
        return None

    growth = {"revenue_yoy": yoy("revenues"), "net_income_yoy": yoy("net_income_loss"),
              "eps_yoy": yoy("diluted_earnings_per_share"),
              "revenue_cagr": cagr("revenues"), "eps_cagr": cagr("diluted_earnings_per_share")}

    valuation = {
        "pe": ratio(mcap, ni) if (mcap and ni and ni > 0) else None,
        "ps": ratio(mcap, rev) if (mcap and rev) else None,
        "pb": ratio(mcap, eq) if (mcap and eq and eq > 0) else None,
        "earnings_yield": pct(ni, mcap) if (mcap and ni) else None,
    }

    implied_growth = _reverse_dcf_growth(mcap, ni) if (mcap and ni) else None

    # ── Free cash flow (the extra EDGAR fields are absent on the Polygon path,
    #     so every derived metric here degrades gracefully to None) ────────────
    capex = _fin_val(cf, "capex")
    fcf   = (ocf - capex) if (ocf is not None and capex is not None) else None
    fcf_block = {
        "fcf":        fcf,
        "fcf_margin": pct(fcf, rev),
        "fcf_yield":  pct(fcf, mcap) if (fcf is not None and mcap) else None,
    }

    # ── EV / EBITDA ───────────────────────────────────────────────────────────
    da         = _fin_val(inc, "depreciation_amortization")
    cash_bal   = _fin_val(bal, "cash")
    debt_cur   = _fin_val(bal, "debt_current")
    total_debt = (sum(v for v in (ltd, debt_cur) if v is not None)
                  if (ltd is not None or debt_cur is not None) else None)
    ebitda     = (oi + da) if (oi is not None and da is not None) else None
    ev         = (mcap + (total_debt or 0) - (cash_bal or 0)) if mcap else None
    ev_ebitda  = ratio(ev, ebitda) if (ev is not None and ebitda and ebitda > 0) else None

    # ── Quality scores ────────────────────────────────────────────────────────
    f_score, f_basis = _piotroski_f_score(inc, bal, cf)
    z_score, z_zone  = _altman_z(rev, oi, ca, cl, asts,
                                 _fin_val(bal, "liabilities"),
                                 _fin_val(bal, "retained_earnings"), mcap)
    quality = {"f_score": f_score, "f_basis": f_basis,
               "z_score": z_score, "z_zone": z_zone}

    # Trend arrays oldest -> newest for charting
    order = list(range(len(inc) - 1, -1, -1))

    def _fcf_at(i):
        o, c = (_fin_val(cf, "net_cash_flow_from_operating_activities", i),
                _fin_val(cf, "capex", i))
        return (o - c) if (o is not None and c is not None) else None

    trend = {
        "periods":          [str(inc.iloc[i]["Period"])[:7] for i in order],
        "revenue":          [_fin_val(inc, "revenues", i) for i in order],
        "net_income":       [_fin_val(inc, "net_income_loss", i) for i in order],
        "eps":              [_fin_val(inc, "diluted_earnings_per_share", i) for i in order],
        "operating_margin": [pct(_fin_val(inc, "operating_income_loss", i),
                                  _fin_val(inc, "revenues", i)) for i in order],
        "fcf":              [_fcf_at(i) for i in order],
    }

    return {
        "ok": True,
        "as_of": str(inc.iloc[0]["Period"])[:10] if "Period" in inc.columns else "latest",
        "market_cap": mcap, "price": price,
        "income": {"revenue": rev, "gross_profit": gp, "operating_income": oi,
                   "net_income": ni, "eps_diluted": eps, "rnd": rnd},
        "balance": {"assets": asts, "liabilities": _fin_val(bal, "liabilities"),
                    "equity": eq, "current_assets": ca, "current_liabilities": cl,
                    "long_term_debt": ltd},
        "cashflow": {"operating": ocf,
                     "investing": _fin_val(cf, "net_cash_flow_from_investing_activities"),
                     "financing": _fin_val(cf, "net_cash_flow_from_financing_activities")},
        "margins": margins, "returns": returns, "leverage": leverage,
        "growth": growth, "valuation": valuation,
        "fcf": fcf_block, "ev_ebitda": ev_ebitda, "quality": quality,
        "implied_growth": implied_growth,
        # Net debt (total debt − cash) and enterprise value power the DCF's
        # EV→equity bridge; None when the balance-sheet fields are unavailable.
        "net_debt": (((total_debt or 0) - (cash_bal or 0))
                     if (total_debt is not None or cash_bal is not None) else None),
        "ev": ev,
        "trend": trend,
        "source": financials.get("source", "Polygon") if isinstance(financials, dict) else "Polygon",
    }


def dcf_valuation(fundamentals, price, wacc=0.09, terminal_growth=0.025, years=10):
    """Two-stage unlevered DCF (FCFF) → fair value per share + upside/downside.

    Transparent by construction: returns every assumption, the year-by-year
    projection, the enterprise-value→equity bridge, bull/base/bear scenarios, a
    WACC × terminal-growth sensitivity grid, and the FCF growth the market is
    implying at today's price. Shares are derived from market cap ÷ price so no
    separate share count is needed and the result ties out to the quoted price.

    Assumptions mirror the reverse-DCF (_reverse_dcf_growth): 9% WACC, 2.5%
    terminal growth, 10-year horizon. Base FCF is the mean of up to the last 3
    positive annual FCF values (normalises a single noisy capex year). Stage-1
    growth fades linearly to the terminal rate over the horizon.

    Returns {"ok": False, "reason": ...} when a credible DCF can't be built
    (missing price/market cap, or no positive free cash flow to project)."""
    if not fundamentals or not fundamentals.get("ok"):
        return {"ok": False, "reason": "fundamentals unavailable"}
    if not price or price <= 0:
        return {"ok": False, "reason": "no price"}
    mcap = fundamentals.get("market_cap")
    if not mcap or mcap <= 0:
        return {"ok": False, "reason": "no market cap"}
    if wacc <= terminal_growth:
        return {"ok": False, "reason": "WACC must exceed terminal growth"}

    # Base FCF: average of up to the last 3 positive annual FCF values (the trend
    # array is oldest→newest); fall back to the single latest FCF.
    fcf_hist = [x for x in (fundamentals.get("trend", {}).get("fcf") or [])
                if isinstance(x, (int, float)) and x > 0]
    base_fcf = (sum(fcf_hist[-3:]) / len(fcf_hist[-3:])) if fcf_hist else None
    if base_fcf is None:
        latest = fundamentals.get("fcf", {}).get("fcf")
        base_fcf = latest if (isinstance(latest, (int, float)) and latest > 0) else None
    if not base_fcf or base_fcf <= 0:
        return {"ok": False, "reason": "no positive free cash flow to project"}

    net_debt = fundamentals.get("net_debt") or 0.0
    shares   = mcap / price

    # Base-case stage-1 growth: FCF CAGR if it's sane, else revenue CAGR, else 8%.
    # Clamped so the model can't assume implausible decade-long hyper-growth.
    fcf_cagr = None
    if len(fcf_hist) >= 2 and fcf_hist[0] > 0:
        n = len(fcf_hist) - 1
        fcf_cagr = (fcf_hist[-1] / fcf_hist[0]) ** (1 / n) - 1
    rev_cagr = fundamentals.get("growth", {}).get("revenue_cagr")
    g_base = (fcf_cagr if fcf_cagr is not None
              else (rev_cagr / 100.0 if isinstance(rev_cagr, (int, float)) else 0.08))
    g_base = max(terminal_growth + 0.005, min(g_base, 0.20))

    def _fair_value(g1, w=wacc, tg=terminal_growth):
        """Enterprise value → equity → per-share for a given stage-1 growth."""
        if w <= tg:
            return None, None
        proj, fcf, pv_sum = [], base_fcf, 0.0
        for t in range(1, years + 1):
            g_t = g1 + (tg - g1) * (t - 1) / (years - 1) if years > 1 else tg
            fcf = fcf * (1 + g_t)
            pv  = fcf / (1 + w) ** t
            pv_sum += pv
            proj.append({"year": t, "growth": g_t, "fcf": fcf, "pv": pv})
        term_val = proj[-1]["fcf"] * (1 + tg) / (w - tg)
        pv_term  = term_val / (1 + w) ** years
        ev       = pv_sum + pv_term
        equity   = ev - net_debt
        fv       = equity / shares if shares else None
        return fv, {"projection": proj, "pv_explicit": pv_sum, "terminal_value": term_val,
                    "pv_terminal": pv_term, "enterprise_value": ev, "equity_value": equity}

    fv_base, detail = _fair_value(g_base)

    def _scn(g1):
        fv, _ = _fair_value(g1)
        return {"growth": g1, "fair_value": fv,
                "upside": (fv / price - 1) if fv else None}

    scenarios = {
        "bear": _scn(max(terminal_growth + 0.005, g_base - 0.03)),
        "base": _scn(g_base),
        "bull": _scn(min(0.25, g_base + 0.03)),
    }

    # WACC × terminal-growth sensitivity of the base-case fair value per share.
    wacc_axis = [round(wacc + d, 4) for d in (-0.02, -0.01, 0.0, 0.01, 0.02)]
    tg_axis   = [round(terminal_growth + d, 4) for d in (-0.01, -0.005, 0.0, 0.005, 0.01)]
    sensitivity = []
    for w in wacc_axis:
        row = []
        for tg in tg_axis:
            fv, _ = _fair_value(g_base, w=w, tg=tg)
            row.append(fv)
        sensitivity.append(row)

    # Reverse-solve the stage-1 FCF growth the market is pricing in at today's price.
    implied_growth = None
    lo, hi = -0.20, 0.50
    fv_lo, _ = _fair_value(lo)
    fv_hi, _ = _fair_value(hi)
    if fv_lo is not None and fv_hi is not None and fv_lo <= price <= fv_hi:
        for _ in range(60):
            mid = (lo + hi) / 2
            fv_mid, _ = _fair_value(mid)
            if fv_mid < price:
                lo = mid
            else:
                hi = mid
        implied_growth = (lo + hi) / 2

    return {
        "ok": True,
        "price": price, "fair_value": fv_base,
        "upside": (fv_base / price - 1) if fv_base else None,
        "wacc": wacc, "terminal_growth": terminal_growth, "years": years,
        "base_fcf": base_fcf, "base_growth": g_base, "net_debt": net_debt,
        "shares": shares, "market_implied_growth": implied_growth,
        "enterprise_value": detail["enterprise_value"],
        "equity_value": detail["equity_value"],
        "pv_explicit": detail["pv_explicit"],
        "terminal_value": detail["terminal_value"],
        "pv_terminal": detail["pv_terminal"],
        "projection": detail["projection"],
        "scenarios": scenarios,
        "sensitivity": {"wacc_axis": wacc_axis, "tg_axis": tg_axis, "grid": sensitivity},
    }


def _score_band(value, bands, higher_better=True):
    """Map a metric to a 0-100 sub-score via ordered (cutoff, score) bands.

    bands run best→worst. higher_better: value earns a band's score when it is
    >= that cutoff (cutoffs descending). Otherwise when value <= cutoff (cutoffs
    ascending). The final band acts as the floor. None-in → None-out."""
    if value is None:
        return None
    for cutoff, score in bands:
        if (higher_better and value >= cutoff) or (not higher_better and value <= cutoff):
            return score
    return bands[-1][1]


def compute_scorecard(fundamentals=None, dcf=None, momentum_score=None,
                      risk=None, consensus=None):
    """Blend factor sub-scores into a 0-100 composite quality/attractiveness score.

    Descriptive by design — it characterises the stock's *profile* (valuation,
    growth, profitability, financial health, momentum, risk, sentiment); it is not
    a buy/sell recommendation (the sourced analyst verdict is surfaced separately).
    Every factor is None-safe and the composite averages only what it can compute.

    Inputs are already-derived pieces so this stays a pure, testable function:
      fundamentals  → compute_fundamentals() dict
      dcf           → dcf_valuation() dict (for the valuation factor's upside)
      momentum_score→ 0-100 technical-posture score
      risk          → {"sharpe","vol","max_dd"} (vol/max_dd as decimals)
      consensus     → consensus_from_recommendation() dict
    Returns {"ok", "composite", "label", "factors":[{name,score,grade,detail}...]}"""
    f = fundamentals if (fundamentals and fundamentals.get("ok")) else {}
    val_, marg, ret_, lev, grw, q, fcf = (
        f.get("valuation", {}), f.get("margins", {}), f.get("returns", {}),
        f.get("leverage", {}), f.get("growth", {}), f.get("quality", {}), f.get("fcf", {}))

    def _avg(vals):
        xs = [v for v in vals if v is not None]
        return sum(xs) / len(xs) if xs else None

    factors = []   # (name, score, detail, weight)

    # ── Valuation — DCF upside first, free-cash-flow yield as support ──────────
    val_parts, val_bits = [], []
    if dcf and dcf.get("ok") and dcf.get("upside") is not None:
        u = dcf["upside"]
        val_parts.append(_score_band(u, [(0.30, 92), (0.15, 80), (0.0, 62),
                                          (-0.15, 45), (-0.30, 32), (-0.60, 18), (-1e9, 8)]))
        val_bits.append(f"DCF {u * 100:+.0f}%")
    if fcf.get("fcf_yield") is not None:
        val_parts.append(_score_band(fcf["fcf_yield"], [(6, 90), (4, 75), (2.5, 60),
                                                        (1, 45), (0, 30), (-1e9, 15)]))
        val_bits.append(f"FCF yield {fcf['fcf_yield']:.1f}%")
    v_score = _avg(val_parts)
    if v_score is not None:
        factors.append(("Valuation", v_score, ", ".join(val_bits), 0.20))

    # ── Growth ────────────────────────────────────────────────────────────────
    g_band = lambda x: _score_band(x, [(20, 95), (15, 85), (10, 72), (5, 55), (0, 38), (-1e9, 15)])
    g_score = _avg([g_band(grw.get("revenue_cagr")), g_band(grw.get("eps_cagr")),
                    g_band(grw.get("revenue_yoy"))])
    if g_score is not None:
        gb = [f"Rev CAGR {grw['revenue_cagr']:.0f}%"] if grw.get("revenue_cagr") is not None else []
        if grw.get("eps_cagr") is not None: gb.append(f"EPS CAGR {grw['eps_cagr']:.0f}%")
        factors.append(("Growth", g_score, ", ".join(gb), 0.15))

    # ── Profitability ─────────────────────────────────────────────────────────
    p_score = _avg([
        _score_band(marg.get("net"),   [(20, 90), (15, 80), (10, 66), (5, 48), (0, 28), (-1e9, 12)]),
        _score_band(ret_.get("roe"),   [(20, 90), (15, 80), (10, 65), (5, 48), (0, 28), (-1e9, 12)]),
        _score_band(marg.get("gross"), [(50, 88), (40, 78), (30, 66), (20, 52), (10, 38), (-1e9, 20)]),
    ])
    if p_score is not None:
        pb = [f"Net margin {marg['net']:.0f}%"] if marg.get("net") is not None else []
        if ret_.get("roe") is not None: pb.append(f"ROE {ret_['roe']:.0f}%")
        factors.append(("Profitability", p_score, ", ".join(pb), 0.15))

    # ── Financial health ──────────────────────────────────────────────────────
    h_score = _avg([
        _score_band(lev.get("current_ratio"),  [(2, 88), (1.5, 74), (1.2, 62), (1.0, 50), (-1e9, 30)]),
        _score_band(lev.get("debt_to_equity"), [(0.3, 88), (0.6, 76), (1.0, 62), (2.0, 42), (1e9, 20)],
                    higher_better=False),
        _score_band(q.get("z_score"),          [(3.0, 88), (1.8, 55), (-1e9, 25)]),
        (q["f_score"] / 9 * 100 if q.get("f_score") is not None else None),
    ])
    if h_score is not None:
        hb = []
        if q.get("f_score") is not None: hb.append(f"F-Score {q['f_score']}/9")
        if lev.get("debt_to_equity") is not None: hb.append(f"D/E {lev['debt_to_equity']:.2f}")
        factors.append(("Financial Health", h_score, ", ".join(hb), 0.15))

    # ── Momentum (technical posture) ──────────────────────────────────────────
    if momentum_score is not None:
        factors.append(("Momentum", float(momentum_score),
                        f"Technical posture {int(momentum_score)}/100", 0.12))

    # ── Risk (higher score = safer / better risk-adjusted) ────────────────────
    r = risk or {}
    r_score = _avg([
        _score_band(r.get("sharpe"),  [(2, 92), (1.5, 82), (1.0, 72), (0.5, 58), (0, 40), (-1e9, 20)]),
        _score_band(r.get("vol"),     [(0.15, 85), (0.25, 70), (0.35, 55), (0.50, 38), (1e9, 20)],
                    higher_better=False),
        _score_band(r.get("max_dd"),  [(-0.10, 85), (-0.20, 68), (-0.35, 48), (-0.50, 30), (-1e9, 15)]),
    ])
    if r_score is not None:
        rb = []
        if r.get("sharpe") is not None: rb.append(f"Sharpe {r['sharpe']:.2f}")
        if r.get("max_dd") is not None: rb.append(f"Max DD {r['max_dd'] * 100:.0f}%")
        factors.append(("Risk", r_score, ", ".join(rb), 0.12))

    # ── Sentiment (Wall-Street consensus) ─────────────────────────────────────
    if consensus and consensus.get("score") is not None:
        s_score = max(5.0, min(95.0, 50 + consensus["score"] * 22))
        factors.append(("Sentiment", s_score,
                        f"Analysts: {consensus['verdict']} ({consensus['total']})", 0.11))

    if not factors:
        return {"ok": False}

    tw = sum(w for _, _, _, w in factors)
    composite = round(sum(sc * w for _, sc, _, w in factors) / tw)
    label = next(l for c, l in [(75, "Strong overall profile"), (60, "Above-average profile"),
                                (45, "Mixed profile"), (30, "Below-average profile"),
                                (-1, "Weak profile")] if composite >= c)

    def _grade(sc):
        return ("Strong" if sc >= 80 else "Above-avg" if sc >= 65 else
                "Average" if sc >= 50 else "Below-avg" if sc >= 35 else "Weak")

    return {
        "ok": True, "composite": composite, "label": label,
        "factors": [{"name": n, "score": round(sc), "grade": _grade(sc), "detail": d}
                    for n, sc, d, _ in factors],
    }


def detect_support_resistance(df, window=20, num_levels=5):
    highs = df["High"].values
    lows  = df["Low"].values
    n     = len(highs)
    resistance, support = [], []
    for i in range(window, n - window):
        if highs[i] == max(highs[i - window: i + window + 1]):
            resistance.append(round(float(highs[i]), 2))
        if lows[i] == min(lows[i - window: i + window + 1]):
            support.append(round(float(lows[i]), 2))

    def cluster(levels, tol=0.01):
        levels = sorted(set(levels), reverse=True)
        clustered = []
        for lv in levels:
            if not clustered or abs(lv - clustered[-1]) / max(clustered[-1], 1) > tol:
                clustered.append(lv)
        return clustered[:num_levels]

    return cluster(resistance), cluster(support)


def build_correlation_matrix(df, benchmark_tickers=None):
    cols = {"Stock": df["Daily_Return"]}
    if benchmark_tickers:
        for b in benchmark_tickers:
            col = f"{b}_Return"
            if col in df.columns:
                cols[b.replace("^", "").replace("I:", "")] = df[col]
    return pd.DataFrame(cols).dropna().corr()


def run_monte_carlo(df, n_simulations=1000, forecast_days=252, log=print):
    log(f"Monte Carlo: {n_simulations:,} paths x {forecast_days} trading days...")
    returns    = df["Daily_Return"].dropna()
    mu, sigma  = returns.mean(), returns.std()
    last_price = df["Close"].iloc[-1]
    rand          = np.random.standard_normal((forecast_days, n_simulations))
    daily_factors = np.exp((mu - 0.5 * sigma ** 2) + sigma * rand)
    paths         = np.zeros((forecast_days + 1, n_simulations))
    paths[0]      = last_price
    for t in range(1, forecast_days + 1):
        paths[t] = paths[t - 1] * daily_factors[t - 1]
    fp   = paths[-1]
    pcts = np.percentile(fp, [5, 25, 50, 75, 95])
    summary = {
        "Last Price":              round(last_price, 2),
        "Forecast Horizon (days)": forecast_days,
        "Simulations":             n_simulations,
        "Mean Forecast":           round(fp.mean(), 2),
        "Median (P50)":            round(pcts[2], 2),
        "Bear Case (P5)":          round(pcts[0], 2),
        "Low Case (P25)":          round(pcts[1], 2),
        "Bull Case (P75)":         round(pcts[3], 2),
        "Best Case (P95)":         round(pcts[4], 2),
        "Prob. of Gain":           f"{(fp > last_price).mean()*100:.1f}%",
        "Ann. Volatility":         f"{sigma * np.sqrt(252) * 100:.2f}%",
    }
    log(f"   P5 ${summary['Bear Case (P5)']:,.2f}  "
        f"P50 ${summary['Median (P50)']:,.2f}  "
        f"P95 ${summary['Best Case (P95)']:,.2f}")
    return pd.DataFrame(paths), summary


# ── Custom Forecast (GARCH + ML + Monte Carlo) ────────────────────────────────


def _fit_garch_volatility(returns, forecast_days):
    """
    Fit GARCH(1,1) on daily returns and produce multi-step ahead volatility forecasts.
    Returns an array of daily sigma values (length = forecast_days).
    """
    from arch import arch_model
    r_pct = returns * 100  # scale for numerical stability
    model = arch_model(r_pct, vol="Garch", p=1, q=1, dist="normal", rescale=False)
    res   = model.fit(disp="off", show_warning=False)

    omega = float(res.params["omega"])
    alpha = float(res.params["alpha[1]"])
    beta  = float(res.params["beta[1]"])

    # Last conditional variance (pct²)
    h0 = float(res.conditional_volatility.iloc[-1]) ** 2

    # If alpha+beta >= 1 (IGARCH), long-run variance is undefined.
    # Fall back to holding current variance constant.
    long_run_var = omega / (1 - alpha - beta) if (alpha + beta) < 1 else h0

    # Correct GARCH(1,1) multi-step ahead variance forecast
    vols = []
    for i in range(1, forecast_days + 1):
        h_i = long_run_var + (alpha + beta) ** i * (h0 - long_run_var)
        vols.append(np.sqrt(max(h_i, 1e-12)) / 100)  # convert back to decimal

    return np.array(vols)


def _engineer_features(df):
    """Build ML feature matrix from OHLCV dataframe."""
    ret   = df["Daily_Return"]
    close = df["Close"]
    f     = pd.DataFrame(index=df.index)

    for lag in [1, 2, 3, 5, 10]:
        f[f"ret_lag{lag}"] = ret.shift(lag)

    for w in [10, 20, 60]:
        f[f"vol_{w}d"] = ret.rolling(w).std()

    # RSI-14
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    f["rsi14"] = 100 - 100 / (1 + gain / (loss + 1e-8))

    # Price / moving-average ratios
    f["ma20_ratio"] = close / close.rolling(20).mean()
    f["ma50_ratio"] = close / close.rolling(50).mean()

    # Volume ratio vs 20-day average
    f["vol_ratio"] = df["Volume"] / (df["Volume"].rolling(20).mean() + 1e-8)

    # Target: next-day return
    f["target"] = ret.shift(-1)

    return f.dropna()


def _train_ml_drift(df, log=print):
    """
    Train a Random Forest + XGBoost ensemble on engineered features.
    Returns a predicted daily drift (float) for use in the Monte Carlo simulation.
    """
    try:
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.preprocessing import StandardScaler

        feat_df = _engineer_features(df)
        if len(feat_df) < 60:
            log("  ML: Not enough data — using historical mean drift.")
            return float(df["Daily_Return"].mean())

        X = feat_df.drop(columns=["target"]).values
        y = feat_df["target"].values

        split     = int(len(X) * 0.8)
        X_train   = X[:split]
        y_train   = y[:split]

        scaler       = StandardScaler()
        X_train_s    = scaler.fit_transform(X_train)
        X_last_s     = scaler.transform(X[-1:])

        rf = RandomForestRegressor(n_estimators=150, random_state=42, n_jobs=-1)
        rf.fit(X_train_s, y_train)
        pred_rf = float(rf.predict(X_last_s)[0])

        try:
            import xgboost as xgb
            gb = xgb.XGBRegressor(n_estimators=150, learning_rate=0.05,
                                   random_state=42, verbosity=0)
        except ImportError:
            from sklearn.ensemble import GradientBoostingRegressor
            gb = GradientBoostingRegressor(n_estimators=150, random_state=42)

        gb.fit(X_train_s, y_train)
        pred_gb = float(gb.predict(X_last_s)[0])

        drift = (pred_rf + pred_gb) / 2
        log(f"  ML drift — RF: {pred_rf*100:.3f}%  GB: {pred_gb*100:.3f}%  Ensemble: {drift*100:.3f}%")

        # Drift is capped within ±1 std of historical mean to prevent
        # regime extrapolation over the full forecast horizon.
        hist_mean = y_train.mean()
        hist_std  = y_train.std()
        ml_drift  = np.clip(drift, hist_mean - hist_std, hist_mean + hist_std)
        return ml_drift

    except Exception as exc:
        log(f"  ML training failed ({exc}) — using historical mean drift.")
        return float(df["Daily_Return"].mean())


def run_custom_forecast(df, n_simulations=1000, forecast_days=252, log=print):
    """
    Custom Forecast: GARCH(1,1) volatility + ML-ensemble drift + Monte Carlo paths.
    Uses the pre-fetched Polygon dataframe (same data as the rest of the analysis).

    Returns
    -------
    paths_df    : pd.DataFrame  shape (forecast_days+1, n_simulations)
    garch_vols  : np.ndarray    daily volatility curve (length = forecast_days)
    ml_drift    : float         ML-predicted daily drift
    summary     : dict          same key structure as run_monte_carlo summary
    """
    log("Custom Forecast: preparing data...")
    if "Daily_Return" not in df.columns:
        df = df.copy()
        df["Daily_Return"] = df["Close"].pct_change()
    df = df.dropna(subset=["Daily_Return"])

    returns    = df["Daily_Return"].dropna()
    last_price = float(df["Close"].iloc[-1])
    hist_sigma = float(returns.std())

    # ── 1. GARCH volatility forecast ─────────────────────────────────────────
    log("  GARCH(1,1): fitting model...")
    try:
        garch_vols = _fit_garch_volatility(returns, forecast_days)
    except Exception as exc:
        log(f"  GARCH failed ({exc}) — using constant historical volatility.")
        garch_vols = np.full(forecast_days, hist_sigma)

    # ── 2. ML drift ───────────────────────────────────────────────────────────
    log("  ML ensemble: training Random Forest / XGBoost...")
    ml_drift = _train_ml_drift(df, log=log)

    # ── 3. Monte Carlo with GARCH vol + ML drift ──────────────────────────────
    log(f"  Monte Carlo: {n_simulations:,} paths × {forecast_days} days (dynamic σ)...")
    rand  = np.random.standard_normal((forecast_days, n_simulations))
    paths = np.zeros((forecast_days + 1, n_simulations))
    paths[0] = last_price

    for t in range(1, forecast_days + 1):
        sigma_t = garch_vols[t - 1]
        mu_t    = ml_drift - 0.5 * sigma_t ** 2
        paths[t] = paths[t - 1] * np.exp(mu_t + sigma_t * rand[t - 1])

    fp   = paths[-1]
    pcts = np.percentile(fp, [5, 25, 50, 75, 95])

    summary = {
        "Last Price":              round(last_price, 2),
        "Forecast Horizon (days)": forecast_days,
        "Simulations":             n_simulations,
        "Mean Forecast":           round(float(fp.mean()), 2),
        "Median (P50)":            round(float(pcts[2]), 2),
        "Bear Case (P5)":          round(float(pcts[0]), 2),
        "Low Case (P25)":          round(float(pcts[1]), 2),
        "Bull Case (P75)":         round(float(pcts[3]), 2),
        "Best Case (P95)":         round(float(pcts[4]), 2),
        "Prob. of Gain":           f"{(fp > last_price).mean()*100:.1f}%",
        "Ann. Volatility (GARCH)": f"{garch_vols.mean() * np.sqrt(252) * 100:.2f}%",
        "ML Drift (daily)":        f"{ml_drift*100:.4f}%",
    }

    log(f"   P5 ${summary['Bear Case (P5)']:,.2f}  "
        f"P50 ${summary['Median (P50)']:,.2f}  "
        f"P95 ${summary['Best Case (P95)']:,.2f}")

    return pd.DataFrame(paths), garch_vols, ml_drift, summary


def generate_summary_paragraph(ticker, df, company_details, mc_summary, sharpe, sortino,
                               forecast_method="Monte Carlo"):
    latest       = df.iloc[-1]
    first        = df.iloc[0]
    period_ret   = (latest["Close"] / first["Close"] - 1) * 100
    vol_20d      = latest.get("Volatility_20d", np.nan)
    drawdown_60d = df["Drawdown_60d"].min() * 100

    try:
        rsi = float(latest.get("RSI14", np.nan))
    except Exception:
        rsi = np.nan

    ma50_sig = ""
    if "MA50" in df.columns and pd.notna(latest.get("MA50")):
        ma50_sig = ("above its 50-day moving average — bullish"
                    if latest["Close"] > latest["MA50"]
                    else "below its 50-day moving average — cautionary")

    rsi_str = ""
    if pd.notna(rsi):
        if   rsi > 70: rsi_str = f"RSI {rsi:.0f} — overbought territory"
        elif rsi < 30: rsi_str = f"RSI {rsi:.0f} — oversold territory"
        else:          rsi_str = f"RSI {rsi:.0f} — neutral territory"

    w52h = latest.get("52W_High", np.nan)
    w52l = latest.get("52W_Low", np.nan)
    w52_str = ""
    if pd.notna(w52h) and pd.notna(w52l):
        pct_from_high = (latest["Close"] / w52h - 1) * 100
        w52_str = (f"The stock sits {abs(pct_from_high):.1f}% "
                   f"{'below' if pct_from_high < 0 else 'above'} its 52-week high "
                   f"of ${w52h:,.2f} (52-week low: ${w52l:,.2f}).")

    vol_str    = f"20-day annualised volatility: {vol_20d*100:.1f}%." if pd.notna(vol_20d) else ""
    sharpe_str = ""
    if sharpe and pd.notna(sharpe):
        q = "strong" if sharpe > 1 else ("modest" if sharpe > 0.5 else "weak")
        sharpe_str = (f"Sharpe ratio {sharpe:.2f} ({q} risk-adjusted return); "
                      f"Sortino {sortino:.2f} "
                      f"({'well-managed' if sortino > 1 else 'elevated'} downside risk).")

    mc_str = ""
    if mc_summary:
        mc_str = (f"{forecast_method} ({mc_summary['Simulations']:,} paths, "
                  f"{mc_summary['Forecast Horizon (days)']} days): median "
                  f"${mc_summary['Median (P50)']:,.2f} "
                  f"(bear ${mc_summary['Bear Case (P5)']:,.2f} / "
                  f"bull ${mc_summary['Best Case (P95)']:,.2f}), "
                  f"{mc_summary['Prob. of Gain']} probability of gain.")

    company_str = ""
    if company_details:
        company_str = (f"{ticker} ({company_details.get('Name', ticker)}) "
                       f"trades on {company_details.get('Exchange', 'N/A')}.")

    lines = [
        f"{ticker} delivered a cumulative return of {period_ret:+.1f}% over the selected period, "
        f"most recently closing at ${latest['Close']:,.2f}.",
    ]
    for s in [
        ma50_sig and f"Price is currently {ma50_sig}.",
        rsi_str, w52_str,
        vol_str and f"{vol_str} Peak 60-day drawdown: {drawdown_60d:.1f}%.",
        sharpe_str, company_str, mc_str,
        "This report is generated programmatically and does not constitute investment advice."
    ]:
        if s:
            lines.append(s)

    return "  ".join(lines)
