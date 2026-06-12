---
name: ui-qa
description: Visual QA pass for the QuantWizard Streamlit app using Playwright. Launches (or reuses) the local app, drives it through real inputs, screenshots the key views, and checks a regression checklist (raw tracebacks, broken icons, chart title/legend overlap, console errors, overclaiming copy). Use when the user asks to "test the UI", "check the app looks right", "screenshot the app", "do a UI QA pass", or after making UI/CSS/chart changes.
---

# UI QA — QuantWizard visual regression pass

Drive the running app like a user, capture screenshots, and flag visual/behavioral
regressions. This complements `release-check` (which is static/import only).

## Prerequisites
- Playwright MCP tools must be available (`mcp__playwright__browser_*`).
- The app must be running locally. If not, start it (headless) and wait for health:
  ```powershell
  Start-Process -FilePath "py" -ArgumentList "-m","streamlit","run","app.py","--server.port=8501","--server.headless=true" -WindowStyle Hidden
  ```
  Poll `http://localhost:8501/_stcore/health` until it returns `ok`.

## Procedure
1. `browser_resize` to 1440×900 for consistent shots.
2. **Landing page**: navigate to `http://localhost:8501`, wait for "Stock Analysis"
   text. Screenshot the hero, the feature/problem/methodology cards.
3. **Analysis view**: type a ticker (e.g. `AAPL`) into the sidebar input
   (`input[placeholder="e.g. AAPL, SPY, BTC, ETH"]`), submit, wait for
   "Sharpe Ratio". Screenshot the hero, metric cards, charts, and the
   Fundamentals & Valuation panel.
4. **Tabs**: click through Portfolio Builder, Bond Analysis, Stress Test, Strategy;
   screenshot each top section.
5. Read each screenshot and run the checklist below.

## Regression checklist (flag any hit)
- **Raw tracebacks** on screen ("Traceback", "File ...line") — should never be
  user-visible; errors must be friendly messages.
- **Literal directive text** like `:material/...:` or unrendered icon names
  ("monitoring", "show_chart") showing as words instead of glyphs.
- **Chart title/legend overlap** — Plotly title is top-left; horizontal legends
  must sit top-RIGHT (`xanchor="right", x=1`) or they collide with the title.
- **Metric reconciliation** — on a single stock, Sharpe ≈ (annual return − rfr) /
  annual vol; the displayed volatility should match the Sharpe denominator.
- **Overclaiming copy** — no "Live", "real-time", "Refreshes every 30s" for what
  is end-of-day / ~15-min delayed free-tier data.
- **Console errors** — check `browser_console_messages` for JS errors.
- **Contrast** — selected tab label and primary-button text must be readable.

## Output
Report a short PASS/FLAG list with the specific screenshot and a one-line fix for
each flagged item. Prefer DOM reads (`browser_evaluate`) over OCR for exact values
(metric numbers, legend text). Do not commit screenshots — they're throwaway QA
artifacts (add `*.png` QA shots to cleanup).

## Helper
`scripts/ensure_app.py` checks the health endpoint and prints READY / NOT-READY so
you can decide whether to launch the server first.
