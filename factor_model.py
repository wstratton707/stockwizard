"""
factor_model.py — Sector-relative multi-factor scoring for the investable universe.

The job is NOT to find the highest raw metrics. It is to find the strongest
companies *relative to their sector peers* and hand the optimiser a clean
eligible universe, leaving position sizing to the risk model. A 32x forward P/E
is cheap for software and expensive for a utility; ranking those against each
other measures the sector, not the company.

Kept deliberately free of network and Streamlit imports so the whole model can
be unit-tested on synthetic input.

  score_universe(rankings)   -> adds fundamental_score (0-100) + factor detail
  eligible_universe(...)     -> applies hard filters and the per-sector cut

Both operate on the ranking dict precompute already produces:
    {ticker: {sector, mom_12m_adj, mom_6m, mom_3m, ann_vol, quality,
              last_price, analyst, fund: {...}}}
"""

from collections import defaultdict

# ── Versioning (see PART 19 of the audit brief) ──────────────────────────────
# The UI must never describe a model the cached data does not contain. Bump
# FACTOR_MODEL_VERSION whenever factors, weights, directions or the sector
# treatment change, and the builder will fall back to describing what is
# actually present rather than what the code believes.
FACTOR_MODEL_VERSION = 3
METHODOLOGY_NAME = "sector-relative six-factor, v3"

# ── Composite weights ─────────────────────────────────────────────────────────
# Sum to 100 so the composite reads directly as a 0-100 score.
#
# Reasoning, not convention:
#   momentum 23 — the best-evidenced cross-sectional factor there is; the audit
#                 brief asked whether 20-25% is right and it is.
#   quality  21 — the most persistent fundamental signal.
#   value    19 — now an average of up to three yields rather than one ratio,
#                 so it is more reliable than it was and earns a bigger share.
#   growth   12 — deliberately below value and quality: revenue growth and EPS
#                 growth are cousins, and both are partly re-expressed by
#                 momentum. Held down to avoid paying three times for one idea.
#   lowvol   15 — the low-volatility anomaly, and the only factor here that is
#                 measured absolutely.
#   health   10 — real but slow-moving, and the weakest of the fundamentals in
#                 EDGAR-only data.
WEIGHTS = {"momentum": 23, "quality": 21, "value": 19,
           "growth": 12, "lowvol": 15, "health": 10}

# Analyst consensus is an ADJUSTMENT, not a factor. Consensus is largely priced
# in and it is the least reliable feed we carry, so it may move a score by at
# most this many points and never decides a rank on its own. The audit brief's
# instinct that 15% would be far too much is correct.
ANALYST_MAX_POINTS = 3.0

# Below this many scored peers a within-sector rank carries no information, so
# every member falls back to neutral rather than being ranked against one or two
# others and treated as if that meant something.
MIN_PEERS_FOR_RANK = 5

NEUTRAL = 0.5

# Sectors that are not operating companies. They are scored on price factors
# only and are never gated on fundamentals they do not file.
NON_OPERATING = {"Market", "Commodities", "Government", "User", "Unknown"}
_BOND_PREFIX = "Bond-"

FINANCIAL_SECTORS = {"Financials"}


# ── Missing-data taxonomy (PART 9) ────────────────────────────────────────────
# Three states that must not be collapsed:
#
#   MISSING  the field is absent or None. The company may be excellent; we do
#            not know. -> excluded from the peer ranking, scored NEUTRAL.
#   INVALID  present but not meaningful as a ratio. The canonical case is
#            debt/equity computed on NEGATIVE equity, which produces a negative
#            number that would rank as "least levered in sector" — the single
#            most dangerous coercion in the whole model. -> treated as MISSING.
#   NEGATIVE genuinely negative and genuinely informative: a loss-making
#            company has a negative earnings yield, a shrinking company has
#            negative growth. -> KEPT and ranked at the bottom of its sector.
#
# The rule that matters: missing is never zero, and invalid is never a value.

def _num(v):
    """Return a finite float, or None for missing/invalid."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _positive_only(v):
    """For ratios that are meaningless at or below zero (INVALID, not NEGATIVE).

    debt/equity and current ratio both fall here: a negative reading means the
    denominator went negative, not that the company is conservatively financed.
    """
    f = _num(v)
    return f if (f is not None and f > 0) else None


def _pct_rank(values):
    """Average-rank percentile in [0,1]. Ties share their mean rank.

    Rank rather than min-max because min-max is hostage to outliers: one name up
    400% compresses everything else toward zero, so adding a single ticker
    rescales the whole universe.
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [NEUTRAL]
    order = sorted(range(n), key=lambda i: values[i])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            out[order[k]] = avg / (n - 1)
        i = j + 1
    return out


# ── Metric extraction ─────────────────────────────────────────────────────────

def _fund(entry):
    f = entry.get("fund")
    return f if isinstance(f, dict) else {}


def _market_cap(entry):
    f = _fund(entry)
    sh, px = _num(f.get("shares")), _num(entry.get("last_price"))
    return sh * px if (sh and px and sh > 0 and px > 0) else None


def _earnings_yield(entry):
    """EPS / price. Negative earnings give a negative yield — kept, informative."""
    eps, px = _num(_fund(entry).get("eps")), _num(entry.get("last_price"))
    return (eps / px) if (eps is not None and px and px > 0) else None


def _fcf_yield(entry):
    v, mc = _num(_fund(entry).get("fcf")), _market_cap(entry)
    return (v / mc) if (v is not None and mc) else None


def _ebitda_ev(entry):
    """EBITDA / enterprise value. Deliberately inverted vs EV/EBITDA so that
    higher is better, matching every other value metric's direction."""
    f, mc = _fund(entry), _market_cap(entry)
    e = _num(f.get("ebitda"))
    if e is None or not mc:
        return None
    ev = mc + (_num(f.get("debt")) or 0.0) - (_num(f.get("cash")) or 0.0)
    return (e / ev) if ev > 0 else None


# Direction is explicit for every metric: True where a HIGHER raw value should
# rank better. Nothing here relies on a reader inferring it.
VALUE_METRICS = (
    ("earnings_yield", _earnings_yield, True),
    ("fcf_yield",      _fcf_yield,      True),
    ("ebitda_ev",      _ebitda_ev,      True),
)
GROWTH_METRICS = (
    ("rev_cagr", lambda e: _num(_fund(e).get("rev_cagr")), True),
    ("eps_cagr", lambda e: _num(_fund(e).get("eps_cagr")), True),
)
QUALITY_METRICS = (
    ("net_margin", lambda e: _num(_fund(e).get("net_margin")), True),
    ("roe",        lambda e: _num(_fund(e).get("roe")),        True),
    ("f_score",    lambda e: _num(_fund(e).get("f_score")),    True),
)
HEALTH_METRICS = (
    # Lower debt/equity is better -> direction False. Non-positive is INVALID
    # (negative equity), not "no debt".
    ("d2e",       lambda e: _positive_only(_fund(e).get("d2e")),       False),
    ("cur_ratio", lambda e: _positive_only(_fund(e).get("cur_ratio")), True),
    ("f_score",   lambda e: _num(_fund(e).get("f_score")),             True),
)


# ── Sector-specific treatment (PART 8) ────────────────────────────────────────
# Banks, insurers and diversified financials are not operating companies in the
# sense the other factors assume, and we should say plainly what we can and
# cannot do with the data we hold.
#
# EDGAR basics give us margins, ROE, growth, EPS, debt, cash, EBITDA and FCF.
# For a financial:
#
#   EBITDA/EV   — meaningless. Interest is revenue, not a financing cost, and
#                 debt is raw material rather than leverage to net out.  DROPPED
#   FCF yield   — meaningless. Operating cash flow is dominated by balance-sheet
#                 movements (deposits, loan book), not owner earnings.  DROPPED
#   debt/equity — a bank is levered ~10x BY DESIGN. Within-sector ranking fixes
#                 the level but not the meaning: more leverage is not
#                 monotonically worse for a bank.                        DROPPED
#   current ratio — no working-capital cycle to describe.                DROPPED
#   Piotroski F — Piotroski's own sample excluded financial firms; several of
#                 the nine signals do not translate.                     DROPPED
#   earnings yield, ROE, net margin, growth — all standard for financials. KEPT
#
# That empties the health factor. We do NOT invent a substitute: a defensible
# bank-health model needs tier-1 capital, NPL ratios, coverage and net interest
# margin, none of which EDGAR companyfacts gives us at this granularity. The
# safest fallback is to score financials on the factors that do translate and
# redistribute the health weight across them, rather than rank banks on ratios
# that do not describe banks. This is a stated limitation, not a solved problem.
FINANCIALS_DROPPED = {
    "value":   {"fcf_yield", "ebitda_ev"},
    "quality": {"f_score"},
    "health":  {"d2e", "cur_ratio", "f_score"},     # -> factor unavailable
}


def _is_financial(sector):
    return sector in FINANCIAL_SECTORS


def _is_operating(sector):
    return not (sector in NON_OPERATING or str(sector).startswith(_BOND_PREFIX))


def _metrics_for(sector, group, metrics):
    """Drop the metrics that do not describe this sector."""
    if _is_financial(sector):
        banned = FINANCIALS_DROPPED.get(group, set())
        return tuple(m for m in metrics if m[0] not in banned)
    return metrics


# ── Scoring ───────────────────────────────────────────────────────────────────

def _rank_within_sectors(entries, groups, getter, higher_better):
    """Percentile-rank one metric inside each sector.

    Only names that HAVE the metric are ranked against each other; the rest are
    neutral. Coercing a missing value to zero would rank it as the worst (or,
    for an inverted metric, the best) name in its sector, which is how a missing
    debt/equity becomes "least levered".
    """
    out = {}
    for _sector, members in groups.items():
        have = [(t, getter(entries[t])) for t in members]
        have = [(t, v) for t, v in have if v is not None]
        if len(have) < MIN_PEERS_FOR_RANK:
            for t in members:
                out[t] = NEUTRAL
            continue
        ranks = _pct_rank([v for _t, v in have])
        for (t, _v), r in zip(have, ranks):
            out[t] = r if higher_better else (1.0 - r)
        for t in members:
            out.setdefault(t, NEUTRAL)
    return out


def _factor_from(entries, groups, metrics, group_name):
    """Average the sector-relative ranks of a factor's constituent metrics.

    Averaged only over the metrics a company actually has. Averaging in the
    neutral defaults would drag every partially-covered name toward the middle
    and make coverage look like mediocrity.
    """
    per_metric, getters = {}, {}
    for name, getter, higher in metrics:
        getters[name] = getter
        per_metric[name] = _rank_within_sectors(entries, groups, getter, higher)

    out = {}
    for t, e in entries.items():
        sector = e.get("sector", "Unknown")
        usable = _metrics_for(sector, group_name, metrics)
        vals = [per_metric[name][t] for name, getter, _h in usable
                if getter(e) is not None]
        out[t] = (sum(vals) / len(vals)) if vals else None      # None = no data
    return out


def score_universe(rankings: dict) -> dict:
    """Attach fundamental_score (0-100) and factor detail to every entry.

    Mutates and returns `rankings` (the shape precompute already caches).
    """
    entries = {t: e for t, e in rankings.items()
               if t != "_meta" and isinstance(e, dict)}
    if not entries:
        return rankings

    groups = defaultdict(list)
    for t, e in entries.items():
        groups[e.get("sector", "Unknown")].append(t)

    # Sector-relative fundamentals.
    f_value   = _factor_from(entries, groups, VALUE_METRICS,   "value")
    f_growth  = _factor_from(entries, groups, GROWTH_METRICS,  "growth")
    f_quality = _factor_from(entries, groups, QUALITY_METRICS, "quality")
    f_health  = _factor_from(entries, groups, HEALTH_METRICS,  "health")

    # Momentum: sector-relative, because a 20% move means different things in
    # utilities and semiconductors.
    mom_parts = []
    for field in ("mom_12m_adj", "mom_6m", "mom_3m"):
        mom_parts.append(_rank_within_sectors(
            entries, groups, lambda e, f=field: _num(e.get(f)), True))
    f_momentum = {t: sum(p[t] for p in mom_parts) / len(mom_parts) for t in entries}

    # Volatility: ABSOLUTE, and this is a considered choice rather than an
    # oversight. The low-volatility anomaly is a market-wide phenomenon, not a
    # within-sector one; ranking it per sector would force us to hold the most
    # volatile utility and the least volatile biotech, which inverts the very
    # property being selected for. The cost is a standing tilt toward utilities
    # and staples, and that is contained downstream by the builder's per-sector
    # floor and cap rather than by distorting the factor here.
    #
    # Beta and max drawdown are deliberately NOT added: beta already drives
    # expected return, and drawdown is largely a restatement of volatility.
    # Including them would pay three times for one idea.
    vol_vals = [(t, _num(e.get("ann_vol"))) for t, e in entries.items()]
    have_vol = [(t, v) for t, v in vol_vals if v is not None]
    lowvol = {}
    if len(have_vol) >= MIN_PEERS_FOR_RANK:
        ranks = _pct_rank([v for _t, v in have_vol])
        for (t, _v), r in zip(have_vol, ranks):
            lowvol[t] = 1.0 - r                      # lower vol ranks higher
    for t in entries:
        lowvol.setdefault(t, NEUTRAL)

    for t, e in entries.items():
        sector = e.get("sector", "Unknown")
        operating = _is_operating(sector)

        # Two different reasons a factor can be absent, and they must be
        # handled differently. Conflating them is a real scoring bug, not a
        # cosmetic one — the first version of this renormalised over whatever
        # was available, and AEP scored 84.0 on momentum and volatility alone
        # while KO scored 70.5 across all six. Being strong on a narrow base
        # beat being good at everything, which is the opposite of the intent.
        #
        #   NOT APPLICABLE — the sector genuinely has no such factor, e.g.
        #     financial health for a bank (see FINANCIALS_DROPPED). Excluded
        #     from the denominator: it is not evidence we are missing, it is
        #     evidence that does not exist for this kind of company.
        #
        #   APPLICABLE BUT MISSING — the company should have filed it and we
        #     could not get it. Scored NEUTRAL and kept in the denominator, so
        #     an incomplete name is pulled toward the middle instead of being
        #     judged only on its best evidence.
        if operating:
            applicable = {"momentum", "quality", "value", "growth", "health", "lowvol"}
            if _is_financial(sector):
                applicable.discard("health")     # not applicable, see above
            raw = {"momentum": f_momentum.get(t), "quality": f_quality.get(t),
                   "value": f_value.get(t), "growth": f_growth.get(t),
                   "health": f_health.get(t), "lowvol": lowvol.get(t)}
            parts = {k: (raw[k] if raw[k] is not None else NEUTRAL)
                     for k in applicable}
            imputed = sorted(k for k in applicable if raw[k] is None)
        else:
            # Funds, bond ETFs and commodity trusts are not operating
            # companies. They file no statements, so a fundamental score would
            # be fiction. They are scored on the price factors only and carry
            # no fundamental_score at all; the builder pins them as benchmarks
            # and diversifiers rather than ranking them against businesses.
            parts = {"momentum": f_momentum.get(t) or NEUTRAL,
                     "lowvol": lowvol.get(t) or NEUTRAL}
            imputed = []

        wsum = sum(WEIGHTS[k] for k in parts) or 1
        score = sum(WEIGHTS[k] * v for k, v in parts.items()) / wsum * 100.0
        avail = parts

        analyst = _num(e.get("analyst"))
        if analyst is not None:
            score += ANALYST_MAX_POINTS * 2.0 * (analyst - 0.5)
            e["f_analyst"] = round(analyst, 4)
        else:
            e.pop("f_analyst", None)

        score = round(min(100.0, max(0.0, score)), 2)
        # `score` on [0,1] is what the builder, the style tilt and every
        # existing ranking consumer read, so it exists for everything.
        # `fundamental_score` is the 0-100 company score and is None for
        # instruments that are not companies — a bond ETF has no fundamentals
        # and should not appear in a fundamental ranking at all.
        e["score"] = round(score / 100.0, 4)
        e["fundamental_score"] = score if operating else None
        e["is_operating"] = operating
        for k in ("momentum", "quality", "value", "growth", "health", "lowvol"):
            key = "f_lowvol" if k == "lowvol" else f"f_{k}"
            if k in parts:
                e[key] = round(parts[k], 4)
            else:
                e.pop(key, None)
        e["factors_used"] = sorted(avail)
        # Which factors were imputed as neutral because the data was missing.
        # Surfaced rather than hidden: a name carried by defaults is a weaker
        # signal than one carried by evidence, and a reader should be able to
        # tell the difference.
        if imputed:
            e["factors_imputed"] = imputed
        else:
            e.pop("factors_imputed", None)
        e["mcap_est"] = round(_market_cap(e)) if _market_cap(e) else None
        e["model_version"] = FACTOR_MODEL_VERSION

    _apply_hard_filters(entries)
    return rankings


# ── Hard eligibility filters (PART 5) ─────────────────────────────────────────
# Data quality and investability only. These are NOT stock picks: nothing is
# excluded for scoring badly on a factor, only for being unrankable or
# untradeable. A cheap, unloved, low-momentum company should reach the optimiser
# and lose there on its merits.
MIN_PRICE = 5.0
MIN_HISTORY_DAYS = 200


def _apply_hard_filters(entries: dict) -> None:
    """Attach a `gate` reason string to anything ineligible. A flag, not a
    deletion — the Top Stocks page and the cache shape stay intact, and the
    builder skips gated names during automatic selection only."""
    for t, e in entries.items():
        sector = e.get("sector", "Unknown")
        reasons = []

        px = _num(e.get("last_price"))
        if px is not None and px < MIN_PRICE:
            reasons.append(f"price ${px:.2f} below ${MIN_PRICE:.0f}")

        if _num(e.get("ann_vol")) is None:
            reasons.append("no usable price history")

        if _is_operating(sector):
            f = _fund(e)
            if not f:
                reasons.append("no fundamental data")
            else:
                # Genuine financial distress, judged on two signals agreeing.
                # One bad ratio is not a disqualification.
                d2e = _positive_only(f.get("d2e"))
                fs  = _num(f.get("f_score"))
                if d2e is not None and d2e > 3.0 and fs is not None and fs <= 3:
                    reasons.append(f"D/E {d2e:.1f} with F-Score {fs:.0f}/9")

        if reasons:
            e["gate"] = "; ".join(reasons)
        else:
            e.pop("gate", None)


# ── Eligible universe (PART 11) ───────────────────────────────────────────────
# "Top 30 per sector" was the original instinct and it does not work on a
# 331-name universe: sectors hold 28-30 names, so the cut keeps everything and
# screens nothing. Verified in the audit — observed ranks were #1/30, #2/30,
# #3/30 out of groups of 28-30.
#
# A pure percentage fails the other way: at 25% a 12-name sector keeps three,
# too few for the optimiser to have any choice.
#
# So: a percentage with a floor and a cap. It screens meaningfully today and
# keeps working as the universe grows, with no methodology rewrite.
#     30 names  -> 12 kept   (the cut actually bites)
#    300 names  -> 30 kept   (cap binds)
#      8 names  ->  5 kept   (floor binds; small sectors stay represented)
KEEP_PCT = 0.40
KEEP_MIN = 5
KEEP_MAX = 30


def eligible_universe(rankings: dict, include_sectors=None, exclude_sectors=None,
                      exclude_tickers=None, min_market_cap=0.0,
                      always_keep=None, keep_pct=KEEP_PCT,
                      keep_min=KEEP_MIN, keep_max=KEEP_MAX):
    """Apply hard filters and the per-sector cut. Returns (tickers, diagnostics).

    `always_keep` bypasses every filter — a ticker the user typed is their call,
    not the screen's.
    """
    always_keep = set(always_keep or [])
    exclude_tickers = {t.upper() for t in (exclude_tickers or [])}
    exclude_sectors = set(exclude_sectors or [])

    groups = defaultdict(list)
    keep_non_operating = []
    diag = {"gated": [], "too_small": [], "excluded": [], "sector_cut": {}}

    for t, e in rankings.items():
        if t == "_meta" or not isinstance(e, dict):
            continue
        if t in always_keep:
            continue
        if t.upper() in exclude_tickers:
            diag["excluded"].append(t); continue
        sector = e.get("sector", "Unknown")
        if sector in exclude_sectors:
            diag["excluded"].append(t); continue
        # Non-operating instruments bypass the SCORE-BASED cut, not the user's
        # sector choice. Exempting them from this filter too put six bond ETFs
        # into an eighteen-name "moderate" portfolio — each bond category is its
        # own sector label, so each claimed a guaranteed slot downstream and the
        # portfolio came back at beta 0.25. Whether to hold bonds at all is the
        # user's call, made in the preferences step.
        if include_sectors is not None and sector not in include_sectors:
            continue
        if e.get("gate"):
            diag["gated"].append(t); continue
        mc = e.get("mcap_est")
        # Only names WITH an estimate are filtered. A fund files no share count,
        # and absent data must never read as "small".
        if min_market_cap and mc is not None and mc < min_market_cap:
            diag["too_small"].append(t); continue
        if not e.get("is_operating", True):
            # Benchmarks, bond ETFs and commodity trusts bypass the per-sector
            # cut. They are diversifiers the builder pins deliberately, not
            # stock picks competing on a fundamental score they do not have.
            keep_non_operating.append(t)
            continue
        groups[sector].append((t, e.get("fundamental_score") or 0))

    keep = list(always_keep) + keep_non_operating
    for sector, members in groups.items():
        members.sort(key=lambda x: -x[1])
        n = len(members)
        k = max(keep_min, min(keep_max, round(keep_pct * n)))
        k = min(k, n)
        diag["sector_cut"][sector] = f"{k}/{n}"
        keep.extend(t for t, _s in members[:k])
    return list(dict.fromkeys(keep)), diag
