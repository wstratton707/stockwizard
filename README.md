# ◈ StockWizard — Streamlit Web App

Professional stock analysis tool powered by Polygon.io. Generates a full 10-sheet Excel report with Monte Carlo simulation, technical indicators, peer comparison, and more.

---

## Files

| File | Purpose |
|---|---|
| `app.py` | Main Streamlit app — UI, charts, download button |
| `data.py` | All data fetching via Polygon.io API |
| `analysis.py` | Monte Carlo, support/resistance, correlation, summary |
| `excel_builder.py` | All Excel sheet building logic |
| `requirements.txt` | Python dependencies |

---

## Deploy to Streamlit Cloud (free, 5 minutes)

1. **Push to GitHub**
   ```bash
   git init
   git add .
   git commit -m "Initial StockWizard app"
   git remote add origin https://github.com/YOUR_USERNAME/stockwizard.git
   git push -u origin main
   ```

2. **Go to share.streamlit.io**
   - Sign in with GitHub
   - Click "New app"
   - Select your repo and `app.py`
   - Click Deploy

3. **You get a free public URL** like:
   `https://your-username-stockwizard-app-xyz123.streamlit.app`

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
| Add a new data source | Add a function in `data.py` |
| Change MC defaults | Edit the sliders in `app.py` sidebar |
| Add new metrics | Add to the `metrics` list in `app.py` |

---

## API Key

Your Polygon.io API key is set at the top of `app.py`:
```python
POLYGON_API_KEY = "your_key_here"
```

Free tier limits: 5 API calls/minute. Upgrade at polygon.io for higher limits.

---

## Disclaimer

This tool is for informational purposes only and does not constitute financial advice.
Data provided by Polygon.io under their terms of service.
