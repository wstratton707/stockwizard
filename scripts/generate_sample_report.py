"""Generate the pre-run sample Excel report served from the Home page.

Why this exists as a script rather than a one-off file someone dropped in:
a sample report is a snapshot of real market data, so it goes stale. Keeping
the generator in the repo means it can be re-run on demand and the dated
label in the UI stays honest.

    .venv/Scripts/python.exe scripts/generate_sample_report.py            # NKE
    .venv/Scripts/python.exe scripts/generate_sample_report.py --ticker PEP

Writes static/QuantWizard_Sample_<TICKER>.xlsx plus a small JSON sidecar
holding the generation date, which app.py reads so the label can't drift
away from the file.

NKE is the default because the Valuation Lens has something to say about it
— it trades meaningfully below its earnings-justified fair value — which
shows the tool doing work a price chart can't. A mega-cap at an all-time
high produces a report where every risk number is placid.
"""
import argparse
import json
import os
import sys
import warnings
from datetime import date
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


from data import (fetch_stock_data, fetch_company_details, fetch_financials,
                  fetch_sec_financials, fetch_peer_comparison)
from analysis import compute_fundamentals, dcf_valuation, run_monte_carlo, market_beta
from market_data import get_financials_supplement
from excel_report import build_report

BENCHMARKS = ["SPY", "QQQ"]
PEERS = {"NKE": ["ADDYY", "UAA", "LULU"], "PEP": ["KO", "MDLZ", "GIS"]}


def log(msg):
    # The data layer logs arrows and other non-cp1252 characters, which raise
    # UnicodeEncodeError on a default Windows console. Degrade rather than die.
    text = f"  {msg}"
    enc = sys.stdout.encoding or "utf-8"
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode(enc, "replace").decode(enc), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="NKE")
    ap.add_argument("--period", default="5y")
    args = ap.parse_args()
    tk = args.ticker.upper()

    key = os.environ.get("POLYGON_API_KEY", "").strip()
    if not key:
        sys.exit("POLYGON_API_KEY is not set — cannot build the sample report.")

    print(f"Building sample report for {tk} ({args.period})")

    df = fetch_stock_data(tk, period=args.period, benchmark_tickers=BENCHMARKS,
                          api_key=key, log=log)
    if df is None or df.empty:
        sys.exit(f"No price data for {tk}.")
    log(f"price history: {len(df)} rows")

    details = fetch_company_details(tk, key, log=log) or {}
    sector = details.get("Sector") or details.get("sector")

    # Same source priority as the app (app.py: cached_fetch_sec_financials or
    # cached_fetch_financials). Polygon-only was giving the downloadable sample
    # a different balance sheet than the live site: Polygon carries no cash
    # field, so net debt came out as gross debt — $7.96B against a true $0.38B
    # for NKE. The sample has to be the same report the site produces.
    fin = fetch_sec_financials(tk, log=log) or fetch_financials(tk, key, log=log)
    supplement   = get_financials_supplement(tk)
    price = float(df["Close"].iloc[-1])
    fundamentals = compute_fundamentals(fin, market_cap=details.get("Market Cap"),
                                        price=price, supplement=supplement)
    # Beta must be passed here for the same reason the app passes it: without it
    # dcf_valuation falls back to a flat default rate, and the sample workbook
    # would then quote a different WACC — and a different implied growth — than
    # the live site does for the same ticker.
    beta = None
    for _bt in ("SPY", "QQQ"):
        if f"{_bt}_Return" in df.columns:
            beta = market_beta(df["Daily_Return"], df[f"{_bt}_Return"])
            if beta is not None:
                break
    print(f"  market beta: {beta if beta is not None else 'unavailable — DCF will use its default rate'}")
    try:
        dcf = dcf_valuation(fundamentals, price, beta=beta,
                            sector=details.get("Sector"))
    except Exception:
        dcf = None

    mc_sim_df, mc_summary = None, None
    try:
        mc_sim_df, mc_summary = run_monte_carlo(df, n_simulations=1000,
                                                forecast_days=252, log=log)
    except Exception as e:
        log(f"monte carlo skipped: {e}")

    try:
        # Same pipeline the Analysis page uses — multi-source, language and
        # solicitation filtered, capped per publisher. data.fetch_news is
        # Polygon-only and would put two publishers in the report.
        from news_research import report_news_rows
        news = report_news_rows(tk, key, company_name=details.get("Name"))
    except Exception:
        news = None
    # Peers exactly as the app picks and measures them.
    from portfolio_data import suggest_peers
    from peer_groups import peer_group_for
    from analysis import peer_metrics
    import market_data
    peer_list = PEERS.get(tk) or suggest_peers(tk, sector=sector or "",
                                               market_cap=fundamentals.get("market_cap") or 0,
                                               api_key=key)
    try:
        peers = fetch_peer_comparison(tk, peer_list, key, log=log)
    except Exception:
        peers = None
    vendor = {r["Ticker"]: r["Market Cap ($B)"] * 1e9
              for r in (peers.to_dict("records") if peers is not None else [])
              if isinstance(r.get("Market Cap ($B)"), (int, float))}
    peer_fund = [peer_metrics(tk, fundamentals)]
    for p in peer_list[:4]:
        try:
            from datetime import timedelta
            end = date.today()
            bars = market_data.get_bars(p, str(end - timedelta(days=30)), str(end))
            pf = compute_fundamentals(fetch_sec_financials(p, log=log), market_cap=vendor.get(p),
                                      price=float(bars["Close"].iloc[-1]))
            peer_fund.append(peer_metrics(p, pf))
        except Exception as e:
            log(f"peer {p} skipped: {e}")
    peer_fund = [r for r in peer_fund if r]

    from data import fetch_sec_segments, fetch_sec_filings
    try:
        from valuation import get_valuation_data
        vdata = get_valuation_data(tk)
    except Exception:
        vdata = None
    buf = build_report(
        tk, df, financials=fin, fundamentals=fundamentals, dcf=dcf,
        company_details=details, mc_summary=mc_summary, mc_sim_df=mc_sim_df,
        news_rows=news, peer_fund=peer_fund,
        peer_group=(peer_group_for(tk) or ("same sector, nearest in size",))[0],
        peer_df=peers, segments=fetch_sec_segments(tk, log=log), valuation_data=vdata,
        filings=fetch_sec_filings(tk, log=log), period_label=args.period.upper(),
        price_source=market_data.price_source(tk))

    out_dir = ROOT / "static"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"QuantWizard_Sample_{tk}.xlsx"
    buf.seek(0)
    out.write_bytes(buf.read())

    meta = out_dir / "sample_report.json"
    meta.write_text(json.dumps({
        "ticker": tk,
        "file": out.name,
        "generated": date.today().isoformat(),
        "period": args.period.upper(),
        "bytes": out.stat().st_size,
    }, indent=2), encoding="utf-8")

    print(f"\nwrote {out.relative_to(ROOT)}  ({out.stat().st_size/1_048_576:.2f} MB)")
    print(f"wrote {meta.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
