import os
import stripe
import streamlit as st

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID   = os.environ.get("STRIPE_PRICE_ID", "")

stripe.api_key = STRIPE_SECRET_KEY


def create_checkout_session(success_url, cancel_url, email=None):
    try:
        kwargs = {
            "payment_method_types": ["card"],
            "line_items": [{"price": STRIPE_PRICE_ID, "quantity": 1}],
            "mode": "subscription",
            "success_url": success_url + "?session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": cancel_url,
        }
        if email:
            kwargs["customer_email"] = email
        session = stripe.checkout.Session.create(**kwargs)
        return session
    except Exception as e:
        st.error(f"Payment error: {e}")
        return None


def verify_session(session_id):
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if session.payment_status == "paid" or session.status == "complete":
            return True, session.customer_email
        return False, None
    except Exception:
        return False, None


def check_subscription(email):
    try:
        customers = stripe.Customer.list(email=email, limit=1)
        if not customers.data:
            return False
        customer = customers.data[0]
        subs = stripe.Subscription.list(customer=customer.id, status="active", limit=1)
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

    with col1:
        st.markdown("""
        <div style="background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:2rem">
            <div style="font-size:0.72rem;font-weight:600;letter-spacing:0.5px;
                        text-transform:uppercase;color:#64748b;margin-bottom:0.75rem">Free</div>
            <div style="font-family:'DM Mono',monospace;font-size:2.5rem;font-weight:500;
                        color:#0f172a;margin-bottom:0.25rem">$0</div>
            <div style="font-size:0.85rem;color:#94a3b8;margin-bottom:1.5rem">Forever free</div>
            <div style="font-size:0.88rem;color:#334155;line-height:2.2">
                ✓ &nbsp;Full Excel report download<br>
                ✓ &nbsp;Monte Carlo simulation<br>
                ✓ &nbsp;RSI, MACD, Bollinger Bands<br>
                ✓ &nbsp;Support & resistance<br>
                ✓ &nbsp;News headlines<br>
                ✓ &nbsp;Up to 5 year history<br>
                ✗ &nbsp;Live intraday charts<br>
                ✗ &nbsp;Day trader mode<br>
                ✗ &nbsp;Real-time price updates
            </div>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown("""
        <div style="background:#0f172a;border:2px solid #38bdf8;border-radius:16px;padding:2rem;position:relative">
            <div style="position:absolute;top:-14px;left:50%;transform:translateX(-50%);
                        background:#38bdf8;color:#0f172a;font-size:0.7rem;font-weight:700;
                        padding:4px 16px;border-radius:20px;white-space:nowrap">
                MOST POPULAR
            </div>
            <div style="font-size:0.72rem;font-weight:600;letter-spacing:0.5px;
                        text-transform:uppercase;color:#38bdf8;margin-bottom:0.75rem">Pro</div>
            <div style="font-family:'DM Mono',monospace;font-size:2.5rem;font-weight:500;
                        color:#fff;margin-bottom:0.25rem">$9.99</div>
            <div style="font-size:0.85rem;color:#64748b;margin-bottom:1.5rem">per month · cancel anytime</div>
            <div style="font-size:0.88rem;color:#94a3b8;line-height:2.2">
                ✓ &nbsp;Everything in Free<br>
                ✓ &nbsp;<span style="color:#38bdf8">Live intraday candlestick charts</span><br>
                ✓ &nbsp;<span style="color:#38bdf8">Real-time price (30s refresh)</span><br>
                ✓ &nbsp;<span style="color:#38bdf8">Day trader mode</span><br>
                ✓ &nbsp;<span style="color:#38bdf8">1min 5min 15min 1hr candles</span><br>
                ✓ &nbsp;<span style="color:#38bdf8">Volume spike detection</span><br>
                ✓ &nbsp;<span style="color:#38bdf8">Pre-market & after-hours</span><br>
                ✓ &nbsp;<span style="color:#38bdf8">10 year history</span><br>
                ✓ &nbsp;Priority support
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)
        if st.button("Upgrade to Pro — $9.99/month", type="primary",
                     use_container_width=True, key="upgrade_pricing"):
            st.session_state["show_payment"] = True
            st.rerun()
