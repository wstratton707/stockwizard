"""auth.py — sign-in for QuantWizard, built on Streamlit's native OIDC.

Why native (`st.login` / `st.user` / `st.logout`) rather than our own form:

  * We never see, transmit, hash or store a password. For a product about
    people's money, the smallest possible amount of credential-handling code we
    own is the correct amount.
  * Streamlit sets and validates the session cookie itself, so "stay signed in
    when I refresh" works without us hand-rolling refresh-token rotation.
    `st.context.cookies` is read-only — Python cannot set a cookie — so the
    alternative was a third-party cookie component plus our own token lifecycle.
  * Identity lives with the provider, so password reset, email verification,
    lockout and MFA are theirs to get right, not ours.

What replaced what
------------------
The old gate was `st.session_state["user_email"]` populated from a free-text box,
with the address mirrored into `?email=` so it survived a refresh. That was not
authentication: anyone could type any address and read that person's saved and
tracked portfolios, and `check_subscription(?email=)` would hand out Pro to
anyone who knew a subscriber's address.

Data continuity
---------------
`current_email()` is the single key everything is stored under, and it matches
the `user_email` column already used by `saved_portfolios` and
`tracked_portfolios`. A user who signs in with the same address they typed
before keeps their existing data — no migration, no backfill.

Configuration (secrets.toml — see README)
-----------------------------------------
    [auth]
    redirect_uri  = "https://<your-app>/oauth2callback"
    cookie_secret = "<long random string>"

    [auth.auth0]
    client_id           = "..."
    client_secret       = "..."
    server_metadata_url = "https://<tenant>.us.auth0.com/.well-known/openid-configuration"

Every deployment needs its own `redirect_uri` registered with the provider —
Streamlit Cloud and Render are different origins and each needs its own callback
URL allow-listed in Auth0.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import streamlit as st

# The key under `[auth.<provider>]` in secrets.toml. Auth0 is used because its
# database connections give real email+password, while still leaving the door
# open to Google/Apple later without touching this code.
PROVIDER = "auth0"

# ── Making `[auth]` reachable from environment variables ─────────────────────
#
# Streamlit reads auth config ONLY from `.streamlit/secrets.toml`, relative to
# the working directory. Its secrets flow is one-directional — st.secrets is
# copied into os.environ, never the reverse — so an environment variable can
# never become an `[auth]` key on its own, and `[auth.auth0]` is nested TOML
# that a flat variable cannot express anyway.
#
# Hosts that have no secrets-pane hit this hard. Render exposes Secret Files
# under /etc/secrets/, which Streamlit never looks at: the file is present,
# correct, and completely ignored, and the app reports sign-in as unconfigured
# while holding a valid config.
#
# So rather than read env vars as secrets, we WRITE the file from them at
# import — before anything can touch st.secrets, which parses once and caches.
# That makes ordinary environment variables the single place all config lives,
# on every host, which is what everything else in this app already does.
_STREAMLIT_SECRETS = Path(".streamlit/secrets.toml")

# Mounted-file locations, tried before falling back to env vars so an existing
# Render Secret File keeps working without being reconfigured.
_MOUNTED_SECRET_CANDIDATES = (
    "/etc/secrets/secrets.toml",             # Render's documented mount point
    "/etc/secrets/.streamlit/secrets.toml",  # if the file was named with the path
    "secrets.toml",                          # Render also drops it in the project root
)

# All five are required — a partial config produces a button that fails at click
# time instead of rendering as unconfigured, which is strictly worse.
_ENV_REDIRECT_URI  = "AUTH_REDIRECT_URI"
_ENV_COOKIE_SECRET = "AUTH_COOKIE_SECRET"
_ENV_CLIENT_ID     = "AUTH_CLIENT_ID"
_ENV_CLIENT_SECRET = "AUTH_CLIENT_SECRET"
_ENV_METADATA_URL  = "AUTH_SERVER_METADATA_URL"


def _toml_str(value: str) -> str:
    """Quote a value as a TOML basic string.

    Escaped rather than interpolated raw: a stray quote or backslash in a
    generated cookie secret would otherwise produce a file that fails to parse,
    and Streamlit reports that as no configuration at all.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _stage_secrets() -> None:
    """Put an `[auth]` block where Streamlit will find it.

    Order: an existing file wins (Streamlit Cloud's Secrets pane already IS this
    file, and a developer's local one must not be clobbered), then a
    host-mounted file, then environment variables.
    """
    try:
        if _STREAMLIT_SECRETS.exists():
            return

        for candidate in _MOUNTED_SECRET_CANDIDATES:
            src = Path(candidate)
            if src.is_file():
                _STREAMLIT_SECRETS.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, _STREAMLIT_SECRETS)
                return

        env = {k: (os.environ.get(k) or "").strip() for k in (
            _ENV_REDIRECT_URI, _ENV_COOKIE_SECRET,
            _ENV_CLIENT_ID, _ENV_CLIENT_SECRET, _ENV_METADATA_URL)}
        if not all(env.values()):
            return

        _STREAMLIT_SECRETS.parent.mkdir(parents=True, exist_ok=True)
        _STREAMLIT_SECRETS.write_text(
            "# Generated at startup from environment variables by auth.py.\n"
            "# Do not edit — it is rebuilt on every boot and is gitignored.\n"
            "[auth]\n"
            f"redirect_uri = {_toml_str(env[_ENV_REDIRECT_URI])}\n"
            f"cookie_secret = {_toml_str(env[_ENV_COOKIE_SECRET])}\n"
            "\n"
            f"[auth.{PROVIDER}]\n"
            f"client_id = {_toml_str(env[_ENV_CLIENT_ID])}\n"
            f"client_secret = {_toml_str(env[_ENV_CLIENT_SECRET])}\n"
            f"server_metadata_url = {_toml_str(env[_ENV_METADATA_URL])}\n",
            encoding="utf-8")
    except OSError:
        # A read-only filesystem must not stop the app booting — the Sign in
        # button degrades to disabled, exactly as when nothing is configured.
        pass


_stage_secrets()


# ── State ─────────────────────────────────────────────────────────────────────

def auth_configured() -> bool:
    """True when `[auth]` is present in secrets.

    Checked before every call into `st.login`/`st.user`, because when auth is
    NOT configured Streamlit raises `AttributeError` on `st.user.is_logged_in`
    rather than returning False, and `st.login()` raises outright. Without this
    guard the app would hard-crash on any instance whose secrets haven't been
    filled in yet — which, with two deployments, is a state we will be in.
    """
    try:
        return bool(st.secrets.get("auth", {}).get("redirect_uri"))
    except Exception:
        return False


def is_signed_in() -> bool:
    """True when there's a live, unexpired session."""
    if not auth_configured():
        return False
    try:
        if not bool(st.user.is_logged_in):
            return False
    except Exception:
        return False
    return not _token_expired()


def _token_expired() -> bool:
    """Whether the identity token's `exp` claim has passed.

    Streamlit's own docs are explicit that it does NOT check issuance or
    expiry implicitly — the cookie would otherwise keep someone signed in
    indefinitely after the provider considered the session over. Absent or
    unparseable `exp` is treated as NOT expired: the provider is the authority
    on session validity and we shouldn't sign people out over a missing claim.
    """
    try:
        exp = st.user.get("exp")
    except Exception:
        return False
    try:
        return exp is not None and float(exp) < time.time()
    except (TypeError, ValueError):
        return False


def current_email() -> str | None:
    """Verified email address, lowercased — the key all user data hangs off.

    Returns None when signed out, so callers can't accidentally read or write
    another user's rows by falling back to a stale value.
    """
    if not is_signed_in():
        return None
    try:
        email = st.user.get("email") or ""
    except Exception:
        return None
    return email.strip().lower() or None


def current_name() -> str:
    """Display name, falling back to the local part of the email."""
    if not is_signed_in():
        return ""
    try:
        name = (st.user.get("name") or "").strip()
    except Exception:
        name = ""
    if name:
        return name
    email = current_email() or ""
    return email.split("@")[0] if email else ""


def email_verified() -> bool:
    """Whether the provider says the address is verified.

    Not enforced anywhere yet — surfaced so a future gate (paid signup, say)
    can require it without re-deriving how to ask.
    """
    if not is_signed_in():
        return False
    try:
        return bool(st.user.get("email_verified", False))
    except Exception:
        return False


# ── Actions ───────────────────────────────────────────────────────────────────

def sign_in() -> None:
    """Start the provider redirect. No-op with a clear message if unconfigured."""
    if not auth_configured():
        st.error("Sign-in isn't configured on this deployment yet "
                 "(missing `[auth]` in secrets).")
        return
    st.login(PROVIDER)


def sign_out() -> None:
    """Clear the session cookie and any app state keyed to the old user."""
    # Anything derived from the previous identity must go with it, or the next
    # person to sign in on this browser inherits it.
    for key in ("user_email", "is_pro", "_sub_checked",
                "found_portfolios", "port_selected_weights"):
        st.session_state.pop(key, None)
    if auth_configured():
        st.logout()


# ── UI ────────────────────────────────────────────────────────────────────────

def render_nav_control() -> None:
    """The top-right control: a Sign in button, or the signed-in user + Sign out.

    Deliberately compact — it lives in the nav bar next to the page links.
    """
    if not auth_configured():
        st.button("Sign in", key="nav_signin_unconfigured",
                  use_container_width=True, disabled=True,
                  help="Sign-in isn't configured on this deployment yet.")
        return

    if is_signed_in():
        with st.popover(_short_label(), use_container_width=True):
            _name  = current_name()
            _email = current_email() or ""
            st.markdown(f"**{_name or _email}**")
            # Auth0 hands back the address as the display name for email+password
            # signups, so a plain name-then-email popover printed the same string
            # twice. Only show the address when it adds something.
            if _email and _email != _name:
                st.caption(_email)
            if not email_verified():
                st.caption("Unverified — check your inbox for the confirmation link.")
            if st.button("Sign out", key="nav_signout", use_container_width=True):
                sign_out()
                st.rerun()
    else:
        if st.button("Sign in", key="nav_signin", type="primary",
                     use_container_width=True):
            sign_in()


def _short_label() -> str:
    """First name only — the nav chip must never wrap.

    A full name wrapped to two lines and pushed the bar taller than the page
    links beside it. The full name and address are one click away in the popover,
    so the chip only has to be recognisably *you*.
    """
    name = (current_name() or "").strip()
    # Email+password signups come back with the address as the display name, so
    # the chip would read "wstratton90@gmai…". Use the part before the @ instead.
    if "@" in name:
        name = name.split("@")[0]
    first = name.split()[0] if name else ""
    if not first:
        email = current_email() or ""
        first = email.split("@")[0] if email else "Account"
    return first if len(first) <= 12 else first[:11] + "…"


def require_sign_in(feature: str = "this page",
                    blurb: str = "") -> bool:
    """Gate a page. Returns True when signed in; otherwise renders a prompt.

    Renders an unmissable panel rather than a one-line caption — the previous
    gate was a small form that read as an optional newsletter box, so it wasn't
    obvious that signing in was required at all.
    """
    if is_signed_in():
        return True

    st.markdown(f"""
<div class="signin-gate">
  <div class="signin-gate-eyebrow">Sign in required</div>
  <div class="signin-gate-title">Sign in to use {feature}</div>
  <div class="signin-gate-body">{blurb or
    "Your portfolios are private to your account and are tracked from the day "
    "you save them. Signing in keeps them yours."}</div>
</div>""", unsafe_allow_html=True)

    _c = st.columns([1.2, 3])
    with _c[0]:
        if not auth_configured():
            st.button("Sign in", disabled=True, use_container_width=True,
                      key=f"gate_signin_unconfigured_{feature}",
                      help="Sign-in isn't configured on this deployment yet.")
            st.caption("Ask the site owner to finish configuring sign-in.")
        else:
            if st.button("Sign in / Create account", type="primary",
                         use_container_width=True, key=f"gate_signin_{feature}"):
                sign_in()
    return False
