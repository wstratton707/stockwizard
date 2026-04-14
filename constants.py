# constants.py
# Central configuration for Stock Wizard.

# ─────────────────────────────────────────────────────────────────────────────
# DEVELOPMENT PHASE FLAG
# Set to True  → all payments disabled, all features unlocked, no Stripe calls.
# Set to False → payments re-enabled; SHOW_PRICING in app.py controls the UI.
# ─────────────────────────────────────────────────────────────────────────────
DEV_MODE_FREE = True

# Hardcoded fallback — used if the live fetch below fails.
RISK_FREE_RATE = 0.045  # 4.5%

# In-memory cache so we only hit FRED once per process lifetime (refreshes daily).
_rfr_cache: dict = {"rate": None, "ts": 0.0}


def get_risk_free_rate() -> float:
    """
    Returns the current 3-month US T-bill yield as a decimal (e.g. 0.0525 = 5.25%).
    Fetches from FRED on first call each day; falls back to RISK_FREE_RATE if unavailable.
    No API key required — uses the public FRED CSV endpoint.
    """
    import time, requests

    now = time.time()
    if _rfr_cache["rate"] is not None and (now - _rfr_cache["ts"]) < 86_400:
        return _rfr_cache["rate"]

    try:
        r = requests.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO",
            timeout=5,
        )
        if r.status_code == 200:
            lines = [ln for ln in r.text.strip().splitlines() if ln and not ln.startswith("DATE")]
            # Walk backwards to find the most recent non-missing value
            for line in reversed(lines):
                parts = line.split(",")
                if len(parts) == 2 and parts[1].strip() not in (".", ""):
                    rate = float(parts[1]) / 100.0
                    _rfr_cache["rate"] = rate
                    _rfr_cache["ts"]   = now
                    return rate
    except Exception:
        pass

    return RISK_FREE_RATE  # fallback
