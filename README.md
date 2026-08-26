# MarketsNow

Unified market intelligence dashboard — combining macro stress monitoring, stock screening, options analysis, and real-time market breadth into a single multi-page Streamlit application.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Streamlit](https://img.shields.io/badge/Streamlit-1.32+-red)

## Pages

| Page | Description |
|------|-------------|
| **Home Dashboard** | Overview with stress gauge, market breadth cards, upcoming economic events, and news ticker |
| **📡 Market Stress** | 25+ macro indicators (VIX, credit spreads, yield curve, NFCI, etc.), composite stress score with historical chart, AI-powered analysis (Claude), next-day S&P 500 direction forecast, economic calendar |
| **⚙ Wheel Screener** | Options wheel strategy screener — IV smile, yield vs delta, theta decay charts, wheel scoring, market breadth panel |
| **📈 Momentum Screener** | S&P 100 + Nasdaq 100 (~170 stocks) ranked by composite score combining technical momentum, fundamental quality, and sentiment. Sector heatmap, deep dive with candlestick charts, optional options flow overlay |
| **📺 Market Monitor** | Bloomberg-style price dashboard with sparklines, trend scoring, detailed metrics table, market breadth, and Unusual Whales market tide + SPY gamma exposure (optional) |

## CLI

A standalone terminal monitor (Rich-based) is also included:

```bash
python -m cli.terminal_monitor
python -m cli.terminal_monitor --tickers SPY QQQ IWM DIA QQQI --interval 60
```

## Setup

### 1. Clone and install

```bash
git clone https://github.com/scottmarino-io/MarketsNow.git
cd MarketsNow
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure API keys

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

| Key | Required | Source |
|-----|----------|--------|
| `FRED_API_KEY` | Yes (for stress monitor) | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) — free |
| `MASSIVE_API_KEY` | Yes (for screener, wheel, monitor) | [massiveclient.com](https://massiveclient.com) |
| `ANTHROPIC_API_KEY` | Optional (for AI analysis) | [console.anthropic.com](https://console.anthropic.com) |
| `UNUSUAL_WHALES_API_KEY` | Optional (Market Tide + Gamma on Breadth) | [unusualwhales.com/api](https://unusualwhales.com/api) |

### 3. Run

```bash
source .venv/bin/activate   # if not already active
streamlit run app.py
```

## Project Structure

```
MarketsNow/
├── app.py                          # Home dashboard (entry point)
├── pages/
│   ├── 1_📡_Market_Stress.py       # Macro stress monitor
│   ├── 2_⚙_Wheel_Screener.py      # Options wheel screener
│   ├── 3_📈_Momentum_Screener.py   # Stock momentum screener
│   └── 4_📺_Market_Monitor.py      # Price monitor dashboard
├── modules/
│   ├── stress/                     # Stress monitor business logic
│   │   ├── config.py               # Indicator definitions & thresholds
│   │   ├── data_fetchers.py        # FRED, Yahoo Finance, CNN data
│   │   ├── stress_calculator.py    # Percentile scoring & classification
│   │   ├── ai_analysis.py          # Claude-powered analysis
│   │   ├── direction_indicator.py  # Next-day direction forecast
│   │   ├── economic_calendar.py    # Forex Factory calendar
│   │   └── news_ticker.py          # Scrolling news headlines
│   ├── screener/                   # Screener business logic
│   │   ├── universe.py             # S&P 100 + Nasdaq 100 tickers
│   │   ├── fetchers.py             # yfinance + Massive API data
│   │   ├── signals.py              # Technical indicators & scoring
│   │   └── options_flow.py         # Options chain analysis
│   ├── market/                     # Market-wide modules
│   │   ├── breadth.py              # Advance/decline breadth proxy
│   │   ├── uw_fetchers.py          # Unusual Whales: market tide, gamma exposure (SPY proxy)
│   │   └── uw_display.py           # Shared Tide/Gamma card + chart rendering
│   └── shared/                     # Cross-page utilities
│       ├── api_keys.py             # Centralized key management
│       └── theme.py                # Colors, CSS, Plotly defaults
├── cli/
│   └── terminal_monitor.py         # Rich-based terminal dashboard
├── .streamlit/config.toml          # Streamlit theme config
├── .env.example                    # API key template
├── requirements.txt                # Python dependencies
└── .gitignore
```

## Data Sources

- **FRED** (Federal Reserve Bank of St. Louis) — macro economic indicators
- **Yahoo Finance** — stock prices, fundamentals, S&P 500 data
- **Massive API** — real-time snapshots, options chains, short interest, breadth
- **Unusual Whales** — market tide (net call/put premium), SPY gamma exposure (optional)
- **CNN Fear & Greed Index** — sentiment gauge
- **Forex Factory** — economic calendar
- **Google News / CNBC / MarketWatch RSS** — news headlines
- **Anthropic Claude** — AI-powered analysis (optional)

## License

Private repository.
