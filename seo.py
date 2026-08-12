"""
seo.py — give the crawled HTML a title, a description and our favicon.

THE PROBLEM
-----------
`st.set_page_config(page_title=..., page_icon=...)` sets the tab title and icon
*after* React has booted. The HTML a crawler is served is Streamlit's own
`static/index.html`, which ships with:

    <title>Streamlit</title>
    <link rel="shortcut icon" href="./favicon.png" />   <- Streamlit's icon
    (no <meta name="description"> at all)

which is why quantwizard.co listed on Google as "Streamlit ... Streamlit" with a
generic icon while the live tab looked correct. Nothing inside the Streamlit
script API can fix it, because by the time our code runs the document has already
been sent.

THE FIX
-------
Rewrite that file on disk at start-up, and replace Streamlit's `favicon.png` in
the same directory with ours — Streamlit serves that directory at the site root,
so `/favicon.png` becomes our mark, which is the exact URL Google looks for.

WHAT THIS DOES NOT FIX
----------------------
This makes the one indexed URL *look* right. It does not make the app rankable:
a Streamlit app is a single websocket-rendered URL with no crawlable per-ticker
pages, which is the structural problem a separate static content site solves.
See the SEO section of `claudenotes.md` before treating this as an SEO strategy.

DEPLOYMENT
----------
Patching site-packages is undone by every rebuild, so this has to run on every
boot: `app.py` calls `apply()` at import. It is idempotent, it never raises, and
it no-ops on a read-only filesystem. It can also be run as a deploy step with
`python seo.py`, which is the more reliable of the two — a start-up patch can
miss the very first request if a crawler beats the first browser to the app.
"""

from __future__ import annotations

import os
import shutil

# ── What the listing should say ───────────────────────────────────────────────
# TITLE is what Google shows as the blue link; it truncates around 60 characters.
# DESCRIPTION is the snippet underneath, truncated around 155-160. Both are
# deliberately concrete — a crawler cannot see any of the rendered page, so these
# two strings are the entire search listing.
SITE_URL = os.getenv("SITE_URL", "https://quantwizard.co").rstrip("/")

TITLE = "QuantWizard — Equity research reports in 30 seconds"

DESCRIPTION = (
    "A full equity research report on any US stock in 30 seconds: DCF valuation, "
    "Monte Carlo, fundamentals, peers and risk — exported to Excel, Word or "
    "PowerPoint."
)

# Streamlit serves its static directory at the site root, so this resolves to
# https://<site>/favicon.png once `apply()` has copied our icon into place.
FAVICON_URL = f"{SITE_URL}/favicon.png"

# `enableStaticServing = true` in .streamlit/config.toml publishes ./static at
# /app/static/<file>. The logo is a stand-in: a proper 1200x630 social card would
# preview better when the link is shared. It is not used for the search favicon.
OG_IMAGE_URL = f"{SITE_URL}/app/static/logo.png"

# Bumping this re-applies the patch over an older injection. v2 added the
# crawlable <noscript> body; without a bump, an instance already carrying v1
# would skip it forever.
_MARKER = "<!-- quantwizard-seo v2 -->"

# ── The body a crawler actually reads ─────────────────────────────────────────
# Streamlit ships an empty shell — the entire <body> is a <div id="root"> and a
# "you need to enable JavaScript" line — and delivers every pixel of content over
# a WEBSOCKET. Googlebot executes JavaScript but does not hold websockets open,
# so its render pass finds an empty root. There is literally no text on this site
# for a crawler to read, which is why the search snippet was the title repeated
# back ("Streamlit ... Streamlit") and why assistants reported the domain as
# resolving to nothing.
#
# This replaces Streamlit's noscript line with an honest description of the app.
# noscript is the correct place for it: it is genuinely what a visitor without
# JavaScript sees, it is not hidden from anyone, and it describes exactly what
# the page does — so it is a real fallback, not cloaking. It will not make the
# app rank; it makes the listing legible. Ranking needs crawlable per-topic
# pages, which needs the separate static site.
_NOSCRIPT_BODY = """<noscript>
      <h1>QuantWizard — equity research reports and portfolio tools</h1>
      <p>QuantWizard generates a full equity research report on any US-listed
         stock in about thirty seconds: discounted-cash-flow valuation, Monte
         Carlo simulation, fundamentals from SEC EDGAR, peer comparison and risk
         statistics, exported as an Excel workbook, a PowerPoint deck or a Word
         document.</p>
      <p>It also builds model portfolios from a ranked universe using published
         portfolio-construction methods — mean-variance optimisation, a
         sector-relative multi-factor screen, covariance shrinkage, historical
         backtesting and crash stress tests — and tracks their value forward
         against the S&amp;P 500.</p>
      <p>QuantWizard is a research tool, not a registered investment adviser.
         Nothing it produces is investment advice. See the
         <a href="/?page=terms">Terms of Service</a> and
         <a href="/?page=privacy">Privacy Policy</a>.</p>
      <p>This application needs JavaScript enabled to run.</p>
    </noscript>"""

_ROBOTS_TXT = f"""# QuantWizard
User-agent: *
Allow: /

Sitemap: {SITE_URL}/sitemap.xml
"""


def _sitemap_xml() -> str:
    """Just the apex, deliberately.

    Every `?page=` URL returns byte-identical HTML with the same title, so
    listing them would be submitting duplicates — and the canonical tag points
    all of them at `/` anyway. Listing URLs a canonical tag disavows is a
    contradictory signal. This file becomes useful when there are genuinely
    distinct pages to put in it, which means the static content site.
    """
    from datetime import date
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{SITE_URL}/</loc>
    <lastmod>{date.today().isoformat()}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>
"""


def _head_block() -> str:
    """The tags to inject, replacing Streamlit's bare <title>."""
    return f"""{_MARKER}
    <title>{TITLE}</title>
    <meta name="description" content="{DESCRIPTION}" />
    <link rel="canonical" href="{SITE_URL}/" />

    <meta property="og:type" content="website" />
    <meta property="og:site_name" content="QuantWizard" />
    <meta property="og:title" content="{TITLE}" />
    <meta property="og:description" content="{DESCRIPTION}" />
    <meta property="og:url" content="{SITE_URL}/" />
    <meta property="og:image" content="{OG_IMAGE_URL}" />

    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="{TITLE}" />
    <meta name="twitter:description" content="{DESCRIPTION}" />
    <meta name="twitter:image" content="{OG_IMAGE_URL}" />

    <link rel="icon" type="image/png" href="/favicon.png" />
    <link rel="apple-touch-icon" href="/favicon.png" />

    <script type="application/ld+json">
    {{
      "@context": "https://schema.org",
      "@type": "Organization",
      "name": "QuantWizard",
      "url": "{SITE_URL}/",
      "logo": "{FAVICON_URL}"
    }}
    </script>"""


def _streamlit_static_dir() -> str | None:
    try:
        import streamlit
        return os.path.join(os.path.dirname(streamlit.__file__), "static")
    except Exception:
        return None


def _patch_index(static_dir: str, log) -> bool:
    """Rewrite index.html's <title> into the full head block. Idempotent."""
    path = os.path.join(static_dir, "index.html")
    if not os.path.exists(path):
        log("seo: index.html not found — skipped")
        return False

    html = open(path, encoding="utf-8").read()

    if _MARKER in html:
        return True   # already patched this boot, or by the deploy step

    # An older marker version means a stale block is in there; drop it back to a
    # bare <title> so the replace below has something to match.
    if "quantwizard-seo" in html:
        start = html.find("<!-- quantwizard-seo")
        end   = html.find("</script>", html.find("application/ld+json", start))
        if start != -1 and end != -1:
            html = html[:start] + "<title>Streamlit</title>" + html[end + 9:]

    if "<title>" not in html:
        log("seo: no <title> to replace — skipped")
        return False

    head = html.find("<title>")
    tail = html.find("</title>") + len("</title>")
    html = html[:head] + _head_block() + html[tail:]

    # Streamlit's own icon tag would otherwise still be in the document, and the
    # last icon link wins in some crawlers. Remove it rather than out-order it.
    html = html.replace('<link rel="shortcut icon" href="./favicon.png" />', "")

    # Give the crawler a body to read. Matched loosely because Streamlit has
    # reworded this line between releases.
    import re as _re
    html, n = _re.subn(r"<noscript>.*?</noscript>", _NOSCRIPT_BODY, html,
                       count=1, flags=_re.DOTALL)
    if not n:
        log("seo: no <noscript> to replace — body left empty for crawlers")

    open(path, "w", encoding="utf-8").write(html)
    log("seo: index.html patched")
    return True


def _write_file(static_dir: str, name: str, content: str, log) -> bool:
    """Drop a file into Streamlit's static dir, which is served at the site root."""
    path = os.path.join(static_dir, name)
    try:
        if os.path.exists(path) and open(path, encoding="utf-8").read() == content:
            return True
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        log(f"seo: {name} written")
        return True
    except Exception as e:
        log(f"seo: could not write {name} ({e})")
        return False


def _patch_favicon(static_dir: str, log) -> bool:
    """Put our mark where Streamlit's favicon lives, so /favicon.png is ours."""
    ours = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "favicon.png")
    if not os.path.exists(ours):
        log("seo: assets/favicon.png missing — favicon left as Streamlit's")
        return False
    target = os.path.join(static_dir, "favicon.png")
    try:
        # Compare bytes so a redeploy doesn't rewrite an identical file.
        if os.path.exists(target) and os.path.getsize(target) == os.path.getsize(ours):
            if open(target, "rb").read() == open(ours, "rb").read():
                return True
        shutil.copyfile(ours, target)
        log("seo: favicon replaced")
        return True
    except Exception as e:
        log(f"seo: favicon copy failed ({e})")
        return False


def apply(log=lambda _m: None) -> bool:
    """Patch the served HTML and favicon. Never raises — SEO is not worth a crash.

    Returns True only if both halves succeeded.
    """
    try:
        static_dir = _streamlit_static_dir()
        if not static_dir or not os.path.isdir(static_dir):
            log("seo: streamlit static dir not found — skipped")
            return False
        ok_html    = _patch_index(static_dir, log)
        ok_icon    = _patch_favicon(static_dir, log)
        ok_robots  = _write_file(static_dir, "robots.txt",  _ROBOTS_TXT,    log)
        ok_sitemap = _write_file(static_dir, "sitemap.xml", _sitemap_xml(), log)
        return ok_html and ok_icon and ok_robots and ok_sitemap
    except Exception as e:
        # A read-only filesystem is the expected failure, and it is survivable:
        # the app runs exactly as before, just with Streamlit's listing.
        log(f"seo: skipped ({e})")
        return False


if __name__ == "__main__":
    ok = apply(log=print)
    print("seo: done" if ok else "seo: incomplete — see messages above")
