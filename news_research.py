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

# ── Source reliability tiers ─────────────────────────────────────────────────
# Our upstreams are lopsided: Polygon's feed is ~95% Motley Fool and Finnhub's is
# ~75% Yahoo, so an unweighted merge reads as two publishers with a rotating
# byline. Tier drives ranking, and MAX_PER_SOURCE below stops any one outlet from
# owning the page.
_TIER_WIRE = (          # paid company announcements, not independent reporting
    "globenewswire", "pr newswire", "prnewswire", "business wire", "businesswire",
    "accesswire", "newsfile", "ein presswire", "issuerdirect", "acquire media",
)
_TIER_MAJOR = (         # primary financial press / wire services
    "reuters", "bloomberg", "wall street journal", "wsj", "financial times",
    "associated press", "cnbc", "barron", "marketwatch", "investor's business daily",
    "investors business daily", "new york times", "washington post", "the economist",
    "axios", "forbes", "fortune", "nikkei", "guardian",
)
_TIER_SECONDARY = (     # aggregators, commentary, retail-facing outlets
    "yahoo", "seeking alpha", "seekingalpha", "benzinga", "motley fool", "fool.com",
    "zacks", "thestreet", "investing.com", "business insider", "insider monkey",
    "simply wall st", "247wallst", "quartz", "invezz", "tipranks",
)


def source_tier(source):
    """Lower is better. 0 = official filing, 1 = major press, 2 = secondary,
    3 = unknown/other, 4 = press-release wire."""
    s = (source or "").lower()
    if "sec edgar" in s:
        return 0
    if any(k in s for k in _TIER_MAJOR):
        return 1
    if any(k in s for k in _TIER_SECONDARY):
        return 2
    if any(k in s for k in _TIER_WIRE):
        return 4
    return 3


MAX_PER_SOURCE = 3      # no single outlet may own more than this many slots
MIN_FEED = 8            # below this we relax the cap rather than show a stub feed


def is_english(text):
    """Reject headlines that aren't in a Latin script.

    Polygon's wire carries global editions, so a Hebrew or Japanese release turns
    up verbatim in a US retail feed. Judged by script rather than by a language
    library: count ASCII letters against letters from non-Latin blocks (Hebrew
    0x590+, Arabic 0x600+, Cyrillic 0x400+, CJK 0x4E00+). The 0x2FF floor keeps
    accented Latin ("Société", "Über") on the English side.
    """
    t = text or ""
    latin = sum(1 for c in t if "a" <= c.lower() <= "z")
    other = sum(1 for c in t if ord(c) > 0x2FF and c.isalpha())
    return latin >= other


# Plaintiff firms that publish "deadline to join the class action" wire releases.
# These are advertisements for legal services dressed as news; they flood the PR
# wires and crowd out actual company coverage.
_LAW_FIRMS = (
    "rosen law", "rosen, global", "the rosen", "pomerantz", "levi & korsinsky",
    "bronstein, gewirtz", "glancy prongay", "robbins geller", "kessler topaz",
    "faruqi & faruqi", "schall law", "kahn swick", "bragar eagel", "block & leviton",
    "berger montague", "gross law", "howard g. smith", "johnson fistel", "scott+scott",
    "labaton", "bernstein liebhard",
)


def is_solicitation(title, summary=""):
    """True for plaintiff-firm class-action adverts, which are not investor news.

    Deliberately narrow: a law-firm name, or 'class action' paired with the
    call-to-action wording. Genuine enforcement coverage ("SEC charges X with
    fraud") has neither and is kept — that's real news about the company.
    """
    hay = f"{title or ''} {summary or ''}".lower()
    if any(f in hay for f in _LAW_FIRMS):
        return True
    if "class action" in hay and any(
        k in hay for k in ("deadline", "lead plaintiff", "encourag",
                           "secure counsel", "suffered losses", "reminds investors")):
        return True
    return False


def _source_brand(source):
    """Collapse a publisher's editions to one brand so the per-source cap can't be
    gamed by regional splits — "Yahoo", "Yahoo Finance", "Yahoo Finance UK" and
    "Yahoo Sports" are one outlet's worth of quota, not four."""
    s = re.sub(r"[^a-z0-9 ]", " ", (source or "").lower())
    words = [w for w in s.split() if w]
    if words and words[0] == "the":
        words = words[1:]
    return words[0] if words else "unknown"


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


def _gnews_rss(query, days, limit, tickers):
    """Google News RSS — keyless, and the only upstream that spans the wider press.

    Polygon and Finnhub between them return ~4 distinct outlets for a ticker (and
    only 2 for the market-wide feed); this one routinely returns 20+, which is what
    makes the per-source cap in _diversify meaningful. Titles arrive as
    "Headline - Publisher", and the publisher is also given in <source>, so the
    suffix is stripped.
    """
    out = []
    try:
        import xml.etree.ElementTree as ET
        from email.utils import parsedate_to_datetime

        r = requests.get("https://news.google.com/rss/search",
                         params={"q": f"{query} when:{days}d", "hl": "en-US",
                                 "gl": "US", "ceid": "US:en"},
                         headers={"User-Agent": "Mozilla/5.0 (compatible; QuantWizard/1.0)"},
                         timeout=10)
        if r.status_code != 200:
            return out
        for it in ET.fromstring(r.content).findall(".//item")[:limit]:
            title = (it.findtext("title") or "").strip()
            src_el = it.find("source")
            source = (src_el.text if src_el is not None else "") or "Google News"
            # "Why Apple Stock Dropped Today - Yahoo Finance" -> drop the byline
            if source and title.endswith(f" - {source}"):
                title = title[: -(len(source) + 3)]
            ts = 0
            try:
                ts = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp())
            except Exception:
                pass
            out.append(_mk(title, it.findtext("link"), source, "gnews", ts,
                           "", None, list(tickers)))
    except Exception:
        pass
    return out


def _fetch_gnews_market(days=2, limit=60):
    """Market-wide headlines. Polygon's market feed is two publishers deep, so
    without this the News page is a Motley Fool column with a GlobeNewswire
    sidebar."""
    return _gnews_rss(
        "(stock market OR S&P 500 OR Nasdaq OR Dow Jones OR Federal Reserve "
        "OR earnings OR Wall Street)", days, limit, [])


def _fetch_gnews(ticker, company_name=None, days=7, limit=40):
    """Per-ticker Google News search."""
    out = []
    try:
        name = (company_name or "").strip()
        # Quote the company name so multi-word names aren't OR'd apart, and require
        # a finance word — otherwise a consumer brand pulls in its sponsorships
        # (a plain "Coca-Cola" search returned UFC.com and Yahoo Sports).
        subject = f'"{name}" OR {ticker.upper()}' if name else ticker.upper()
        q = f"({subject}) (stock OR shares OR earnings OR investors OR analyst)"
        out = _gnews_rss(q, days, limit, [ticker.upper()])
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
    # gnews last: it carries no summary, so when the same story also arrives from
    # Polygon/Finnhub we want to keep the copy that has one.
    prio = {"sec": 0, "polygon": 1, "finnhub": 2, "fmp": 3, "gnews": 4}
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
            _fetch_gnews(ticker, company_name) +
            _fetch_edgar_8k(ticker))
    # Relevance: the story must actually name the company in its title or summary.
    #
    # Neither of the obvious shortcuts works. Symbol-scoped endpoints are not
    # self-certifying — Finnhub's company-news returns market round-ups ("Top Three
    # ETFs to Watch") that merely mention the company. And a["tickers"] is worse
    # than useless: for finnhub/fmp _mk fills it with the symbol we queried, while
    # Polygon tags every ETF round-up with its holdings, so an AAPL page fills up
    # with "SCHF vs. SPGM". Tag count doesn't separate them either — measured on
    # live data, real Apple stories carried 9-11 tickers and the noise carried 4-5.
    #
    # Matching title+summary rather than title alone matters for mega-caps, which
    # are usually referenced in the body: AAPL 5 -> 11 relevant, MSFT 4 -> 8.
    name_tokens = [w.lower() for w in re.split(r"\W+", company_name or "")
                   if len(w) > 3][:2]
    tk = ticker.lower()

    def relevant(a):
        if a["provider"] == "sec":
            return True                  # a filing is by definition about the issuer
        hay = f"{a['title']} {a['summary']}".lower()
        return tk in hay or any(tok in hay for tok in name_tokens)

    def names_in_title(a):
        t = (a["title"] or "").lower()
        return tk in t or any(tok in t for tok in name_tokens)

    # Language and solicitation are a hard gate applied before relevance, not part
    # of it — the relevance fallback below deliberately widens the net, and it must
    # not be able to pull a Hebrew wire release or a class-action advert back in.
    deduped = [a for a in _dedupe(arts)
               if is_english(a["title"]) and not is_solicitation(a["title"], a["summary"])]
    arts = [a for a in deduped if relevant(a)]
    # Quiet tickers (and any ticker whose company_name we weren't given) can filter
    # down to nothing; show the wider feed rather than an empty section.
    if len(arts) < 3:
        arts = deduped
    # Rank: stories *about* the company first (a passing mention shouldn't head the
    # feed on a day of real company news), then by source quality, then newest.
    # -tier because sort is descending and lower tier == better.
    arts.sort(key=lambda a: (names_in_title(a) or a["provider"] == "sec",
                             -source_tier(a["source"]),
                             a["ts"]), reverse=True)
    return _diversify(arts, limit)


def _diversify(ranked, limit, max_per_source=None):
    """Pick `limit` articles spread across publishers, best-ranked first.

    Round-robin: take each outlet's top story, then everyone's second, and so on
    up to `max_per_source`. Taking a straight top-N and merely capping runs lets a
    prolific outlet still own the tail once the pool thins out (Coca-Cola filtered
    down to nine outlets and Yahoo took seven slots that way).

    Selection is diversified, then the result is restored to rank order so the
    feed still reads most-relevant-first.
    """
    cap = MAX_PER_SOURCE if max_per_source is None else max_per_source
    rank_of, buckets, order = {}, {}, []
    for i, a in enumerate(ranked):
        rank_of[id(a)] = i
        b = _source_brand(a["source"])
        if b not in buckets:
            buckets[b] = []
            order.append(b)
        buckets[b].append(a)

    picked = []
    for depth in range(cap):
        for b in order:
            if len(buckets[b]) > depth:
                picked.append(buckets[b][depth])
                if len(picked) >= limit:
                    picked.sort(key=lambda a: rank_of[id(a)])
                    return picked
    # Too few outlets to fill the page at full quota. Don't pad up to `limit` — on
    # Coca-Cola that bought 4 extra articles at the cost of letting Yahoo hold 7 of
    # 24 slots. Only top up to MIN_FEED so a thinly-covered ticker still has a
    # usable section; past that, a shorter, broader feed is the better answer.
    if len(picked) < MIN_FEED:
        chosen = {id(a) for a in picked}
        picked += [a for a in ranked if id(a) not in chosen][: MIN_FEED - len(picked)]
    picked.sort(key=lambda a: rank_of[id(a)])
    return picked


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
            a = _mk(it.get("title"), it.get("article_url"),
                    (it.get("publisher") or {}).get("name", "Polygon"),
                    "polygon", _iso_to_ts(it.get("published_utc")),
                    it.get("description"), None, tks)
            # This feed used to be returned raw, which is how a Hebrew-language
            # plaintiff-firm release ended up as the top story on the News page.
            # Trending counts are taken from everything; only the shown list is
            # filtered, so a story we won't display still informs the tickers.
            if is_english(a["title"]) and not is_solicitation(a["title"], a["summary"]):
                arts.append(a)
    except Exception:
        pass
    trending = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:14]

    # Polygon's market wire is two publishers deep; once the plaintiff-firm
    # releases are stripped it can't fill a page. Google News supplies the breadth.
    arts += [a for a in _fetch_gnews_market()
             if is_english(a["title"]) and not is_solicitation(a["title"], a["summary"])]
    # Same ranking and per-publisher cap as the per-ticker feed, so the market page
    # isn't three-quarters one wire service either.
    arts = _dedupe(arts)
    arts.sort(key=lambda a: (-source_tier(a["source"]), a["ts"]), reverse=True)
    return {"articles": _diversify(arts, 24), "trending": trending}


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
