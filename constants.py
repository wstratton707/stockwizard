# constants.py
# Central configuration for Stock Wizard.

# ─────────────────────────────────────────────────────────────────────────────
# DEVELOPMENT PHASE FLAG
# Set to True  → all payments disabled, all features unlocked, no Stripe calls.
# Set to False → payments re-enabled; SHOW_PRICING in app.py controls the UI.
# ─────────────────────────────────────────────────────────────────────────────
DEV_MODE_FREE = True

# Update RISK_FREE_RATE here when the Fed rate environment changes.
RISK_FREE_RATE = 0.045  # 4.5% — US risk-free rate proxy (update periodically)
