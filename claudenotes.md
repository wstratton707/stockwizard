# Claude Notes — QuantWizard
Internal reference file for Claude. Tracks known issues, architectural decisions,
strategy, and onboarding context. Not for users.

Founders: Wyatt Stratton, Nicholas Carriello (both IU Bloomington)
- Wyatt LinkedIn: https://www.linkedin.com/in/wyattstratton7/
- Nicholas LinkedIn: https://www.linkedin.com/in/nicholas-carriello-471306254/

Last substantially revised: 2026-07-30.

---

## FOR ANY CLAUDE READING THIS — START HERE

QuantWizard is a Streamlit equity-research SaaS. Free tier + Pro ($9.99/mo,
currently DISABLED via `DEV_MODE_FREE = True`). Read this whole file first.

**The single most important thing to know (2026-07-30):**
The product's paid wedge is a **downloadable research report**, and we do not
hold a licence that permits redistributing market data to end users. The primary
price source is `yfinance`, which is unofficial scraping of Yahoo endpoints and
is prohibited for commercial use. Every affordable vendor tier is personal-use
only. See "THE DATA LICENSING WALL" below.

**FOUNDER DECISION 2026-08-01 — do not re-litigate this.** Wyatt has weighed the
risk and chosen to **launch on yfinance and switch to a licensed feed once
revenue supports it** (~$1,000/mo named as the trigger). The reasoning is sound:
Yahoo's enforcement has been technical rather than legal, the CFAA route is
closed for public data after hiQ, no C&D against yfinance is on record, and a
sub-$1k/mo product is not worth suing. A future Claude should support this plan,
not reopen it. What remains useful is keeping the switch **cheap** (see "The
switch cost" below) and watching the trigger.

**The second most important thing:**
The old version of this file said Polygon's free tier capped us at ~18 months of
history and told future Claudes not to assume deep data. **That is obsolete.**
`market_data.get_bars` now tries yfinance first (deep history, same-day closes)
with Polygon as fallback, and the app uses 5-year windows throughout
(`portfolio_builder._PRICE_HISTORY_YEARS = 5`). The old 2-year labels are gone.
The constraint moved from *availability* to *licensing*.

---

## WHAT THE APP DOES

Five pages, routed by `?page=` in `app.py` (not tabs — the old 4-tab description
was stale). Nav is a custom sticky bar.

**Home** — hero, sample report download (static `sample_report.json` +
prebuilt workbook in `static/`), report carousel, ticker tape, methodology cards.

**Analysis (free)** — any ticker (stock / ETF / crypto). Price chart with
MA/volume/S&R overlays, one switchable indicator (RSI / MACD / Bollinger),
Valuation Lens (price vs earnings-justified fair value from EDGAR EPS),
Analyst View (Finnhub consensus + earnings surprises), Fundamentals &
Valuation table, "What's Priced In" two-stage DCF with sensitivity grid,
peers, correlation, Monte Carlo or Custom Forecast (GARCH + ML ensemble),
multi-source news with an optional Claude-written grounded brief.
Exports: **Excel workbook, PowerPoint deck, Word memo**.

**News** — market-wide pulse + trending tickers + per-ticker research.

**Portfolio Builder (Pro-gated, currently open)** — 5-step wizard: prefs →
universe → optimise → backtest → Monte Carlo forecast. Factor-tilted CAPM
expected returns, Ledoit-Wolf covariance, SLSQP mean-variance, 5-year backtest
with quarterly rebalancing, efficient frontier, per-holding attribution.
Exports Excel + PowerPoint.

**Your Portfolios** — forward mark-to-market tracking of saved portfolios from
inception (NOT a backtest). Email-only beta sign-in.

Stress Test / Bond analysis live in `stress_test.py` and are reachable from the
Pro surface area.

---

## FILE MAP

| File | What it does |
|---|---|
| `app.py` | Main app — routing, all five pages, charts, export buttons |
| `market_data.py` | Multi-source router: Finnhub quotes → Polygon; yfinance bars → Polygon; yfinance financial-statement supplement |
| `data.py` | Analysis-page fetching + enrichment; SEC EDGAR fundamentals; news; ETF/crypto metadata |
| `analysis.py` | Pure analytics: fundamentals, DCF, WACC, scorecard, Monte Carlo, GARCH, ML drift, downside deviation |
| `valuation.py` | Valuation Lens — EDGAR EPS/dividends vs long monthly price, split-adjusted |
| `portfolio_builder.py` | Builder UI (5 steps) |
| `portfolio_analysis.py` | Optimiser, backtest engine, portfolio Monte Carlo, metrics |
| `portfolio_data.py` | Universe, price fetching, two-layer Supabase cache, factor selection |
| `precompute.py` | Nightly ranking job (GitHub Actions cron) |
| `tracker.py` | Forward lot-based mark-to-market for Your Portfolios |
| `your_portfolios.py` | Your Portfolios tab |
| `stress_test.py` | Crash scenarios (single source of truth: `CRASH_SCENARIOS`) + Portfolio Autopsy |
| `excel_builder.py` / `pptx_builder.py` / `docx_builder.py` | The three report formats |
| `news_research.py` | Multi-source news aggregation, dedupe, themes, AI brief |
| `chart_theme.py` / `chart_tokens.py` | One shared chart design system |
| `database.py` | Supabase REST wrapper: cache, saved + tracked portfolios |
| `constants.py` | `DEV_MODE_FREE`, live risk-free rate (FRED DGS3MO), ERP |
| `disclaimers.py` | All legal/methodology strings |
| `validate.py` / `validate_metrics.py` / `predeploy_check.py` | Validation suites + deploy gate |

---

## THE DATA LICENSING WALL — read before touching the data layer

Verified 2026-07-30 against vendor pricing pages.

| Source | Cost | Licence reality |
|---|---|---|
| yfinance (Yahoo) | $0 | **Unofficial scraping. Prohibited for commercial use.** Currently our PRIMARY bar source. |
| Massive (ex-Polygon) Basic | $0 | 2yr history, 5 calls/min, "individual use, non-pros only" |
| Massive Starter / Developer / Advanced | $29 / $79 / $199 | 5 / 10 / 20yr, unlimited calls — **all still marked individual use** |
| EODHD EOD | $19.99 | Explicitly "Personal use"; commercial needs their unlisted Startups/Enterprise plan |
| Finnhub | free → $49.99+ | Currently used free for quotes + analyst consensus |
| **SEC EDGAR** | **$0** | **US Government work — public domain. Unlimited commercial use and redistribution.** |

**The conclusion that matters:** a $60/month budget cannot buy a redistribution
licence. Paying $29 to Massive buys reliability and 5-year depth; it does *not*
buy the right to put their prices in a file a customer downloads.

### The switch cost — keep this number small

Audited 2026-08-01. Only **four** yfinance call sites exist in the product
(matches in `.claude/skills/` are a third-party skill pack, not ours):

| Site | Purpose | Notes |
|---|---|---|
| `market_data._yahoo_bars` | single-ticker bars | already behind the `get_bars()` router |
| `market_data.get_bars_batch` | batched download | one function |
| `market_data.get_financials_supplement` | capex / FCF / balance-sheet fields | probably **deletable** — EDGAR carries these; it exists only to patch Polygon's missing capex + cash |
| `valuation.py` (~line 102) | long monthly unadjusted price + split history | **the only bypass of the router** |

Switching providers is therefore ~2 functions, not a refactor. **To keep it that
way:** move `valuation.py` behind `market_data` so there is exactly one file to
change, and never add a direct `yf.*` call outside `market_data.py`. If a future
change needs Yahoo data somewhere new, add it to the router instead.

### Trigger to switch

Wyatt's stated trigger is ~$1,000/mo revenue (~100 subscribers at $9.99).
Massive Developer at $79/mo would be ~8% of that — affordable well before the
trigger, so this is a comfortable margin, not a squeeze.

**Refinement worth honouring: enforcement tracks visibility, not revenue.** A
front-page Hacker News post at $200/mo draws more attention than $1,500/mo of
quiet subscriptions. Treat the trigger as **revenue OR a visibility spike,
whichever comes first** — if a launch post, press mention or viral thread lands,
move regardless of MRR.

### What to do about it (design, not spend)

1. **Lean the paid product onto EDGAR.** Fundamentals, financial statements,
   EPS, dividends, the DCF, the Valuation Lens, the scorecard — all already
   EDGAR-sourced, all public domain, all freely redistributable. This is our
   strongest legal asset and it happens to be the genuinely differentiated part.
2. **Derived analytics are defensible; raw data is not.** A Sharpe ratio, a DCF
   fair value, a Monte Carlo percentile are our computed outputs. Vendor terms
   restrict redistributing *their data*, not statistics derived from it.
3. **Fix the one clearly-exposed artifact:** `excel_builder._build_price_sheet`
   dumps up to 1,300 rows of raw OHLCV into the downloaded workbook. That is
   redistribution of licensed data in the most literal form. Replace it with
   derived series and rendered charts. Costs nothing, removes the sharpest edge.
4. **Email Massive and Finnhub for a startup/commercial quote.** Costs $0. Many
   vendors have unpublished early-stage tiers. Do this before spending anything.

Do NOT propose "just keep using yfinance, nobody checks." The exposure isn't
a lawsuit, it's that the endpoint changes without notice and the product dies
at 3am — and that we'd be charging for redistributed data we don't own.

---

## COMPETITIVE PICTURE (researched 2026-07-30)

### Who succeeded

**stockanalysis.com** — the closest thing to a template. Founded 2019 by a solo
founder, grew to ~9M visits/month, ad-supported free tier plus Pro at **$79/yr**.
Won on **SEO at scale**: 100,000+ indexable ticker and fund pages, licensed data
underneath (S&P Global, Cboe, Benzinga, Finnhub). Lesson: in this niche the
winning channel is organic search against ticker-shaped queries, and the moat is
breadth of indexed pages, not depth of features.

**Simply Wall St** — $10.95/mo, our closest ASP comp, and notably their wedge is
also *visual reports*. Funded and large. Confirms retail will pay ~$10 for a
report-shaped artifact — and that we cannot outspend them, only out-specialise.

**Koyfin** — went the other way: restructured in 2026 to Free / Plus $39 /
Premium $79 / Advisor Core $209 / Advisor Pro $299, serving 30,000+ advisors.
They abandoned the cheap prosumer tier. Lesson: the $10/mo retail lane is
brutal, and the escape hatch is upmarket to advisors. We explicitly chose *not*
to go there (see memory: launch strategy, 2026-06-23) — that decision should be
revisited annually, not treated as permanent.

### Who failed, and why

Fintech post-mortems cluster on three causes, and only one is about product:
funding dependence in a pulled-back market (Clim8, TenureX), infrastructure that
broke under load at the exact moment users cared, and **no differentiation /
never solved monetisation**. Bootstrapped survivors are the ones with real unit
economics rather than growth metrics dressed as a business model.

For us the relevant failure mode is the third: a competent tool that never found
a reason for anyone to switch. Our answer has to be the report artifact plus
radical transparency about method — not feature count.

### The structural problem nobody has flagged yet

**Streamlit cannot execute the channel that works in this niche.** The app is
effectively one URL that renders client-side over a websocket. stockanalysis.com
wins with 100,000 crawlable pages; we have approximately one. SEO — the proven,
$0-marginal-cost channel for retail stock research — is *structurally closed* to
our current stack.

This is not an argument to rewrite the app. It's an argument to put a **separate
static marketing/content site** in front of it (free hosting), with the Streamlit
app on an `app.` subdomain. That unlocks the channel at zero infrastructure cost.

---

## THE $60/MONTH BUDGET — recommended allocation

**Headline: 0% to ads, ~10% to hosting, ~85% banked toward the licensing gate.**

### Why 0% to advertising (the arithmetic)

Finance & insurance Google Ads CPC averages **$3.46** in 2026; commercial-intent
terms like "stock research tool" sit at or above that. $60/month buys ~17 clicks.

| Assumption | Optimistic | Absurdly optimistic |
|---|---|---|
| Clicks/mo | 17 | 17 |
| Landing → signup | 4% | 10% |
| Free → paid | 4% | 20% |
| **Customers/mo** | **0.03** | **0.34** |
| Implied CAC | ~$2,200 | ~$180 |
| Payback at $9.99/mo | 18 years | 18 months |

Median SaaS CAC payback is 6.8 months. Retail investing tools churn hard, so an
18-month payback in the *best* case is a losing trade. **Do not buy ads at this
budget.** Ads become worth revisiting only when a landing page converts a warm
organic cohort at a measured rate — i.e. after we know the funnel numbers, not
before.

### Recommended monthly split

| Line | Amount | Notes |
|---|---|---|
| App hosting | **$0 today** | Already on Streamlit Community Cloud (free). Its limits are real — ~1GB RAM, sleeps when idle, ephemeral disk — and this app renders matplotlib into workbooks, so an OOM under concurrent report builds is the likely first failure. **Don't pre-pay to avoid it:** wait until a beta user actually hits a crash or a cold start, then move to Railway Hobby (~$5) or Render (~$7). Budget for it, don't spend it yet. |
| Marketing/content site | **$0** | Cloudflare Pages or Vercel free tier. Static. |
| Domain | **~$2** | ~$12–35/yr amortised. Needed regardless; needed *first* for the content site. |
| Supabase | **$0** | Free tier is sufficient at this scale. Note it pauses after ~7 days idle; Pro is $25/mo — buy only when a real user hits it. |
| Transactional email | **$0** | Resend/Supabase free tiers cover magic-link auth and waitlist. |
| Market data | **$0 for now** | Paying $29 for an "individual use" tier does not solve the blocker. Get a commercial quote first. |
| **Reserve** | **~$53** | Bank it. |

### What the reserve is for, in priority order

1. **A commercial data contract** once a vendor quotes one. This is the purchase
   that unblocks charging at all.
2. **A one-off ToS + Privacy Policy review.** Legally required before taking
   payments. A template plus a few hundred dollars of review is the realistic
   shape; banking $53/mo funds it in two months.
3. Supabase Pro, if and when free-tier limits actually bite.

At zero users, the correct spend is near-zero. Accumulating toward one unblocking
purchase beats dribbling $60/mo into channels that cannot convert.

---

## PRE-LAUNCH GATE — what must be true before promoting or charging

Ordered. Do not skip to promotion.

### Blocking for *any* public promotion (even free)
1. ~~**Waitlist persistence.**~~ DONE 2026-08-01 — now Supabase-backed. Was a
   local CSV on Streamlit Cloud's ephemeral disk, destroying every signup.
2. **Real auth.** Your Portfolios keys on a typed, unverified email — anyone can
   type someone else's address and read their portfolios. Supabase magic-link.
   Also fix `?email=` in the URL granting Pro via `check_subscription`.
3. **ToS + Privacy Policy pages.** Required before collecting emails at scale,
   mandatory before payments.
4. **Two Supabase tables must actually be created.** Both DDLs are in
   `database.py` and `README.md`. Until they exist, Your Portfolios shows an
   empty state to every visitor, and waitlist signups only reach the app log.
   This is a two-minute paste into the Supabase SQL editor and it is currently
   the single highest-value unblocked action:
   - `tracked_portfolios` — Your Portfolios persistence
   - `waitlist` — signup capture (`email` **must** be `unique`; the insert
     upserts on it)

### Blocking for charging money
5. **Resolve the data licence** (see the wall above). Either a commercial quote,
   or a redesign that ships only EDGAR + derived analytics in paid artifacts.
6. **Remove raw OHLCV from the exported workbook.**
7. **Re-enable Stripe**: `DEV_MODE_FREE = False`, `SHOW_PRICING = True`, and
   confirm `STRIPE_PRICE_ID` (+ `STRIPE_PRICE_ID_ANNUAL` if the annual toggle is
   shown — it is now gated on that variable existing).
8. **Run `predeploy_check.py`** and have it pass.

### Not blocking, but do it before the beta cohort sees it
9. Gate report exports behind Pro (currently free — the wedge is being given
   away).
10. `/ui-qa` pass. Correctness work landed 2026-07-29/30 but the rendered UI has
    not been driven since.

---

## THE ONE-DAY PLAN — 2026-08-02

Built from a live Playwright drive of the running app (2026-08-01), not from
reading code. Every finding below was observed or measured, and the evidence is
stated so it can be re-checked.

### What the drive found

**1. Advertised Pro features that can't be reached.** Corrected 2026-08-01 after
Wyatt pointed out the overstatement — stress testing *is* embedded in the
Portfolio Builder (`portfolio_builder.py` ~1138, the crash cards on the backtest
step, which read the shared `stress_test.CRASH_SCENARIOS`). What is genuinely
unrouted:
- **Portfolio Autopsy** — CSV upload, P&L attribution, correlation culprits.
  Advertised in `payments.py` and the Analysis pricing card. No route exists.
- **The standalone Stress Test page** (`render_stress_test`, defined and never
  called): arbitrary tickers/weights rather than only your built portfolio, and
  5 scenarios rather than the Builder's 3.
- **Bond Analysis** — no UI at all; `fetch_bond_data` is imported in `app.py`
  and never used.

`_PAGES` is only `("home","analysis","news","builder","portfolios")`. Routing
`render_stress_test` is still the cheapest win available — it exposes Autopsy and
the fuller scenario set from code that already works.

**2. The pricing model is inverted.** After removing the three that don't exist
and Save/Load (recommended for deletion), **Pro contains exactly one feature: the
Portfolio Builder.** Meanwhile the Free tier advertises "Excel + PowerPoint
export" — the reports, which the launch strategy identifies as *the wedge and the
most pay-worthy asset*. We give away the thing worth paying for and charge for
the thing that isn't ready.

**3. The one paid feature underperforms doing nothing.** A default Balanced
(risk 5) build produced: KO 16.7%, QQQ 16.5%, JNJ 14.3%, SPY 12.5%, GOOGL 7.9%,
GLD 4.6%, SPG 2.7%. Measured against the live risk-free rate (3.82%):
- portfolio beta **0.71** → CAPM expected return **7.36%**
- SPY expected return **8.82%**
- **33.6% of the portfolio is SPY + QQQ + GLD**

So the optimised, paid portfolio has a *lower expected return than SPY*, and a
third of it **is** index funds the user can buy for free — while the app's own
disclaimer says "a low-cost index fund is the benchmark to beat." Root cause:
SPY/QQQ/GLD are pinned into the candidate set as *benchmarks*
(`build_candidate_universe`) and then compete as *holdings*, and they win on
Sharpe. This is the concrete, user-visible form of the "optimiser discards the
factor model" problem in [[project-portfolio-rebuild]].

**4. The universe rot is now user-facing.** The build printed *"Could not load
data for: CTRA, K — excluded from analysis"* — dead tickers from the hardcoded
`SECTOR_UNIVERSE`, surfaced to a user who never chose them.

**5. The single-stock Monte Carlo is indefensible and it's on the free page
everyone sees first.** `run_monte_carlo` still uses raw historical drift
(`mu = returns.mean()`). For AAPL over 1Y it printed: median **$472.30** from
$308.91 (+53% in a year) and **95.7% probability of gain**. The portfolio Monte
Carlo was already fixed to CAPM for exactly this reason; the single-stock one
never was. Same product, two methodologies, and the visible one is the wrong one.

**6. Two different volatility figures on one screen.** The metric card shows
full-period `ann_std` (25.9%); "The Bottom Line" underneath shows
`Volatility_20d` (36.7%). Both are labelled annualised volatility.

**7. Activation friction.** The Analysis panel presents 15+ controls — ticker,
date range, 2 benchmarks, peers, 6 module checkboxes, method, 2 sliders — before
"Run Analysis". The hero says "Enter a ticker — free, no account needed"; the
form says otherwise. Activation (% of visitors who generate a report) is the
metric the beta exists to measure, and this is the gate.

**8. Two landing pages.** Home, and Analysis-with-no-ticker, carry different
hero copy, different feature lists and different pricing blocks. They will drift.

### The plan, in order

Ordered by "what makes this worth paying for", not by effort.

**MORNING — make Pro real (≈3h)**

1. **Route Stress Test. (~30 min, highest ROI of the day.)** Add `"stress"` to
   `_PAGES`, a nav button, and `elif _page == "stress": render_stress_test(...)`.
   650 lines of working, disclaimed, already-styled feature go from invisible to
   shipped. This alone takes Pro from one feature to three (Stress Test + the
   Portfolio Autopsy that lives in the same module).
2. **Gate the reports behind Pro. (~45 min.)** `_stock_exports()` in `app.py`
   checks `st.session_state["is_pro"]`; free users get the first page as a
   preview image plus an upgrade prompt. This is the actual wedge — it should be
   the reason to pay. Update both pricing cards to match reality.
3. **Delete what we don't sell. (~30 min.)** Remove Bond Analysis from the
   pricing copy (no UI exists and none is planned today), drop the unused
   `fetch_bond_data` import, and delete Save/Load per the product decision above.
4. **Reconcile the two pricing cards and the two landing pages. (~1h.)** One
   source of truth for the feature list; Home is the landing page, Analysis with
   no ticker becomes a short prompt, not a second homepage.

**MIDDAY — make the paid feature defensible (≈3h)**

5. **Stop benchmarks being holdings. (~1h.)** SPY/QQQ/GLD should be the *line on
   the chart*, not 34% of the allocation. Either exclude `sector in ("Market",
   "Commodities")` from the optimiser's asset set and plot them as comparison
   only, or hard-cap them at ~5% combined. Then re-measure: the portfolio must
   beat SPY's expected return at equal or lower beta, or the feature has no
   argument. Use `scripts/evaluate_portfolio_model.py` — do not eyeball it.
6. **Fix the single-stock Monte Carlo drift. (~45 min.)** Move `run_monte_carlo`
   onto the same CAPM basis as the portfolio version, or at minimum blend and cap
   it. A 95.7% probability of gain destroys more credibility than the whole
   Valuation Lens builds.
7. **Kill the universe-rot warning. (~30 min.)** Drop the 10 known-dead tickers
   from `SECTOR_UNIVERSE`, and downgrade the message from a user-facing warning
   to a log line — a user should never see us fail to load something they didn't
   ask for.
8. **Resolve the two volatility figures. (~15 min.)** One definition, one number,
   stated once.

**AFTERNOON — trust and conversion (≈2h)**

9. **Auth. (~1.5h.)** Supabase magic-link. Today anyone can type any email and
   read that person's portfolios; `?email=` in the URL can also grant Pro via
   `check_subscription`. This blocks payments and it is a privacy hole now.
10. **Cut activation friction. (~30 min.)** Ticker + Run above the fold;
    everything else into a collapsed "Options". Target: ticker → report in two
    clicks.

**IF TIME REMAINS**
- Instrument activation/retention before inviting anyone (you cannot improve what
  you don't measure, and the beta exists to produce those numbers).
- Remove raw OHLCV from the exported workbook (licensing exposure, and a better
  sheet without it).

### The through-line

The product is closer than it looks: the Analysis page, the Valuation Lens, the
DCF, the attribution table and the three report formats are genuinely good and
genuinely differentiated. **What's missing is a coherent commercial shape.** The
best asset is free, the paid tier is three-fifths fictional and one-fifth weaker
than SPY, and a finished feature sits unrouted. A day spent on the list above
does not add a single new capability — it makes the ones already built into
something a person can be asked to pay for.

## ROADMAP

### Phase 0 — unblock (weeks 1–2, $0)
Waitlist → Supabase. Magic-link auth. ToS/PP drafted. Run the tracked DDL.
Email Massive + Finnhub for commercial quotes. Buy the domain.

### Phase 1 — free beta (weeks 3–6, $5–7/mo)
10–30 users from IU Bloomington (Kelley) investment clubs and finance courses —
warm, on-target, and reachable without spend. Payments stay off. Goal is not
revenue; it is three numbers we do not currently have: **activation** (% who
generate a report), **retention** (% back in week 2), and **the objection** (why
the rest didn't). Instrument before inviting.

### Phase 2 — the SEO surface (weeks 4–12, $0 marginal)
Static site on Cloudflare Pages at the apex domain; Streamlit at `app.`.
Content that is *generatable from EDGAR*, which we can legally publish at scale:
per-company valuation write-ups, DCF explainers, methodology pages. This is the
only channel with a proven track record in this exact niche, and our EDGAR
dependency is what makes it legally safe to publish at volume.

### Phase 3 — charge (after the gate closes)
Pro at $9.99/mo. Reports gated. Annual only if an annual Stripe price exists.

### Phase 4 — reconsider the lane
If retail conversion is under ~2% after a real cohort, revisit the advisor
market seriously (Koyfin's $209–299 tiers exist because that lane pays). The
2026-06-23 decision to avoid advisors was made without conversion data; revisit
it with data.

---

## KNOWN ISSUES

### [RESOLVED 2026-08-01 — but REDEPLOY/RECONFIG NEEDED] Supabase silently dead in production
The deployed `SUPABASE_URL` was set to the **REST endpoint**
(`https://<ref>.supabase.co/rest/v1`) rather than the **project URL**. Every call
site builds `{SUPABASE_URL}/rest/v1/<table>`, so requests went to
`/rest/v1/rest/v1/<table>` and PostgREST returned PGRST125 "Invalid path".

**Blast radius was everything Supabase-backed, and it was invisible.** Every
function in `database.py` catches failure and returns None/False, so: the price
cache never cached (every portfolio build cold-fetched), `get_sharpe_rankings`
returned `{}` so the Builder silently fell through to its slow live-candidate
path, saved portfolios never saved, and Your Portfolios reported "the
tracked_portfolios table isn't in this project" about a table that was present
with rows in it. Graceful degradation hid a total outage.

Fixes: `_normalise_base()` strips a trailing `/rest/v1` or `/rest`, so either
env-var form now works; `_table_status()` distinguishes `no_creds` / `bad_key` /
`bad_url` / `no_table` / `unreachable` instead of collapsing every non-200 into
the confidently-wrong "no_table"; `your_portfolios.py` renders a specific message
per mode. Both status helpers share one implementation so they can't drift.

**Lesson for future work:** resilient-by-default error handling in `database.py`
means misconfiguration presents as "the app works, just slowly." When something
Supabase-backed seems merely sluggish, check `_table_status()` first.

### [ACCEPTED RISK — revisit at the trigger] Data licensing
Founder decision 2026-08-01: launch on yfinance, switch to a licensed feed at
~$1,000/mo revenue or on any visibility spike, whichever comes first. See the
wall above. Keep the switch cost at ~2 functions; do not add `yf.*` calls
outside `market_data.py`. This is a deliberate, dated call — not an oversight.

### [RESOLVED 2026-08-01] Waitlist emails lost on every redeploy
Was a local `waitlist.csv` on Streamlit Community Cloud's ephemeral disk —
rebuilt on every push, recycled whenever the app slept. Now
`database.save_waitlist_email()` → Supabase, upserting on a unique `email` so a
repeat signup is a no-op. Validation tightened (the old test was `"@" in email`,
which accepted `"@"` itself). If Supabase is unreachable the signup is written to
stderr as `WAITLIST-FALLBACK` (greppable in Manage app → logs) plus the local CSV,
so nothing is dropped; the visitor sees success either way, because the failure
isn't theirs. `waitlist.csv` is now gitignored — it was not, so a `git add .`
would have committed user emails to a public repo.
**Requires the `waitlist` DDL to be run — see below.**

### [RESOLVED 2026-08-01 — needs Auth0 config] Email-only auth was a privacy hole
Was: a free-text email box, mirrored to `?email=` to survive refresh. Typing
someone's address returned their portfolios, and `check_subscription(?email=)`
granted Pro to anyone who knew a subscriber's address.

Now `auth.py` on **Streamlit's native OIDC** (`st.login`/`st.user`/`st.logout`)
with **Auth0** as provider — chosen by Wyatt 2026-08-01. Streamlit owns the
session cookie, so refresh persistence is native; we never handle a password.
Sign-in control sits top-right in the nav; Your Portfolios and the Builder's
save/load are gated; the `?email=` Pro path is deleted.

Deliberate details:
- `auth_configured()` guards every call — with no `[auth]` secrets,
  `st.user.is_logged_in` raises `AttributeError` rather than returning False, so
  an unguarded access would hard-crash any instance whose secrets lag.
- `_token_expired()` checks the `exp` claim, because Streamlit's docs state it
  does **not** verify expiry implicitly.
- Data stays keyed by email, so anyone from the old beta gate keeps their
  portfolios by signing in with the same address. No migration.

**Still to do (Wyatt, ~15 min):** create the Auth0 tenant and paste the block
from `.streamlit/secrets.toml.example` into each deployment. Until then the
Sign in button renders disabled — by design, not broken.

Still open: `delete_portfolio` / `delete_tracked_portfolio` take a UUID with no
ownership check. Not exploitable through the UI (ids only ever come from your
own list) but it should verify `user_email` before deleting.

### [OPEN] ToS + Privacy Policy missing
Required before payments.

### [OPEN] SEO is structurally closed on Streamlit
Needs the separate static site. See the competitive section.

### [OPEN] Report exports are free
The wedge is ungated.

### [OPEN — product decision] Two overlapping "save" concepts
The Builder's last step offers **Save Portfolio** (→ `saved_portfolios`: weights +
prefs + metrics, reloadable in the Builder) and **Track this forward** (→
`tracked_portfolios`: dated share lots, marked to market in Your Portfolios).
They sit side by side, share the same email/name inputs, and Wyatt — who built
them — asked what the difference was (2026-08-01). That is the signal.

Recommendation on the table: **collapse to one "Save & track" action** and drop
`saved_portfolios` / `save_portfolio` / `load_portfolios` / `delete_portfolio`
and the Load UI (~80 lines, one table). Rationale: the Builder derives weights
from *preferences* against current prices, so a stored weight vector is a stale
snapshot of a computation that gets redone on load anyway — which is why the
Load fix restores prefs and rebuilds rather than restoring weights. Track's lots
already encode the allocation, and Your Portfolios is a better home than a
dropdown below the download buttons where nobody scrolls. If the "designed it
but haven't bought it" case matters later, add a `paper` flag to a tracked
portfolio rather than a second table.

Not actioned — Wyatt's call.

### [OPEN] Static ETF metadata is stale
`data.ETF_METADATA` / `ETF_TOP_HOLDINGS` are hardcoded and rotting — XLE still
lists PXD (acquired May 2024), SQQQ shows 0 holdings, MATIC was renamed POL.
Shown to users as current.

### [OPEN] Quality factor is ranked universe-wide, not sector-relative
Documented honestly in the UI now, but ranking a utility's ROE against a
semiconductor's still measures the sector. Interacts with the "Unknown" bucket
that most of the dynamic universe falls into.

### [OPEN] `DEV_MODE_FREE = True`
Everything unlocked, Stripe bypassed. Intentional; flip at Phase 3.

### [RESOLVED 2026-07-29/30] Correctness pass
14 files, ~40 verified numeric checks. Highlights: `_peer_colors` NameError
crashing the peer section; backtest rolling metrics/stress windows using the
contribution-inflated account value instead of NAV (monthly deposits read as
market moves); Sortino dividing by std-of-negative-days instead of downside
deviation; WACC weighting net debt because `compute_fundamentals` never emitted
the `cash` key `estimate_wacc` read; "Max Drawdown" in the deck being a 60-day
rolling figure (understated by 37pts on a test series); Monte Carlo cache keyed
on last date only, so the date-range slider was a no-op; rankings TTL 26h
defeating a 5-day weekend fallback; rebalancing section mathematically unable to
produce a recommendation; tracker sell path subtracting market value from
contributed capital. Plus stale methodology copy across disclaimers, the
pricing card, and the builder captions.

### [RESOLVED, historical]
Thundering-herd rate limiting in precompute (now batched via `get_bars_batch`,
~50x faster); `RISK_FREE_RATE` NameError; GOOGL/META duplicated across sectors;
in-sample selection bias in the final portfolio trim.

---

## ARCHITECTURAL DECISIONS (the "why")

**Why EDGAR is the primary fundamentals source.** Free, authoritative, 10+ years,
no rate cap — and public domain, which is now also the licensing answer.
`fetch_sec_financials` first, Polygon's 4-period endpoint as fallback.

**Why a yfinance supplement exists.** Polygon's cash-flow endpoint has no capex
line and its balance sheet has no cash, so FCF and Altman-Z came out N/A and net
debt silently collapsed to gross debt. `get_financials_supplement` fills those
gaps. Everything degrades to None rather than breaking.

**Why the backtest carries a NAV column.** Contributions are not performance. All
return/risk/drawdown metrics compound the contribution-free NAV index; the
account value is display-only. `SP500_NAV` exists so the benchmark is compared on
the same basis.

**Why expected returns are CAPM + a capped factor tilt.** Pure CAPM makes E(R) a
monotone function of beta, so "maximise return" degenerates to "maximise beta"
and the whole selection model is discarded at the moment it matters. The tilt is
additive and capped at ±2%/yr — measured at the knee of the return/turnover
curve. Do not replace μ with a rank-derived return; it widens dispersion into an
unregularised SLSQP and produces *more* extreme weights.

**Why Ledoit-Wolf shrinkage is kept despite not helping.** Measured twice: it
does NOT reduce turnover at ~18 assets (30.5% sample vs 31.7% shrunk). Kept
because it helps as N approaches T. **Do not repeat the stability claim.**

**Why sector caps are SLSQP constraints with feasibility checks.** Uncapped MVO
piles into one sector. But an infeasible constraint makes SLSQP fail, and the
failure path silently returned equal weights — a broken portfolio that looks
deliberate. Both directions are now checked before a constraint is added.

**Why buy/sell language is only ever sourced.** Analyst consensus is shown as
"analysts say, not us" with Finnhub cited, alongside a separate neutral technical
posture. Never an un-sourced QuantWizard call — it would contradict our own
disclaimer.

**Why one chart theme.** `chart_theme.py` / `chart_tokens.py`. Every figure routes
through `ct.style()`. See memory: design taste — rounded card grids, emoji,
default font pairings (DM Sans/Inter) and grey backgrounds are all rejected.

---

## VALIDATION

```bash
python predeploy_check.py      # full data gate (network + keys), exits non-zero
python validate_metrics.py     # data correctness
python validate.py             # backtest vs known benchmarks
```
Plus two skills: `release-check` (static, every push) and `ui-qa` (Playwright
visual pass, after UI/chart changes).

---

## INFRASTRUCTURE

- **Deploy:** **Streamlit Community Cloud**, auto-deploys from `main`. Verified
  2026-08-01 from `.streamlit/config.toml` (`enableStaticServing`, which the Home
  report carousel depends on) and the `*.streamlit.app` BASE_URL fallback in
  `app.py`. There is no Procfile / railway.json / render.yaml / Dockerfile.
  *(An earlier version of this file said Railway. It was wrong and got repeated
  into code comments and user-facing strings before anyone caught it — check the
  repo, not this line, if it matters.)*
- **Secrets:** Manage app → Settings → Secrets, in TOML. Streamlit also exposes
  them as environment variables, which is why `os.getenv` works throughout.
- **Filesystem is ephemeral** — rebuilt on every push, recycled when the app
  sleeps. Nothing may be persisted to disk; that is what killed the old
  `waitlist.csv`. Logs are under Manage app → logs.
- **DB:** Supabase REST (no SDK) — `api_cache`, `saved_portfolios`,
  `tracked_portfolios` (DDL in `database.py`; **not yet run**)
- **Cron:** GitHub Actions, weekday mornings → `precompute.py`
- **Env:** `POLYGON_API_KEY`, `FINNHUB_API_KEY`, `FMP_API_KEY`, `SUPABASE_URL`,
  `SUPABASE_KEY`, `ANTHROPIC_API_KEY` (news brief), `STRIPE_SECRET_KEY`,
  `STRIPE_PRICE_ID`, optional `STRIPE_PRICE_ID_ANNUAL`
- **Brand:** rename to QuantWizard is complete in code. Remaining: the GitHub
  repo is still `stockwizard`, and 4 asset URLs in `app.py` point at it. Rename
  repo + update URLs + redeploy together, never separately.
