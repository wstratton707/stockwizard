# ◈ QuantWizard — Streamlit Web App

Professional stock & portfolio analysis tool powered by a multi-source data layer (Polygon, Yahoo Finance, Finnhub, SEC EDGAR). Generates Excel and PowerPoint research reports with Monte Carlo simulation, technical indicators, peer comparison, and portfolio optimization.

---

## Files

| File | Purpose |
|---|---|
| `app.py` | Main Streamlit app — UI, charts, download button |
| `market_data.py` | Multi-source data router (Finnhub quotes, Yahoo bars, Polygon fallback) |
| `data.py` | Stock data fetching & enrichment (routes through `market_data.py`) |
| `analysis.py` | Monte Carlo, support/resistance, correlation, summary |
| `excel_builder.py` | All Excel sheet building logic |
| `requirements.txt` | Python dependencies |

---

## Deploy to Streamlit Cloud (free, 5 minutes)

1. **Push to GitHub**
   ```bash
   git init
   git add .
   git commit -m "Initial QuantWizard app"
   git remote add origin https://github.com/YOUR_USERNAME/quantwizard.git
   git push -u origin main
   ```

2. **Go to share.streamlit.io**
   - Sign in with GitHub
   - Click "New app"
   - Select your repo and `app.py`
   - Click Deploy

3. **You get a free public URL** like:
   `https://your-username-quantwizard-app-xyz123.streamlit.app`

---

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open http://localhost:8501 in your browser.

---

## Edit the app

| What to change | Where |
|---|---|
| Colours, fonts, layout | Edit the CSS block at top of `app.py` |
| Add a new chart | Add a Plotly chart in `app.py` after the existing charts |
| Change Excel formatting | Edit the relevant `_build_*` function in `excel_builder.py` |
| Add a new data source | Add/route it in `market_data.py` |
| Change MC defaults | Edit the sliders in `app.py` sidebar |
| Add new metrics | Add to the `metrics` list in `app.py` |

---

## API Keys

Keys are read from environment variables (or Streamlit secrets in deployment) — not hardcoded:
```
POLYGON_API_KEY=...               # bulk/grouped bars + fallback
FINNHUB_API_KEY=...               # real-time quotes (free tier)
SUPABASE_URL / SUPABASE_KEY=...   # persistent cache
```
Yahoo Finance (via `yfinance`) needs no key and serves the primary daily history; SEC EDGAR (fundamentals) needs no key either.

---

## Validation & pre-deploy

Two layers of checks:

- **Fast gate (every push):** the `release-check` skill — syntax-checks changed
  files and imports the core modules. No network needed.
- **Data gate (before deploy):** `python predeploy_check.py` — runs the
  data-correctness suite (`validate_metrics.py`) plus the backtest
  self-consistency guard (`validate.py`). Needs network + API keys and exits
  non-zero on any failure, so it can gate a deploy.

```bash
python predeploy_check.py      # full data gate
python validate_metrics.py     # data-correctness only
python validate.py             # backtest accuracy vs known benchmarks
```

The data gate catches the classes of bug that crash trust in a money product:
wrong/double-scaled numbers, a report that ignores user inputs, a silently-wrong
data source, or a stale live quote.

---

## Disclaimer

This tool is for informational purposes only and does not constitute financial advice.
Data provided by Polygon, Yahoo Finance, Finnhub, and SEC EDGAR under their respective terms of service.
