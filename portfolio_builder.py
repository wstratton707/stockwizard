import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime

import auth
from constants import (DEV_MODE_FREE, get_risk_free_rate,
                       get_long_risk_free_rate, EQUITY_RISK_PREMIUM)
from allocators import risk_ladder
from disclaimers import render_inline, render_section
import disclaimers as _disc

from portfolio_data import (
    fetch_portfolio_prices, fetch_portfolio_prices_cached,
    build_candidate_universe, select_by_factors,
    get_ticker_info, get_sharpe_rankings,
    SECTOR_UNIVERSE, SECTOR_ETFS,
    BOND_UNIVERSE, BOND_ETFS,
)
# Cached wrappers for repeat-call hot paths (validation + ticker info during
# step transitions and rebuilds). Same call shape as their underlying funcs.
from cached_fetchers import (
    cached_validate_ticker as _cached_validate_ticker,
    cached_get_ticker_info as _cached_get_ticker_info,
)
from database import save_portfolio, load_portfolios, delete_portfolio, save_tracked_portfolio
from tracker import dollars_to_lots
from portfolio_analysis import (
    compute_stock_metrics, compute_correlation_matrix,
    optimise_portfolio, generate_efficient_frontier,
    backtest_portfolio, compute_backtest_metrics,
    compute_monthly_heatmap, run_portfolio_monte_carlo,
    compute_diversification_score, get_rebalancing_recommendations,
    compute_betas, capm_expected_returns, portfolio_beta, shrunk_covariance,
    portfolio_capm,
    factor_tilted_expected_returns, FACTOR_ALPHA_MAX,
)
# Report builders are imported at the point of use, not here.
# Between them they pull in matplotlib, openpyxl, python-docx and python-pptx
# — about 100 MB of resident memory that every visitor paid for even though
# only the ones who click Export ever need it. Python never releases an
# imported module, so an eager import is a permanent tax on the whole process.
from importlib.util import find_spec
PPTX_AVAILABLE = find_spec("pptx") is not None   # cheap: does not import pptx
import chart_theme as ct

# Accent for the goal/target callout and the rolling-correlation series. The
# categorical ramp owns it, so it stays in step with every other multi-series
# chart instead of being a private violet.
_ACCENT = ct.series_color(3)

# The shared red→white→blue ramp, as the list-of-pairs form Plotly expects.
_DIVERGING = [list(_stop) for _stop in ct.color.diverging]

# These were a private hex palette. They are now aliases onto the shared chart
# tokens, so the single source of truth still holds, but the names survive —
# they are referenced from ~30 call sites, most of them KPI cards and raw HTML
# rather than chart code, and rewriting those is churn with no visible payoff.
DARK   = ct.color.ink
BLUE   = ct.color.brand
GREEN  = ct.color.positive
RED    = ct.color.negative
AMBER  = ct.color.value_line
PURPLE = _ACCENT
MUTED  = ct.color.ink_muted

# ── Session state keys ────────────────────────────────────────────────────────
_K_STEP        = "port_step"
_K_PREFS       = "port_prefs"
_K_OPTIMISED   = "port_optimised"
_K_WEIGHTS     = "port_selected_weights"
_K_BACKTEST    = "port_backtest"
_K_MC          = "port_mc"
_K_EXCEL       = "port_excel"
_K_PPTX        = "port_pptx"
_K_FOUND_PORTS = "found_portfolios"
_K_RANKINGS    = "port_rankings"   # cached get_sharpe_rankings result

# ── Optimizer / fetch config ──────────────────────────────────────────────────
# How many names the user can ask for. This used to be a single hard-coded 18
# fed straight to the optimiser, which meant the portfolio contained 18 holdings
# whatever the user chose — the "minimum holdings" slider only ever ran as a
# post-optimisation pruning floor and never reached selection, so asking for 20
# silently produced 18. The count is now a preference threaded through candidate
# pooling, sector budgets, selection and the pruning floor.
_MIN_HOLDINGS_ALLOWED  = 10     # below this, mean-variance has too little to work with
_MAX_HOLDINGS_ALLOWED  = 50     # above this, weights fall under a basis point of signal
_DEFAULT_HOLDINGS      = 18     # the previous hard cap, kept as the default
_PRICE_HISTORY_YEARS   = 5      # years of OHLCV history fetched per candidate
                               # (yfinance has no depth cap; longer window → more
                               #  stable covariance/betas + real crashes in risk stats)
_EF_PORTFOLIOS         = 4_000  # random portfolios for efficient-frontier scatter
                                # (4k renders an identical-looking cloud ~2x faster)
_MC_SIMULATIONS        = 1_000  # Monte Carlo paths
_MIN_WEIGHT            = 0.01   # positions below 1% are dropped post-optimisation
_FETCH_WORKERS         = 5      # ThreadPoolExecutor pool size for parallel fetches
_TOP_N_PER_SECTOR      = 2      # candidates kept per sector in fallback universe


def _target_holdings(prefs) -> int:
    """How many names the user asked for, clamped to what the engine supports.

    Reads the old `min_holdings` key as a fallback so a portfolio saved before
    this control changed still rebuilds instead of silently reverting to 18.
    """
    n = prefs.get("target_holdings", prefs.get("min_holdings", _DEFAULT_HOLDINGS))
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = _DEFAULT_HOLDINGS
    return max(_MIN_HOLDINGS_ALLOWED, min(_MAX_HOLDINGS_ALLOWED, n))


def _prune_floor(n: int) -> float:
    """Weight below which a position is dropped as noise, scaled to portfolio size.

    A flat 1% is right at 18 names (equal weight 5.6%, so 1% is a fifth of a
    normal position) and wrong at 50, where equal weight is 2% and a flat 1%
    would prune half the tail the user explicitly asked for — reproducing, at
    the top of the range, exactly the "I asked for N and got fewer" complaint
    this change exists to fix. Never looser than the old 1%.
    """
    return min(_MIN_WEIGHT, 0.2 / max(n, 1))


ALL_SECTORS        = list(SECTOR_UNIVERSE.keys())
ALL_BOND_CATEGORIES = list(BOND_UNIVERSE.keys())

# Built once at import time — reused in every render
def _build_sector_lookup():
    lkp = {}
    for s, tl in SECTOR_UNIVERSE.items():
        for t in tl:
            lkp[t] = s
    for s, etf in SECTOR_ETFS.items():
        lkp[etf] = f"{s} ETF"
    for cat, tl in BOND_UNIVERSE.items():
        for t in tl:
            lkp[t] = "Bonds"
    bond_set = {t for tl in BOND_UNIVERSE.values() for t in tl}
    return lkp, bond_set

_SECTOR_LOOKUP, _BOND_SET = _build_sector_lookup()


def _metric_card(label, value, color=ct.color.ink, subtitle=None):
    sub = f"<div style='font-size:0.75rem;color:#64748b;margin-top:2px'>{subtitle}</div>" if subtitle else ""
    return f"""
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;
                padding:1.1rem;text-align:center">
        <div style="font-size:0.68rem;font-weight:600;letter-spacing:0.5px;
                    text-transform:uppercase;color:#64748b;margin-bottom:0.35rem">{label}</div>
        <div style="font-family:'JetBrains Mono',monospace;font-size:1.25rem;
                    font-weight:500;color:{color}">{value}</div>
        {sub}
    </div>"""


def _section_header(text):
    st.markdown(f"""
    <div style="font-size:0.7rem;font-weight:600;letter-spacing:0.8px;text-transform:uppercase;
                color:#64748b;border-bottom:1px solid #e2e8f0;padding-bottom:0.5rem;
                margin-bottom:1rem;margin-top:1.75rem">{text}</div>
    """, unsafe_allow_html=True)


# ── Step 0 — Preferences ──────────────────────────────────────────────────────

def _factor_caption(meta: dict) -> str:
    """Describe the model the CACHED DATA actually contains, not the one the
    code implements.

    These drifted apart in production: the interface asserted a six-factor
    sector-relative composite with an analyst adjustment while the live
    rankings still held the previous four-factor blend, because the cron had
    not re-run since the rework. Reading the stamped version means the copy
    cannot outrun the data again.
    """
    from factor_model import FACTOR_MODEL_VERSION, WEIGHTS
    v = meta.get("factor_model_version")
    stamp = (f"  ·  Rankings computed {meta.get('computed_at', 'unknown')}."
             if meta.get("computed_at") else "")

    if v == FACTOR_MODEL_VERSION:
        mix = " · ".join(f"{k} {w}%" for k, w in WEIGHTS.items())
        return (
            f"**Factor score** is the sector-relative composite ({mix}), plus an "
            f"analyst-consensus adjustment of at most ±3 points where three or more "
            f"analysts cover the name. Value averages earnings yield, free-cash-flow "
            f"yield and EBITDA/EV against sector peers; volatility is ranked across "
            f"the whole universe, because the low-volatility effect is market-wide "
            f"rather than within-sector. Financials are scored without EV/EBITDA, "
            f"free cash flow or leverage ratios, which do not describe a bank. "
            f"Missing data is neutral, never zero. **Tilt vs CAPM** is how much the "
            f"score moves the expected return behind the Maximum Sharpe and Minimum "
            f"Volatility models, capped at ±2%/yr. The Risk-Matched Model sizes "
            f"positions from the covariance matrix alone." + stamp)

    return (
        f"**Factor score** comes from the previous factor model (v{v or 'unversioned'}), "
        f"because the nightly ranking job has not yet re-run since the current model "
        f"(v{FACTOR_MODEL_VERSION}) shipped. Names were ranked on momentum, quality, "
        f"low volatility and risk-adjusted return, with quality measured across the "
        f"whole universe rather than within sector. Sector-relative valuation, growth "
        f"and financial-health factors are **not** reflected in these scores yet."
        + stamp)


def _render_step_0():
    st.markdown(render_inline(_disc.BUILDER_SCOPE), unsafe_allow_html=True)

    _section_header("Model Inputs")
    preset_choice = st.selectbox(
        "Starting preset",
        ["Balanced", "Conservative", "Growth", "Aggressive"],
        index=0,
        help="A starting point for the model. Change any input below."
    )
    preset_map = {"Conservative": 3, "Balanced": 5, "Growth": 7, "Aggressive": 8}
    risk = st.slider(
        "Risk Level",
        min_value=1, max_value=10, value=preset_map[preset_choice],
        help="A modelling input, not a suitability assessment. It slides the model "
             "between three risk-based weightings: 1 = minimum variance, "
             "5-6 = equal risk contribution, 10 = equal weight."
    )
    st.caption(f"Preset: {preset_choice.lower()} · higher settings hold more volatility "
               f"and spread capital more evenly; lower settings concentrate into the "
               f"calmest holdings.")
    # These describe what the optimiser DOES. The previous copy promised
    # "maximum growth, heavy equities" at the top of the scale and "all-in on
    # high-growth equities" at 10, while the ladder actually converges on equal
    # weight — measured weight spread 19.8pp at level 2, 11.1pp at 5, 2.1pp at
    # 9, and level 10 is literally 1/N. The aggressive build was the MOST
    # diversified of the three. Copy that inverts the algorithm is worse than
    # no copy.
    risk_labels = {
        (1,3): ("Concentrated in the calmest names",
                "Minimum-variance weighting. Fewest effective holdings, largest "
                "single positions, lowest expected volatility."),
        (4,6): ("Balanced by risk contribution",
                "Each holding contributes a similar share of portfolio risk. "
                "Volatile names get smaller positions."),
        (7,9): ("Spread more evenly",
                "Moves toward equal weight. More effective holdings, smaller "
                "maximum position, higher expected volatility."),
        (10,10):("Equal weight",
                "Every holding the same size. Highest expected volatility and "
                "the widest spread of capital, with no view on which name is best."),
    }
    for (lo,hi),(label,desc) in risk_labels.items():
        if lo <= risk <= hi:
            st.markdown(f"""
            <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
                        padding:0.85rem 1rem;margin-top:0.5rem">
                <span style="font-weight:600;color:{ct.color.ink}">{label}</span>
                <span style="color:#64748b;font-size:0.85rem;margin-left:8px">{desc}</span>
            </div>""", unsafe_allow_html=True)

    _section_header("Investment Parameters")
    c1, c2, c3 = st.columns(3)
    horizon = c1.selectbox("Investment Horizon",
        ["1 year", "3 years", "5 years", "10 years", "20+ years"], index=2)
    starting_capital = c2.number_input("Starting Capital ($)",
        min_value=1000, max_value=10_000_000, value=10000, step=1000, format="%d")
    monthly_contribution = c3.number_input("Monthly Contribution ($)",
        min_value=0, max_value=100_000, value=500, step=100, format="%d")

    # Advanced — smart defaults mean most people can skip all of this. Collapsed by
    # default so the primary flow is just risk + capital + contribution. Replaces
    # the ~20 always-on sector/bond checkboxes (which did nothing unless toggled)
    # with a few multiselects that default to sensible behaviour.
    with st.expander("Advanced options  ·  sectors, bonds, exclusions, goal", expanded=False):
        st.caption("Sensible defaults are already applied — most people can skip this.")
        target_value = st.number_input("Target goal ($) — optional",
            min_value=0, max_value=100_000_000, value=0, step=5000, format="%d",
            help="Leave at 0 if you don't have a specific goal.")
        exclude_sector_names = st.multiselect(
            "Exclude sectors", ALL_SECTORS, default=[],
            help="Leave empty to consider every sector (the default).")
        _default_bonds = ALL_BOND_CATEGORIES if risk <= 6 else []
        included_bond_categories = st.multiselect(
            "Bond categories to include", ALL_BOND_CATEGORIES, default=_default_bonds,
            help="Bonds add stability; included by default at lower risk levels.")
        excl_industries = st.multiselect(
            "Exclude industries (values-based)",
            ["Tobacco", "Defense", "Fossil Fuels", "Gambling"], default=[])

    included_sectors = [s for s in ALL_SECTORS if s not in exclude_sector_names]
    excluded_sectors = []
    if "Fossil Fuels" in excl_industries: excluded_sectors.append("Energy")
    if "Defense" in excl_industries:      excluded_sectors.append("Industrials")

    st.markdown("---")
    if st.button("Next → Build Universe", type="primary", key="step0_next"):
        st.session_state[_K_PREFS] = {
            "risk_tolerance":          risk,
            "horizon":                 horizon,
            "starting_capital":        starting_capital,
            "monthly_contribution":    monthly_contribution,
            "target_value":            target_value if target_value > 0 else None,
            "include_sectors":         included_sectors,
            "exclude_sectors":         excluded_sectors,
            "include_bond_categories": included_bond_categories,
            "user_tickers":            [],
            "exclude_tickers":         [],
        }
        st.session_state[_K_STEP] = 1
        st.rerun()


# ── Step 1 — Universe ─────────────────────────────────────────────────────────

def _render_step_1(api_key):
    prefs = st.session_state.get(_K_PREFS, {})

    _section_header("Add Your Own Stocks (Optional)")
    st.markdown("<div style='font-size:0.85rem;color:#64748b;margin-bottom:0.5rem'>"
                "QuantWizard will suggest a portfolio automatically. "
                "You can optionally add tickers you specifically want included.</div>",
                unsafe_allow_html=True)

    user_tickers_raw = st.text_input(
        "Tickers to INCLUDE (comma separated)",
        placeholder="e.g. AAPL, TSLA, NVDA",
        key="user_tickers_input"
    )
    exclude_tickers_raw = st.text_input(
        "Tickers to EXCLUDE (comma separated)",
        placeholder="e.g. META, AMZN",
        key="exclude_tickers_input"
    )

    _section_header("Portfolio Style")
    col1, col2 = st.columns(2)
    with col1:
        use_etfs = st.checkbox("Include Sector ETFs (XLK, XLV etc.)", value=True,
                               help="ETFs provide broad sector exposure with lower volatility")
        max_per_stock = st.slider(
            "Max weight per stock (%)", 5, 40, 25, step=5,
            help="Caps single-position concentration during optimization.",
        )
        min_mcap_label = st.selectbox(
            "Minimum market cap", ["Any", "$2B+", "$10B+", "$50B+"], index=0,
            help="Filters auto-selected names by estimated market cap (diluted "
                 "shares from SEC filings times the latest close). Tickers you "
                 "add yourself are never filtered; names without an estimate "
                 "(ETFs) pass.",
        )
    with col2:
        target_holdings = st.slider(
            "Number of holdings", _MIN_HOLDINGS_ALLOWED, _MAX_HOLDINGS_ALLOWED,
            _DEFAULT_HOLDINGS, step=1,
            help="How many positions to build. This drives selection, so the "
                 "portfolio comes back with this many names — if the screen "
                 "cannot fill them after your sector and market-cap filters, "
                 "you'll be told how many it found and why.",
        )
        style_tilt = st.radio(
            "Style tilt", ["Balanced", "Value tilt", "Growth tilt"],
            index=0, horizontal=True, key="style_tilt_radio",
            help="Leans the screen toward cheaper names (value: higher earnings "
                 "yield vs sector peers) or faster-growing ones (growth: revenue "
                 "and EPS growth vs sector peers). Affects which names are "
                 "selected, not how they are weighted.",
        )

    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("← Back", key="step1_back"):
            st.session_state[_K_STEP] = 0
            st.rerun()
    with col2:
        if st.button("Next → Optimise Portfolio", type="primary", key="step1_next"):
            user_tickers = [t.strip().upper() for t in user_tickers_raw.split(",") if t.strip()]
            excl_tickers = [t.strip().upper() for t in exclude_tickers_raw.split(",") if t.strip()]

            # Validate all user-supplied tickers before proceeding
            all_to_validate = list(dict.fromkeys(user_tickers + excl_tickers))
            invalid = []
            if all_to_validate:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                with st.spinner(f"Validating {len(all_to_validate)} ticker(s)..."):
                    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as _exe:
                        _futs = {_exe.submit(_cached_validate_ticker, t, api_key): t
                                 for t in all_to_validate}
                        for _fut in as_completed(_futs):
                            t = _futs[_fut]
                            try:
                                ok, _ = _fut.result()
                                if not ok:
                                    invalid.append(t)
                            except Exception:
                                invalid.append(t)

            if invalid:
                _inc_bad = [t for t in invalid if t in user_tickers]
                _exc_bad = [t for t in invalid if t in excl_tickers]
                _parts = []
                if _inc_bad:
                    _parts.append(f"Include — {', '.join(_inc_bad)}")
                if _exc_bad:
                    _parts.append(f"Exclude — {', '.join(_exc_bad)}")
                st.error(f"Unrecognised ticker(s): {' · '.join(_parts)}. "
                         f"Check the symbols and try again.")
            else:
                prefs["user_tickers"]    = user_tickers
                prefs["exclude_tickers"] = excl_tickers
                prefs["use_etfs"]        = use_etfs
                prefs["max_per_stock"]   = max_per_stock / 100
                prefs["target_holdings"] = target_holdings
                prefs["style_tilt"]      = style_tilt
                prefs["min_mcap"]        = {"Any": 0, "$2B+": 2e9, "$10B+": 10e9,
                                            "$50B+": 50e9}[min_mcap_label]
                st.session_state[_K_PREFS] = prefs
                st.session_state[_K_STEP]  = 2
                st.rerun()


# ── Step 2 — Optimise ─────────────────────────────────────────────────────────

def _render_step_2(api_key):
    prefs = st.session_state.get(_K_PREFS, {})

    if _K_OPTIMISED not in st.session_state:
        progress  = st.progress(0, text="Building candidate universe...")
        log_area  = st.empty()
        log_lines = []

        def log(msg):
            log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}]  {msg}")
            log_area.code("\n".join(log_lines[-10:]), language=None)

        try:
            from collections import defaultdict

            ALWAYS_KEEP    = {"SPY", "QQQ", "GLD", "TLT"}
            risk_tolerance = prefs.get("risk_tolerance", 5)
            excl_sectors   = set(prefs.get("exclude_sectors", []))
            incl_sectors   = set(prefs.get("include_sectors", list(SECTOR_UNIVERSE.keys())))
            excl_tickers   = set(t.upper() for t in prefs.get("exclude_tickers", []))
            user_tickers   = [t.upper() for t in prefs.get("user_tickers", [])]
            target_n       = _target_holdings(prefs)

            # ── Try pre-computed multi-factor rankings (considers ALL ~330 tickers) ──
            # Cache in session so back-and-forward navigation doesn't re-fetch.
            used_precompute = False
            if _K_RANKINGS not in st.session_state:
                st.session_state[_K_RANKINGS] = get_sharpe_rankings(api_key)
            rankings = st.session_state[_K_RANKINGS]

            if rankings:
                used_precompute = True
                # Use .get not .pop — .pop mutates the cached session-state dict
                # so subsequent renders (back/forward navigation) lose _meta.
                meta        = rankings.get("_meta", {})
                computed_at = meta.get("computed_at", "unknown")
                is_partial  = meta.get("partial", False)
                n_ranked    = len(rankings)
                freshness   = f"{'partial — ' + str(meta.get('tickers_done','?')) + '/' + str(meta.get('tickers_total','?')) + ' tickers · ' if is_partial else ''}{computed_at}"
                log(f"   Pre-computed rankings loaded — {n_ranked} tickers · computed {freshness}")
                progress.progress(10, text=f"Selecting best stocks from {n_ranked}-ticker universe...")

                # Style tilt: a bounded bonus on the sector-relative value or
                # growth rank. (f - 0.5) spans ±0.5, so ×0.3 moves the selection
                # score by at most ±0.15 — enough to reorder a sector's middle,
                # not enough to drag a bottom-decile name to the top. Selection
                # only: weights still come from the risk model.
                _style = prefs.get("style_tilt", "Balanced")
                def _sel_score(data):
                    base = data.get("score", data.get("sharpe", 0)) or 0
                    if _style == "Value tilt":
                        return base + 0.3 * (data.get("f_value", 0.5) - 0.5)
                    if _style == "Growth tilt":
                        return base + 0.3 * (data.get("f_growth", 0.5) - 0.5)
                    return base

                # Hard eligibility filters + the per-sector cut, in
                # factor_model.eligible_universe. Data quality and
                # investability only — nothing is dropped for scoring badly on
                # a factor, so a cheap unloved name still reaches the optimiser
                # and loses there on its merits rather than here.
                from factor_model import eligible_universe as _elig
                # Market and Commodities are always admissible: they are the
                # benchmark and diversifier sleeves the pinning logic below
                # draws on. Bond sleeves are admitted ONLY where the user asked
                # for them — each bond category is its own sector label, so
                # admitting them all hands six of eighteen slots to bonds.
                _bond_sectors = {f"Bond-{c}" for c in
                                 prefs.get("include_bond_categories", [])}
                if _bond_sectors:
                    _bond_sectors |= {"Government"}
                _elig_sectors = set(incl_sectors) | {"Market", "Commodities"} | _bond_sectors
                _eligible, _diag = _elig(
                    rankings,
                    include_sectors=_elig_sectors,
                    exclude_sectors=excl_sectors,
                    exclude_tickers=excl_tickers,
                    min_market_cap=prefs.get("min_mcap", 0),
                    always_keep=set(user_tickers),
                )
                _eligible_set = set(_eligible)
                if _diag["gated"]:
                    log(f"   Screen gate excluded {len(_diag['gated'])}: "
                        f"{', '.join(sorted(_diag['gated'])[:8])}"
                        f"{' …' if len(_diag['gated']) > 8 else ''}")
                if _diag["too_small"]:
                    log(f"   Market-cap floor excluded {len(_diag['too_small'])} name(s)")
                log(f"   Eligible universe: {len(_eligible)} of {n_ranked} "
                    f"— per-sector cut {_diag['sector_cut']}")

                # Group the eligible names by sector for the slot allocation
                # below, which balances sector representation against score.
                sector_groups: dict = defaultdict(list)
                for ticker in _eligible:
                    data = rankings.get(ticker)
                    if not isinstance(data, dict):
                        continue
                    _sec = data.get("sector", "Unknown")
                    # Every bond category carries its own sector label
                    # ("Bond-Municipal", "Bond-Corporate", "Government", ...).
                    # The slot allocation below guarantees each sector a floor,
                    # so leaving them separate handed six of eighteen slots to
                    # fixed income and returned a "balanced" portfolio at beta
                    # 0.25. Bonds are one sleeve competing for one floor slot,
                    # not six sectors.
                    #
                    # Worth recording that this only became visible now: the
                    # previous sector filter dropped every bond label, so the
                    # user's bond-category preference had no effect at all.
                    if _sec.startswith("Bond-") or _sec == "Government":
                        _sec = "Fixed Income"
                    sector_groups[_sec].append((ticker, _sel_score(data)))

                # Conservative profile — skip growth sectors
                GROWTH_SECTORS = {"Technology", "Consumer Discretionary",
                                  "Communication Services", "Financials"}
                skipped_sectors = []
                if risk_tolerance <= 3:
                    for gs in GROWTH_SECTORS:
                        if gs in sector_groups:
                            del sector_groups[gs]
                            skipped_sectors.append(gs)

                if skipped_sectors:
                    st.info(f"Conservative profile: growth sectors excluded — "
                            f"{', '.join(skipped_sectors)}.")

                # Pin benchmarks
                pinned = ["SPY"]
                if risk_tolerance >= 4:
                    pinned.append("QQQ")
                if risk_tolerance <= 3:
                    pinned += ["GLD", "TLT"]

                # Top 2 per sector by combined score
                candidates = list(user_tickers)
                for t in pinned:
                    if t not in candidates and t not in excl_tickers:
                        candidates.append(t)

                # Flexible sector allocation. Hard top-2-per-sector treated every
                # sector as equally deep: if Health Care held five names scoring
                # 0.96-0.91 and Utilities' best was 0.53, both still contributed
                # exactly two. Now every sector gets a guaranteed floor (so
                # diversification is preserved), and the remaining slots go to the
                # best names anywhere, capped per sector so nothing runs away.
                # The per-sector ceiling has to scale with the requested size or
                # it becomes the real cap: 11 sectors x 4 is 44 names, so a
                # 50-name request could never be filled however deep the pool.
                _MIN_PER_SECTOR = 1
                _MAX_PER_SECTOR = max(4, target_n // 4)
                per_sector = {s: sorted(v, key=lambda x: x[1], reverse=True)
                              for s, v in sector_groups.items()}

                taken = {s: 0 for s in per_sector}
                for sector, ranked in per_sector.items():        # floor first
                    for t, _ in ranked[:_MIN_PER_SECTOR]:
                        if t not in candidates:
                            candidates.append(t)
                            taken[sector] += 1

                # Then fill by global score until the candidate pool is deep enough
                # to give the optimiser real choice (~2.5x the final portfolio).
                pool_target = target_n * 2.5
                remaining = sorted(
                    ((t, sc, s) for s, v in per_sector.items() for t, sc in v),
                    key=lambda x: x[1], reverse=True)
                for t, _sc, sector in remaining:
                    if len(candidates) >= pool_target:
                        break
                    if t in candidates or taken[sector] >= _MAX_PER_SECTOR:
                        continue
                    candidates.append(t)
                    taken[sector] += 1

                _spread = {s: n for s, n in sorted(taken.items(), key=lambda x: -x[1]) if n}
                log(f"   Sector spread: {_spread}")

                # Build sector_map from rankings
                sector_map = {t: rankings[t]["sector"] for t in candidates if t in rankings}
                for t in pinned:
                    sector_map.setdefault(t, "Market")
                for t in user_tickers:
                    sector_map.setdefault(t, "User")

                log(f"   Selected {len(candidates)} candidates from full universe")
            else:
                # ── Fallback: original candidate building (top 5 per sector) ──
                log("   No pre-computed rankings — using live candidate building")
                log("   Tip: run precompute.py daily to enable full-universe selection")
                candidates, sector_map, skipped_sectors = build_candidate_universe(
                    prefs, api_key, log=log)
                if skipped_sectors:
                    st.info(f"Conservative profile: growth sectors excluded — "
                            f"{', '.join(skipped_sectors)}.")

            progress.progress(15, text=f"Fetching 5-year price history for {len(candidates)} candidates...")

            # Fetch prices — uses Supabase cache if available (instant on repeat runs)
            price_dict, close_df, returns_df, failed = fetch_portfolio_prices_cached(
                candidates, period_years=_PRICE_HISTORY_YEARS, api_key=api_key, log=log)
            # price_dict is a per-ticker re-shaping of the closes close_df already
            # holds, and nothing anywhere reads it back — it used to be filtered
            # down and parked in session state for the life of every session, a
            # second copy of the price matrix in a less compact layout. Released
            # here instead.
            del price_dict
            progress.progress(40, text="Finalising stock selection...")

            # Trim to the requested number of holdings for the optimizer.
            # When precompute was used: rank by precompute score (avoids in-sample bias).
            # Fallback: rank by 5-year Sharpe (only option when precompute unavailable).
            if used_precompute:
                available = [t for t in candidates if t in returns_df.columns]
                pinned_set = {t for t, s in sector_map.items()
                              if s in ("Market", "Commodities", "User")}
                # Sector allocation, then selection within sector — NOT a
                # global sort by composite score.
                #
                # Five of the six factors are percentile ranks computed within a
                # sector, so each averages 0.50 in every sector by construction
                # and contributes nothing to a cross-sector comparison. Only
                # low-volatility is absolute. Measured on the live universe,
                # mean quality is exactly 0.50 in all eleven sectors while mean
                # low-vol runs 0.83 (Utilities) to 0.14 (Technology) — and the
                # mean composite tracks it precisely, 55.4 down to 46.1. A
                # global sort by this score is therefore a sort of sectors by
                # inverse volatility wearing a fundamental costume, which is how
                # portfolios came back holding zero Technology however the
                # weights were set.
                from factor_model import select_holdings as _select
                best_tickers = _select(
                    available, rankings,
                    n=target_n,
                    always_keep=[t for t in available if t in pinned_set],
                    max_per_sector=max(3, target_n // 6),
                    score_key=_sel_score)
                _spread2 = {}
                for _t in best_tickers:
                    _s2 = rankings.get(_t, {}).get("sector", "?")
                    _spread2[_s2] = _spread2.get(_s2, 0) + 1
                log(f"   Final portfolio: {len(best_tickers)} stocks across "
                    f"{len(_spread2)} sectors {_spread2} — {', '.join(best_tickers)}")
            else:
                best_tickers = select_by_factors(
                    returns_df, sector_map, max_total=target_n,
                    # 11 sectors x 2 caps the fallback at 22 names; scale it so a
                    # large request is reachable without precompute too.
                    top_n_per_sector=max(_TOP_N_PER_SECTOR, target_n // 9))
                log(f"   Final portfolio: {len(best_tickers)} stocks (multi-factor) — {', '.join(best_tickers)}")
            # Floor guard: mean-variance optimisation, the frontier and the
            # correlation matrix all need a real multi-asset set. If a fetch
            # failure left us with almost nothing, stop cleanly instead of
            # building a degenerate 1-stock "portfolio" or crashing downstream.
            if len(best_tickers) < 3:
                raise ValueError(
                    f"insufficient tickers: only {len(best_tickers)} stock(s) had "
                    f"usable price history — need at least 3 to build a portfolio")

            returns_df = returns_df[best_tickers]
            close_df   = close_df[[t for t in best_tickers if t in close_df.columns]]
            sector_map = {t: sector_map.get(t, "Unknown") for t in best_tickers}
            progress.progress(50, text="Computing stock metrics...")

            # Market (SPY) daily returns for CAPM betas — reuse if SPY is already a
            # holding, else fetch it once (cached). Drives expected returns:
            # E(R) = Rf + beta * ERP, so nothing is forecast off its own recent run.
            if "SPY" in returns_df.columns:
                market_returns = returns_df["SPY"]
            else:
                try:
                    _, _, _spy_df, _ = fetch_portfolio_prices_cached(
                        ("SPY",), period_years=_PRICE_HISTORY_YEARS, api_key=api_key,
                        log=lambda m: None)
                    market_returns = _spy_df["SPY"] if "SPY" in _spy_df.columns else None
                except Exception:
                    market_returns = None
            # Expected returns now carry the factor view, not just beta. Scores
            # come from whichever selection path ran, so the optimiser weights on
            # the same evidence that chose the names.
            _factor_scores = {}
            if used_precompute:
                _factor_scores = {t: rankings[t].get("score")
                                  for t in returns_df.columns
                                  if t in rankings and rankings[t].get("score") is not None}
            _betas  = compute_betas(returns_df, market_returns)
            capm_mu = factor_tilted_expected_returns(_betas, _factor_scores)
            if _factor_scores:
                _tilt = {t: (capm_mu[t] - (get_risk_free_rate() + _betas[t] * EQUITY_RISK_PREMIUM))
                         for t in _factor_scores}
                _hi = max(_tilt.values()) * 100 if _tilt else 0
                _lo = min(_tilt.values()) * 100 if _tilt else 0
                log(f"   Factor tilt applied to expected returns: {_lo:+.1f}% … {_hi:+.1f}% "
                    f"on {len(_factor_scores)} of {len(returns_df.columns)} holdings")

            # Metrics
            stock_metrics = compute_stock_metrics(returns_df, market_returns)
            corr_matrix   = compute_correlation_matrix(returns_df)
            progress.progress(65, text="Running optimisation...")

            # Optimise
            target_ret = None
            if prefs.get("target_value") and prefs.get("starting_capital"):
                horizon_map = {"1 year":1,"3 years":3,"5 years":5,"10 years":10,"20+ years":20}
                yrs = horizon_map.get(prefs.get("horizon","5 years"), 5)
                target_ret = ((prefs["target_value"]/prefs["starting_capital"])**(1/yrs)-1) if yrs > 0 else None

            portfolios = optimise_portfolio(returns_df,
                                            risk_tolerance=prefs.get("risk_tolerance", 5),
                                            target_return=target_ret,
                                            sector_map=sector_map,
                                            max_weight=prefs.get("max_per_stock", 0.30),
                                            expected_returns=capm_mu)

            # ── Risk-matched model: the ladder, not the three-anchor blend ────
            # The blend interpolated min-vol -> max-Sharpe -> max-return, and a
            # walk-forward showed two of those three anchors were the same
            # portfolio: volatility 17.32 / 17.35 / 17.39 and drawdown -38.69 /
            # -38.69 / -38.68 for the blend, max-Sharpe and min-vol. That is the
            # CAPM degeneracy — with mu = Rf + beta*ERP, maximising Sharpe is
            # maximising beta/sigma, which lands on minimum variance — so the
            # risk slider was moving between two points that coincided.
            #
            # GMV -> ERC -> 1/N instead. None of the three needs an expected
            # return, so the degeneracy cannot come back, and the ladder measured
            # monotone in both volatility (17.4 -> 20.6) and drawdown (-38.7 ->
            # -41.6) across levels 1 to 10. max_sharpe / min_vol / max_return are
            # left exactly as they were: they are separate options a user picks
            # deliberately, and the frontier chart plots them.
            portfolios["recommended"] = risk_ladder(
                returns_df,
                risk_tolerance=prefs.get("risk_tolerance", 5),
                sector_map=sector_map,
                max_weight=prefs.get("max_per_stock", 0.30))

            # Re-measure the return target against the weights actually shipped.
            # optimise_portfolio checked it against the blend it no longer
            # returns, so leaving this alone would warn about a target the user's
            # portfolio may well hit, or stay silent about one it misses.
            if target_ret is not None and capm_mu:
                _ach = sum(portfolios["recommended"].get(t, 0.0) * capm_mu.get(t, 0.0)
                           for t in portfolios["recommended"])
                portfolios["target_met"] = _ach >= target_ret * 0.95
                if not portfolios["target_met"]:
                    portfolios["target_achieved"]  = round(_ach * 100, 1)
                    portfolios["target_requested"] = round(target_ret * 100, 1)
            progress.progress(80, text="Generating efficient frontier...")

            # Same CAPM basis as the optimizer + the plotted marker, so the
            # "Your Portfolio" star lands on the cloud instead of floating below it.
            ef_df = generate_efficient_frontier(returns_df, n_portfolios=_EF_PORTFOLIOS,
                                                expected_returns=capm_mu)

            # Get ticker info — parallel fetches
            from concurrent.futures import ThreadPoolExecutor, as_completed
            ticker_info = {}
            with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as _exe:
                _futs = {_exe.submit(_cached_get_ticker_info, t, api_key): t for t in returns_df.columns}
                for _fut in as_completed(_futs):
                    _t = _futs[_fut]
                    try:
                        ticker_info[_t] = _fut.result()
                    except Exception:
                        ticker_info[_t] = {"name": _t, "sector": "Unknown", "exchange": "", "market_cap": 0}

            progress.progress(95, text="Computing diversification score...")
            recommended_weights = portfolios["recommended"]
            div_score = compute_diversification_score(recommended_weights, returns_df)

            progress.progress(100, text="Done!")
            log_area.empty()
            progress.empty()

            if not portfolios.get("target_met", True):
                st.warning(
                    f"Your target return of **{portfolios['target_requested']}%/yr** "
                    f"could not be achieved with the selected tickers. "
                    f"The highest achievable is **{portfolios['target_achieved']}%/yr**. "
                    f"The risk-matched model has been optimised for the highest "
                    f"risk-adjusted return instead."
                )

            st.session_state[_K_OPTIMISED] = {
                "close_df":      close_df,
                "returns_df":    returns_df,
                "market_returns": market_returns,
                "stock_metrics": stock_metrics,
                "corr_matrix":   corr_matrix,
                "portfolios":    portfolios,
                "ef_df":         ef_df,
                "ticker_info":   ticker_info,
                "div_score":     div_score,
                "factor_scores": _factor_scores,
                "sector_map":    sector_map,
                "rankings_meta": (rankings.get("_meta", {}) if used_precompute else
                                  {"computed_at": "live (no precompute cache)"}),
                "failed":        failed,
            }

        except Exception as e:
            progress.empty()
            log_area.empty()
            err_str = str(e).lower()
            if "rate limit" in err_str or "429" in err_str:
                st.warning(
                    "⏳ **Market data API is busy right now.** "
                    "Wait 30 seconds and try again — this happens during peak hours."
                )
            elif "api key" in err_str or "missing" in err_str:
                st.error("API key error — contact support.")
            elif "upstream price fetch failed" in err_str:
                # Not a ticker problem: these names came from our own ranked
                # universe. It is the data provider refusing a burst, and the
                # burst scales with Number of holdings — a 50-holding request
                # takes the whole eligible universe as its candidate pool.
                st.error(
                    "**Market data is unavailable right now.** The price provider "
                    "returned nothing for any of the selected stocks — this is a "
                    "rate limit or an outage on their side, not a problem with your "
                    "settings. Wait a minute and try again, or lower **Number of "
                    "holdings**, which fetches fewer names."
                )
            elif "no valid price" in err_str or "all" in err_str and "failed" in err_str:
                st.error(
                    "Could not fetch price data for the selected tickers. "
                    "Check that your tickers are valid US stock symbols."
                )
            elif "insufficient tickers" in err_str:
                st.error(
                    "Not enough of the selected stocks had usable price history "
                    "to build a diversified portfolio. Add a few more well-known "
                    "tickers (or remove some exclusions) and try again."
                )
            else:
                st.error(f"Something went wrong building your portfolio: {e}")
                import traceback as _tb; print(_tb.format_exc())   # server log, not UI
            if st.button("← Back", key="step2_err_back"):
                st.session_state[_K_STEP] = 1
                del st.session_state[_K_OPTIMISED]
                st.rerun()
            return

    opt = st.session_state[_K_OPTIMISED]
    portfolios    = opt["portfolios"]
    ef_df         = opt["ef_df"]
    stock_metrics = opt["stock_metrics"]
    corr_matrix   = opt["corr_matrix"]
    div_score     = opt["div_score"]
    ticker_info   = opt["ticker_info"]

    # Warn about tickers that couldn't be loaded, distinguishing user-requested vs auto-selected
    if opt.get("failed"):
        _user_tickers_req = [t.upper() for t in prefs.get("user_tickers", [])]
        _user_failed = [t for t in opt["failed"] if t.upper() in _user_tickers_req]
        _auto_failed = [t for t in opt["failed"] if t.upper() not in _user_tickers_req]
        if _user_failed:
            st.warning(
                f"Your requested ticker(s) **{', '.join(_user_failed)}** could not be included — "
                f"insufficient price history (less than 60 trading days). They have been excluded from the portfolio."
            )
        if _auto_failed:
            st.warning(f"Could not load data for: {', '.join(_auto_failed)} — excluded from analysis")

    # Portfolio selector
    _section_header("Choose a Model")
    _RISK_MATCHED = "Risk-Matched Model (targets the risk level you set)"
    _MAX_SHARPE   = "Maximum Sharpe Ratio (highest modelled risk/return)"
    _MIN_VOL      = "Minimum Volatility (lowest modelled risk)"
    port_choice = st.radio("Optimisation objective:",
                           [_RISK_MATCHED, _MAX_SHARPE, _MIN_VOL],
                           index=0, key="port_choice")

    choice_map = {
        _RISK_MATCHED: "recommended",
        _MAX_SHARPE:   "max_sharpe",
        _MIN_VOL:      "min_vol",
    }
    selected_key     = choice_map[port_choice]
    selected_weights = portfolios[selected_key]

    # Clean weights — remove allocations too small to be real positions, but
    # never drop so many that we fall below what the user asked for. The floor
    # scales with the requested size (see _prune_floor): 1% is a fifth of a
    # normal position at 18 names and half of one at 50.
    _target_n = _target_holdings(prefs)
    _floor    = _prune_floor(_target_n)
    sorted_w  = sorted(selected_weights.items(), key=lambda x: x[1], reverse=True)
    above_threshold = [(k, v) for k, v in sorted_w if v >= _floor]

    if len(above_threshold) >= _target_n:
        kept    = dict(above_threshold)
        dropped = [k for k, v in sorted_w if v < _floor]
    else:
        # Keep the top _target_n regardless of threshold to satisfy user pref
        kept    = dict(sorted_w[:_target_n])
        dropped = [k for k, _ in sorted_w[_target_n:]]

    total = sum(kept.values())
    selected_weights = {k: v / total for k, v in kept.items()} if total > 0 else kept
    if dropped:
        st.info(f"{len(dropped)} position(s) with weight below {_floor:.2%} were removed by "
                f"the optimizer and excluded from the portfolio: {', '.join(dropped)}")

    # Say so when the screen could not fill the request. Silently returning
    # fewer names than asked for is the bug this control had for its whole life;
    # returning fewer *with a reason* is a legitimate outcome of tight filters.
    if len(selected_weights) < _target_n:
        st.warning(
            f"You asked for {_target_n} holdings and this portfolio has "
            f"{len(selected_weights)}. The screen ran out of names that pass your "
            f"filters — widen the sector selection, lower the minimum market cap, "
            f"or reduce the number of holdings.")

    # Portfolio metrics
    returns_df = opt["returns_df"]
    ann_ret, ann_vol, sharpe, pbeta, pbeta_adj = 0, 0, 0, 1.0, 1.0
    tickers_in = [t for t in selected_weights if t in returns_df.columns]
    _cap = None
    if tickers_in:
        # One canonical expected-return calculation, shared with the Compare
        # panel below. This block previously used the 3-month rate and the raw
        # beta while Compare used the 10-year rate and an adjusted beta, so the
        # same portfolio showed two different returns and two Sharpes.
        _cap = portfolio_capm(selected_weights, returns_df, stock_metrics=stock_metrics)
        ann_ret, ann_vol = _cap["exp_return"], _cap["vol"]
        sharpe, pbeta, pbeta_adj = _cap["sharpe"], _cap["beta_raw"], _cap["beta_adj"]
    _rfr_pct = (_cap["rf"] if _cap else get_long_risk_free_rate() * 100)

    # ── Why each holding is here ──────────────────────────────────────────────
    # Every number below was already computed and then discarded at render time.
    # Showing it is the difference between "here is a portfolio" and "here is why
    # this portfolio" — and it's the part a user is actually paying for, since
    # the maths itself is invisible to them.
    _section_header("How These Holdings Were Screened")
    _fs   = opt.get("factor_scores", {}) or {}
    _meta = opt.get("rankings_meta", {}) or {}
    _rows = []
    for _t, _w in sorted(selected_weights.items(), key=lambda x: -x[1]):
        _m   = stock_metrics.get(_t, {})
        _sc  = _fs.get(_t)
        _b   = _m.get("beta")
        # Mirrors factor_tilted_expected_returns exactly. Read the cap from the
        # constant rather than repeating 0.02 — a hardcoded copy here would keep
        # displaying the old tilt after the optimiser's cap was retuned.
        _tilt = (2.0 * (_sc - 0.5) * FACTOR_ALPHA_MAX * 100) if _sc is not None else None
        _rows.append({
            "Holding":    _t,
            "Weight":     f"{_w*100:.1f}%",
            "Sector":     _SECTOR_LOOKUP.get(_t, opt.get("sector_map", {}).get(_t, "—")),
            "Factor score": f"{_sc:.2f}" if _sc is not None else "—",
            "Tilt vs CAPM": f"{_tilt:+.1f}%" if _tilt is not None else "—",
            "Beta":       f"{_b:.2f}" if _b is not None else "—",
            "Exp. return": f"{_m.get('capm_return'):.1f}%" if _m.get("capm_return") is not None else "—",
            "Volatility": f"{_m.get('ann_vol'):.1f}%" if _m.get("ann_vol") is not None else "—",
        })
    if _rows:
        st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)
        st.caption(_factor_caption(_meta))

        if _meta.get("partial"):
            st.warning(
                f"Rankings are partial — {_meta.get('tickers_done','?')} of "
                f"{_meta.get('tickers_total','?')} tickers were scored when the "
                f"nightly job last ran. Selection used what was available.")

    # ── What each holding contributes ─────────────────────────────────────────
    # Two scores per name, per the screen/risk-model split: the factor score
    # says how the company ranks against its own sector; this block says what
    # the position does for THIS portfolio — which is a different question, and
    # the one a reader of the weights actually has. A middling-scored staple
    # can be the most useful line in the book if it is uncorrelated with the
    # rest; the best-scored name can be the least useful if it duplicates risk
    # the portfolio already holds.
    _rk_all = st.session_state.get(_K_RANKINGS) or {}
    _cdf    = opt.get("close_df")
    _held   = [t for t in selected_weights if _cdf is not None and t in _cdf.columns]
    if len(_held) >= 3 and _cdf is not None and len(_cdf) > 21:
        import numpy as _np
        _rets = _cdf[_held].pct_change().dropna()
        _wv   = _np.array([selected_weights[t] for t in _held], dtype=float)
        _wv   = _wv / _wv.sum()
        _cv   = _rets.cov().values * 252
        _cm   = _rets.corr().values
        _pvar = float(_wv @ _cv @ _wv)
        # Fractional risk contributions — sum to 1, so each is directly
        # comparable with the holding's weight share.
        _rc   = (_wv * (_cv @ _wv) / _pvar) if _pvar > 0 else _wv

        _FLAB = [("f_momentum", "momentum"), ("f_quality", "quality"),
                 ("f_value", "value"), ("f_growth", "growth"),
                 ("f_health", "fin. health"), ("f_lowvol", "low volatility")]

        _section_header("What Each Holding Contributes")
        st.caption(
            "**Sector rank** is the holding's factor score against its own sector. "
            "**Avg corr** is its weight-averaged correlation with the rest of the "
            "portfolio — lower means it moves more independently. **Risk share vs "
            "weight** compares the slice of portfolio risk a holding supplies with "
            "the capital it takes: supplying less risk than weight is what "
            "diversification looks like in numbers.")
        _by_sec = {}
        for _t2, _d2 in _rk_all.items():
            if _t2 == "_meta" or not isinstance(_d2, dict):
                continue
            _by_sec.setdefault(_d2.get("sector", "Unknown"), []).append(
                (_t2, _d2.get("score", 0) or 0))

        for _i, _t in enumerate(sorted(_held, key=lambda x: -selected_weights[x])):
            _d    = _rk_all.get(_t, {}) if isinstance(_rk_all.get(_t), dict) else {}
            _sec  = _d.get("sector")
            _rank_txt = "—"
            if _sec and _sec in _by_sec and len(_by_sec[_sec]) >= 5:
                _peers = sorted(_by_sec[_sec], key=lambda x: -x[1])
                _pos   = next((_j + 1 for _j, (_pt, _ps) in enumerate(_peers) if _pt == _t), None)
                if _pos:
                    _rank_txt = (f"top {max(1, round(_pos / len(_peers) * 100))}% "
                                 f"of {_sec} · #{_pos}/{len(_peers)}")
            _comps = [(lab, _d.get(k)) for k, lab in _FLAB if _d.get(k) is not None]
            if _comps:
                _comps.sort(key=lambda x: -x[1])
                _fact_txt = (f"strong: {_comps[0][0]}, {_comps[1][0]} · "
                             f"weak: {_comps[-1][0]}") if len(_comps) >= 3 else "—"
            else:
                _fact_txt = "—"
            # Weighted average correlation with everything else in the book.
            _others = [_j for _j in range(len(_held)) if _held[_j] != _t]
            _wo     = _np.array([_wv[_j] for _j in _others])
            _idx    = _held.index(_t)
            _avgc   = float((_wo * _cm[_idx, _others]).sum() / _wo.sum()) if _wo.sum() > 0 else 0.0
            _rshare, _wshare = _rc[_idx] * 100, _wv[_idx] * 100
            _is_div = _rshare < _wshare - 0.5
            _rs_col = GREEN if _is_div else "#64748b"
            st.markdown(f"""
            <div style="display:flex;justify-content:space-between;align-items:center;gap:1rem;
                        padding:0.55rem 1rem;border-radius:8px;margin-bottom:0.35rem;
                        background:#f8fafc;border:1px solid #e2e8f0;font-size:0.82rem">
                <span style="font-weight:600;color:#0f172a;min-width:4.5rem">{_t}
                    <span style="color:#94a3b8;font-weight:400">{_wshare:.1f}%</span></span>
                <span style="color:#334155;flex:1">{_rank_txt}</span>
                <span style="color:#64748b;flex:1.3">{_fact_txt}</span>
                <span style="color:{_rs_col};white-space:nowrap">avg corr {_avgc:.2f} ·
                    risk {_rshare:.1f}% vs weight {_wshare:.1f}%</span>
            </div>""", unsafe_allow_html=True)

    _section_header("Portfolio Overview")
    cols = st.columns(5)
    for _card in [
        (cols[0], "Expected Ann. Return", f"{ann_ret:.1f}%",  ct.color.positive if ann_ret > 0 else ct.color.negative),
        # Both betas, as the Excel report does. They are different objects: the
        # raw one describes the past, the adjusted one is what the expected
        # return above is actually built on. Showing only the raw beside a
        # number derived from the adjusted invited exactly the reconciliation
        # failure this section used to have.
        (cols[1], "Portfolio Beta",       f"{pbeta:.2f}",      ct.color.ink,
         f"{pbeta_adj:.2f} adjusted, used"),
        (cols[2], "Expected Volatility",  f"{ann_vol:.1f}%",  ct.color.ink),
        (cols[3], "Sharpe Ratio",         f"{sharpe:.2f}",    ct.color.positive if sharpe > 1 else ct.color.value_line),
        (cols[4], "Diversification",      f"{div_score}/10",  ct.color.positive if div_score > 6 else ct.color.value_line),
    ]:
        col, label, value, color = _card[:4]
        sub = _card[4] if len(_card) > 4 else None
        with col:
            st.markdown(_metric_card(label, value, color, subtitle=sub),
                        unsafe_allow_html=True)

    with st.expander("About these numbers — methodology & assumptions"):
        st.markdown(f"""
**Expected Ann. Return** — CAPM estimate: risk-free rate + (portfolio beta × {EQUITY_RISK_PREMIUM*100:g}% equity risk
premium). It reflects how much *market* risk the portfolio carries, not which stocks recently ran up
— so it isn't inflated by a hot recent stretch. Past returns do not guarantee future performance.

**Portfolio Beta** — How much the portfolio moves with the market (S&P 500). 1.0 = moves with the
market; below 1.0 = less market-sensitive (more defensive); above 1.0 = more sensitive.

**Expected Volatility** — Annualised standard deviation of daily returns over the
{_PRICE_HISTORY_YEARS}-year window, from a Ledoit-Wolf shrunk covariance matrix.
Higher volatility = wider range of possible outcomes.

**Sharpe Ratio** — The *expected* return above (the CAPM figure above, minus the live risk-free rate
— currently {_rfr_pct:.2f}%) divided by that historical volatility. It is therefore forward-looking in
the numerator and backward-looking in the denominator, and it is **not** a trailing Sharpe: it does
not say what this portfolio would have earned per unit of risk over the past five years. Above 1.0
is generally considered good.

**Diversification Score** — Proprietary 1–10 score combining:
effective number of holdings (Herfindahl index), average pairwise correlation, and
concentration penalty for any single position above 25%.

**Important caveats:**
- Expected returns are a model, not a forecast; volatility and drawdown are historical and will not
  perfectly predict future risk
- Survivorship bias: the universe only includes stocks that still exist today
- Maximum weight per stock is whatever you set in the Universe step —
  currently {prefs.get('max_per_stock', 0.30)*100:.0f}%. Sector concentration is capped at 40%.
- Stock selection uses a diversified multi-factor score — Momentum (30%) + Quality (30%,
  fundamentals: margins/ROE/growth/Piotroski) + Low-volatility (20%) + risk-adjusted return (20%).
  It's a factor *tilt*, not a prediction — no method reliably forecasts returns, and a
  low-cost index fund is the benchmark to beat.
        """)

    # ── Side-by-Side Portfolio Strategy Comparison ───────────────────────
    _section_header("Compare Portfolio Strategies")
    _cmp_rows = []
    for _ck, _clabel in [("recommended","Risk-Matched"),("max_sharpe","Max Sharpe"),("min_vol","Min Volatility")]:
        _cw = portfolios.get(_ck, {})
        if not _cw:
            continue
        # Same scaled floor the chosen portfolio was pruned with, so the compare
        # panel doesn't count positions the portfolio itself keeps.
        _cw = {k: v for k, v in _cw.items() if v >= _floor}
        _ct = sum(_cw.values())
        _cw = {k: v/_ct for k, v in _cw.items()}
        _ct2 = [t for t in _cw if t in returns_df.columns]
        if not _ct2:
            continue
        _cwa = np.array([_cw[t] for t in _ct2]); _cwa /= _cwa.sum()
        _car  = returns_df[_ct2].mean().values @ _cwa * 252 * 100   # historical (fallback)
        _ccov = shrunk_covariance(returns_df[_ct2])
        _cvol = np.sqrt(_cwa @ _ccov @ _cwa) * 100
        # Headline return + Sharpe use CAPM expected returns — the SAME basis the
        # optimizer maximises. On a historical basis the "Max Sharpe" card could show
        # a *lower* Sharpe than "Min Volatility" (it maximises *expected*, not past,
        # risk-adjusted return), which reads like a bug. Fall back to the historical
        # mean only if a CAPM number is missing for any holding.
        # Same canonical basis as the Overview — see portfolio_capm().
        _cc   = portfolio_capm(_cw, returns_df, stock_metrics=stock_metrics)
        _cer, _cvol, _csh = _cc["exp_return"], _cc["vol"], _cc["sharpe"]
        _ccum = (1 + (returns_df[_ct2] @ _cwa)).cumprod()
        _cdd  = ((_ccum - _ccum.cummax()) / _ccum.cummax()).min() * 100
        _top_t = max(_cw, key=_cw.get)
        _cmp_rows.append({"key": _ck, "label": _clabel,
            "exp_ret": _cer, "vol": _cvol, "sharpe": _csh,
            "max_dd": _cdd, "holdings": len(_cw),
            "top": f"{_top_t} ({_cw[_top_t]*100:.0f}%)"})

    if _cmp_rows:
        _cmp_cols = st.columns(len(_cmp_rows))
        for _ccol, _crow in zip(_cmp_cols, _cmp_rows):
            _is_sel  = (_crow["key"] == selected_key)
            _cborder = f"2px solid {ct.color.brand}" if _is_sel else "1px solid #e2e8f0"
            _cbg     = "#eff6ff" if _is_sel else "#ffffff"
            _clbl    = ("" if _is_sel else "") + _crow["label"]
            with _ccol:
                st.markdown(f"""
                <div style="background:{_cbg};border:{_cborder};border-radius:8px;
                            padding:1rem;text-align:center">
                    <div style="font-size:0.68rem;font-weight:700;letter-spacing:1px;
                                color:{ct.color.brand if _is_sel else "#64748b"};text-transform:uppercase;
                                margin-bottom:0.75rem">{_clbl}</div>
                    <div style="font-size:1.5rem;font-weight:700;
                                color:{ct.color.positive if _crow['exp_ret']>0 else ct.color.negative}">
                        {_crow['exp_ret']:+.1f}%</div>
                    <div style="font-size:0.68rem;color:#64748b;margin-bottom:0.6rem">Exp. Return · CAPM</div>
                    <div style="font-size:0.82rem;color:#0f172a;margin-bottom:2px">
                        {_crow['sharpe']:.2f} Sharpe</div>
                    <div style="font-size:0.82rem;color:#0f172a;margin-bottom:2px">
                        {_crow['vol']:.1f}% Volatility</div>
                    <div style="font-size:0.82rem;color:{ct.color.negative};margin-bottom:0.5rem">
                        {_crow['max_dd']:.1f}% Max DD</div>
                    <div style="font-size:0.7rem;color:#64748b">{_crow['holdings']} holdings</div>
                    <div style="font-size:0.68rem;color:#64748b">{_crow['top']}</div>
                </div>""", unsafe_allow_html=True)
        st.caption(f"Expected return & Sharpe are forward-looking (CAPM: Rf + β·ERP) — the "
                   f"same basis the optimizer maximizes. Volatility and max drawdown are "
                   f"historical, over the {_PRICE_HISTORY_YEARS}-year price window.")

    # Holdings table
    _section_header("Model Holdings")
    holdings_data = []
    for ticker, weight in sorted(selected_weights.items(), key=lambda x: x[1], reverse=True):
        m    = stock_metrics.get(ticker, {})
        info = ticker_info.get(ticker, {})
        holdings_data.append({
            "Ticker":          ticker,
            "Company":         info.get("name", ticker)[:25],
            "Weight":          f"{weight*100:.1f}%",
            "Beta":            f"{m.get('beta'):.2f}" if m.get("beta") is not None else "—",
            "Expected (CAPM)": f"{m.get('capm_return'):.1f}%" if m.get("capm_return") is not None else "—",
            "Ann. Ret (hist)": f"{m.get('ann_return',0):.1f}%",
            "Volatility":      f"{m.get('ann_vol',0):.1f}%",
            "Sharpe":          f"{m.get('sharpe',0):.2f}",
            "Max Drawdown":    f"{m.get('max_drawdown',0):.1f}%",
        })
    st.dataframe(pd.DataFrame(holdings_data), use_container_width=True, hide_index=True)

    # ── Explainability Panel ──────────────────────────────────────────────
    _section_header("How These Holdings Were Screened")
    _sec_lookup = _SECTOR_LOOKUP

    for _et, _ew in sorted(selected_weights.items(), key=lambda x: x[1], reverse=True):
        _em  = stock_metrics.get(_et, {})
        _ese = _sec_lookup.get(_et, "Portfolio")
        _esh = _em.get("sharpe", 0)
        _ear = _em.get("ann_return", 0)
        _eav = _em.get("ann_vol", 0)
        _esc = ct.color.positive if _esh >= 1 else ct.color.value_line if _esh >= 0.5 else ct.color.negative
        st.markdown(f"""
        <div style="background:#f8fafc;border:1px solid #e2e8f0;
                    border-left:3px solid {ct.color.brand};border-radius:6px;
                    padding:0.6rem 1rem;margin-bottom:0.4rem;
                    display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem">
            <div>
                <span style="font-weight:700;color:#0f172a;font-size:0.9rem">{_et}</span>
                <span style="font-size:0.72rem;color:#64748b;margin-left:8px;
                            background:#f1f5f9;padding:1px 7px;border-radius:3px">{_ese}</span>
            </div>
            <div style="font-size:0.8rem;color:#64748b">
                <b style="color:{ct.color.positive if _ear>0 else ct.color.negative}">{_ear:.1f}%</b> return &nbsp;·&nbsp;
                <b style="color:#0f172a">{_eav:.1f}%</b> vol &nbsp;·&nbsp;
                <b style="color:{_esc}">Sharpe {_esh:.2f}</b> &nbsp;·&nbsp;
                <b style="color:{ct.color.brand}">{_ew*100:.1f}% weight</b>
            </div>
            <div style="font-size:0.7rem;color:#64748b;font-style:italic">
                Top-ranked by Sharpe in {_ese}
            </div>
        </div>""", unsafe_allow_html=True)

    # Allocation pie chart
    col1, col2 = st.columns(2)
    with col1:
        _pie_tickers = list(selected_weights.keys())
        fig_pie = go.Figure(go.Pie(
            labels=_pie_tickers,
            values=[round(v*100,1) for v in selected_weights.values()],
            hole=0.45,
            textinfo="label+percent",
            marker=dict(colors=[ct.series_color(i) for i in range(len(_pie_tickers))],
                        line=dict(color=ct.color.paper, width=1)),
        ))
        # Full-bleed donut: the 44px value-axis gutter would push it off-centre.
        ct.style(fig_pie, height=380, grid=False, crosshair=False, legend=None,
                 title="Portfolio Allocation", margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_pie, use_container_width=True)

    with col2:
        # Efficient frontier
        fig_ef = go.Figure()
        # Random-portfolio cloud, tinted by Sharpe on a sequential single hue
        # (light -> dark green = worse -> better risk-adjusted return). Cloud and
        # star share the CAPM return basis, so the star sits on the frontier edge.
        fig_ef.add_trace(go.Scatter(
            x=ef_df["Volatility"], y=ef_df["Return"],
            mode="markers",
            marker=dict(
                color=ef_df["Sharpe"],
                colorscale=[[0.0, ct.color.corridor_high],
                            [1.0, ct.color.corridor_base]],
                size=ct.marker.size, opacity=0.55,
                colorbar=dict(title=dict(text="Sharpe", side="top"),
                              thickness=10, len=0.8, x=1.0, xanchor="left",
                              tickfont=dict(size=ct.font.size.axis), outlinewidth=0),
            ),
            name="Random portfolios",
            hovertemplate="Volatility %{x:.1f}%<br>Return %{y:.1f}%<extra></extra>",
        ))
        # "Your Portfolio" — bright brand blue with a white halo so it reads
        # clearly against the green cloud it now sits on.
        fig_ef.add_trace(go.Scatter(
            x=[ann_vol], y=[ann_ret],
            mode="markers",
            marker=dict(color=ct.color.brand, size=17, symbol="star",
                        line=dict(color=ct.marker.fill, width=2)),
            name="Your portfolio",
            hovertemplate=("Your portfolio<br>Volatility %{x:.1f}%"
                           "<br>Return %{y:.1f}%<extra></extra>"),
        ))
        # Right margin holds the Sharpe colorbar (x=1.0, anchored left).
        ct.style(
            fig_ef, height=380, legend="bottom", crosshair=False,
            title="Efficient Frontier",
            x=ct.plain_axis(title="Volatility (%)", showgrid=False),
            y=ct.plain_axis(title="Expected Return (%)"),
            margin=dict(l=0, r=44, t=40, b=44),
        )
        st.plotly_chart(fig_ef, use_container_width=True)

    # Correlation heatmap
    _section_header("Correlation Between Holdings")
    tickers_show = list(selected_weights.keys())
    corr_show    = corr_matrix.loc[
        [t for t in tickers_show if t in corr_matrix.index],
        [t for t in tickers_show if t in corr_matrix.columns]
    ]
    if not corr_show.empty:
        fig_corr = px.imshow(corr_show, text_auto=".2f",
                             color_continuous_scale=_DIVERGING,
                             zmin=-1, zmax=1, aspect="auto")
        ct.style(fig_corr, height=320, grid=False, crosshair=False, legend=None,
                 x=ct.category_axis(), y=ct.category_axis(),
                 margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig_corr, use_container_width=True)

    # ── Sector Exposure vs. S&P 500 Benchmark ────────────────────────────
    _section_header("Sector Exposure vs. S&P 500")
    _SP500_SECTOR_W = {
        "Technology": 29.5, "Health Care": 12.8, "Financials": 13.2,
        "Consumer Discretionary": 10.4, "Communication Services": 8.6,
        "Industrials": 8.9, "Consumer Staples": 6.2, "Energy": 3.9,
        "Utilities": 2.5, "Materials": 2.3, "Real Estate": 2.2,
    }
    _sec_map2 = _SECTOR_LOOKUP
    _bond_set = _BOND_SET
    _port_sectors = {}
    for _t, _w in selected_weights.items():
        _s = _sec_map2.get(_t, "Bonds" if _t in _bond_set else "Other")
        if _s in _SP500_SECTOR_W or _s == "Bonds":
            _port_sectors[_s] = _port_sectors.get(_s, 0) + _w * 100

    _sec_labels = sorted(set(list(_SP500_SECTOR_W.keys()) + list(_port_sectors.keys())))
    _pv = [round(_port_sectors.get(s, 0), 1) for s in _sec_labels]
    _sv = [_SP500_SECTOR_W.get(s, 0) for s in _sec_labels]

    fig_sec = go.Figure()
    fig_sec.add_trace(go.Bar(name="Your Portfolio", x=_sec_labels, y=_pv,
        marker_color=ct.color.brand, text=[f"{v:.1f}%" for v in _pv],
        textposition="outside", textfont=dict(size=ct.font.size.axis)))
    fig_sec.add_trace(go.Bar(name="S&P 500", x=_sec_labels, y=_sv,
        marker_color=ct.color.ink_muted, opacity=0.55,
        text=[f"{v:.1f}%" for v in _sv],
        textposition="outside", textfont=dict(size=ct.font.size.axis)))
    # Deep bottom margin carries the angled sector labels.
    ct.style(
        fig_sec, height=360, barmode="group",
        x=ct.category_axis(tickangle=-30),
        y=ct.pct_axis(title="Weight (%)"),
        margin=dict(l=0, r=0, t=30, b=100),
    )
    st.plotly_chart(fig_sec, use_container_width=True)

    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("← Back", key="step2_back"):
            st.session_state[_K_STEP] = 1
            if _K_OPTIMISED in st.session_state:
                del st.session_state[_K_OPTIMISED]
            st.rerun()
    with col2:
        if st.button("Next → Run Backtest", type="primary", key="step2_next"):
            st.session_state[_K_WEIGHTS] = selected_weights
            st.session_state[_K_STEP] = 3
            st.rerun()


# ── Step 3 — Backtest ─────────────────────────────────────────────────────────

def _render_step_3():
    prefs   = st.session_state.get(_K_PREFS, {})
    opt     = st.session_state.get(_K_OPTIMISED, {})
    weights = st.session_state.get(_K_WEIGHTS, {})

    if _K_BACKTEST not in st.session_state:
        with st.spinner("Running backtest..."):
            try:
                close_df  = opt["close_df"]
                start_cap = prefs.get("starting_capital", 10000)
                monthly   = prefs.get("monthly_contribution", 500)

                backtest_df      = backtest_portfolio(close_df, weights, start_cap, monthly)
                backtest_metrics = compute_backtest_metrics(backtest_df, start_cap)
                heatmap_df       = compute_monthly_heatmap(backtest_df)

                # Equal-weight (1/N) benchmark over the SAME names, capital and
                # contribution schedule — only the weights differ, so the
                # comparison isolates what the optimiser actually contributed.
                #
                # This is here because of the most-replicated result in the
                # portfolio literature: DeMiguel, Garlappi & Uppal (2009) tested
                # 14 optimisation models on seven datasets and none beat naive
                # 1/N consistently out of sample. A tool that never shows that
                # comparison is making a claim it has declined to test. It will
                # sometimes lose. That is the honest outcome, not a bug.
                equal_metrics = None
                try:
                    if weights:
                        _eq_w = {t: 1.0 / len(weights) for t in weights}
                        equal_metrics = compute_backtest_metrics(
                            backtest_portfolio(close_df, _eq_w, start_cap, monthly),
                            start_cap)
                except Exception:
                    equal_metrics = None      # never block the real backtest

                st.session_state[_K_BACKTEST] = {
                    "df":      backtest_df,
                    "metrics": backtest_metrics,
                    "heatmap": heatmap_df,
                    "equal":   equal_metrics,
                }
            except Exception as e:
                st.error(f"Backtest failed: {e}")
                import traceback as _tb; print(_tb.format_exc())   # server log, not UI
                return

    bt     = st.session_state[_K_BACKTEST]
    bt_df  = bt["df"]
    bt_met = bt["metrics"]
    hmap   = bt["heatmap"]
    prefs  = st.session_state.get(_K_PREFS, {})
    start_cap = prefs.get("starting_capital", 10000)

    # Contribution-free growth indices. EVERY return, volatility, beta,
    # correlation and drawdown figure below reads from these, never from the
    # "Portfolio"/"SP500" account-value columns: a monthly contribution lands as a
    # one-day step in account value (+5% in month one on a $10k/$500 plan), and
    # pct_change() cannot tell that step apart from a market move. It inflated the
    # rolling volatility, distorted the stress-test beta, and made the historical
    # drawdown windows look shallower than they were. Both fall back for
    # backtests cached before these columns existed.
    _nav = bt_df["NAV"] if "NAV" in bt_df.columns else bt_df["Portfolio"]
    _bench_nav = (bt_df["SP500_NAV"] if "SP500_NAV" in bt_df.columns
                  else bt_df.get("SP500"))
    _has_bench = _bench_nav is not None and not _bench_nav.isna().all()
    # In percent, to match the rolling return/vol series it is subtracted from.
    _rfr_pct = get_risk_free_rate() * 100

    # Key metrics
    _section_header("Backtest Results")
    cols = st.columns(4)
    gain = bt_met.get("Total Gain/Loss", 0)
    for _card in [
        (cols[0], "Final Value",      f"${bt_met.get('Final Value',0):,.0f}",    GREEN),
        (cols[1], "Total Return",     f"{bt_met.get('Total Return',0):.1f}%",    GREEN if bt_met.get("Total Return",0)>0 else RED),
        (cols[2], "vs S&P 500",       f"{bt_met.get('vs S&P 500',0):.1f}%" if isinstance(bt_met.get('vs S&P 500'), float) else "N/A", GREEN if isinstance(bt_met.get('vs S&P 500'), float) and bt_met.get('vs S&P 500',0)>0 else RED),
        # `Sharpe Ratio` is None when the window is too short to justify one, or
        # when the value came back implausible — formatting that with :.2f would
        # raise, and comparing it with > would raise too.
        (cols[3], "Sharpe Ratio",
         f"{bt_met['Sharpe Ratio']:.2f}" if isinstance(bt_met.get("Sharpe Ratio"), (int, float)) else "—",
         GREEN if (bt_met.get("Sharpe Ratio") or 0) > 1 else AMBER),
    ]:
        col, label, value, color = _card[:4]
        sub = _card[4] if len(_card) > 4 else None
        with col:
            st.markdown(_metric_card(label, value, color, subtitle=sub),
                        unsafe_allow_html=True)

    # ── Did the optimiser earn its keep? ──────────────────────────────────────
    _eqm = bt.get("equal")
    if _eqm:
        _o_r, _e_r = bt_met.get("Total Return", 0) or 0, _eqm.get("Total Return", 0) or 0
        _o_s, _e_s = bt_met.get("Sharpe Ratio", 0) or 0, _eqm.get("Sharpe Ratio", 0) or 0
        _d_r, _d_s = _o_r - _e_r, _o_s - _e_s
        _won = _d_s >= 0
        _verdict = ("The optimiser beat equal weight on risk-adjusted return over "
                    "this window." if _won else
                    "Equal weight beat the optimiser on risk-adjusted return over "
                    "this window — which happens often enough that we show it.")
        _c = "#059669" if _won else "#b45309"
        st.markdown(
            f"""<div style="border-top:1px solid #e2e8f0;margin-top:1rem;padding-top:0.9rem;
                        font-family:var(--font-sans)">
  <div style="font-size:0.62rem;font-weight:700;letter-spacing:0.9px;text-transform:uppercase;
              color:#64748b;margin-bottom:0.55rem">Sanity check · vs equal weight (1/N)</div>
  <div style="display:flex;gap:2.2rem;flex-wrap:wrap;font-size:0.82rem;color:#334155">
    <span>Total return &nbsp;<b>{_o_r:,.1f}%</b> optimised
          &nbsp;vs&nbsp; <b>{_e_r:,.1f}%</b> equal
          &nbsp;<span style="color:{_c}">({_d_r:+.1f} pts)</span></span>
    <span>Sharpe &nbsp;<b>{_o_s:.2f}</b> optimised
          &nbsp;vs&nbsp; <b>{_e_s:.2f}</b> equal
          &nbsp;<span style="color:{_c}">({_d_s:+.2f})</span></span>
  </div>
  <div style="font-size:0.75rem;color:#64748b;margin-top:0.5rem;max-width:70ch">
    {_verdict} Same holdings, same capital, same contributions — only the weights
    differ. Published tests find naive 1/N hard to beat out of sample, so this is
    the comparison worth checking.
  </div>
</div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)
    cols2 = st.columns(4)
    for _card in [
        # `Ann. Return` is None under ~3 months of history — compute_backtest_metrics
        # refuses to annualise a window too short to justify it, rather than
        # scaling a few weeks into an impossible yearly figure.
        (cols2[0], "Ann. Return",
         (f"{bt_met['Ann. Return']:.1f}%" if bt_met.get("Ann. Return") is not None else "—"),
         GREEN if (bt_met.get("Ann. Return") or 0) > 0 else RED),
        (cols2[1], "Max Drawdown",     f"{bt_met.get('Max Drawdown',0):.1f}%",    AMBER),
        (cols2[2], "Best Month",       f"{bt_met.get('Best Month',0):.1f}%",      GREEN),
        (cols2[3], "% Months Positive",f"{bt_met.get('% Months Positive',0):.0f}%",GREEN if bt_met.get('% Months Positive',0)>50 else RED),
    ]:
        col, label, value, color = _card[:4]
        sub = _card[4] if len(_card) > 4 else None
        with col:
            st.markdown(_metric_card(label, value, color, subtitle=sub),
                        unsafe_allow_html=True)

    # Portfolio vs S&P vs Contributions chart
    _section_header("Portfolio Growth vs Benchmark")
    fig_bt = go.Figure()
    fig_bt.add_trace(go.Scatter(
        x=bt_df.index, y=bt_df["Portfolio"],
        name="Your Portfolio", line=dict(color=ct.color.brand, width=ct.stroke.value)))
    if "SP500" in bt_df.columns and not bt_df["SP500"].isna().all():
        fig_bt.add_trace(go.Scatter(
            x=bt_df.index, y=bt_df["SP500"],
            name="S&P 500 (SPY)", line=dict(color=ct.color.ink_muted,
                                            width=ct.stroke.price, dash="dot")))
    fig_bt.add_trace(go.Scatter(
        x=bt_df.index, y=bt_df["Contrib"],
        name="Total Contributed", line=dict(color=ct.color.value_line,
                                            width=ct.stroke.price, dash="dash")))
    # Section header supplies the title, so keep the tight top margin; the left
    # gutter is the theme's, so the "$" tick labels are no longer clipped.
    ct.style(fig_bt, height=380, margin=dict(l=44, r=16, t=10, b=30))
    st.plotly_chart(fig_bt, use_container_width=True)

    # Drawdown chart — use the contribution-free NAV index so monthly cash
    # inflows can't mask real market drawdowns (falls back to Portfolio for any
    # older cached backtest that predates the NAV column).
    _section_header("Drawdown from Peak")
    peak     = _nav.cummax()
    drawdown = (_nav - peak) / peak * 100
    fig_dd   = go.Figure()
    fig_dd.add_trace(go.Scatter(
        x=bt_df.index, y=drawdown,
        fill="tozeroy", fillcolor=ct._rgba(ct.color.negative, 0.12),
        line=dict(color=ct.color.negative, width=ct.stroke.price), name="Drawdown"))
    # zero=False: drawdown is negative-only, so pinning the base at zero would
    # be pinning the top of the series and flattening it against the axis.
    ct.style(
        fig_dd,
        height=220, legend=None,
        margin=dict(l=52, r=16, t=10, b=24),
        x=ct.time_axis(fy_ticks=False),
        y=ct.pct_axis(tick_format=".0f", zero=False),
    )
    st.plotly_chart(fig_dd, use_container_width=True)

    # ── Historical Stress Tests ───────────────────────────────────────────
    _section_header("Historical Stress Tests")
    # Read from stress_test.CRASH_SCENARIOS rather than a second hardcoded list.
    # The two used to disagree on the same crashes — this section had the 2022
    # window ending 10-14 and the 2018 one starting 09-20, while the Stress Test
    # tab used 10-12 and 10-01 — so the same portfolio showed different "actual"
    # returns for the same crash depending on which page you were looking at.
    from stress_test import CRASH_SCENARIOS as _CRASHES, RECENT_CRASHES as _RECENT
    _STRESS_PERIODS = [
        (_n, _CRASHES[_n]["start"], _CRASHES[_n]["end"],
         _CRASHES[_n]["description"].split("·")[0].strip(),
         _CRASHES[_n]["market_shock"])
        for _n in _RECENT if _n in _CRASHES
    ]
    _stress_rows = []
    for _sn, _ss, _se, _sdesc, _known_sp in _STRESS_PERIODS:
        _ss_dt, _se_dt = pd.Timestamp(_ss), pd.Timestamp(_se)
        _bt_min, _bt_max = bt_df.index.min(), bt_df.index.max()
        if _ss_dt >= _bt_min and _se_dt <= _bt_max:
            _psl = _nav.loc[_ss_dt:_se_dt]
            if len(_psl) > 1:
                _pr = (_psl.iloc[-1] / _psl.iloc[0] - 1) * 100
                _sr = None
                if _has_bench:
                    _ssl = _bench_nav.loc[_ss_dt:_se_dt].dropna()
                    if len(_ssl) > 1:
                        _sr = (_ssl.iloc[-1] / _ssl.iloc[0] - 1) * 100
                # `is not None`, not truthiness: a benchmark that came back exactly
                # flat over the window is a real reading, not a missing one.
                _stress_rows.append({"name": _sn, "desc": _sdesc,
                    "port": round(_pr, 1),
                    "sp": round(_sr, 1) if _sr is not None else None,
                    "vs": round(_pr - _sr, 1) if _sr is not None else None,
                    "est": False})
        else:
            _p_ret = _nav.pct_change().dropna()
            _s_ret = (_bench_nav.pct_change().dropna() if _has_bench else pd.Series(dtype=float))
            _al = pd.concat([_p_ret.rename("p"), _s_ret.rename("s")], axis=1).dropna()
            if len(_al) > 20:
                _beta = np.cov(_al["p"], _al["s"])[0,1] / max(np.var(_al["s"]), 1e-10)
                _est  = round(_beta * _known_sp, 1)
                _stress_rows.append({"name": _sn, "desc": _sdesc,
                    "port": _est, "sp": _known_sp,
                    "vs": round(_est - _known_sp, 1), "est": True})

    if _stress_rows:
        _scols = st.columns(len(_stress_rows))
        for _scol, _sr in zip(_scols, _stress_rows):
            _pc = RED if _sr["port"] < 0 else GREEN
            _badge = " ·&nbsp;estimated" if _sr["est"] else " ·&nbsp;actual"
            with _scol:
                st.markdown(f"""
                <div style="background:#ffffff;border:1px solid #e2e8f0;
                            border-radius:8px;padding:1rem;text-align:center">
                    <div style="font-size:0.67rem;font-weight:700;color:#64748b;
                                text-transform:uppercase;letter-spacing:0.8px;
                                margin-bottom:0.5rem">{_sr['name']}</div>
                    <div style="font-size:1.6rem;font-weight:700;color:{_pc}">
                        {_sr['port']:+.1f}%</div>
                    <div style="font-size:0.67rem;color:#64748b;margin-bottom:0.4rem">
                        Portfolio{_badge}</div>
                    {f'<div style="font-size:0.82rem;color:#0f172a">vs S&amp;P:&nbsp;<b style="color:{GREEN if (_sr["vs"] or 0)>0 else RED}">{_sr["vs"]:+.1f}%</b></div>' if _sr["vs"] is not None else ""}
                    <div style="font-size:0.67rem;color:#64748b;margin-top:0.4rem;
                                font-style:italic">{_sr['desc']}</div>
                </div>""", unsafe_allow_html=True)
    else:
        st.info("Not enough backtest history for stress testing.")

    # Monthly heatmap
    _section_header("Monthly Returns Heatmap")
    if hmap is not None and not hmap.empty:
        month_names = ["Jan","Feb","Mar","Apr","May","Jun",
                       "Jul","Aug","Sep","Oct","Nov","Dec"]
        hmap = hmap.copy()
        hmap.columns = [month_names[int(c)-1] if str(c).isdigit() and int(c) <= 12 else str(c) for c in hmap.columns]
        fig_hmap = px.imshow(
            hmap.fillna(0),
            text_auto=".1f",
            # Green-positive rather than the shared blue-positive ramp: on a
            # monthly-return grid, red/green is the convention readers already
            # have, and inverting it here would misread at a glance.
            color_continuous_scale=[[0.0, ct.color.negative],
                                    [0.5, ct.color.paper],
                                    [1.0, ct.color.positive]],
            zmin=-15, zmax=15,
            aspect="auto",
        )
        ct.style(
            fig_hmap,
            height=max(200, len(hmap)*35 + 60),
            margin=dict(l=52, r=16, t=10, b=10),
            legend=None, grid=False, crosshair=False,
            x=ct.category_axis(), y=ct.category_axis(),
            coloraxis_showscale=False,
        )
        fig_hmap.update_traces(textfont_size=9)
        st.plotly_chart(fig_hmap, use_container_width=True)

    # ── Rolling Performance Metrics ───────────────────────────────────────
    _section_header("Rolling Performance Metrics")
    _port_ret_full = _nav.pct_change().dropna()
    _roll_w = min(252, max(60, len(_port_ret_full) // 3))

    _rc1, _rc2, _rc3 = st.columns(3)

    with _rc1:
        _roll_vol = (_port_ret_full.rolling(60).std() * np.sqrt(252) * 100).dropna()
        _fig_rvol = go.Figure(go.Scatter(
            x=_roll_vol.index, y=_roll_vol,
            fill="tozeroy", fillcolor=ct._rgba(ct.color.value_line, 0.08),
            line=dict(color=ct.color.value_line, width=ct.stroke.price),
            hovertemplate="Vol: %{y:.1f}%<extra></extra>",
        ))
        ct.style(
            _fig_rvol,
            height=230, legend=None,
            margin=dict(l=44, r=10, t=32, b=26),
            x=ct.time_axis(fy_ticks=False),
            y=ct.pct_axis(tick_format=".0f"),
            title=dict(text="Rolling 60D Volatility (%)",
                       font=dict(size=12, color=ct.color.ink, family=ct.font.data),
                       x=0, xanchor="left"),
        )
        st.plotly_chart(_fig_rvol, use_container_width=True)

    with _rc2:
        _rret = _port_ret_full.rolling(_roll_w).mean() * 252 * 100
        _rvol = _port_ret_full.rolling(_roll_w).std() * np.sqrt(252) * 100
        # Excess of the risk-free rate, matching the headline Sharpe and every
        # other Sharpe in the app. Without it this line ran high by Rf/vol —
        # roughly +0.3 at 15% volatility — so the chart and the metric card
        # above it disagreed about the same portfolio.
        _rsh  = ((_rret - _rfr_pct) / _rvol.replace(0, np.nan)).dropna()
        _fig_rsh = go.Figure(go.Scatter(
            x=_rsh.index, y=_rsh,
            line=dict(color=ct.color.brand, width=ct.stroke.price),
            hovertemplate="Sharpe: %{y:.2f}<extra></extra>",
        ))
        _fig_rsh.add_hline(y=1.0, line_dash="dot", line_color=ct.color.positive, opacity=0.5,
                           annotation_text="1.0", annotation_font_size=9)
        _fig_rsh.add_hline(y=0.0, line_dash="dot", line_color=ct.color.negative, opacity=0.4)
        # zero=False: a rolling Sharpe goes negative, and the 0.0 reference line
        # above is the meaningful boundary, not an axis floor.
        ct.style(
            _fig_rsh,
            height=230, legend=None,
            margin=dict(l=44, r=10, t=32, b=26),
            x=ct.time_axis(fy_ticks=False),
            y=ct.plain_axis(tick_format=".1f", zero=False),
            title=dict(text=f"Rolling {_roll_w//21}M Sharpe Ratio",
                       font=dict(size=12, color=ct.color.ink, family=ct.font.data),
                       x=0, xanchor="left"),
        )
        st.plotly_chart(_fig_rsh, use_container_width=True)

    with _rc3:
        if _has_bench:
            _sp_ret = _bench_nav.pct_change().dropna()
            _aligned = pd.concat([_port_ret_full.rename("port"),
                                  _sp_ret.rename("sp")], axis=1).dropna()
            if len(_aligned) > _roll_w:
                _rcorr = _aligned["port"].rolling(_roll_w).corr(_aligned["sp"]).dropna()
                _fig_rc = go.Figure(go.Scatter(
                    x=_rcorr.index, y=_rcorr,
                    fill="tozeroy", fillcolor=ct._rgba(_ACCENT, 0.08),
                    line=dict(color=_ACCENT, width=ct.stroke.price),
                    hovertemplate="Corr: %{y:.2f}<extra></extra>",
                ))
                _fig_rc.add_hline(y=0.7, line_dash="dot", line_color=ct.color.value_line,
                                  opacity=0.5, annotation_text="0.7 high",
                                  annotation_font_size=9)
                ct.style(
                    _fig_rc,
                    height=230, legend=None,
                    margin=dict(l=44, r=10, t=32, b=26),
                    x=ct.time_axis(fy_ticks=False),
                    y=ct.plain_axis(range=[-1.1, 1.1], tick_format=".1f"),
                    title=dict(text="Rolling Correlation vs S&P 500",
                               font=dict(size=12, color=ct.color.ink, family=ct.font.data),
                               x=0, xanchor="left"),
                )
                st.plotly_chart(_fig_rc, use_container_width=True)
            else:
                st.info("Not enough data for rolling correlation.")
        else:
            st.info("S&P 500 benchmark data unavailable.")

    # ── Holdings Return Attribution ────────────────────────────────────────
    _section_header("Holdings Return Attribution")
    _weights_for_attr = st.session_state.get(_K_WEIGHTS, weights)
    _sm_attr          = opt.get("stock_metrics", {})
    _attr_rows = []
    for _at, _aw in sorted(_weights_for_attr.items(), key=lambda x: x[1], reverse=True):
        _m = _sm_attr.get(_at, {})
        _ar = _m.get("ann_return", 0)
        _contrib = _aw * _ar
        _attr_rows.append({
            "Ticker":         _at,
            "Weight":         _aw,
            "Ann. Return (%)":  round(_ar, 2),
            "Contribution (%)": round(_contrib, 2),
        })
    if _attr_rows:
        _attr_df = pd.DataFrame(_attr_rows).sort_values("Contribution (%)")
        _attr_colors = [GREEN if v >= 0 else RED for v in _attr_df["Contribution (%)"]]
        _fig_attr = go.Figure(go.Bar(
            x=_attr_df["Contribution (%)"],
            y=_attr_df["Ticker"],
            orientation="h",
            marker_color=_attr_colors, marker_line_width=0,
            text=[f"{v:+.2f}%" for v in _attr_df["Contribution (%)"]],
            textposition="outside",
            textfont=dict(size=ct.font.size.grid, family=ct.font.data,
                          color=ct.color.ink_muted),
            customdata=np.stack([_attr_df["Weight"]*100, _attr_df["Ann. Return (%)"]], axis=-1),
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Weight: %{customdata[0]:.1f}%<br>"
                "Ann. Return: %{customdata[1]:.2f}%<br>"
                "Contribution: %{x:+.2f}%<extra></extra>"
            ),
        ))
        # Horizontal bars: the value axis is x, so the gridlines live there and
        # y is the category axis. zero=False because contributions are signed.
        ct.style(
            _fig_attr,
            height=max(260, len(_attr_rows) * 38 + 60),
            legend=None, crosshair=False,
            margin=dict(l=64, r=72, t=38, b=30),
            x=ct.pct_axis(tick_format=".1f", zero=False,
                          title="Contribution to Portfolio Return (%)",
                          zeroline=True, zerolinecolor=ct.color.rule),
            y=ct.category_axis(),
            title=dict(text="Weighted Return Contribution by Holding",
                       font=dict(size=13, color=ct.color.ink, family=ct.font.data),
                       x=0, xanchor="left"),
        )
        st.plotly_chart(_fig_attr, use_container_width=True)

    # Rebalancing recommendations
    #
    # This used to derive `current_holdings` by dividing today's capital by
    # today's prices at the TARGET weights — i.e. it built a book that was
    # already perfectly on target, then asked what to rebalance. The answer was
    # always "nothing", so the section printed "Portfolio is balanced" every
    # single time and no user ever saw a recommendation.
    #
    # The drift it should be showing is real and computable from data already in
    # hand: buy the target weights at the start of the price window, hold without
    # rebalancing, and by today the winners have grown past their target weight.
    # That is the position a user who acted on this portfolio and left it alone
    # would actually hold.
    _section_header("Drift From Target Weights")
    weights = st.session_state.get(_K_WEIGHTS, {})
    opt     = st.session_state.get(_K_OPTIMISED, {})
    close_df_rb = opt.get("close_df")
    if weights and close_df_rb is not None and len(close_df_rb) > 1:
        _rb_capital = prefs.get("starting_capital", 10000)
        _entry_prices = {t: float(close_df_rb[t].iloc[0])
                         for t in weights if t in close_df_rb.columns}
        latest_prices = {t: float(close_df_rb[t].iloc[-1])
                         for t in weights if t in close_df_rb.columns}
        current_holdings = {
            t: (_rb_capital * weights[t]) / _entry_prices[t]
            for t in weights
            if _entry_prices.get(t, 0) > 0 and latest_prices.get(t, 0) > 0
        }
        recs = get_rebalancing_recommendations(current_holdings, weights, latest_prices)
        _rb_from = pd.Timestamp(close_df_rb.index[0]).strftime("%b %Y")
        st.caption(
            f"An illustration of allocation drift, not a trade list. It assumes the "
            f"model's target weights were held from {_rb_from} with no rebalancing; "
            f"the gaps below are what price moves alone have done to the allocation "
            f"since. Nothing here accounts for your tax position, cost basis, or "
            f"anything else about your circumstances.")
        if recs:
            for r in recs:
                _below  = r["Action"] == "BUY"
                _label  = "Below target" if _below else "Above target"
                action_color = GREEN if _below else RED
                st.markdown(f"""
                <div style="display:flex;justify-content:space-between;align-items:center;
                            padding:0.6rem 1rem;border-radius:8px;margin-bottom:0.4rem;
                            background:#f8fafc;border:1px solid #e2e8f0">
                    <span style="font-weight:600;color:#0f172a;font-size:0.9rem">{r['Ticker']}</span>
                    <span style="color:{action_color};font-weight:700;font-size:0.85rem">{_label}</span>
                    <span style="color:#64748b;font-size:0.82rem">{r['Off Target']} off target</span>
                    <span style="font-family:'JetBrains Mono',monospace;font-size:0.82rem;color:#0f172a">
                        ${r['Difference']:,.0f}
                    </span>
                </div>""", unsafe_allow_html=True)
        else:
            st.success("The model's weights are still on target — no drift to show.")

    # ── Fee drag (compact callout) ─────────────────────────────────────────
    _ETF_FEES = {
        "SPY":0.0945,"QQQ":0.20,"XLK":0.10,"XLV":0.10,"XLF":0.10,
        "XLY":0.10,"XLP":0.10,"XLI":0.10,"XLE":0.10,"XLB":0.10,
        "XLRE":0.10,"XLU":0.10,"XLC":0.10,"IEF":0.15,"TLT":0.15,
        "LQD":0.14,"HYG":0.48,"AGG":0.03,"BND":0.03,"SHY":0.15,
        "TIP":0.19,"VTIP":0.04,"MUB":0.07,"BNDX":0.07,"EMB":0.39,
    }
    _wt_fee = sum(weights.get(t, 0) * _ETF_FEES.get(t, 0) / 100 for t in weights)
    _fee_yrs = {"1 year":1,"3 years":3,"5 years":5,"10 years":10,"20+ years":20}.get(
        prefs.get("horizon","5 years"), 5)
    _fee_cap = prefs.get("starting_capital", 10000)
    _fee_mo  = prefs.get("monthly_contribution", 500)
    _fee_ret = (bt_met.get("Ann. Return") or 0) / 100
    _fee_mr_with    = max((1 + max(_fee_ret - _wt_fee, -0.5)) ** (1/12) - 1, -0.5)
    _fee_mr_without = (1 + _fee_ret) ** (1/12) - 1 if _fee_ret > -1 else 0

    _vwf, _vwof = _fee_cap, _fee_cap
    _vals_f, _vals_nf = [_vwf], [_vwof]
    for _ in range(_fee_yrs * 12):
        _vwf  = _vwf  * (1 + _fee_mr_with)    + _fee_mo
        _vwof = _vwof * (1 + _fee_mr_without)  + _fee_mo
        _vals_f.append(_vwf); _vals_nf.append(_vwof)
    _fee_drag = _vwof - _vwf

    # A single compact callout instead of a chart + card row: for a low-cost ETF
    # mix the drag is tiny and doesn't warrant a chart; this still surfaces cost
    # awareness, and the numbers scale up honestly for pricier funds / longer horizons.
    _fee_pct = (_fee_drag / _vwof * 100) if _vwof else 0
    st.markdown(f"""
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-left:3px solid #94a3b8;
                border-radius:8px;padding:0.7rem 1.05rem;margin-top:0.75rem;
                font-size:0.9rem;color:#334155;line-height:1.5">
      <span style="font-weight:700;color:#0f172a">Fee drag</span>
      &nbsp;·&nbsp; {_wt_fee*100:.2f}% weighted expense ratio
      &nbsp;·&nbsp; <span style="color:#dc2626;font-weight:600">${_fee_drag:,.0f} ({_fee_pct:.1f}%)</span>
      estimated cost over {_fee_yrs} years
    </div>""", unsafe_allow_html=True)

    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("← Back", key="step3_back"):
            st.session_state[_K_STEP] = 2
            if _K_BACKTEST in st.session_state:
                del st.session_state[_K_BACKTEST]
            st.rerun()
    with col2:
        if st.button("Next → Monte Carlo Forecast", type="primary", key="step3_next"):
            st.session_state[_K_STEP] = 4
            st.rerun()


# ── Step 4 — Monte Carlo Forecast ─────────────────────────────────────────────

def _render_step_4():
    prefs   = st.session_state.get(_K_PREFS, {})
    opt     = st.session_state.get(_K_OPTIMISED, {})
    weights = st.session_state.get(_K_WEIGHTS, {})
    bt      = st.session_state.get(_K_BACKTEST, {})

    if _K_MC not in st.session_state:
        horizon_map = {"1 year":1,"3 years":3,"5 years":5,"10 years":10,"20+ years":20}
        forecast_yr = horizon_map.get(prefs.get("horizon","5 years"), 5)

        with st.spinner(f"Running {forecast_yr}-year Monte Carlo simulation..."):
            try:
                returns_df = opt["returns_df"]
                start_cap  = prefs.get("starting_capital", 10000)
                monthly    = prefs.get("monthly_contribution", 500)
                target_val = prefs.get("target_value")

                mc_sim_df, mc_summary, milestones = run_portfolio_monte_carlo(
                    returns_df, weights, start_cap, monthly,
                    forecast_years=forecast_yr,
                    n_simulations=_MC_SIMULATIONS,
                    target_value=target_val,
                    log=lambda m: None,
                    market_returns=opt.get("market_returns"),
                )
                st.session_state[_K_MC] = {
                    "sim_df":     mc_sim_df,
                    "summary":    mc_summary,
                    "milestones": milestones,
                }
            except Exception as e:
                st.error(f"Monte Carlo failed: {e}")
                import traceback as _tb; print(_tb.format_exc())   # server log, not UI
                return

    mc_data    = st.session_state[_K_MC]
    mc_sim_df  = mc_data["sim_df"]
    mc_summary = mc_data["summary"]
    milestones = mc_data["milestones"]
    prefs      = st.session_state.get(_K_PREFS, {})
    start_cap  = prefs.get("starting_capital", 10000)

    _section_header("Monte Carlo Forecast Results")

    # ── Timeline selector for probability metrics ──────────────────────────
    horizon_map = {"1 year": "1yr", "3 years": "3yr", "5 years": "5yr", "10 years": "10yr"}
    available_horizons = [lbl for lbl, key in horizon_map.items() if key in milestones]
    # Add the full forecast horizon as an option
    forecast_horizon_label = mc_summary.get("Forecast Horizon", "10 years")
    full_horizon_yr = forecast_horizon_label.replace(" years","yr").replace(" year","yr")
    horizon_options = available_horizons + (
        [f"{forecast_horizon_label} (full)"]
        if full_horizon_yr not in [horizon_map.get(h) for h in available_horizons]
        else []
    )
    if not horizon_options:
        horizon_options = [forecast_horizon_label]

    sel_horizon = st.selectbox(
        "Show probabilities at horizon:",
        options=horizon_options,
        index=min(2, len(horizon_options)-1),  # default to 5yr if available
        key="mc_horizon_select",
        help="Choose the time horizon for the probability metrics below.",
    )

    # Resolve which probability data to use
    sel_key = horizon_map.get(sel_horizon.replace(" (full)",""))
    if sel_key and sel_key in milestones:
        ms_data = milestones[sel_key]
        prob_gain_val    = ms_data.get("prob_gain",    "—")
        prob_double_val  = ms_data.get("prob_double",  "—")
        prob_loss_val    = ms_data.get("prob_loss_20", "—")
        prob_goal_val    = ms_data.get("prob_goal")
        tot_invested_val = ms_data.get("total_invested", mc_summary.get("Total Invested", 0))
    else:
        prob_gain_val    = mc_summary.get("Prob. of Any Gain",   "—")
        prob_double_val  = mc_summary.get("Prob. of Doubling",   "—")
        prob_loss_val    = mc_summary.get("Prob. of >20% Loss",  "—")
        prob_goal_val    = mc_summary.get("Prob. of Reaching Goal")
        tot_invested_val = mc_summary.get("Total Invested", start_cap)

    st.caption(
        f"Probabilities at **{sel_horizon}** · "
        f"Total invested by then: **${tot_invested_val:,.0f}** · "
        f"'Any Gain' = portfolio exceeds total invested · "
        f"'Doubling' = exceeds 2× total invested"
    )

    # Probability gauges
    cols = st.columns(3)
    for _card in [
        (cols[0], "Prob. of Any Gain",   prob_gain_val,   GREEN),
        (cols[1], "Prob. of Doubling",    prob_double_val, BLUE),
        (cols[2], "Prob. of >20% Loss",   prob_loss_val,   RED),
    ]:
        col, label, value, color = _card[:4]
        sub = _card[4] if len(_card) > 4 else None
        with col:
            st.markdown(_metric_card(label, value, color, subtitle=sub),
                        unsafe_allow_html=True)

    with st.expander("How Monte Carlo probabilities are calculated"):
        st.markdown("""
**What this simulation does:**
Runs 1,000 independent scenarios for your portfolio using correlated daily returns
sampled from historical data. Each scenario compounds daily over the forecast period
with monthly contributions added throughout.

**Return assumption:**
Each stock's expected return uses CAPM — the risk-free rate plus its beta × a 5%
equity risk premium. The forecast is driven by how much *market* risk each holding
carries, not by which names recently ran up, so a hot recent stretch can't distort it.

**Correlation:**
Cross-asset correlations are preserved using Cholesky decomposition of the
historical covariance matrix — so when tech stocks fall together, the simulation
reflects that.

**What these probabilities mean:**
- **Prob. of Any Gain** — % of simulations where final value exceeds total amount invested
- **Prob. of Doubling** — % of simulations where final value exceeds 2× total invested
- **Prob. of >20% Loss** — % of simulations where final value is more than 20% below total invested

**Limitations:**
- Assumes returns are log-normally distributed (fat tails in real markets are larger)
- Does not model tax drag, fund fees, or trading costs
- Black swan events (2008, COVID) are underweighted relative to their real-world impact
- Probabilities are illustrative — not a guarantee of any outcome
        """)

    st.caption(
        "Monte Carlo assumes log-normally distributed returns and stationary volatility. "
        "It does not model recessions, black-swan events, or regime changes. "
        "Probabilities are illustrative, not guaranteed. Not investment advice."
    )

    if prob_goal_val is not None:
        st.markdown(f"""
        <div style="background:#f8fafc;border:2px solid #8b5cf6;border-radius:12px;
                    padding:1rem;text-align:center;margin-top:0.75rem">
            <div style="font-size:0.68rem;font-weight:600;letter-spacing:0.5px;
                        text-transform:uppercase;color:#64748b">Prob. of Reaching Your Goal</div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:2rem;
                        font-weight:500;color:{PURPLE};margin-top:4px">
                {prob_goal_val}
            </div>
            <div style="font-size:0.78rem;color:#64748b;margin-top:4px">
                Target: ${prefs.get('target_value',0):,.0f} by {sel_horizon}
            </div>
        </div>""", unsafe_allow_html=True)

    # P50 / bear / bull
    _section_header("Outcome Range")
    cols2 = st.columns(5)
    for col, label, key, color in [
        (cols2[0], "Bear (P5)",  "Bear Case (P5)",  RED),
        (cols2[1], "Low (P25)",  "Low Case (P25)",  AMBER),
        (cols2[2], "Median",     "Median (P50)",    DARK),
        (cols2[3], "Bull (P75)", "Bull Case (P75)", BLUE),
        (cols2[4], "Best (P95)", "Best Case (P95)", GREEN),
    ]:
        val = mc_summary.get(key, 0)
        with col:
            st.markdown(_metric_card(label, f"${val:,.0f}" if isinstance(val,(int,float)) else val, color), unsafe_allow_html=True)

    # Fan chart
    _section_header("Simulation Fan Chart")
    # All paths, not the first 300: the Outcome Range cards above are computed from
    # the full set, so sampling here made the fan's endpoints disagree with the
    # Bear/Median/Best numbers printed a few inches above it.
    x_days = list(range(len(mc_sim_df)))
    pcts   = np.percentile(mc_sim_df.values, [5, 25, 50, 75, 95], axis=1)

    fig_mc = go.Figure()
    fig_mc.add_trace(go.Scatter(x=x_days,y=pcts[4],name="P95 (Best)",
                                line=dict(color=GREEN,width=1.5)))
    fig_mc.add_trace(go.Scatter(x=x_days,y=pcts[3],name="P75 (Bull)",
                                line=dict(color=BLUE,width=1),
                                fill="tonexty",fillcolor="rgba(14,165,233,0.07)"))
    fig_mc.add_trace(go.Scatter(x=x_days,y=pcts[2],name="Median",
                                line=dict(color=DARK,width=2.5)))
    fig_mc.add_trace(go.Scatter(x=x_days,y=pcts[1],name="P25 (Low)",
                                line=dict(color=AMBER,width=1),
                                fill="tonexty",fillcolor="rgba(245,158,11,0.07)"))
    fig_mc.add_trace(go.Scatter(x=x_days,y=pcts[0],name="P5 (Bear)",
                                line=dict(color=RED,width=1.5)))
    fig_mc.add_hline(y=start_cap, line_dash="dot", line_color=MUTED, opacity=0.5,
                     annotation_text="Starting capital", annotation_position="right")
    if prefs.get("target_value"):
        fig_mc.add_hline(y=prefs["target_value"], line_dash="dash", line_color=PURPLE,
                         opacity=0.7, annotation_text="Your goal", annotation_position="right")
    ct.style(
        fig_mc,
        height=400,
        margin=dict(l=64, r=16, t=26, b=40),
        x=ct.linear_axis(title="Trading Days"),
        # zero=False: the fan starts at the opening capital, and anchoring the
        # axis at $0 would squeeze the whole projection into the top band.
        y=ct.value_axis(zero=False, title="Portfolio Value ($)"),
    )
    st.plotly_chart(fig_mc, use_container_width=True)

    # Milestone table
    _section_header("Projected Value at Key Milestones")
    ms_rows = []
    for horizon, pct_data in milestones.items():
        ms_rows.append({
            "Horizon":    horizon,
            "Bear (P5)":  f"${pct_data['P5']:,.0f}",
            "Low (P25)":  f"${pct_data['P25']:,.0f}",
            "Median":     f"${pct_data['P50']:,.0f}",
            "Bull (P75)": f"${pct_data['P75']:,.0f}",
            "Best (P95)": f"${pct_data['P95']:,.0f}",
        })
    st.dataframe(pd.DataFrame(ms_rows), use_container_width=True, hide_index=True)

    # Export downloads
    _section_header("Download Full Portfolio Report")

    # Both formats share one gate: they are the same portfolio in two wrappers.
    # Note this only skips the export block — the Back / Start New navigation
    # below must still render, or a signed-out user is stranded on this step.
    from entitlements import require_export, record, render_quota_note
    _exp_ok, _exp_user = require_export("portfolio")

    _gen_col1, _gen_col2 = st.columns(2)
    with _gen_col1:
        if _exp_ok and st.button("Generate Excel Report", type="primary",
                                 use_container_width=True, key="gen_excel"):
            with st.spinner("Building Excel report..."):
                try:
                    bt_data    = st.session_state.get(_K_BACKTEST, {})
                    opt_data   = st.session_state.get(_K_OPTIMISED, {})
                    stock_mets = opt_data.get("stock_metrics", {})
                    corr_mat   = opt_data.get("corr_matrix")
                    div_sc     = opt_data.get("div_score", 5)
                    t_info     = opt_data.get("ticker_info", {})
                    from portfolio_excel import build_portfolio_excel
                    excel_buf  = build_portfolio_excel(
                        preferences           = prefs,
                        final_weights         = weights,
                        stock_metrics         = stock_mets,
                        backtest_df           = bt_data.get("df"),
                        backtest_metrics      = bt_data.get("metrics", {}),
                        heatmap_df            = bt_data.get("heatmap"),
                        mc_sim_df             = mc_sim_df,
                        mc_summary            = mc_summary,
                        milestones            = milestones,
                        corr_matrix           = corr_mat,
                        diversification_score = div_sc,
                        ticker_info           = t_info,
                    )
                    st.session_state[_K_EXCEL] = excel_buf
                    record(_exp_user, "portfolio_excel",
                           f"{len(weights)} holdings")
                except Exception as e:
                    st.error(f"Excel build failed: {e}")
                    import traceback as _tb; print(_tb.format_exc())   # server log, not UI

    with _gen_col2:
        if _exp_ok and PPTX_AVAILABLE and st.button(
                "Generate PowerPoint Report", type="primary",
                use_container_width=True, key="gen_pptx"):
            with st.spinner("Building PowerPoint report..."):
                try:
                    bt_data    = st.session_state.get(_K_BACKTEST, {})
                    opt_data   = st.session_state.get(_K_OPTIMISED, {})
                    stock_mets = opt_data.get("stock_metrics", {})
                    corr_mat   = opt_data.get("corr_matrix")
                    div_sc     = opt_data.get("div_score", 5)
                    t_info     = opt_data.get("ticker_info", {})
                    from pptx_builder import build_portfolio_pptx
                    pptx_buf   = build_portfolio_pptx(
                        preferences           = prefs,
                        final_weights         = weights,
                        stock_metrics         = stock_mets,
                        backtest_df           = bt_data.get("df"),
                        backtest_metrics      = bt_data.get("metrics", {}),
                        mc_sim_df             = mc_sim_df,
                        mc_summary            = mc_summary,
                        milestones            = milestones,
                        corr_matrix           = corr_mat,
                        diversification_score = div_sc,
                        ticker_info           = t_info,
                    )
                    st.session_state[_K_PPTX] = pptx_buf
                    record(_exp_user, "portfolio_pptx",
                           f"{len(weights)} holdings")
                except Exception as e:
                    st.error(f"PowerPoint build failed: {e}")
                    import traceback as _tb; print(_tb.format_exc())   # server log, not UI

    _dl_col1, _dl_col2 = st.columns(2)
    with _dl_col1:
        if _exp_ok and _K_EXCEL in st.session_state:
            st.download_button(
                label="Download Portfolio Report (.xlsx)",
                data=st.session_state[_K_EXCEL],
                file_name=f"QuantWizard_Portfolio_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True, type="primary", key="download_portfolio",
            )
    with _dl_col2:
        if _exp_ok and _K_PPTX in st.session_state:
            st.session_state[_K_PPTX].seek(0)
            st.download_button(
                label="Download Portfolio Report (.pptx)",
                data=st.session_state[_K_PPTX],
                file_name=f"QuantWizard_Portfolio_{datetime.now().strftime('%Y%m%d')}.pptx",
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                use_container_width=True, type="primary", key="download_portfolio_pptx",
            )
    if _exp_ok:
        render_quota_note(_exp_user)

    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("← Back to Backtest", key="step4_back"):
            st.session_state[_K_STEP] = 3
            if _K_MC in st.session_state:
                del st.session_state[_K_MC]
            st.rerun()
    with col2:
        if st.button("Start New Portfolio", key="step4_restart"):
            # _K_PPTX was missing, so a freshly-started portfolio still offered the
            # previous one's PowerPoint download.
            for k in [_K_STEP, _K_PREFS, _K_OPTIMISED, _K_WEIGHTS,
                      _K_BACKTEST, _K_MC, _K_EXCEL, _K_PPTX, _K_RANKINGS]:
                st.session_state.pop(k, None)
            st.rerun()

    # ── Save / Load portfolio ──────────────────────────────────────────────
    st.markdown("---")
    _section_header("Save & Load Portfolios")

    _sv_col, _ld_col = st.columns(2)

    with _sv_col:
        st.markdown("**Save this portfolio**")
        # The address comes from the signed-in session, never a text box. A
        # free-text field here let anyone write a portfolio into someone else's
        # account just by typing their address — and it made "which email did I
        # use?" a thing users had to remember.
        _save_email = auth.current_email() or ""
        if _save_email:
            st.caption(f"Saving to **{_save_email}**")
        else:
            st.info("Sign in to save or track this portfolio — "
                    "use the **Sign in** button at the top right.")
        _save_name  = st.text_input("Portfolio name", placeholder="My Growth Portfolio",
                                    key="save_port_name",
                                    disabled=not _save_email)
        if st.button("Save Portfolio", key="save_port_btn"):
            if _save_email.strip() and _save_name.strip():
                _weights  = st.session_state.get(_K_WEIGHTS, {})
                _prefs    = st.session_state.get(_K_PREFS, {})
                _bt       = st.session_state.get(_K_BACKTEST, {})
                _metrics  = _bt.get("metrics", {}) if isinstance(_bt, dict) else {}
                _ok = save_portfolio(
                    user_email=_save_email,
                    name=_save_name,
                    weights=_weights,
                    preferences=_prefs,
                    metrics=_metrics,
                )
                if _ok:
                    st.success("Portfolio saved!")
                else:
                    # Say what actually went wrong. "Check your connection" was
                    # wrong for every cause except an actual network drop, and it
                    # sent debugging in the wrong direction for a malformed
                    # SUPABASE_URL more than once.
                    from database import last_write_error
                    st.error(f"Save failed — {last_write_error()}")
            else:
                st.warning("Enter both an email and a name.")

        st.markdown("<div style='color:#94a3b8;font-size:0.8rem;margin:0.4rem 0'>— or —</div>",
                    unsafe_allow_html=True)
        if st.button("Track this forward", key="track_port_btn",
                     help="Save to 'Your Portfolios' and track its real performance from today."):
            if _save_email.strip() and _save_name.strip():
                _tw  = st.session_state.get(_K_WEIGHTS, {})
                _cap = float(st.session_state.get(_K_PREFS, {}).get("starting_capital", 10000) or 10000)
                if not _tw:
                    st.warning("Build a portfolio first.")
                else:
                    _alloc = {tk: w * _cap for tk, w in _tw.items() if w and w > 0}
                    with st.spinner("Pricing holdings…"):
                        _lots, _skipped = dollars_to_lots(_alloc, datetime.today().strftime("%Y-%m-%d"))
                    if not _lots:
                        st.error("Couldn't price the holdings.")
                    else:
                        _pid = save_tracked_portfolio(_save_email, _save_name, _lots,
                                                      datetime.today().strftime("%Y-%m-%d"))
                        if _pid:
                            st.success("Now tracking — open the **Your Portfolios** tab.")
                        else:
                            # Report the reason the write itself gave, rather than
                            # re-probing and guessing. The old version asked
                            # tracked_storage_status() and announced "the table
                            # isn't in this project" for any non-200 — including a
                            # doubled /rest/v1 in the URL, where the table was
                            # present and the advice was actively misleading.
                            from database import last_write_error
                            st.error(f"Couldn't save — {last_write_error()}")
            else:
                st.warning("Enter both an email and a name.")

    with _ld_col:
        st.markdown("**Load a saved portfolio**")
        # Same rule as saving: your own account only. This used to accept any
        # address and return that person's portfolios to whoever asked.
        _load_email = auth.current_email() or ""
        if not _load_email:
            st.caption("Sign in to see portfolios you've saved.")
        elif st.button("Find My Portfolios", key="load_port_btn"):
            _saved = load_portfolios(_load_email)
            if _saved:
                st.session_state[_K_FOUND_PORTS] = _saved
            else:
                st.info("You haven't saved any portfolios yet.")

        if _K_FOUND_PORTS in st.session_state:
            _saved = st.session_state[_K_FOUND_PORTS]
            for _p in _saved:
                _pcols = st.columns([3, 1, 1])
                with _pcols[0]:
                    _date = _p.get("created_at", "")[:10]
                    st.markdown(f"**{_p['name']}** <span style='color:#94a3b8;font-size:0.78rem'>"
                                f"saved {_date}</span>", unsafe_allow_html=True)
                    _tickers = ", ".join(list(_p.get("weights", {}).keys())[:6])
                    st.caption(_tickers)
                with _pcols[1]:
                    if st.button("Load", key=f"load_{_p['id']}"):
                        # Restore the saved risk profile and rebuild from current
                        # market data. Setting only _K_WEIGHTS (the old behaviour)
                        # left the optimiser frame, backtest, forecast and both
                        # export buffers in place from the PREVIOUS portfolio — so
                        # a loaded allocation was displayed next to another
                        # portfolio's backtest, drawdown and Monte Carlo, and any
                        # loaded ticker absent from the stale price frame was
                        # silently dropped.
                        st.session_state[_K_WEIGHTS] = _p["weights"]
                        if _p.get("preferences"):
                            st.session_state[_K_PREFS] = _p["preferences"]
                        for _k in (_K_OPTIMISED, _K_BACKTEST, _K_MC,
                                   _K_EXCEL, _K_PPTX):
                            st.session_state.pop(_k, None)
                        st.session_state[_K_STEP] = 2
                        st.info(f"Loaded **{_p['name']}** — its risk profile is "
                                f"restored and the analysis is being rebuilt on "
                                f"today's prices, so weights may differ from the "
                                f"saved copy.")
                        st.rerun()
                with _pcols[2]:
                    if st.button("Delete", key=f"del_{_p['id']}"):
                        delete_portfolio(_p["id"])
                        del st.session_state[_K_FOUND_PORTS]
                        st.rerun()

    st.markdown(render_section("Backtesting Methodology", _disc.BACKTEST),
                unsafe_allow_html=True)
    st.markdown(render_section("Portfolio Optimisation", _disc.OPTIMISATION),
                unsafe_allow_html=True)
    st.markdown(render_section("Monte Carlo Projections", _disc.MONTE_CARLO),
                unsafe_allow_html=True)
    st.markdown(render_inline(_disc.FULL_FOOTER), unsafe_allow_html=True)


# ── Main entry point ──────────────────────────────────────────────────────────

def render_portfolio_builder(api_key, is_pro=False):
    """Main entry point — renders the full portfolio builder UI."""

    # DEV_MODE_FREE bypasses the Pro gate entirely.
    # Original paywall UI preserved below — do not delete.
    if not DEV_MODE_FREE and not is_pro:
        st.markdown("""
        <div style="background:#0f172a;border:1px solid #334155;border-radius:16px;
                    padding:2.5rem;text-align:center;margin:1rem 0">
            <div style="font-size:1.75rem;margin-bottom:0.75rem"></div>
            <div style="color:#fff;font-weight:600;font-size:1.2rem;margin-bottom:0.5rem">
                Portfolio Builder is a Pro Feature
            </div>
            <div style="color:#94a3b8;font-size:0.9rem;margin-bottom:1.5rem;
                        max-width:480px;margin-left:auto;margin-right:auto">
                Build custom portfolios with backtesting, efficient frontier optimisation,
                Monte Carlo simulation, Sharpe-ranked stock selection, and full Excel report export.
            </div>
            <div style="color:#38bdf8;font-size:1.1rem;font-weight:600">$9.99 / month</div>
            <div style="color:#64748b;font-size:0.8rem;margin-top:4px">Cancel anytime</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Upgrade to Pro", type="primary", key="upgrade_portfolio"):
            st.session_state["show_payment"] = True
            st.rerun()
        return

    # Sign-in and a recorded acceptance of the terms, before anything is built.
    # This is the one feature that outputs something shaped like a personal
    # recommendation, so it is the one that most needs the user to have been
    # told, in an act they performed, that it isn't one.
    from legal import require_agreement
    if not require_agreement("the Portfolio Builder"):
        return

    st.markdown("""
    <div style="background:linear-gradient(135deg,var(--brand-1),var(--brand-2));border-radius:14px;
                padding:1.6rem 1.8rem;color:#e2e8f0;margin-bottom:1.2rem">
        <div style="font-size:1.5rem;font-weight:700;color:#fff">Portfolio Builder</div>
        <div style="font-size:0.95rem;color:#b0c4de;margin-top:0.4rem;max-width:640px">
            Build a risk-optimized portfolio from a ranked universe — backtested over five
            years, with an efficient frontier, Monte Carlo forecast and a one-click Excel report.
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Step indicator ─────────────────────────────────────────────────────────
    steps      = ["① Preferences", "② Universe", "③ Optimise", "④ Backtest", "⑤ Forecast"]
    curr_step  = st.session_state.get(_K_STEP, 0)
    step_html  = "".join(
        f'<div style="flex:1;padding:8px 4px;border-radius:8px;text-align:center;font-size:11px;'
        f'{"background:#eff6ff;border:1px solid #93c5fd;color:#1d4ed8;font-weight:500" if i==curr_step else "background:#f8fafc;border:1px solid #e2e8f0;color:#64748b" if i>curr_step else "background:#f0fdf4;border:1px solid #86efac;color:#15803d;font-weight:500"}">'
        f'{s}</div>'
        for i, s in enumerate(steps))
    st.markdown(f'<div style="display:flex;gap:5px;margin-bottom:1.5rem">{step_html}</div>',
                unsafe_allow_html=True)

    # ── Dispatcher ─────────────────────────────────────────────────────────────
    if curr_step == 0:
        _render_step_0()
    elif curr_step == 1:
        _render_step_1(api_key)
    elif curr_step == 2:
        _render_step_2(api_key)
    elif curr_step == 3:
        _render_step_3()
    elif curr_step == 4:
        _render_step_4()
