"""news_research.py — multi-source stock news aggregation, enrichment & AI briefs.

Pulls recent news for a ticker from every source we already have keys for —
Polygon, Finnhub, FMP, and SEC EDGAR 8-K filings (official material events) —
normalises them to one shape, de-duplicates across sources, tags each item with
a theme and a sentiment, and (optionally) synthesises a grounded, source-cited
brief with Claude Haiku.

Every network call is wrapped so one dead source never breaks the feed; the whole
thing degrades to "whatever we could fetch."
"""
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from data import _get, SEC_HEADERS, _sec_load_cik_map
from market_data import FINNHUB_BASE, finnhub_key, to_finnhub_symbol

# ── Theme classification (keyword → theme, first match wins) ──────────────────
THEME_RULES = [
    ("Earnings",          ["earnings", " eps", "revenue", "quarter", "guidance",
                           "beats", "misses", "results", "profit", "forecast", "outlook"]),
    ("Analyst",           ["analyst", "upgrade", "downgrade", "price target",
                           "rating", "initiat", "overweight", "underweight", "reiterat"]),
    ("M&A",               ["acqui", "merger", "buyout", "takeover", " deal", "stake", "divest"]),
    ("Product",           ["launch", "unveil", "release", "approval", " fda", "patent",
                           "partnership", "contract", "rollout"]),
    ("Legal/Regulatory",  ["lawsuit", " sue", "settle", "investigation", "regulat",
                           "antitrust", "fine ", "probe", "recall", "subpoena", "sec charges"]),
    ("Management",        [" ceo", " cfo", "executive", "resign", "appoint", "board",
                           "layoff", "restructur", "steps down"]),
    ("Capital Return",    ["dividend", "buyback", "repurchase", "split"]),
    ("Macro",             ["fed ", "inflation", "interest rate", "tariff", "recession", "jobs report"]),
]

_POS = ("surge", "jump", "beat", "upgrade", "record", "growth", "gain", "soar",
        "rally", "win", "strong", "raise", "boost", "top", "outperform", "high", "approve")
_NEG = ("plunge", "drop", "miss", "downgrade", "lawsuit", "fall", "slump", "warn",
        "cut", "loss", "weak", "decline", "probe", "recall", "sink", "slash", "concern", "fear")


def _clean(txt):
    return re.sub(r"\s+", " ", (txt or "")).strip()


def tag_theme(title, summary=""):
    hay = f" {(title or '').lower()} {(summary or '').lower()} "
    for theme, kws in THEME_RULES:
        if any(k in hay for k in kws):
            return theme
    return "General"


def _keyword_sentiment(title, summary=""):
    hay = f"{(title or '').lower()} {(summary or '').lower()}"
    p = sum(hay.count(w) for w in _POS)
    n = sum(hay.count(w) for w in _NEG)
    if p > n:
        return "Positive"
    if n > p:
        return "Negative"
    return "Neutral"


# ── Source fetchers (each returns a list of normalised dicts, [] on failure) ──
def _fetch_polygon(ticker, api_key, limit=20):
    out = []
    try:
        data = _get("/v2/reference/news", api_key,
                    params={"ticker": ticker.upper(), "limit": limit,
                            "order": "desc", "sort": "published_utc"})
        for it in (data or {}).get("results", []):
            tks = [t.upper() for t in (it.get("tickers") or [])]
            ins = next((i for i in (it.get("insights") or [])
                        if i.get("ticker", "").upper() == ticker.upper()), None)
            sent = (ins.get("sentiment") if ins else "").capitalize() or None
            ts = _iso_to_ts(it.get("published_utc"))
            out.append(_mk(it.get("title"), it.get("article_url"),
                           (it.get("publisher") or {}).get("name", "Polygon"),
                           "polygon", ts, it.get("description"), sent, tks))
    except Exception:
        pass
    return out


def _fetch_finnhub(ticker, days=21, limit=20):
    out = []
    key = finnhub_key()
    if not key:
        return out
    try:
        to_d = datetime.now(timezone.utc).date()
        fr_d = to_d - timedelta(days=days)
        r = requests.get(f"{FINNHUB_BASE}/company-news",
                         params={"symbol": to_finnhub_symbol(ticker), "token": key,
                                 "from": fr_d.isoformat(), "to": to_d.isoformat()},
                         timeout=8)
        if r.status_code == 200:
            for it in (r.json() or [])[:limit]:
                out.append(_mk(it.get("headline"), it.get("url"),
                               it.get("source", "Finnhub"), "finnhub",
                               it.get("datetime"), it.get("summary"), None,
                               [ticker.upper()]))
    except Exception:
        pass
    return out


def _fetch_fmp(ticker, limit=20):
    out = []
    import os
    key = os.environ.get("FMP_API_KEY", "").strip()
    if not key:
        return out
    try:
        r = requests.get("https://financialmodelingprep.com/api/v3/stock_news",
                         params={"tickers": ticker.upper(), "limit": limit, "apikey": key},
                         timeout=8)
        if r.status_code == 200:
            for it in (r.json() or []):
                out.append(_mk(it.get("title"), it.get("url"),
                               it.get("site", "FMP"), "fmp",
                               _iso_to_ts(it.get("publishedDate")), it.get("text"),
                               None, [(it.get("symbol") or ticker).upper()]))
    except Exception:
        pass
    return out


def _fetch_edgar_8k(ticker, limit=6):
    """Recent 8-K filings (official 'material event' disclosures) — a news source
    almost nobody surfaces to retail."""
    out = []
    try:
        cik = _sec_load_cik_map(log=lambda m: None).get(ticker.upper())
        if not cik:
            return out
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                         headers=SEC_HEADERS, timeout=15)
        if r.status_code != 200:
            return out
        rec = r.json().get("filings", {}).get("recent", {})
        forms = rec.get("form", []); dates = rec.get("filingDate", [])
        accns = rec.get("accessionNumber", []); docs = rec.get("primaryDocument", [])
        items_desc = rec.get("primaryDocDescription", [])
        cik_int = str(int(cik))
        n = 0
        for i, form in enumerate(forms):
            if form != "8-K":
                continue
            acc = accns[i].replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{docs[i]}"
            desc = items_desc[i] if i < len(items_desc) else ""
            title = f"8-K filing — {desc}" if desc else "8-K material-event filing"
            ts = _iso_to_ts(dates[i] + "T12:00:00Z")
            out.append(_mk(title, url, "SEC EDGAR", "sec", ts,
                           "Official material-event disclosure filed with the SEC.",
                           "Neutral", [ticker.upper()]))
            n += 1
            if n >= limit:
                break
    except Exception:
        pass
    return out


def _iso_to_ts(s):
    if not s:
        return 0
    if isinstance(s, (int, float)):
        return int(s)
    try:
        s = str(s).replace("Z", "+00:00")
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:
        try:
            return int(datetime.strptime(str(s)[:10], "%Y-%m-%d")
                       .replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            return 0


def _mk(title, url, source, provider, ts, summary, sentiment, tickers):
    title = _clean(title)
    summary = _clean(summary)[:400]
    if not sentiment:
        sentiment = _keyword_sentiment(title, summary)
    return {
        "title": title, "url": url or "", "source": source or provider,
        "provider": provider, "ts": ts or 0,
        "date": (datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
                 if ts else ""),
        "summary": summary, "sentiment": sentiment,
        "theme": tag_theme(title, summary),
        "tickers": tickers or [],
    }


def _norm_key(title):
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())[:48]


def _dedupe(articles):
    """Drop duplicates across sources — same story from different publishers —
    keeping the richest copy (longest summary, preferring an official/Polygon one)."""
    prio = {"sec": 0, "polygon": 1, "finnhub": 2, "fmp": 3}
    best = {}
    for a in articles:
        k = _norm_key(a["title"])
        if not k:
            continue
        cur = best.get(k)
        if cur is None or (
            (prio.get(a["provider"], 9), -len(a["summary"]))
            < (prio.get(cur["provider"], 9), -len(cur["summary"]))
        ):
            best[k] = a
    return list(best.values())


def aggregate_news(ticker, api_key, company_name=None, limit=24, days=21):
    """Fetch + merge + dedupe + rank recent news for `ticker`. Newest first."""
    arts = (_fetch_polygon(ticker, api_key, limit) +
            _fetch_finnhub(ticker, days, limit) +
            _fetch_fmp(ticker, limit) +
            _fetch_edgar_8k(ticker))
    # Relevance: title mentions the ticker or a company-name token (title-primary).
    name_tokens = [w.lower() for w in re.split(r"\W+", company_name or "")
                   if len(w) > 3][:2]
    tk = ticker.lower()
    def relevant(a):
        if a["provider"] in ("sec", "finnhub", "fmp"):
            return True                      # symbol-scoped endpoints
        t = a["title"].lower()
        return tk in t or any(tok in t for tok in name_tokens) or ticker.upper() in a["tickers"]
    arts = [a for a in _dedupe(arts) if relevant(a)]
    arts.sort(key=lambda a: a["ts"], reverse=True)
    return arts[:limit]


def sentiment_summary(articles):
    """Overall sentiment split + a simple net score in [-1, 1]."""
    if not articles:
        return {"positive": 0, "negative": 0, "neutral": 0, "score": 0.0, "n": 0}
    p = sum(1 for a in articles if a["sentiment"] == "Positive")
    n = sum(1 for a in articles if a["sentiment"] == "Negative")
    z = sum(1 for a in articles if a["sentiment"] == "Neutral")
    tot = max(1, p + n + z)
    return {"positive": p, "negative": n, "neutral": z,
            "score": round((p - n) / tot, 2), "n": p + n + z}


def theme_counts(articles):
    out = {}
    for a in articles:
        out[a["theme"]] = out.get(a["theme"], 0) + 1
    return dict(sorted(out.items(), key=lambda kv: kv[1], reverse=True))


def market_pulse(api_key, limit=90, universe=None):
    """Market-wide feed + 'trending' tickers, from ONE Polygon call (rate-safe).
    Trending = tickers appearing most across recent market articles. When a
    `universe` set is supplied, trending is restricted to it — which strips out
    OTC/variant junk (GOOGM, SPCX, …) and keeps names we actually cover."""
    arts, freq = [], {}
    uni = {u.upper() for u in universe} if universe else None
    try:
        data = _get("/v2/reference/news", api_key,
                    params={"limit": limit, "order": "desc", "sort": "published_utc"})
        for it in (data or {}).get("results", []):
            tks = [t.upper() for t in (it.get("tickers") or [])]
            for t in tks:
                if uni is not None:
                    if t not in uni:
                        continue
                elif not (t.isalpha() and len(t) <= 5):
                    continue
                freq[t] = freq.get(t, 0) + 1
            arts.append(_mk(it.get("title"), it.get("article_url"),
                            (it.get("publisher") or {}).get("name", "Polygon"),
                            "polygon", _iso_to_ts(it.get("published_utc")),
                            it.get("description"), None, tks))
    except Exception:
        pass
    trending = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:14]
    return {"articles": arts, "trending": trending}


# ── Catalysts (what's coming up / just filed) ─────────────────────────────────
def get_catalysts(ticker):
    """Forward-looking context: next earnings date (Finnhub) and the most recent
    8-K material-event filing (EDGAR). Every piece is optional."""
    out = {}
    key = finnhub_key()
    if key:
        try:
            today = datetime.now(timezone.utc).date()
            r = requests.get(f"{FINNHUB_BASE}/calendar/earnings",
                             params={"symbol": to_finnhub_symbol(ticker), "token": key,
                                     "from": today.isoformat(),
                                     "to": (today + timedelta(days=120)).isoformat()},
                             timeout=8)
            if r.status_code == 200:
                ec = [e for e in (r.json() or {}).get("earningsCalendar", [])
                      if e.get("date", "") >= today.isoformat()]
                if ec:
                    ec.sort(key=lambda e: e["date"])
                    out["next_earnings"] = {
                        "date": ec[0]["date"],
                        "eps_estimate": ec[0].get("epsEstimate"),
                    }
        except Exception:
            pass
    try:
        e8 = _fetch_edgar_8k(ticker, limit=1)
        if e8:
            out["latest_8k"] = {"date": e8[0]["date"], "url": e8[0]["url"]}
    except Exception:
        pass
    return out


# ── AI brief (Claude Haiku, strictly grounded + source-cited) ─────────────────
_BRIEF_MODEL = "claude-haiku-4-5-20251001"


def ai_news_brief(ticker, articles, company_name=None, api_key=None):
    """A short, grounded 'what's happening' brief synthesised ONLY from the
    supplied articles, with [n] citations. Returns {"text", "sources"} or None
    (no key, no SDK, no articles, or any error — always degrades gracefully).

    Grounding is enforced in the prompt: the model may use only the numbered
    items and must cite them; it is told not to invent anything. This is the
    guardrail that keeps AI-written 'news' safe for a finance product.
    """
    import os
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or not articles:
        return None
    try:
        import anthropic
    except Exception:
        return None

    used = articles[:12]
    lines = [f"[{i}] ({a['date']}, {a['source']}) {a['title']}."
             f"{(' ' + a['summary'][:220]) if a['summary'] else ''}"
             for i, a in enumerate(used, 1)]
    name = company_name or ticker
    prompt = (
        f"You are an equity-research news analyst. Below are recent, dated news "
        f"items about {name} ({ticker.upper()}), each numbered.\n\n"
        f"{chr(10).join(lines)}\n\n"
        f"Using ONLY the items above — invent nothing, add no outside knowledge — "
        f"write a briefing for an individual investor:\n"
        f"• A 2–3 sentence \"What's happening\" overview.\n"
        f"• Then 3–5 short bullets of the most important developments, each ending "
        f"with its source number(s) in brackets, e.g. [2] or [1][4].\n"
        f"Be factual and neutral, no hype, no price predictions or recommendations. "
        f"If the items don't support a point, leave it out."
    )
    try:
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=_BRIEF_MODEL, max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content
                       if getattr(b, "type", "") == "text").strip()
        if not text:
            return None
        return {"text": text,
                "sources": [{"n": i, "title": a["title"], "url": a["url"],
                             "source": a["source"]} for i, a in enumerate(used, 1)]}
    except Exception:
        return None
