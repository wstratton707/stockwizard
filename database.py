"""
database.py — Supabase persistent cache layer (pure requests, no SDK needed)

Usage:
    from database import cache_get, cache_set

    data = cache_get("AAPL_2024-01-01_2024-12-31")
    if data is None:
        data = fetch_from_polygon(...)
        cache_set("AAPL_2024-01-01_2024-12-31", data, ttl_hours=24)
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
_TABLE       = "api_cache"
_TIMEOUT     = 8  # seconds


def _headers() -> dict:
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


def _base_url() -> str:
    return f"{SUPABASE_URL}/rest/v1/{_TABLE}"


def _available() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


# ── Public API ────────────────────────────────────────────────────────────────

def cache_get(cache_key: str):
    """
    Retrieve a cached value by key.
    Returns the Python object if found and not expired, else None.
    """
    if not _available():
        return None
    try:
        now = datetime.now(timezone.utc).isoformat()
        r = requests.get(
            _base_url(),
            headers=_headers(),
            params={
                "cache_key":  f"eq.{cache_key}",
                "expires_at": f"gt.{now}",
                "select":     "data",
                "limit":      "1",
            },
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            rows = r.json()
            if rows:
                return rows[0]["data"]
    except Exception as e:
        logger.warning(f"cache_get({cache_key}): {e}")
    return None


def cache_set(cache_key: str, data, ttl_hours: float = 24.0) -> bool:
    """
    Store a value in the cache with a TTL.
    data must be JSON-serialisable (dict or list).
    Returns True on success.
    """
    if not _available():
        return False
    try:
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
        r = requests.post(
            _base_url(),
            headers={**_headers(), "Prefer": "resolution=merge-duplicates"},
            json={
                "cache_key":  cache_key,
                "data":       data,
                "expires_at": expires_at,
            },
            timeout=_TIMEOUT,
        )
        return r.status_code in (200, 201, 204)
    except Exception as e:
        logger.warning(f"cache_set({cache_key}): {e}")
        return False


def cache_delete(cache_key: str) -> bool:
    """Delete a specific cache entry."""
    if not _available():
        return False
    try:
        r = requests.delete(
            _base_url(),
            headers=_headers(),
            params={"cache_key": f"eq.{cache_key}"},
            timeout=_TIMEOUT,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"cache_delete({cache_key}): {e}")
        return False


def cache_purge_expired() -> bool:
    """Delete all expired entries — call occasionally to keep the table clean."""
    if not _available():
        return False
    try:
        now = datetime.now(timezone.utc).isoformat()
        r = requests.delete(
            _base_url(),
            headers=_headers(),
            params={"expires_at": f"lt.{now}"},
            timeout=_TIMEOUT,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"cache_purge_expired: {e}")
        return False


def is_connected() -> bool:
    """Quick health check — returns True if Supabase is reachable."""
    if not _available():
        return False
    try:
        r = requests.get(
            _base_url(),
            headers=_headers(),
            params={"select": "cache_key", "limit": "1"},
            timeout=_TIMEOUT,
        )
        return r.status_code == 200
    except Exception:
        return False
