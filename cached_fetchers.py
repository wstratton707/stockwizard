"""
cached_fetchers.py — Streamlit-cached wrappers around expensive fetchers.

Why a separate module?
  • Keeps data.py / analysis.py pure so precompute_strategy.py, precompute.py
    and CI tools don't pull in Streamlit as a dependency.
  • Centralises TTL / max_entries decisions so they're easy to tune.

Gotchas handled here:
  • max_entries set on every decorator so Railway's ~512MB worker can't OOM.
  • API keys passed as `_api_key` so they're not part of the cache hash.
  • Heavy functions that take DataFrames cache by proxy keys (ticker + last
    date + params) with the df passed as unhashable — avoids slow content
    hashing AND avoids "UnhashableParamError" from Streamlit.
  • FMP free tier is 250 req/day → ETF details TTL is a long 48h with an
    empty-result fallback cached 1h so errors don't hammer the quota.
"""

from __future__ import annotations
import streamlit as st

from data import (
    validate_ticker as _validate_ticker,
    fetch_stock_data as _fetch_stock_data,
    fetch_ohlcv as _fetch_ohlcv,
    fetch_company_details as _fetch_company_details,
    fetch_news as _fetch_news,
    fetch_peer_comparison as _fetch_peer_comparison,
    fetch_sector_data as _fetch_sector_data,
    fetch_next_earnings as _fetch_next_earnings,
    fetch_crypto_data as _fetch_crypto_data,
    fetch_crypto_details as _fetch_crypto_details,
    fetch_etf_details as _fetch_etf_details,
    detect_asset_type as _detect_asset_type,
)
from analysis import (
    run_monte_carlo as _run_monte_carlo,
    run_custom_forecast as _run_custom_forecast,
    detect_support_resistance as _detect_support_resistance,
    build_correlation_matrix as _build_correlation_matrix,
)

# ── Tuning constants ──────────────────────────────────────────────────────────
_TTL_SHORT   = 15 * 60       # 15 min — news, intraday-ish
_TTL_MEDIUM  = 60 * 60       # 1h    — prices, peers, sector
_TTL_LONG    = 6 * 60 * 60   # 6h    — earnings dates
_TTL_XLONG   = 24 * 60 * 60  # 24h   — company metadata, asset-type detection
_TTL_FMP     = 48 * 60 * 60  # 48h   — ETF details (FMP is 250 req/day)

_MAX_ENTRIES = 50   # per decorator — fits comfortably on 512MB Railway worker


# ── Data fetchers ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=_TTL_MEDIUM, max_entries=_MAX_ENTRIES, show_spinner=False)
def cached_validate_ticker(ticker, _api_key):
    return _validate_ticker(ticker, _api_key)


@st.cache_data(ttl=_TTL_XLONG, max_entries=_MAX_ENTRIES, show_spinner=False)
def cached_detect_asset_type(ticker, _api_key):
    return _detect_asset_type(ticker, _api_key)


@st.cache_data(ttl=_TTL_MEDIUM, max_entries=_MAX_ENTRIES, show_spinner=False)
def cached_fetch_stock_data(ticker, benchmark_tickers, _api_key,
                            start_override=None, end_override=None, bar_size="1d"):
    # log is intentionally dropped — caching replays should be silent
    return _fetch_stock_data(
        ticker, benchmark_tickers=benchmark_tickers, api_key=_api_key,
        log=lambda m: None,
        start_override=start_override, end_override=end_override, bar_size=bar_size,
    )


@st.cache_data(ttl=_TTL_MEDIUM, max_entries=_MAX_ENTRIES, show_spinner=False)
def cached_fetch_ohlcv(ticker, period, _api_key,
                       start_override=None, end_override=None, bar_size="1d"):
    return _fetch_ohlcv(
        ticker, period, _api_key, log=lambda m: None,
        start_override=start_override, end_override=end_override, bar_size=bar_size,
    )


@st.cache_data(ttl=_TTL_XLONG, max_entries=_MAX_ENTRIES, show_spinner=False)
def cached_fetch_company_details(ticker, _api_key):
    return _fetch_company_details(ticker, _api_key, log=lambda m: None)


@st.cache_data(ttl=_TTL_SHORT, max_entries=_MAX_ENTRIES, show_spinner=False)
def cached_fetch_news(ticker, _api_key):
    return _fetch_news(ticker, _api_key, log=lambda m: None)


@st.cache_data(ttl=_TTL_MEDIUM, max_entries=_MAX_ENTRIES, show_spinner=False)
def cached_fetch_peer_comparison(ticker, peers_tuple, _api_key):
    # peers passed as tuple so Streamlit can hash it
    return _fetch_peer_comparison(ticker, list(peers_tuple), _api_key, log=lambda m: None)


@st.cache_data(ttl=_TTL_MEDIUM, max_entries=_MAX_ENTRIES, show_spinner=False)
def cached_fetch_sector_data(ticker, _api_key, sector,
                             start_override=None, end_override=None, bar_size="1d"):
    return _fetch_sector_data(
        ticker, _api_key, sector, log=lambda m: None,
        start_override=start_override, end_override=end_override, bar_size=bar_size,
    )


@st.cache_data(ttl=_TTL_LONG, max_entries=_MAX_ENTRIES, show_spinner=False)
def cached_fetch_next_earnings(ticker, _api_key):
    return _fetch_next_earnings(ticker, _api_key)


@st.cache_data(ttl=_TTL_MEDIUM, max_entries=_MAX_ENTRIES, show_spinner=False)
def cached_fetch_crypto_data(ticker, _api_key,
                             start_override=None, end_override=None, bar_size="1d"):
    return _fetch_crypto_data(
        ticker, api_key=_api_key, log=lambda m: None,
        start_override=start_override, end_override=end_override, bar_size=bar_size,
    )


@st.cache_data(ttl=_TTL_MEDIUM, max_entries=_MAX_ENTRIES, show_spinner=False)
def cached_fetch_crypto_details(ticker):
    return _fetch_crypto_details(ticker)


@st.cache_data(ttl=_TTL_FMP, max_entries=_MAX_ENTRIES, show_spinner=False)
def cached_fetch_etf_details(ticker, _fmp_key):
    """FMP free tier is 250 req/day — long TTL + empty-result fallback caching."""
    try:
        result = _fetch_etf_details(ticker, _fmp_key)
    except Exception:
        result = {}
    return result or {}


# ── Heavy computations ────────────────────────────────────────────────────────
# DataFrames are expensive to hash by content. These wrappers hash by proxy
# keys (ticker + last date + params) and take the df as unhashable via `_df`.

@st.cache_data(ttl=_TTL_MEDIUM, max_entries=10, show_spinner=False)
def cached_run_monte_carlo(ticker, last_date, n_simulations, forecast_days, _df):
    return _run_monte_carlo(_df, n_simulations=n_simulations,
                            forecast_days=forecast_days, log=lambda m: None)


@st.cache_data(ttl=_TTL_MEDIUM, max_entries=10, show_spinner=False)
def cached_run_custom_forecast(ticker, last_date, n_simulations, forecast_days, _df):
    return _run_custom_forecast(_df, n_simulations=n_simulations,
                                forecast_days=forecast_days, log=lambda m: None)


@st.cache_data(ttl=_TTL_MEDIUM, max_entries=_MAX_ENTRIES, show_spinner=False)
def cached_detect_support_resistance(ticker, last_date, _df):
    return _detect_support_resistance(_df)


@st.cache_data(ttl=_TTL_MEDIUM, max_entries=_MAX_ENTRIES, show_spinner=False)
def cached_build_correlation_matrix(ticker, last_date, benchmarks_tuple, _df):
    benchmarks = list(benchmarks_tuple) if benchmarks_tuple else None
    return _build_correlation_matrix(_df, benchmarks)
