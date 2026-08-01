import os
import stripe
import streamlit as st
from icons import icon

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from constants import DEV_MODE_FREE

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID   = os.environ.get("STRIPE_PRICE_ID", "")
# Annual plan. The pricing card used to offer an "Annual billing (save 34%) —
# $79/yr" toggle while checkout only ever passed STRIPE_PRICE_ID, so anyone
# choosing annual would have been billed the monthly price. The toggle is now
# gated on this being configured, and the chosen plan is what gets charged.
STRIPE_PRICE_ID_ANNUAL = os.environ.get("STRIPE_PRICE_ID_ANNUAL", "")
ANNUAL_PRICE_LABEL     = os.environ.get("STRIPE_ANNUAL_LABEL", "$79/yr")

# Validate key at startup but do NOT set stripe.api_key globally —
# pass it per-call so the key never appears in module-level state or error traces.
if not DEV_MODE_FREE:
    if not STRIPE_SECRET_KEY:
        raise EnvironmentError("STRIPE_SECRET_KEY is not set. Payments cannot be processed.")


def create_checkout_session(success_url, cancel_url, email=None, annual=None):
    # DEV_MODE_FREE: Stripe is disabled — callers should not reach this function,
    # but return None safely if they do.
    if DEV_MODE_FREE:
        return None
    # Which plan the user picked on the pricing card. Defaults to the toggle in
    # session state so existing call sites don't need to thread it through.
    if annual is None:
        annual = bool(st.session_state.get("annual_billing"))
    price_id = STRIPE_PRICE_ID_ANNUAL if annual else STRIPE_PRICE_ID
    if not price_id:
        st.error("That billing plan isn't available right now. Please choose monthly, "
                 "or contact support.")
        return None
    # ── Original Stripe logic preserved below — do not delete ──
    try:
        kwargs = {
            "payment_method_types": ["card"],
            "line_items": [{"price": price_id, "quantity": 1}],
            "mode": "subscription",
            "success_url": success_url + "?session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": cancel_url,
        }
        if email:
            kwargs["customer_email"] = email
        session = stripe.checkout.Session.create(**kwargs, api_key=STRIPE_SECRET_KEY)
        return session
    except Exception as e:
        st.error(f"Payment error: {e}")
        return None


def verify_session(session_id):
    # DEV_MODE_FREE: no Stripe session to verify.
    if DEV_MODE_FREE:
        return False, None
    # ── Original Stripe logic preserved below — do not delete ──
    try:
        session = stripe.checkout.Session.retrieve(session_id, api_key=STRIPE_SECRET_KEY)
        if session.payment_status == "paid" and session.status == "complete":
            return True, session.customer_email
        return False, None
    except Exception:
        return False, None


def check_subscription(email):
    # DEV_MODE_FREE: treat every user as subscribed.
    if DEV_MODE_FREE:
        return True
    # ── Original Stripe logic preserved below — do not delete ──
    try:
        customers = stripe.Customer.list(email=email, limit=1, api_key=STRIPE_SECRET_KEY)
        if not customers.data:
            return False
        customer = customers.data[0]
        subs = stripe.Subscription.list(customer=customer.id, status="active", limit=1, api_key=STRIPE_SECRET_KEY)
        return len(subs.data) > 0
    except Exception:
        return False


def render_pricing_section():
    st.markdown("""
    <div style="margin:3rem 0 1rem">
        <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;
                    text-transform:uppercase;color:#64748b;border-bottom:1px solid #e2e8f0;
                    padding-bottom:0.5rem;margin-bottom:1.5rem">
            Pricing
        </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    # Inline SVG rather than the ✓ / ✗ characters these replaced: those are
    # OS-rendered, so their weight and shape changed between platforms.
    _ok = icon("check", 15)
    _no = icon("cross", 15)

    with col1:
        st.markdown(f"""
        <div style="background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:2rem">
            <div style="font-size:0.72rem;font-weight:600;letter-spacing:0.5px;
                        text-transform:uppercase;color:#64748b;margin-bottom:0.75rem">Free</div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:2.5rem;font-weight:500;
                        color:#0f172a;margin-bottom:0.25rem">$0</div>
            <div style="font-size:0.85rem;color:#64748b;margin-bottom:1.5rem">Forever free</div>
            <div style="font-size:0.88rem;color:#334155;line-height:2.2">
                <div style="display:flex;align-items:center;gap:0.6rem;padding:0.16rem 0"><span style="color:#059669;display:inline-flex;flex:0 0 auto">{_ok}</span><span style="color:#334155">Full Excel report download</span></div>
                <div style="display:flex;align-items:center;gap:0.6rem;padding:0.16rem 0"><span style="color:#059669;display:inline-flex;flex:0 0 auto">{_ok}</span><span style="color:#334155">Monte Carlo simulation</span></div>
                <div style="display:flex;align-items:center;gap:0.6rem;padding:0.16rem 0"><span style="color:#059669;display:inline-flex;flex:0 0 auto">{_ok}</span><span style="color:#334155">RSI, MACD, Bollinger Bands</span></div>
                <div style="display:flex;align-items:center;gap:0.6rem;padding:0.16rem 0"><span style="color:#059669;display:inline-flex;flex:0 0 auto">{_ok}</span><span style="color:#334155">Support & resistance</span></div>
                <div style="display:flex;align-items:center;gap:0.6rem;padding:0.16rem 0"><span style="color:#059669;display:inline-flex;flex:0 0 auto">{_ok}</span><span style="color:#334155">News headlines</span></div>
                <div style="display:flex;align-items:center;gap:0.6rem;padding:0.16rem 0"><span style="color:#059669;display:inline-flex;flex:0 0 auto">{_ok}</span><span style="color:#334155">Up to 10 years of price history</span></div>
                <div style="display:flex;align-items:center;gap:0.6rem;padding:0.16rem 0"><span style="color:#cbd5e1;display:inline-flex;flex:0 0 auto">{_no}</span><span style="color:#94a3b8">Portfolio Builder &amp; backtest</span></div>
                <div style="display:flex;align-items:center;gap:0.6rem;padding:0.16rem 0"><span style="color:#cbd5e1;display:inline-flex;flex:0 0 auto">{_no}</span><span style="color:#94a3b8">Stress Test &amp; Portfolio Autopsy</span></div>
                <div style="display:flex;align-items:center;gap:0.6rem;padding:0.16rem 0"><span style="color:#cbd5e1;display:inline-flex;flex:0 0 auto">{_no}</span><span style="color:#94a3b8">Save &amp; forward-track portfolios</span></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        # Only offer annual when an annual price actually exists in Stripe —
        # otherwise the toggle promises a plan checkout cannot sell.
        billing = (st.toggle("Annual billing (save 34%)", key="annual_billing")
                   if STRIPE_PRICE_ID_ANNUAL else False)
        price_display = ANNUAL_PRICE_LABEL if billing else "$9.99/mo"
        savings_tag   = "BEST VALUE" if billing else "MOST POPULAR"

        st.markdown(f"""
        <div style="background:#0f172a;border:2px solid #38bdf8;border-radius:16px;padding:2rem;position:relative">
            <div style="position:absolute;top:-14px;left:50%;transform:translateX(-50%);
                        background:#38bdf8;color:#0f172a;font-size:0.7rem;font-weight:700;
                        padding:4px 16px;border-radius:20px;white-space:nowrap">
                {savings_tag}
            </div>
            <div style="font-size:0.72rem;font-weight:600;letter-spacing:0.5px;
                        text-transform:uppercase;color:#38bdf8;margin-bottom:0.75rem">Pro</div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:2.5rem;font-weight:500;
                        color:#fff;margin-bottom:0.25rem">{price_display}</div>
            <div style="font-size:0.85rem;color:#64748b;margin-bottom:1.5rem">
                {"Billed annually · cancel anytime" if billing else "per month · cancel anytime"}
            </div>
            <div style="font-size:0.88rem;color:#94a3b8;line-height:1.9">
                {"".join(
                    f'<div style="display:flex;align-items:center;gap:0.6rem;padding:0.16rem 0">'
                    f'<span style="color:#38bdf8;display:inline-flex;flex:0 0 auto">{_ok}</span>'
                    f'<span style="color:{_c}">{_t}</span></div>'
                    # These must match the gates the app actually enforces and the
                    # Pro list on the Analysis landing page. The old list sold Day
                    # Trader Mode, intraday candles and pre-market/after-hours —
                    # none of which exist any more (app.py pins Investor Mode) —
                    # and named "10 year history" as Pro while the date-range
                    # slider offers 10Y to everyone.
                    for _t, _c in [
                        ("Everything in Free", "#94a3b8"),
                        ("<strong>Portfolio Builder</strong> — ranked universe, "
                         "5-year backtest, efficient frontier", "#38bdf8"),
                        ("<strong>Stress Test</strong> — 5 historical crash scenarios", "#38bdf8"),
                        ("<strong>Portfolio Autopsy</strong> — CSV upload, P&amp;L attribution", "#38bdf8"),
                        ("<strong>Bond analysis</strong> — 60+ ETFs across 6 categories", "#38bdf8"),
                        ("Portfolio Monte Carlo with milestone probabilities", "#38bdf8"),
                        ("Save, load &amp; forward-track portfolios", "#38bdf8"),
                        ("Priority support", "#94a3b8"),
                    ])}
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)
        btn_label = f"Upgrade to Pro — {price_display}"
        if st.button(btn_label, type="primary", use_container_width=True, key="upgrade_pricing"):
            st.session_state["show_payment"] = True
            st.rerun()
