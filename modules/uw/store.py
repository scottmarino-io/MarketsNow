"""Read layer over the UW capture store.

This is the only module the Streamlit pages should import. It never touches the
UW API -- everything is served from the local SQLite store, so the Wheel and
Stress pages keep working after the subscription lapses.

The key design idea: IV rank is just a percentile against a trailing
distribution. Once the distribution is captured, you can compute the percentile
of a *live* IV reading sourced from anywhere (Massive, yfinance-derived, your
own chain math). UW is needed to build the baseline, not to use it.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "uw.sqlite"

TRADING_DAYS = 252


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def store_available() -> bool:
    return DB_PATH.exists()


# --------------------------------------------------------------------------- #
# realized volatility -- computed here, not taken from UW
# --------------------------------------------------------------------------- #

def realized_vol(ticker: str, window: int = 30, as_of: str | None = None) -> float | None:
    """Annualized close-to-close realized vol over `window` trading days.

    Computed from stored closes so the definition is ours and reproducible.
    Returns None if there is not a full window of data.
    """
    sql = (
        "SELECT close FROM vol_state WHERE ticker=? AND close IS NOT NULL "
        + ("AND date <= ? " if as_of else "")
        + "ORDER BY date DESC LIMIT ?"
    )
    params = (ticker, as_of, window + 1) if as_of else (ticker, window + 1)
    with _conn() as conn:
        closes = [r["close"] for r in conn.execute(sql, params)]

    if len(closes) < window + 1:
        return None

    closes.reverse()
    rets = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]
    if len(rets) < 2:
        return None

    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


# --------------------------------------------------------------------------- #
# IV rank / percentile against the captured baseline
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=512)
def _iv_distribution(ticker: str, lookback: int = 252) -> tuple[float, ...]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT volatility_30 FROM vol_state "
            "WHERE ticker=? AND volatility_30 IS NOT NULL "
            "ORDER BY date DESC LIMIT ?",
            (ticker, lookback),
        ).fetchall()
    return tuple(sorted(r["volatility_30"] for r in rows))


def iv_percentile(ticker: str, current_iv: float, lookback: int = 252) -> float | None:
    """Percentile (0-100) of `current_iv` within the captured trailing window.

    Pass a live IV from whatever source the page already uses. This is the
    function that keeps working post-subscription.
    """
    dist = _iv_distribution(ticker, lookback)
    if len(dist) < 30:
        return None
    below = sum(1 for v in dist if v < current_iv)
    return 100.0 * below / len(dist)


def iv_baseline_depth(ticker: str) -> int:
    """How many usable IV observations exist. Guard your scoring on this --
    a percentile off 30 points is not the same claim as one off 500."""
    return len(_iv_distribution(ticker))


# --------------------------------------------------------------------------- #
# the wheel screener's actual question
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class VolSpread:
    ticker: str
    implied: float | None
    realized: float | None
    spread: float | None          # implied - realized, in vol points (decimal)
    iv_pctile: float | None
    depth: int

    @property
    def is_rich(self) -> bool:
        """Implied meaningfully above realized AND elevated vs own history.

        Both conditions matter. Tonight's GDX case had IV rank 57.8 with
        implied 6 points BELOW realized -- rank alone would have passed it.
        """
        return (
            self.spread is not None
            and self.spread > 0.03
            and self.iv_pctile is not None
            and self.iv_pctile >= 60
            and self.depth >= 120
        )


def vol_spread(ticker: str, current_iv: float | None = None) -> VolSpread:
    """Core wheel-screener input: is this premium actually rich?

    If `current_iv` is omitted, the most recent captured implied is used --
    fine for research, but pass a live value in the app.
    """
    if current_iv is None:
        with _conn() as conn:
            row = conn.execute(
                "SELECT volatility_30 FROM vol_state WHERE ticker=? "
                "AND volatility_30 IS NOT NULL ORDER BY date DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        current_iv = row["volatility_30"] if row else None

    rv = realized_vol(ticker, 30)
    return VolSpread(
        ticker=ticker,
        implied=current_iv,
        realized=rv,
        spread=(current_iv - rv) if (current_iv is not None and rv is not None) else None,
        iv_pctile=iv_percentile(ticker, current_iv) if current_iv is not None else None,
        depth=iv_baseline_depth(ticker),
    )


# --------------------------------------------------------------------------- #
# GEX factor for the stress composite
# --------------------------------------------------------------------------- #

def gex_percentile(ticker: str = "SPY", current_net_gex: float | None = None,
                   lookback: int = 252) -> float | None:
    """Percentile of net GEX vs its own trailing distribution.

    Feed this into the stress composite as a factor. It is mechanically
    orthogonal to the FRED indicators -- dealer gamma positioning does not
    co-move with credit spreads or the yield curve, so it raises the effective
    rank of the composite rather than just adding a correlated column.
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT net_gex FROM gex_daily WHERE ticker=? AND net_gex IS NOT NULL "
            "ORDER BY date DESC LIMIT ?",
            (ticker, lookback),
        ).fetchall()
    vals = [r["net_gex"] for r in rows]
    if len(vals) < 30:
        return None
    if current_net_gex is None:
        current_net_gex = vals[0]
    below = sum(1 for v in vals if v < current_net_gex)
    return 100.0 * below / len(vals)


def latest_gex_levels(ticker: str, source: str = "oi") -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM gex_levels WHERE ticker=? AND source=? "
            "ORDER BY date DESC LIMIT 1",
            (ticker, source),
        ).fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------- #
# research helper: the question this whole store exists to answer
# --------------------------------------------------------------------------- #

def vrp_panel(min_depth: int = 120) -> list[VolSpread]:
    """Cross-sectional IV-vs-RV snapshot across every captured ticker.

    This is the panel that makes the VRP question answerable. One snapshot of
    20 tickers tells you nothing; 170 tickers x 500 days is a dataset.
    """
    with _conn() as conn:
        tickers = [
            r["ticker"]
            for r in conn.execute("SELECT DISTINCT ticker FROM vol_state ORDER BY ticker")
        ]
    out = [vol_spread(t) for t in tickers]
    return sorted(
        (v for v in out if v.spread is not None and v.depth >= min_depth),
        key=lambda v: v.spread,
        reverse=True,
    )
