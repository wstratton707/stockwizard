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


# ── Discount rate ─────────────────────────────────────────────────────────────
# Every valuation here used to discount at a flat 9%, so a regulated utility and
# an unprofitable biotech were priced with the same cost of capital. That is the
# single biggest weakness in a reverse DCF: the implied-growth number is only as
# credible as the rate you solved it against.
#
# Two of the inputs cannot be derived from the data we hold — Polygon's
# statements carry no interest expense, so there is no actual cost of debt to
# read, and no effective tax rate. They are assumptions, and they are named
# constants here (not buried in a formula) so the UI and the reports can state
# them. An assumption a user can see and disagree with is honest; the same
# assumption hidden inside a function is not.

US_STATUTORY_TAX = 0.21        # federal statutory rate, used as the tax shield
IG_CREDIT_SPREAD = 0.012       # investment-grade spread over the risk-free rate
HY_CREDIT_SPREAD = 0.045       # spread applied to heavily levered balance sheets
DEFAULT_WACC     = 0.09        # fallback only, when beta is unavailable


def market_beta(stock_returns, market_returns):
    """Ordinary market beta = cov(stock, market) / var(market).

    Kept local rather than importing portfolio_analysis.compute_betas: that one
    is built for a DataFrame of many names inside the optimiser, and importing it
    here would pull the whole portfolio stack into the single-stock path.
    """
    try:
        s = pd.Series(stock_returns).dropna()
        m = pd.Series(market_returns).dropna()
        j = pd.concat([s.rename("s"), m.rename("m")], axis=1).dropna()
        if len(j) < 60:
            return None
        var = float(j["m"].var())
        if not var:
            return None
        return round(float(j["s"].cov(j["m"])) / var, 3)
    except Exception:
        return None


def blume_adjust(beta):
    """Blume-adjusted beta: 2/3 x raw + 1/3 x 1.0.

    A regression beta is a noisy estimate with a large standard error, and
    betas mean-revert toward 1 over time — so the raw number is a poor forward
    input even when it is a fine description of the past. Measured live: NFLX
    over one year regressed to 0.27 (it fell while the market rose), which fed
    straight into its CAPM expected return and made the forecast absurd.

    Standard practice (Bloomberg ships this as "adjusted beta"). Applied where
    beta is used to form a FORWARD estimate — cost of equity, WACC, CAPM
    expected returns, forecast drift — not where the historical beta is being
    reported as a measurement.
    """
    if beta is None:
        return None
    try:
        return (2.0 / 3.0) * float(beta) + (1.0 / 3.0)
    except (TypeError, ValueError):
        return None


def downside_deviation(returns, mar=0.0, periods=252):
    """Annualised downside deviation about `mar` — the correct Sortino denominator.

    This is NOT `returns[returns < 0].std()`, which is what every Sortino in this
    codebase used to divide by. That expression measures how much the losing days
    differ from EACH OTHER, around their own (negative) mean, and averages over
    only their own count. Both mistakes shrink the denominator, so it inflated
    every Sortino ratio the app reported — and by more the choppier the series.

    The definition (Sortino & Price, 1994) is the root-mean-square shortfall below
    the target, taken over ALL observations:

        sqrt( mean( min(r - mar, 0)^2 ) ) * sqrt(periods)

    Returns None when there is no measurable downside, so callers render "N/A"
    instead of dividing by zero.
    """
    r = pd.Series(returns).dropna().astype(float)
    if r.empty:
        return None
    shortfall = (r - mar).clip(upper=0.0)
    dd = float(np.sqrt(float((shortfall ** 2).mean())) * np.sqrt(periods))
    return dd if dd > 0 else None


def cost_of_equity(beta, rf=None, erp=None, adjust=True):
    """CAPM cost of equity: Rf + beta x ERP.

    Uses the LONG risk-free rate (10-year) — this rate discounts multi-year
    cash flows, so the 3-month cash rate is the wrong duration — and
    Blume-adjusts beta by default, because this is a forward-looking estimate
    rather than a description of the past. Pass adjust=False for the raw
    regression beta.
    """
    from constants import get_long_risk_free_rate, EQUITY_RISK_PREMIUM
    if beta is None:
        return None
    b = blume_adjust(beta) if adjust else float(beta)
    r = get_long_risk_free_rate() if rf is None else rf
    p = EQUITY_RISK_PREMIUM if erp is None else erp
    return r + float(b) * p


def _credit_spread(total_debt, market_cap):
    """Spread over the risk-free rate, stepped by how levered the company is.

    A crude proxy, and deliberately only two steps — without interest expense
    there is nothing to calibrate a finer curve against, and a smooth-looking
    function would imply precision that isn't there.
    """
    if not total_debt or not market_cap or market_cap <= 0:
        return IG_CREDIT_SPREAD
    return HY_CREDIT_SPREAD if (total_debt / market_cap) > 1.0 else IG_CREDIT_SPREAD


def estimate_wacc(fundamentals, beta, rf=None, erp=None, tax_rate=US_STATUTORY_TAX):
    """Blended WACC = (E/V)·Re + (D/V)·Rd·(1 - t), or None if beta is unknown.

    Returns (wacc, basis) where `basis` is a dict of every input used, so the
    caller can show its work rather than presenting a rate on faith.
    """
    from constants import get_long_risk_free_rate, EQUITY_RISK_PREMIUM
    re = cost_of_equity(beta, rf=rf, erp=erp)
    if re is None:
        return None, None

    # Long rate: a WACC discounts cash flows for years, and the cost of debt is
    # priced off the long curve too, not off 3-month bills.
    r = get_long_risk_free_rate() if rf is None else rf
    p = EQUITY_RISK_PREMIUM if erp is None else erp
    e = fundamentals.get("market_cap") or 0.0
    # GROSS debt is the right weight for a WACC: the cost of debt is paid on the
    # debt outstanding, not on debt-minus-cash. `total_debt` is now carried
    # directly on the fundamentals dict — this used to reconstruct it as
    # net_debt + balance["cash"], but compute_fundamentals never put a "cash" key
    # in its balance block, so cash always read None and the weight silently
    # became NET debt. For a net-cash company that clamped d to 0 and the WACC
    # collapsed to the pure cost of equity.
    d = 0.0
    try:
        td = fundamentals.get("total_debt")
        if td is None:
            # Older/partial dicts: fall back to the net-debt + cash reconstruction.
            nd   = fundamentals.get("net_debt")
            cash = (fundamentals.get("balance") or {}).get("cash")
            td   = (float(nd) + float(cash or 0.0)) if nd is not None else None
        d = max(0.0, float(td)) if td is not None else 0.0
    except Exception:
        d = 0.0
    v = e + d
    if v <= 0:
        return None, None

    rd = r + _credit_spread(d, e)
    wacc = (e / v) * re + (d / v) * rd * (1.0 - tax_rate)
    # A WACC below the terminal growth rate breaks a Gordon terminal value, and
    # anything above ~20% produces nonsense fair values. Clamp and say so.
    wacc_clamped = max(0.05, min(0.20, wacc))
    return round(wacc_clamped, 4), {
        "beta": beta, "beta_adjusted": blume_adjust(beta), "risk_free": r, "erp": p,
        "cost_of_equity": re, "cost_of_debt": rd, "tax_rate": tax_rate,
        "equity_weight": e / v, "debt_weight": d / v,
        "clamped": abs(wacc_clamped - wacc) > 1e-9,
    }


def _reverse_dcf_growth(market_cap, base_earnings, years=10, discount=0.09,
                        terminal_growth=0.025):
    """
    Reverse DCF on ACCOUNTING EARNINGS — a secondary cross-check, not the headline.

    The primary reverse DCF is inside `dcf_valuation`, which solves the same
    question against free cash flow and returns it as `market_implied_growth`.
    That one is the number to show a user: a DCF discounts cash, and net income
    is not cash — for a capex-heavy business the two diverge materially.

    Both exist because they answer slightly different questions, but they WILL
    disagree, so never present them side by side as though they were the same
    quantity. This one stays for the earnings-basis comparison in the workbook.

    Returns the implied growth as a decimal, or None if it can't be solved.
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


def compute_fundamentals(financials, market_cap=None, price=None, supplement=None):
    """
    Turn raw Polygon statements (from data.fetch_financials) + market cap into a
    structured analytics dict: margins, returns, leverage, growth, valuation
    multiples, a reverse-DCF implied growth, and 4-period trend arrays.

    `supplement` is the optional dict from market_data.get_financials_supplement:
    capex, free cash flow and the balance-sheet fields Polygon's endpoints don't
    return. Passed in rather than fetched here so this stays a pure function.
    Without it, FCF and Altman-Z simply remain None, as they did before.

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

    def _split_factor_to_oldest():
        """Cumulative share-split factor between the oldest period and today.

        EDGAR reports EPS and share counts AS FILED, so a split makes both
        series discontinuous. Apple's FY2019 EPS is 11.89 on 4.65bn shares and
        FY2020's is 3.28 on 17.53bn, because of the 4:1 in August 2020. A CAGR
        taken across that boundary measures the split rather than the business:
        (7.46 / 8.31)^(1/9) - 1 = -1.2% for a company whose net income compounded
        at +10.4% over the same span, with a shrinking share count.

        Splits are inferred from the diluted-share series itself, because we
        carry no corporate-actions feed. An adjacent-period jump of more than
        50% in share count is a split; ordinary issuance and buybacks do not
        move a share count by half in a year. The raw ratio is rounded to a
        whole number when it is close to one, since the measured jump also
        contains a year of buybacks — Apple's is 3.77, which is a 4:1.
        """
        rows = len(inc)
        factor = 1.0
        for i in range(rows - 1):
            new_sh = _fin_val(inc, "diluted_shares", i)
            old_sh = _fin_val(inc, "diluted_shares", i + 1)
            if not new_sh or not old_sh or old_sh <= 0:
                continue
            ratio = new_sh / old_sh
            if 1 / 1.5 <= ratio <= 1.5:
                continue

            # A share count can also jump on a large equity raise, and treating
            # that as a split would understate the old EPS and inflate the CAGR.
            # The discriminator is that splits are declared in CLEAN ratios —
            # 2:1, 4:1, 10:1 — while a raise lands wherever the raise lands.
            # Apple's measured 3.77 is a 4:1 carrying a year of buybacks; a 1.6x
            # issuance is not near any whole number and is left alone.
            #
            # Not an EPS-inverse test, which was the first attempt and was worse:
            # it assumes earnings hold still across the split. Nvidia's did not,
            # so its 10:1 showed an EPS ratio of 4 against a share ratio of 10,
            # the check rejected a real split, and its EPS CAGR collapsed from
            # 61.9% to 7.4% for a company whose net income went $0.6bn to $72.9bn.
            fwd = ratio if ratio > 1.0 else 1.0 / ratio
            whole = round(fwd)
            if whole < 2 or abs(fwd - whole) / whole > 0.12:
                continue                                       # not a clean split
            factor = factor * whole if ratio > 1.0 else factor / whole
        return factor

    def eps_cagr_adjusted():
        """EPS CAGR with the oldest endpoint restated onto today's share base."""
        cur = _fin_val(inc, "diluted_earnings_per_share", 0)
        n   = len(inc) - 1
        old = _fin_val(inc, "diluted_earnings_per_share", n)
        if not (cur and old and n >= 1):
            return None
        old_adj = old / _split_factor_to_oldest()
        if old_adj <= 0:
            return None
        return round(((cur / old_adj) ** (1 / n) - 1) * 100, 1)

    growth = {"revenue_yoy": yoy("revenues"), "net_income_yoy": yoy("net_income_loss"),
              "eps_yoy": yoy("diluted_earnings_per_share"),
              "revenue_cagr": cagr("revenues"), "eps_cagr": eps_cagr_adjusted()}

    valuation = {
        "pe": ratio(mcap, ni) if (mcap and ni and ni > 0) else None,
        "ps": ratio(mcap, rev) if (mcap and rev) else None,
        "pb": ratio(mcap, eq) if (mcap and eq and eq > 0) else None,
        "earnings_yield": pct(ni, mcap) if (mcap and ni) else None,
    }

    implied_growth = _reverse_dcf_growth(mcap, ni) if (mcap and ni) else None

    # ── Free cash flow (the extra EDGAR fields are absent on the Polygon path,
    #     so every derived metric here degrades gracefully to None) ────────────
    def _sup(field, i=0):
        """Newest-first value from the yfinance supplement, or None."""
        vals = (supplement or {}).get(field) or []
        return vals[i] if i < len(vals) and vals[i] is not None else None

    capex = _fin_val(cf, "capex")
    fcf   = (ocf - capex) if (ocf is not None and capex is not None) else None
    if fcf is None:
        # Polygon's cash-flow endpoint has no capex line at all, so this is the
        # normal path rather than a rare fallback. Prefer yfinance's own FCF
        # figure; otherwise reconstruct it. Capex arrives negative there, hence
        # the addition.
        fcf = _sup("fcf")
        if fcf is None:
            _ocf, _cx = _sup("operating_cf"), _sup("capex")
            if _ocf is not None and _cx is not None:
                fcf = _ocf + _cx if _cx < 0 else _ocf - _cx
    fcf_block = {
        "fcf":        fcf,
        "fcf_margin": pct(fcf, rev),
        "fcf_yield":  pct(fcf, mcap) if (fcf is not None and mcap) else None,
    }

    # ── EV / EBITDA ───────────────────────────────────────────────────────────
    da         = _fin_val(inc, "depreciation_amortization")
    # Polygon's balance sheet has NO cash column. Left to fall through, cash
    # reads as None and net debt below silently becomes GROSS debt — for NKE
    # that was $7.96B against a true $0.38B, an error the size of the entire
    # cash balance, which inflates enterprise value and understates fair value
    # per share in the DCF. The SEC path does carry `cash`; the supplement is
    # what keeps the Polygon fallback from being quietly wrong.
    cash_bal   = _fin_val(bal, "cash")
    if cash_bal is None:
        cash_bal = _sup("cash")
    debt_cur   = _fin_val(bal, "debt_current")
    total_debt = (sum(v for v in (ltd, debt_cur) if v is not None)
                  if (ltd is not None or debt_cur is not None) else None)
    if total_debt is None:
        total_debt = _sup("total_debt")
    ebitda     = (oi + da) if (oi is not None and da is not None) else None
    ev         = (mcap + (total_debt or 0) - (cash_bal or 0)) if mcap else None
    ev_ebitda  = ratio(ev, ebitda) if (ev is not None and ebitda and ebitda > 0) else None

    # ── Quality scores ────────────────────────────────────────────────────────
    f_score, f_basis = _piotroski_f_score(inc, bal, cf)
    # Retained earnings and total assets are absent from Polygon's balance sheet,
    # so Z fell through to None for every ticker. Fill from the supplement.
    z_score, z_zone  = _altman_z(rev, oi,
                                 ca   if ca   is not None else _sup("current_assets"),
                                 cl   if cl   is not None else _sup("current_liabilities"),
                                 asts if asts is not None else _sup("total_assets"),
                                 _fin_val(bal, "liabilities") or _sup("total_liabilities"),
                                 _fin_val(bal, "retained_earnings") or _sup("retained_earnings"),
                                 mcap)
    quality = {"f_score": f_score, "f_basis": f_basis,
               "z_score": z_score, "z_zone": z_zone}

    # Trend arrays oldest -> newest for charting
    order = list(range(len(inc) - 1, -1, -1))

    def _fcf_at(i):
        o, c = (_fin_val(cf, "net_cash_flow_from_operating_activities", i),
                _fin_val(cf, "capex", i))
        if o is not None and c is not None:
            return o - c
        # Both the Polygon frame and the supplement are newest-first, so `i`
        # indexes the same period in each — no mirroring. (`order` reverses the
        # iteration, not the frames.) Mirroring here reversed the FCF trend
        # against the revenue and net-income series beside it.
        vals = (supplement or {}).get("fcf") or []
        return vals[i] if 0 <= i < len(vals) else None

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
                    "long_term_debt": ltd,
                    # `cash` belongs here: estimate_wacc reads it, and its absence
                    # is what made the WACC debt weight net rather than gross.
                    "cash": cash_bal},
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
        # Gross debt, surfaced so estimate_wacc doesn't have to reconstruct it.
        "total_debt": total_debt,
        "cash": cash_bal,
        "ev": ev,
        "trend": trend,
        "source": financials.get("source", "Polygon") if isinstance(financials, dict) else "Polygon",
    }


def dcf_valuation(fundamentals, price, wacc=None, terminal_growth=0.025, years=10,
                  beta=None, sector=None):
    """Two-stage unlevered DCF (FCFF) → fair value per share + upside/downside.

    `wacc` resolution order: an explicit rate wins; otherwise it is derived from
    `beta` via CAPM (see estimate_wacc); otherwise it falls back to DEFAULT_WACC.
    The result carries `wacc_basis` describing which path was taken and every
    input behind it, because a reverse DCF's headline number is meaningless
    without the rate it was solved against.

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

    # Stable growth cannot exceed the growth rate of the economy, and the
    # risk-free rate is the standard proxy for that ceiling (Damodaran): a
    # perpetuity growing faster than the economy eventually becomes the economy.
    # 2.5% clears a 4% ten-year yield comfortably, but hardcoding it would go
    # wrong in a low-rate regime — so state the constraint instead of relying on
    # today's rates to satisfy it.
    from constants import get_long_risk_free_rate as _long_rf
    terminal_growth = min(terminal_growth, _long_rf())

    # Resolve the discount rate before anything depends on it.
    wacc_basis = None
    if wacc is None:
        wacc, wacc_basis = estimate_wacc(fundamentals, beta)
        if wacc is None:
            wacc = DEFAULT_WACC
            wacc_basis = {"fallback": True}
    if wacc <= terminal_growth:
        return {"ok": False, "reason": "WACC must exceed terminal growth"}

    # Base FCF: average of up to the last 3 positive annual FCF values (the trend
    # array is oldest→newest); fall back to the single latest FCF.
    _fcf_all  = [x for x in (fundamentals.get("trend", {}).get("fcf") or [])
                 if isinstance(x, (int, float))]
    fcf_hist  = [x for x in _fcf_all if x > 0]
    _n_neg    = sum(1 for x in _fcf_all if x <= 0)
    base_fcf = (sum(fcf_hist[-3:]) / len(fcf_hist[-3:])) if fcf_hist else None
    if base_fcf is None:
        latest = fundamentals.get("fcf", {}).get("fcf")
        base_fcf = latest if (isinstance(latest, (int, float)) and latest > 0) else None
    if not base_fcf or base_fcf <= 0:
        return {"ok": False, "reason": "no positive free cash flow to project"}

    # An unlevered FCF DCF assumes free cash flow means what it means for an
    # operating company. For a bank or broker-dealer it does not: lending and
    # trading-inventory swings dominate operating cash flow, so FCF is routinely
    # and legitimately negative, and "net debt" is funding rather than leverage.
    #
    # Goldman Sachs is the case that surfaced this. Its FCF is negative in 6 of
    # the last 10 filed years and −$47.2B in FY2025, yet the filter above keeps
    # only the positive years and averages them into a +$10.9B base — discarding
    # the majority of the record to manufacture something to grow. Net debt came
    # in at $221.9B, which for a broker is the business model, not gearing.
    #
    # The model still runs, because refusing outright would be its own kind of
    # wrong for a diversified financial. What it must not do is present the
    # number without saying what it rests on, so the caveats travel with the
    # result and every renderer can surface them.
    # compute_fundamentals does not carry a sector, so callers pass one from
    # company details; the fundamentals lookup is a fallback for callers that do.
    _sector = " ".join([str(sector or "")]
                       + [str(fundamentals.get(k) or "")
                          for k in ("sector", "industry")]).lower()
    _is_financial = any(t in _sector for t in (
        "bank", "broker", "dealer", "insurance", "capital market",
        "financial", "investment banking", "asset management"))

    caveats = []
    if _is_financial:
        caveats.append(
            "This is a financial-sector filer. An unlevered free-cash-flow DCF "
            "assumes FCF means what it does for an operating company; for a bank "
            "or broker it is dominated by lending and trading flows, and net debt "
            "is funding rather than leverage. Treat the fair value as indicative "
            "and prefer a residual-income or P/B-vs-ROE read.")
    if _n_neg and fcf_hist:
        caveats.append(
            f"Base free cash flow averages the last {len(fcf_hist[-3:])} POSITIVE "
            f"year(s); {_n_neg} of the {len(_fcf_all)} filed years were negative "
            f"and were excluded. The base is therefore not representative of the "
            f"full record.")

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

    # Bear is floored at -5%, not at `terminal_growth + 0.005`. That old floor
    # collided with the base case whenever base growth sat near terminal — for a
    # company growing at 3% against a 2.5% terminal rate, bear and base both
    # resolved to 3.0% and the table printed the identical fair value twice,
    # which reads as a broken scenario rather than a conservative one. Stage-1
    # growth is allowed below the terminal rate: it fades toward terminal over
    # the horizon, so a declining near term followed by a mature steady state is
    # exactly what a bear case should express.
    scenarios = {
        "bear": _scn(max(-0.05, g_base - 0.03)),
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
        "wacc": wacc, "wacc_basis": wacc_basis,
        # Travels with the result so every renderer can surface it. A fair value
        # for a broker-dealer, or one built by discarding the negative years, is
        # not wrong so much as conditional — and the condition has to ship with
        # the number rather than living in a methodology sheet two tabs away.
        "caveats": caveats,
        "terminal_growth": terminal_growth, "years": years,
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
        # Named, because a second and shallower drawdown sits a few rows above
        # it on the same sheet. The metrics block reports the worst 60-DAY
        # rolling drawdown; this is the true peak-to-trough, which is what the
        # bands above are calibrated for. Both are right and they disagree by
        # design — for GS, -45.6% against -49% — so each has to say which it is
        # at the point a reader meets it.
        if r.get("max_dd") is not None:
            rb.append(f"Max DD (peak-to-trough) {r['max_dd'] * 100:.0f}%")
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


def detect_support_resistance(df, window=20, num_levels=5, lookback=252,
                              max_dist=0.40):
    """(resistance, support) levels, classified against the CURRENT price.

    The old version scanned the full history and split by swing type, so on a
    5-year chart of a stock in a deep decline every 2021-era swing printed —
    NKE at $43 showed "support" at $165 — and a swing low far above today's
    price is not support in any usable sense. Levels now come from the recent
    window only, split by position vs spot (above = resistance, below =
    support), drop anything further than `max_dist` from the price, and are
    ordered nearest-first.
    """
    if lookback and len(df) > lookback:
        df = df.tail(lookback)
    highs = df["High"].values
    lows  = df["Low"].values
    close = float(df["Close"].iloc[-1])
    n     = len(highs)
    levels = []
    for i in range(window, n - window):
        if highs[i] == max(highs[i - window: i + window + 1]):
            levels.append(round(float(highs[i]), 2))
        if lows[i] == min(lows[i - window: i + window + 1]):
            levels.append(round(float(lows[i]), 2))

    resistance = sorted(lv for lv in set(levels)
                        if close < lv <= close * (1 + max_dist))
    support    = sorted((lv for lv in set(levels)
                         if close * (1 - max_dist) <= lv < close), reverse=True)

    def cluster(ordered, tol=0.01):
        # Nearest-to-price first on input, so each cluster keeps its nearest level.
        clustered = []
        for lv in ordered:
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


def run_monte_carlo(df, n_simulations=1000, forecast_days=252, log=print, seed=42):
    log(f"Monte Carlo: {n_simulations:,} paths x {forecast_days} trading days...")
    returns    = df["Daily_Return"].dropna()
    sigma      = returns.std()
    last_price = df["Close"].iloc[-1]

    # Drift is CAPM (Rf + beta x ERP), NOT the trailing mean return.
    #
    # The trailing mean extrapolates whatever the window happened to do, so a
    # stock that just fell 47% was projected to keep falling: NFLX came out at a
    # median of $43 against a $72 price and an 8% probability of gain, in the
    # same report where the analyst section read Strong Buy. That is not a
    # forecast, it is the last year replayed, and it made the product contradict
    # itself. CAPM asks the defensible question instead — if this name earns its
    # cost of equity, what spread of outcomes does its own volatility imply? —
    # and matches the basis the portfolio engine already uses.
    #
    # Volatility stays empirical: that part the window does measure honestly.
    _beta = None
    for _bcol in ("SPY_Return", "QQQ_Return"):
        if _bcol in df.columns:
            _beta = market_beta(returns, df[_bcol])
            if _beta is not None:
                break
    # cost_of_equity Blume-adjusts the beta and prices off the 10-year rate,
    # which is what an equity expected return should be built from.
    ann_mu = cost_of_equity(_beta if _beta is not None else 1.0)
    mu     = ann_mu / 252.0
    # Seeded local generator, matching the portfolio Monte Carlo and the efficient
    # frontier. Drawing from global numpy state meant the same ticker over the same
    # window produced a different P5/P50/P95 on every run — two reports generated
    # minutes apart disagreed, which reads as a broken model rather than sampling
    # noise. Pass seed=None for genuinely fresh draws.
    rng           = np.random.default_rng(seed)
    rand          = rng.standard_normal((forecast_days, n_simulations))
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
        "Expected Return (CAPM)":  f"{ann_mu * 100:.1f}%",
        "Beta (vs benchmark)":     (round(float(_beta), 2) if _beta is not None else "1.00 (assumed)"),
        "Beta (adjusted, used)":   round(float(blume_adjust(_beta if _beta is not None else 1.0)), 2),
    }
    log(f"   P5 ${summary['Bear Case (P5)']:,.2f}  "
        f"P50 ${summary['Median (P50)']:,.2f}  "
        f"P95 ${summary['Best Case (P95)']:,.2f}")
    return pd.DataFrame(paths), summary


def window_stats(df, rf=None):
    """Every window-dependent headline statistic, computed once, in one place.

    The narrative and the workbook's metric cells used to derive these
    separately, which was fine until they were handed different frames. On a
    5-year AAPL report the Dashboard summary quoted +1159.8% (ten years, the
    full pull), Sharpe 0.69 and Sortino 1.03 (three years, the risk window) and
    a -37.1% drawdown (ten years again), beside metric cells reading +114.2%,
    0.55, 0.81 and -30.2% on the five-year report window. Same code, three
    frames, four wrong numbers in the first paragraph a reader meets.

    Pass the frame the report is actually about; every figure comes back
    measured over it.
    """
    from constants import get_risk_free_rate
    rf = get_risk_free_rate() if rf is None else rf
    close = df["Close"]
    ret = (df["Daily_Return"].dropna() if "Daily_Return" in df.columns
           else close.pct_change().dropna())
    ann_ret = float(ret.mean() * 252)
    ann_vol = float(ret.std() * np.sqrt(252))
    dsd     = downside_deviation(ret)
    cum     = (1 + ret).cumprod()
    return {
        "period_ret":   float(close.iloc[-1] / close.iloc[0] - 1) * 100,
        "ann_ret":      ann_ret * 100,
        "ann_vol":      ann_vol * 100,
        "sharpe":       ((ann_ret - rf) / ann_vol) if ann_vol else float("nan"),
        "sortino":      ((ann_ret - rf) / dsd) if dsd else float("nan"),
        # Both drawdowns, named. They are different measures and the report
        # shows both; neither may stand unlabelled beside the other.
        "dd_60d":       (float(df["Drawdown_60d"].min()) * 100
                         if "Drawdown_60d" in df.columns else float("nan")),
        "dd_peak_trough": float((cum / cum.cummax() - 1).min()) * 100,
        "n_obs":        int(len(close)),
    }


def generate_summary_paragraph(ticker, df, company_details, mc_summary, sharpe, sortino,
                               forecast_method="Monte Carlo", stats=None):
    """`stats` from window_stats(df). When given, nothing here is recomputed —
    the paragraph formats numbers it was handed. Callers that pass a frame but
    no stats get them derived from that same frame, which is safe; passing a
    frame from one window and ratios from another is what this exists to stop."""
    if stats is None:
        stats = window_stats(df)
        sharpe = stats["sharpe"] if sharpe is None else sharpe
        sortino = stats["sortino"] if sortino is None else sortino
    latest       = df.iloc[-1]
    period_ret   = stats["period_ret"]
    vol_20d      = latest.get("Volatility_20d", np.nan)
    drawdown_60d = stats["dd_60d"]

    try:
        rsi = float(latest.get("RSI14", np.nan))
    except Exception:
        rsi = np.nan

    ma50_sig = ""
    if "MA50" in df.columns and pd.notna(latest.get("MA50")):
        ma50_sig = ("above its 50-day moving average — bullish"
                    if latest["Close"] > latest["MA50"]
                    else "below its 50-day moving average — cautionary")

    # Same five zones as excel_builder._technical_posture — the dashboard bullet
    # and this paragraph once read the same RSI 51 as "positive momentum" and
    # "neutral territory" in one workbook.
    rsi_str = ""
    if pd.notna(rsi):
        if   rsi > 70:  rsi_str = f"RSI {rsi:.0f} — overbought territory"
        elif rsi < 30:  rsi_str = f"RSI {rsi:.0f} — oversold territory"
        elif rsi > 55:  rsi_str = f"RSI {rsi:.0f} — positive momentum"
        elif rsi >= 45: rsi_str = f"RSI {rsi:.0f} — neutral territory"
        else:           rsi_str = f"RSI {rsi:.0f} — soft momentum"

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
