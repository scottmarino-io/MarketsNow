"""Backtest configuration — date window, universe, rebalance cadence, horizons."""

import os

# ---------------------------------------------------------------------------
# Data window
# ---------------------------------------------------------------------------
# The Massive Stocks tier exposes ~5 years of daily history. As of this writing
# the earliest available bar is 2021-06-01. Keep a small buffer past that so
# every ticker has some warm-up history before the first rebalance.
DATA_START = "2021-06-01"

# Trading-day warm-up required before a ticker can be scored. score_technical
# uses SMA20/50/200 — 200 sessions (~9.5 months) gives the full set of signals.
# Tickers with fewer sessions are still scored; the missing SMA simply doesn't
# contribute (matches the live app's behaviour).
MIN_HISTORY_SESSIONS = 200

# ---------------------------------------------------------------------------
# Rebalance schedule
# ---------------------------------------------------------------------------
# First/last rebalance dates. The last date must leave room for the longest
# forward-return horizon (6 months) before the end of available data.
REBALANCE_START = "2022-04-01"
REBALANCE_END = "2025-11-28"
REBALANCE_FREQ = "MS"  # month-start (pandas offset alias)

# Forward-return horizons measured in calendar months.
HORIZONS_MONTHS = [1, 3, 6]

# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------
# "sp500" mirrors the Top Picks universe (S&P 500 + Nasdaq 100, ~500 names).
# "combined" is the smaller momentum universe (~170) for quick iteration.
UNIVERSE = os.getenv("BACKTEST_UNIVERSE", "sp500")

BENCHMARK_TICKER = "SPY"

# Number of random baskets to average when computing the random-pick EV
# baseline. More draws = smoother baseline.
RANDOM_DRAWS = 200
RANDOM_SEED = 42

# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

# Massive fetch concurrency. The Stocks tier tolerates modest parallelism.
FETCH_WORKERS = 8


def get_universe() -> list:
    """Return the ticker list for the configured universe."""
    from modules.screener.universe import COMBINED, SP500_COMBINED

    if UNIVERSE == "combined":
        return list(COMBINED)
    return list(SP500_COMBINED)
