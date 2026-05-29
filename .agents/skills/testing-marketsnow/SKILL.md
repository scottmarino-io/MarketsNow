---
name: testing-marketsnow
description: Test the MarketsNow unified Streamlit app end-to-end. Use when verifying page loads, data fetching, navigation, and graceful API key handling.
---

# Testing MarketsNow

## Overview
MarketsNow is a multi-page Streamlit app with 5 pages (Home + 4 sub-pages). It requires up to 3 API keys for full functionality, but the Momentum Screener works without any keys via yfinance.

## Setup

```bash
cd /home/ubuntu/repos/MarketsNow
pip install -r requirements.txt
streamlit run app.py --server.port 8501 --server.headless true
```

## Devin Secrets Needed
- `FRED_API_KEY` — Required for Market Stress page (free from https://fred.stlouisfed.org)
- `MASSIVE_API_KEY` — Required for Wheel Screener, Market Monitor, and breadth data
- `ANTHROPIC_API_KEY` — Optional, for AI analysis on Market Stress page

## What Can Be Tested Without API Keys
- **Home Dashboard** — Renders header, news ticker (RSS), API key prompts, navigation cards
- **Momentum Screener** — Full data load via yfinance (~160 tickers), all 3 tabs (Screener, Sector View, Deep Dive)
- **Graceful degradation** — Market Stress shows FRED key prompt, Wheel Screener and Market Monitor show styled `st.error()` + `st.stop()`
- **Multi-page navigation** — All sidebar links work, page transitions are clean

## What Requires API Keys
- **Market Stress** (FRED) — 25+ indicators, composite stress gauge, historical chart, direction forecast, economic calendar
- **Wheel Screener** (Massive) — Options chains, IV smile, yield vs delta, theta decay, wheel scoring, breadth panel
- **Market Monitor** (Massive) — Price snapshot cards, sparklines, trend scoring, intraday charts, breadth panel
- **AI Analysis** (Anthropic) — Claude-powered market analysis on Stress page
- **Home Dashboard breadth/stress widgets** (FRED + Massive) — Stress gauge and breadth cards on home page

## Test Procedure

### Phase 1: Smoke Test (no keys needed)
1. Start Streamlit app
2. Navigate to Home — verify header, news ticker, info messages for missing keys, 4 nav cards
3. Navigate to Market Stress — verify FRED key prompt in sidebar, no crash
4. Navigate to Wheel Screener — verify styled error "MASSIVE_API_KEY not set", sidebar controls rendered
5. Navigate to Momentum Screener — wait ~30s for yfinance data load, verify:
   - Summary cards (Tickers shown > 100, Bullish/Bearish counts, Avg composite, Tagged setups)
   - Screener tab: sortable table with all columns
   - Sector View tab: bar charts + sector summary table
   - Deep Dive tab: candlestick chart with SMA overlays, Technical + Fundamental panels
6. Navigate to Market Monitor — verify styled error, sidebar with ticker input
7. Navigate back to Home — verify round-trip works

### Phase 2: Full Test (with API keys)
1. Set FRED_API_KEY in .env or sidebar
2. Verify Market Stress page loads all 25+ indicators, gauge, historical chart
3. Set MASSIVE_API_KEY in .env or sidebar
4. Verify Wheel Screener loads options chain for default ticker (QQQI)
5. Verify Market Monitor loads price cards for SPY, QQQ, IWM, DIA, QQQI
6. Verify Home Dashboard shows stress gauge and breadth cards

## Known Issues / Tips
- First load of Momentum Screener can take 5-7 minutes when Massive API key is present (fetches ~170 tickers via yfinance + bulk Massive API snapshot). Without Massive key, it's ~30s. Subsequent loads are cached.
- The `massive` Python package may not have a publicly available PyPI version matching the user's private API — check if import errors occur
- News ticker uses RSS feeds (CNBC, MarketWatch, Google News) and might be empty if feeds are temporarily down
- The app uses Streamlit's `st.cache_data` and `st.cache_resource` extensively — use the "Refresh all data" button or `st.cache_data.clear()` to bust cache
- yfinance occasionally rate-limits on bulk fetches; if fewer than ~100 tickers load, it might be a transient yfinance issue
- The CLI terminal monitor (`cli/terminal_monitor.py`) requires the Massive API key and is tested separately via `python3 -m cli.terminal_monitor`
- **FRED API may experience 504 Gateway Timeouts** — This is an external infrastructure issue (https://api.stlouisfed.org). When FRED is down, Market Stress and Home Dashboard stress gauge will show "None" for all indicators. Verify FRED status with: `curl -s -o /dev/null -w "%{http_code}" "https://api.stlouisfed.org/fred/series/observations?series_id=VIXCLS&api_key=${FRED_API_KEY}&file_type=json&limit=1"`
- **Home Dashboard Market Breadth bug** — The Home page (`app.py`) might show "Add a Massive API key" in the breadth section even when the key is loaded and works on other pages. This may be a key-passing issue specific to the home page's breadth widget.
- When launching with API keys, export them as environment variables before starting Streamlit: `FRED_API_KEY=xxx MASSIVE_API_KEY=yyy ANTHROPIC_API_KEY=zzz streamlit run app.py`
- On Ubuntu 23.04+, use a virtual environment to avoid PEP 668 errors: `python3 -m venv .venv && source .venv/bin/activate`
