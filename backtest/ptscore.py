"""Point-in-time scoring.

Technical pillar is reconstructed cleanly from price history known on each
rebalance date (no look-ahead). Earnings/fundamental pillars, when enabled,
reuse the *current* yfinance snapshot for every historical date and therefore
carry look-ahead bias — this is intentional and clearly flagged in the output.
"""

from typing import Dict, List, Optional

import pandas as pd

from backtest import data
from modules.screener.investment_signals import (
    conviction_grade,
    score_earnings,
    score_fundamental_quality,
)
from modules.screener.signals import calc_technicals, score_technical


def grade_from_tech(tech_score: int) -> str:
    """Technical-tier grade for technical-only mode (tech range -7..+7).

    Distinct from the composite A-F grade. Lets us run the same
    return-by-grade monotonicity test on the clean technical pillar.
    """
    if tech_score >= 5:
        return "A"
    if tech_score >= 3:
        return "B"
    if tech_score >= 1:
        return "C"
    if tech_score >= -2:
        return "D"
    return "F"


def technical_scores_asof(
    panel: Dict[str, pd.DataFrame], asof: pd.Timestamp
) -> pd.DataFrame:
    """Compute the clean point-in-time technical score for every ticker.

    Returns a DataFrame indexed by ticker with technical indicators + tech_score,
    using only price data available on or before `asof`.
    """
    sliced = {}
    for ticker, df in panel.items():
        sub = data.slice_history(df, asof, lookback_sessions=260)
        if len(sub) >= 30:  # calc_technicals requires >=30 sessions
            sliced[ticker] = sub
    if not sliced:
        return pd.DataFrame()

    tech = calc_technicals(sliced)
    tech["tech_score"] = tech.apply(score_technical, axis=1)
    return tech


class CompositeEnricher:
    """Holds the (current) yfinance earnings/fundamental snapshot for full mode.

    Reuses the same current snapshot at every rebalance date — this is the
    documented look-ahead bias of full-composite mode.
    """

    def __init__(self, tickers: List[str]):
        self.tickers = tuple(tickers)
        self.fundamentals = pd.DataFrame()
        self.earnings_details: Dict[str, dict] = {}
        self._loaded = False

    def load(self):
        if self._loaded:
            return
        from modules.screener.earnings import fetch_earnings_details

        # The app's fetch_fundamentals reads the COMBINED universe internally;
        # fetch for our exact universe instead.
        self.fundamentals = _fetch_fundamentals_for(self.tickers)
        self.earnings_details = fetch_earnings_details(self.tickers)
        self._loaded = True

    def earn_score(self, ticker: str) -> int:
        return score_earnings(self.earnings_details.get(ticker, {}))

    def fund_score(self, ticker: str) -> int:
        if self.fundamentals.empty or ticker not in self.fundamentals.index:
            return 0
        return score_fundamental_quality(self.fundamentals.loc[ticker])


def _fetch_fundamentals_for(tickers) -> pd.DataFrame:
    """yfinance fundamentals snapshot for an explicit ticker list (current data)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    import yfinance as yf

    FIELDS = [
        "profitMargins", "returnOnEquity", "debtToEquity",
        "forwardPE", "earningsGrowth", "recommendationMean",
        "trailingEps", "forwardEps",
    ]
    rows = {}

    def _one(t):
        try:
            info = yf.Ticker(t).info
            return t, {f: info.get(f) for f in FIELDS}
        except Exception:
            return t, {f: None for f in FIELDS}

    with ThreadPoolExecutor(max_workers=20) as ex:
        for fut in as_completed([ex.submit(_one, t) for t in tickers]):
            t, row = fut.result()
            rows[t] = row
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame.from_dict(rows, orient="index")


def score_universe_asof(
    panel: Dict[str, pd.DataFrame],
    asof: pd.Timestamp,
    enricher: Optional[CompositeEnricher] = None,
) -> pd.DataFrame:
    """Score every ticker as of `asof`.

    technical-only mode (enricher is None): composite == tech_score,
    grade derived from the technical tier.

    full-composite mode (enricher provided): composite = tech + earnings + fund
    using the existing scoring engine; grade = conviction_grade(composite).
    """
    tech = technical_scores_asof(panel, asof)
    if tech.empty:
        return pd.DataFrame()

    out = pd.DataFrame(index=tech.index)
    out["tech_score"] = tech["tech_score"].astype(int)
    out["price"] = tech["price"]

    if enricher is None:
        out["earn_score"] = 0
        out["fund_score"] = 0
        out["composite"] = out["tech_score"]
        out["grade"] = out["tech_score"].apply(grade_from_tech)
        out["mode"] = "technical_only"
    else:
        out["earn_score"] = [enricher.earn_score(t) for t in out.index]
        out["fund_score"] = [enricher.fund_score(t) for t in out.index]
        out["composite"] = out["tech_score"] + out["earn_score"] + out["fund_score"]
        out["grade"] = out["composite"].apply(conviction_grade)
        out["mode"] = "full_composite"

    out["asof"] = asof
    return out.reset_index().rename(columns={"index": "ticker"})
