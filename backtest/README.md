# Top Picks Backtest

A standalone validation harness for the 🏆 Top Picks investment-scoring model.
It answers the question: **do A-rated picks have a positive expected value (EV)
versus a random pick from the same universe — or versus just buying SPY?**

It walks the scoring model forward in time over monthly rebalances, records
forward returns at 1M / 3M / 6M horizons, and compares the A-rated basket
against a random equal-weight basket and SPY buy-and-hold.

## Running

```bash
# from the repo root, with MASSIVE_API_KEY set
python -m backtest.run_backtest                  # technical-only (rigorous, unbiased)
python -m backtest.run_backtest --full-composite # + earnings/fundamentals (look-ahead biased)
python -m backtest.run_backtest --universe combined --no-plots
```

Outputs land in `backtest/results/` (git-ignored):
- `results_<mode>.csv` — one row per (rebalance date, ticker) with scores + forward returns
- `summary_<mode>.json` — aggregated metrics
- `equity_<mode>.png`, `by_grade_<mode>.png` — plots

Prices are cached to `backtest/.cache/` (parquet, one file per ticker) so repeat
runs are fast and never touch yfinance for price data.

## Two modes

| Mode | Pillars | Bias | What it tells you |
|------|---------|------|-------------------|
| **technical-only** (default) | Technical only | **None** — fully point-in-time | Does the clean technical pillar sort forward returns? |
| **full-composite** (`--full-composite`) | Technical + Earnings + Fundamental | **Look-ahead** on earnings/fundamentals | Approximates the live composite A-F grade |

In technical-only mode the composite equals the technical score (-7..+7) and the
A-F label is a *technical tier* (`grade_from_tech`), since the technical pillar
alone can't reach the composite A threshold (≥10).

## Data sources

- **Prices:** Massive `get_aggs` (split-adjusted daily OHLCV), one call per
  ticker over the full window. The Stocks tier exposes ~5 years of history
  (earliest bar ~2021-06-01).
- **Earnings/fundamentals (full mode only):** yfinance. These return the
  **current** snapshot, which is reused at every historical date — hence the
  look-ahead bias. yfinance also rate-limits/blocks intermittently, so coverage
  in full mode is partial (tickers without data score 0 on those two pillars).

## Honest caveats

1. **Look-ahead bias (full mode):** earnings/fundamental scores use today's data,
   not what was known historically. This inflates full-composite results. Use the
   Massive Financials add-on for a clean fundamental backtest.
2. **Survivorship bias:** the universe is the *current* S&P 500 + Nasdaq 100, so
   stocks that were delisted/dropped are excluded — inflates all results somewhat.
3. **No dividends:** prices are split-adjusted but not total-return. Affects picks
   and SPY consistently, so relative comparisons are roughly fair.
4. **Overlapping windows:** the averaged EV metrics use monthly observations of
   multi-month forward returns (overlapping). The equity-curve plot uses truly
   non-overlapping periods and can therefore disagree with the averaged metric —
   that gap is itself a signal the edge is modest/sample-sensitive.

## Module layout

- `config.py` — window, universe, rebalance cadence, horizons, cache paths
- `data.py` — Massive price loader + parquet cache + point-in-time slicing
- `ptscore.py` — point-in-time scoring (reuses `modules/screener/signals.py`)
- `engine.py` — rebalance loop + forward-return calculation
- `metrics.py` — EV vs random/SPY, hit rate, Sharpe, drawdown, grade monotonicity
- `run_backtest.py` — CLI entrypoint
