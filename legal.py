"""
legal.py — Terms of Service and Privacy Policy.

Kept apart from `disclaimers.py` on purpose. That module holds the *methodological*
disclosures shown beside a number ("this backtest is hypothetical"); this one holds
the two standing legal documents that govern using the site at all. They change on
different schedules and for different reasons.

HOW ACCURATE IS THIS
--------------------
The privacy policy describes what the code actually does, verified against
`auth.py` (Auth0 OIDC — we read email, name, email_verified and token expiry, and
never see a password), `database.py` (three Supabase tables: `waitlist`,
`saved_portfolios`, `tracked_portfolios`) and a search of the tree confirming
there is no analytics, advertising or session-replay tracker anywhere in it. If
you add one, this file is wrong until you update it.

BEFORE THIS GOES LIVE
---------------------
Fill in the four constants below, and have a lawyer read both documents. They are
carefully written but they are not legal advice, and the limitation-of-liability
and governing-law clauses in particular are the ones that matter if anything ever
goes wrong. While any constant is still unfilled, both pages render a visible
notice instead of pretending to be in force.
"""

from __future__ import annotations

import streamlit as st

# ── Fill these in ─────────────────────────────────────────────────────────────
# Each is a real-world decision, not a wording choice, so none of them is guessed
# here. `_UNSET` is the sentinel the render functions check.
_UNSET = ""

ENTITY_NAME     = _UNSET   # e.g. "QuantWizard LLC", or your own name if unincorporated
CONTACT_EMAIL   = _UNSET   # a real monitored inbox — this becomes public
GOVERNING_STATE = _UNSET   # e.g. "Indiana" — the state whose law governs disputes
EFFECTIVE_DATE  = _UNSET   # e.g. "12 August 2026" — the day you publish them

# Kept in one place so the two documents can never quote different dates.
LAST_UPDATED = EFFECTIVE_DATE


def _missing() -> list:
    """Which of the fill-in constants are still empty."""
    return [label for label, value in (
        ("the operating entity's name", ENTITY_NAME),
        ("a contact email address",     CONTACT_EMAIL),
        ("the governing state",         GOVERNING_STATE),
        ("an effective date",           EFFECTIVE_DATE),
    ) if not value.strip()]


def _entity() -> str:
    return ENTITY_NAME.strip() or "the operator of QuantWizard"


def _contact() -> str:
    return CONTACT_EMAIL.strip() or "our contact address"


def _state() -> str:
    return GOVERNING_STATE.strip() or "the state in which the operator is based"


# ── Presentation ──────────────────────────────────────────────────────────────
# Ruled headers on a white page, matching the section headers used in the
# Portfolio Builder. A legal document is a document; it does not want cards.

def _page_head(title: str, standfirst: str) -> str:
    date_line = (f"Effective {EFFECTIVE_DATE.strip()}"
                 if EFFECTIVE_DATE.strip() else "Not yet in force — see the notice below")
    return f"""
    <div style="max-width:760px;margin:0 auto 2rem">
        <div style="font-size:2rem;font-weight:700;color:#0f172a;
                    letter-spacing:-0.02em;margin-bottom:0.35rem">{title}</div>
        <div style="font-size:0.78rem;color:#94a3b8;text-transform:uppercase;
                    letter-spacing:0.8px;font-weight:600;
                    border-bottom:1px solid #e2e8f0;padding-bottom:0.9rem">{date_line}</div>
        <div style="font-size:0.95rem;color:#475569;line-height:1.7;
                    margin-top:1.1rem">{standfirst}</div>
    </div>"""


def _section(number: int, title: str, body: str) -> str:
    """One numbered clause. `body` is HTML — usually <p> and <ul>."""
    return f"""
    <div style="max-width:760px;margin:0 auto">
        <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;
                    text-transform:uppercase;color:#64748b;
                    border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                    margin-bottom:0.9rem;margin-top:2rem">{number}. {title}</div>
        <div style="font-size:0.9rem;color:#334155;line-height:1.75">{body}</div>
    </div>"""


def _unfinished_notice() -> str:
    gaps = _missing()
    if not gaps:
        return ""
    items = "".join(f"<li>{g}</li>" for g in gaps)
    return f"""
    <div style="max-width:760px;margin:0 auto 1.5rem;background:#fffbeb;
                border:1px solid #fde68a;border-radius:8px;padding:1rem 1.25rem;
                font-size:0.84rem;color:#92400e;line-height:1.6">
        <b>This document is not yet in force.</b> It is a complete draft awaiting
        review, and it is still missing {len(gaps)} detail{"s" if len(gaps) > 1 else ""}:
        <ul style="margin:0.5rem 0 0 1.1rem;padding:0">{items}</ul>
    </div>"""


# ══════════════════════════════════════════════════════════════════════════════
# TERMS OF SERVICE
# ══════════════════════════════════════════════════════════════════════════════

def render_terms() -> None:
    st.markdown(_page_head(
        "Terms of Service",
        "These terms are the agreement between you and "
        f"{_entity()} covering your use of QuantWizard. They are written to be read, "
        "not to be skipped. The two clauses that matter most are section 2 — this is "
        "not investment advice — and section 11, which limits what we are liable for."
    ), unsafe_allow_html=True)

    st.markdown(_unfinished_notice(), unsafe_allow_html=True)

    st.markdown(_section(1, "Agreeing to these terms", """
        <p>By using QuantWizard you accept these terms. If you do not accept them,
        please do not use the service. If you are using QuantWizard on behalf of an
        organisation, you confirm you are authorised to bind that organisation.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(2, "What QuantWizard is — and what it is not", f"""
        <p>QuantWizard is a financial data and analytics tool. It applies published
        methods — factor scoring, mean-variance optimisation, discounted cash flow,
        Monte Carlo simulation, historical stress replay — to market and filing data,
        and shows you the output.</p>
        <p><b>QuantWizard is not a registered investment adviser, a broker-dealer, or a
        financial planner, and nothing on it is investment advice.</b> No
        ranking, weight, score, fair value, forecast or stress result is a
        recommendation that you buy, sell or hold anything. We do not know your
        finances, tax position, time horizon, obligations or risk tolerance, and
        nothing here is a suitability assessment. We are not your fiduciary and no
        advisory relationship is created by your use of the service.</p>
        <p>Every figure is generated by an algorithm from historical data. Past
        performance does not predict future results, and investing carries risk
        including the total loss of the amount invested. Consult a licensed adviser,
        accountant or attorney before acting on anything you see here.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(3, "Who may use it", """
        <p>You must be at least 18 years old and legally able to enter into a
        contract. QuantWizard is offered from the United States and is not directed at
        any jurisdiction where providing it would be unlawful. If local law where you
        are restricts financial-information services, complying with that law is your
        responsibility.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(4, "Your account", """
        <p>Signing in is handled by our identity provider. We never see, receive or
        store your password. Keep your credentials secure — anything done through your
        account is treated as done by you, and you should tell us promptly if you
        believe someone else has access to it.</p>
        <p>Accounts are personal. Do not share one, and do not sign in as someone
        else.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(5, "Market data and other sources", """
        <p>QuantWizard displays and derives figures from third-party sources,
        including exchange and vendor price data, analyst estimates, news, official
        interest-rate series and public company filings. That data is owned by those
        providers and licensed, not sold, to you.</p>
        <p>We do not originate the underlying data and cannot guarantee it is
        accurate, complete, current or uninterrupted. Prices may be delayed. Vendors
        restate figures. Filings are amended. Where a source is wrong, our output will
        be wrong with it. Verify anything you intend to act on against a primary
        source.</p>
        <p>You may not redistribute, resell, republish or make bulk data feeds out of
        the market data reached through QuantWizard, and you may not use it to build a
        competing data service.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(6, "Acceptable use", """
        <p>Use QuantWizard for your own research. Do not:</p>
        <ul>
            <li>scrape, crawl, or use bots or scripts to extract data or generate
                reports in bulk;</li>
            <li>circumvent rate limits, access controls, or any feature gate;</li>
            <li>reverse engineer, decompile, or attempt to derive our source code or
                methodology implementations;</li>
            <li>resell, sublicense or commercially redistribute the service or its
                output;</li>
            <li>present our output as your own regulated advice to another person;</li>
            <li>interfere with the service's operation or security, or use it to
                break the law or infringe anyone's rights.</li>
        </ul>
    """), unsafe_allow_html=True)

    st.markdown(_section(7, "Reports you generate", """
        <p>The workbooks, decks and documents QuantWizard produces are yours to use
        for your own investment research, including inside your own organisation. That
        licence is personal and non-exclusive: it does not extend to publishing them
        commercially, selling them, or distributing them as a data product. The
        third-party data inside a report remains subject to section 5.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(8, "Paid plans", """
        <p>Parts of QuantWizard may be offered on a paid subscription. Where they are,
        the price, billing period and what is included are shown at the point of
        purchase, and those details form part of these terms. Payments are processed
        by our payment provider; we do not receive or store your card details.</p>
        <p>Subscriptions renew automatically each period until cancelled. You may
        cancel at any time, effective at the end of the period you have already paid
        for. Unless the law where you live requires otherwise, fees already paid are
        not refundable. We will give notice before any price change, which will take
        effect at your next renewal.</p>
        <p>Where features are provided free or in a beta or preview period, they are
        provided as they are and may change or end at any time.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(9, "Intellectual property", f"""
        <p>QuantWizard — its software, interface, written explanations, report
        templates, methodology and name — belongs to {_entity()} and is protected by
        copyright and other laws. These terms grant you a limited, revocable,
        non-transferable right to use the service, and nothing else.</p>
        <p>Content you supply, such as the portfolios you build and the holdings you
        track, remains yours. You grant us only the permission needed to store, process
        and display it back to you as part of running the service.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(10, "The service is provided as it is", """
        <p>QuantWizard is provided "as is" and "as available", without warranties of
        any kind, whether express or implied, including any implied warranty of
        merchantability, fitness for a particular purpose, accuracy, or
        non-infringement. We do not warrant that the service will be uninterrupted or
        error-free, that defects will be corrected, or that any figure it produces is
        accurate or suitable for any purpose.</p>
        <p>Some jurisdictions do not allow the exclusion of implied warranties, so
        parts of this section may not apply to you.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(11, "Limitation of liability", f"""
        <p>To the fullest extent the law allows, {_entity()} is not liable for any
        indirect, incidental, special, consequential or punitive damages, or for any
        lost profits, lost revenue, lost data, or <b>investment or trading losses</b>,
        arising from or connected to your use of QuantWizard — whether or not we were
        told such damages were possible.</p>
        <p>To the fullest extent the law allows, our total liability for all claims
        relating to the service is limited to the greater of the amount you paid us in
        the twelve months before the claim arose, or one hundred US dollars.</p>
        <p>You are solely responsible for your investment decisions and their
        outcomes. Some jurisdictions do not allow certain limitations of liability, so
        parts of this section may not apply to you.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(12, "Indemnity", f"""
        <p>You agree to indemnify and hold {_entity()} harmless from claims, losses
        and reasonable legal costs arising from your use of the service, your breach of
        these terms, or your violation of any law or third-party right — including any
        claim brought by someone who relied on output you passed on to them.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(13, "Suspension and termination", """
        <p>You may stop using QuantWizard at any time and ask us to delete your
        account. We may suspend or end access if you breach these terms, if we are
        required to by law, or if we discontinue the service. Sections 5 through 12 and
        section 15 survive termination.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(14, "Changes", """
        <p>We may change the service and these terms. When a change is material we
        will update the effective date at the top of this page and, where we hold your
        address, tell you. Continuing to use QuantWizard after a change means you
        accept the revised terms.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(15, "Governing law", f"""
        <p>These terms are governed by the laws of the State of {_state()} and the
        United States, without regard to conflict-of-law rules. Disputes will be
        brought in the state or federal courts located in {_state()}, and you and we
        each consent to that jurisdiction. Nothing here removes any right you have to
        bring a claim in your local courts where the law gives you one.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(16, "Contact", f"""
        <p>Questions about these terms can be sent to {_contact()}.</p>
    """), unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# PRIVACY POLICY
# ══════════════════════════════════════════════════════════════════════════════

def render_privacy() -> None:
    st.markdown(_page_head(
        "Privacy Policy",
        "This describes what QuantWizard collects, why, and what you can do about "
        "it. It is deliberately specific: it names the actual data we hold and the "
        "actual companies that process it, rather than reserving broad rights we do "
        "not exercise."
    ), unsafe_allow_html=True)

    st.markdown(_unfinished_notice(), unsafe_allow_html=True)

    st.markdown(_section(1, "The short version", """
        <p>We collect your email address, the portfolios you choose to save, and
        ordinary technical logs. We use them to run the service and nothing else. We do
        not sell or rent your data, we run no advertising or analytics trackers, and we
        never see your password or your card number. You can ask us to delete
        everything.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(2, "What we collect", """
        <p><b>Account information.</b> Signing in goes through our identity provider,
        which returns your email address, your display name if you have one, and
        whether your address has been verified. We never receive your password.</p>
        <p><b>Waitlist signups.</b> If you join the waitlist we store the address you
        typed and which page you typed it on, so we know what people signed up for.</p>
        <p><b>Content you create.</b> Portfolios you save or track: the name you gave
        them, the tickers, the number of shares, the dates you recorded, and when you
        created them. This is financial information about you and we treat it
        accordingly — it is stored only so we can show it back to you.</p>
        <p><b>Technical information.</b> Ordinary server and application logs generated
        by our hosting provider — request times, errors, and similar operational
        records used to keep the service running and to diagnose faults.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(3, "What we do not collect", """
        <ul>
            <li><b>No advertising, analytics or session-replay trackers.</b> There is
                no Google Analytics, no advertising pixel and no third-party
                behavioural tracking in this application.</li>
            <li><b>No payment card details.</b> If and when paid plans are enabled,
                our payment provider handles the transaction; card numbers never reach
                our servers.</li>
            <li><b>No brokerage credentials and no account connections.</b> QuantWizard
                does not link to your broker. Holdings exist here only if you typed or
                uploaded them.</li>
            <li><b>No password.</b> Authentication is delegated, so there is no
                password of yours for us to store or to lose.</li>
        </ul>
    """), unsafe_allow_html=True)

    st.markdown(_section(4, "Why we process it", """
        <p>To let you sign in and to keep your data separate from everyone else's; to
        provide the features you ask for; to tell you about the service where you have
        asked us to; to keep the service secure and working; and to meet legal
        obligations. Where the law requires a legal basis, ours is the performance of
        our agreement with you, our legitimate interest in operating and securing the
        service, and your consent for the waitlist.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(5, "Cookies", """
        <p>QuantWizard sets a session cookie that keeps you signed in across page
        refreshes. It is strictly necessary for the service to function, and removing
        it signs you out. We set no advertising or tracking cookies.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(6, "Who else processes your data", """
        <p>We use a small number of service providers, each handling data only to
        deliver their part of the service:</p>
        <ul>
            <li><b>Our identity provider</b> — authentication and account security.</li>
            <li><b>Our database provider</b> — storing your saved portfolios, tracked
                portfolios and waitlist entry.</li>
            <li><b>Our hosting provider</b> — running the application and its logs.</li>
            <li><b>Our payment provider</b> — processing subscription payments, if and
                when paid plans are enabled.</li>
        </ul>
        <p>Market and filing data providers are worth naming separately: we send them
        the ticker being looked up, never your identity. They do not receive your email
        address, your account, or the contents of your portfolios.</p>
        <p>We do not sell, rent or trade personal data. We would disclose it only where
        legally compelled, to protect our rights or someone's safety, or to a successor
        if the service were ever transferred — in which case this policy would continue
        to apply until you were told otherwise.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(7, "How long we keep it", """
        <p>Account and portfolio data is kept while your account exists. Deleting a
        saved or tracked portfolio in the app removes it from our database. Ask us to
        close your account and we will delete your data within 30 days, apart from
        anything we must keep for legal or accounting reasons. Waitlist entries are
        kept until you ask to be removed. Operational logs are held on our hosting
        provider's standard retention schedule.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(8, "Security", """
        <p>Data is transmitted over encrypted connections and stored with established
        providers. Authentication is delegated so credentials are not ours to lose. No
        system is perfectly secure, though, and we cannot guarantee absolute security —
        tell us promptly if you think your account has been accessed by someone
        else.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(9, "Your rights", f"""
        <p>Wherever you live, you may ask us to give you a copy of the personal data we
        hold about you, correct it, delete it, or stop using it for a particular
        purpose. Depending on where you live — for example in the EEA, the UK, or
        California — you may also have rights to portability, to object to certain
        processing, and not to be discriminated against for exercising any of
        them.</p>
        <p>Write to {_contact()} and we will act on it. We will not charge you for
        making a request, and we will not treat you differently for having made one.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(10, "Children", """
        <p>QuantWizard is not intended for anyone under 18 and we do not knowingly
        collect data from children. If you believe a child has given us personal data,
        contact us and we will delete it.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(11, "Where your data is held", """
        <p>QuantWizard is operated from the United States and our providers may store
        and process data there and in other countries. If you use the service from
        outside the United States, you understand your data will be transferred to and
        processed in the United States, where privacy law differs from your own.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(12, "Changes", """
        <p>If this policy changes we will update the effective date above, and for
        material changes we will tell you where we hold your address.</p>
    """), unsafe_allow_html=True)

    st.markdown(_section(13, "Contact", f"""
        <p>Privacy questions and requests go to {_contact()}.</p>
    """), unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# SIGN-IN + ACCEPTANCE GATE
# ══════════════════════════════════════════════════════════════════════════════
# Two conditions guard the Portfolio Builder: a signed-in identity, and a
# recorded acceptance of the terms.
#
# Why the checkbox is worded the way it is. The instinct is to have the user tick
# "I agree I cannot sue", and that specific promise is worth very little: courts
# routinely void blanket liability waivers, and consumer-protection statutes in
# several states override them regardless of what was ticked. What does hold is
# ordinary clickwrap — an affirmative act, next to a legible link to the terms,
# recorded with a version and a timestamp. The protection then comes from the
# clauses the user agreed to (§2 no advice, §11 limitation of liability), which
# is where such language belongs and where it is drafted to survive.
#
# So: the checkbox binds them to the document, and the document does the work.

TERMS_VERSION = "1.0"

_ACCEPT_KEY = "_terms_accepted_v"


def has_accepted(email: str | None) -> bool:
    """Whether this user has accepted the current terms, session or database."""
    if st.session_state.get(_ACCEPT_KEY) == TERMS_VERSION:
        return True
    if not email:
        return False
    try:
        from database import has_accepted_terms
        if has_accepted_terms(email, TERMS_VERSION):
            st.session_state[_ACCEPT_KEY] = TERMS_VERSION
            return True
    except Exception:
        pass
    return False


def _record_acceptance(email: str | None) -> None:
    st.session_state[_ACCEPT_KEY] = TERMS_VERSION
    if not email:
        return
    try:
        from database import save_terms_acceptance
        save_terms_acceptance(email, TERMS_VERSION)
    except Exception:
        # An unrecorded acceptance is worth less, but blocking a paying user
        # because the database blinked would be worse.
        pass


def require_agreement(feature: str = "the Portfolio Builder") -> bool:
    """Gate a feature behind sign-in and terms acceptance.

    Returns True when the caller may render the feature. Renders the sign-in
    prompt or the agreement itself and returns False otherwise.
    """
    import auth

    # ── Condition 1: identity ────────────────────────────────────────────────
    # When auth isn't configured on this instance there is no sign-in to require
    # — but the agreement below still runs, so the legal gate never silently
    # disappears just because a deployment is missing its Auth0 secrets.
    if auth.auth_configured() and not auth.is_signed_in():
        st.markdown(f"""
        <div style="max-width:620px;margin:2rem auto;padding:2rem 2.25rem;
                    border:1px solid #e2e8f0;border-radius:12px;background:#fff">
            <div style="font-size:1.35rem;font-weight:700;color:#0f172a;
                        margin-bottom:0.5rem">Sign in to use {feature}</div>
            <div style="font-size:0.92rem;color:#475569;line-height:1.7">
                Building a portfolio means saving and tracking it against your
                account, and it requires agreeing to our terms — both of which
                need to know who you are. Signing in takes a moment and we never
                see your password.
            </div>
        </div>""", unsafe_allow_html=True)
        _c = st.columns([1, 1.1, 1])
        with _c[1]:
            if st.button("Sign in", type="primary", use_container_width=True,
                         key="gate_sign_in"):
                auth.sign_in()
        return False

    email = auth.current_email()

    # ── Condition 2: recorded assent ─────────────────────────────────────────
    if has_accepted(email):
        return True

    st.markdown(f"""
    <div style="max-width:660px;margin:2rem auto 0;padding:2rem 2.25rem 1.25rem;
                border:1px solid #e2e8f0;border-radius:12px;background:#fff">
        <div style="font-size:1.35rem;font-weight:700;color:#0f172a;
                    margin-bottom:0.75rem">Before you build a portfolio</div>
        <div style="font-size:0.92rem;color:#334155;line-height:1.75">
            <p style="margin:0 0 0.85rem"><b>This is not financial advice.</b>
            QuantWizard is not a registered investment adviser, a broker-dealer or
            a financial planner. {feature.capitalize()} runs published
            portfolio-construction methods over historical price data and shows
            you the output. It does not know your finances, tax position, time
            horizon or obligations, and no holding or weight it produces is a
            recommendation to buy or sell anything.</p>
            <p style="margin:0 0 0.85rem"><b>The output is modelled, not
            predicted.</b> Backtests are hypothetical and survivorship-biased,
            forecasts are simulations rather than forecasts of fact, and past
            performance does not guarantee future results. Investing risks the
            loss of your principal.</p>
            <p style="margin:0"><b>Your decisions are your own.</b> Section 11 of
            the Terms limits what we are liable for, including investment losses.
            Read it — it is short and it is the clause that matters most.</p>
        </div>
    </div>""", unsafe_allow_html=True)

    _c = st.columns([1, 6, 1])
    with _c[1]:
        agreed = st.checkbox(
            "I have read and agree to the Terms of Service and Privacy Policy, "
            "and I understand that QuantWizard does not provide investment advice.",
            key="gate_agree_box")
        st.markdown(
            '<div style="font-size:0.78rem;color:#94a3b8;margin:-0.4rem 0 0.9rem">'
            'Open the <a href="?page=terms" target="_blank" style="color:#64748b">'
            'Terms of Service</a> and <a href="?page=privacy" target="_blank" '
            'style="color:#64748b">Privacy Policy</a> in a new tab.</div>',
            unsafe_allow_html=True)
        if st.button("Agree and continue", type="primary",
                     use_container_width=True, disabled=not agreed,
                     key="gate_agree_btn"):
            _record_acceptance(email)
            st.rerun()

    return False


# ── Footer link strip ─────────────────────────────────────────────────────────
# Rendered on every page, so the documents are reachable from anywhere — including
# from the waitlist form, which is the point at which we first collect an address.
# These are `?page=` links rather than buttons because a legal document should be
# something a person can link to, bookmark, and send to a lawyer.

def render_legal_links() -> str:
    return """
    <div style="max-width:1100px;margin:2.5rem auto 0;padding-top:1rem;
                border-top:1px solid #e2e8f0;display:flex;gap:1.25rem;
                flex-wrap:wrap;align-items:center;
                font-size:0.78rem;color:#94a3b8">
        <span>&copy; QuantWizard</span>
        <a href="?page=terms" target="_self"
           style="color:#64748b;text-decoration:none">Terms of Service</a>
        <a href="?page=privacy" target="_self"
           style="color:#64748b;text-decoration:none">Privacy Policy</a>
    </div>"""
