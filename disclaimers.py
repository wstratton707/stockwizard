"""
disclaimers.py — Central source of truth for all legal and methodological disclosures.

Import and use these in the UI rather than writing inline disclaimer text.
Update this file when legal review suggests changes — one place to maintain.
"""

# ── Short inline disclaimer (under charts, download buttons) ─────────────────
SHORT = (
    "For informational purposes only. Not investment advice. "
    "Past performance does not guarantee future results."
)

# ── Backtest-specific disclosure ──────────────────────────────────────────────
BACKTEST = (
    "Backtested performance is hypothetical. Results are simulated using historical "
    "price data adjusted for splits and dividends (via Yahoo Finance, with Polygon as "
    "a fallback) and do not reflect "
    "actual trading. Backtests are subject to survivorship bias — the stock universe "
    "contains only currently-listed companies; delisted or bankrupt securities are "
    "excluded, which may overstate historical returns. Transaction costs are modelled "
    "at 0.10% per rebalance event. Taxes, bid-ask spread, and market impact are not "
    "modelled. Actual results would differ."
)

# ── Monte Carlo / forecast disclosure ────────────────────────────────────────
MONTE_CARLO = (
    "Monte Carlo projections are based on historical return distributions and are "
    "inherently uncertain. Simulated paths assume log-normal returns and stationary "
    "correlations — real markets exhibit fat tails, regime changes, and correlation "
    "breaks during crises that models cannot fully capture. Milestone probabilities "
    "(e.g. 'P50') reflect median simulated outcomes, not guaranteed results. "
    "Do not rely on these projections for financial planning without professional advice."
)

# ── Stress test disclosure ────────────────────────────────────────────────────
STRESS_TEST = (
    "Stress results are model estimates, not historical replays. Each holding's move "
    "is approximated as its market beta (estimated from ~2 years of recent daily "
    "returns vs the S&P 500) multiplied by the index's known decline in each crisis, "
    "floored at −95% for a long position. This is a first-order sensitivity estimate: "
    "it assumes betas stay roughly stable, whereas betas and correlations typically "
    "rise in a real crisis, so true drawdowns can be deeper. Holdings whose recent "
    "returns can't be fetched are excluded from a scenario. Past crises do not predict "
    "future drawdowns."
)

# ── Portfolio optimisation disclosure ────────────────────────────────────────
OPTIMISATION = (
    "Portfolio weights are derived from mean-variance optimisation (Markowitz, 1952) "
    "using 2 years of historical returns. Optimised weights are sensitive to the "
    "input return and covariance estimates, which change over time. The 'recommended' "
    "portfolio blends maximum-Sharpe and minimum-volatility allocations weighted by "
    "your risk tolerance. This is a mathematical output, not a personalised financial "
    "recommendation. Sector concentration is capped at 40% per sector; individual "
    "positions are capped at 30%."
)

# ── Dividend / price data note ────────────────────────────────────────────────
DIVIDENDS = (
    "Price data is sourced from Yahoo Finance (with Polygon as a fallback) using split- "
    "and dividend-adjusted closing prices. Dividend reinvestment is implicitly reflected "
    "in the adjusted price series "
    "but does not model the timing or tax treatment of actual dividend payments."
)

# ── Full legal footer (landing page, bottom of reports) ──────────────────────
FULL_FOOTER = (
    "QuantWizard is a financial data and analytics platform. It is not a registered "
    "investment adviser, broker-dealer, or financial planner. All analysis, rankings, "
    "portfolio weights, forecasts, and stress test results are generated algorithmically "
    "for informational and educational purposes only. Nothing on this platform "
    "constitutes investment advice, a solicitation, or an offer to buy or sell any "
    "security. QuantWizard does not have knowledge of your individual financial "
    "situation, investment objectives, or risk tolerance. Past performance of any "
    "strategy or security does not guarantee future results. Investing involves risk, "
    "including the possible loss of principal. Always consult a licensed financial "
    "advisor, accountant, or attorney before making investment decisions."
)

# ── HTML rendering helpers ────────────────────────────────────────────────────

def render_inline(text: str = SHORT) -> str:
    """Returns an HTML disclaimer box for st.markdown(..., unsafe_allow_html=True)."""
    return f"""
    <div style="background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;
                padding:0.65rem 1rem;font-size:0.76rem;color:#92400e;
                margin-top:0.75rem;line-height:1.55">
        {text}
    </div>"""


def render_section(title: str, text: str) -> str:
    """Returns a labelled expandable-style disclosure box."""
    return f"""
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-left:3px solid #94a3b8;
                border-radius:6px;padding:0.75rem 1rem;font-size:0.74rem;
                color:#475569;margin-top:0.5rem;line-height:1.6">
        <strong style="color:#334155;font-size:0.76rem;text-transform:uppercase;
                       letter-spacing:0.5px">{title}</strong><br>
        {text}
    </div>"""


def render_footer() -> str:
    """Full legal footer block."""
    return f"""
    <div style="background:#f1f5f9;border-top:1px solid #e2e8f0;
                border-radius:8px;padding:1.25rem 1.5rem;
                font-size:0.73rem;color:#64748b;line-height:1.65;margin-top:2rem">
        <strong style="color:#334155;display:block;margin-bottom:0.4rem">
            Legal Disclosure
        </strong>
        {FULL_FOOTER}
        <div style="margin-top:0.75rem;color:#94a3b8;font-size:0.7rem">
            Market data provided by Polygon, Yahoo Finance, and Finnhub; company
            fundamentals from SEC EDGAR. Risk-free rate sourced from the US Federal
            Reserve via FRED.
            {DIVIDENDS}
        </div>
    </div>"""
