# constants.py
# Central configuration for Quant Wizard.

# ─────────────────────────────────────────────────────────────────────────────
# DEVELOPMENT PHASE FLAG
# Set to True  → all payments disabled, all features unlocked, no Stripe calls.
# Set to False → payments re-enabled; SHOW_PRICING in app.py controls the UI.
# ─────────────────────────────────────────────────────────────────────────────
DEV_MODE_FREE = True

# Hardcoded fallback — used if the live fetch below fails.
RISK_FREE_RATE = 0.045  # 4.5%

# Equity risk premium for CAPM expected returns: E(R) = Rf + beta * ERP.
# This is the ONE stated assumption in the model — the market's expected return
# above cash. Historical US ERP ≈ 5–6%; forward-looking estimates ≈ 4–5%.
EQUITY_RISK_PREMIUM = 0.05  # 5%

# ── Risk-free rates: TWO of them, on purpose ─────────────────────────────────
# The risk-free rate must match the DURATION of what it is being used for
# (Damodaran). Using one rate everywhere was wrong in one direction or the
# other, and with an inverted or steep curve the error is percentage points:
#
#   • 3-month T-bill (DGS3MO) — the financing/cash rate. Correct for Sharpe and
#     Sortino, where the question is "what did cash pay over the window?".
#   • 10-year Treasury (DGS10) — the long rate. Correct for anything valuing
#     multi-year cash flows: CAPM expected returns, cost of equity, WACC, the
#     DCF, and the terminal-growth ceiling.
#
# Everything is cached for a day and falls back to RISK_FREE_RATE.
_rfr_cache: dict = {}


def _fred_latest(series_id: str) -> float | None:
    """Most recent non-missing value of a FRED series, as a decimal. None on failure."""
    import requests
    try:
        r = requests.get(
            f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}",
            timeout=5,
        )
        if r.status_code != 200:
            return None
        lines = [ln for ln in r.text.strip().splitlines()
                 if ln and not ln.startswith("DATE")]
        # Walk backwards to find the most recent non-missing value
        for line in reversed(lines):
            parts = line.split(",")
            if len(parts) == 2 and parts[1].strip() not in (".", ""):
                return float(parts[1]) / 100.0
    except Exception:
        pass
    return None


def _cached_rate(series_id: str) -> float:
    import time
    now = time.time()
    hit = _rfr_cache.get(series_id)
    if hit and (now - hit["ts"]) < 86_400:
        return hit["rate"]
    rate = _fred_latest(series_id)
    if rate is None:
        return RISK_FREE_RATE
    _rfr_cache[series_id] = {"rate": rate, "ts": now}
    return rate


def get_risk_free_rate() -> float:
    """3-month US T-bill yield, as a decimal. The CASH rate.

    Use for Sharpe/Sortino. For discounting or expected returns over years, use
    get_long_risk_free_rate() instead — see the note above.
    """
    return _cached_rate("DGS3MO")


def get_long_risk_free_rate() -> float:
    """10-year US Treasury yield, as a decimal. The VALUATION rate.

    Use wherever multi-year cash flows are being valued or an equity expected
    return is being formed: CAPM, cost of equity, WACC, DCF, terminal growth.
    """
    return _cached_rate("DGS10")
