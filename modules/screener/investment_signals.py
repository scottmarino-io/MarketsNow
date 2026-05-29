"""Investment scoring engine for medium/long-term opportunity identification."""

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from modules.screener.signals import calc_technicals, score_technical


def score_earnings(detail: dict) -> int:
    """Earnings score: -5 to +5.

    Signals:
    1. EPS Beat Streak (beat ≥2 of last 4 quarters)
    2. EPS Acceleration (recent growth > prior)
    3. Revenue Acceleration (revenue growth trending up)
    4. Post-Earnings Drift (forward EPS vs trailing EPS direction)
    5. Analyst Revision Momentum (forward > trailing EPS)
    """
    score = 0

    # 1. EPS Beat Streak
    beats = detail.get("beat_count", 0) or 0
    misses = detail.get("miss_count", 0) or 0
    if beats >= 3:
        score += 2
    elif beats >= 2:
        score += 1
    if misses >= 3:
        score -= 2
    elif misses >= 2:
        score -= 1

    # 2. EPS Acceleration
    actuals = detail.get("eps_actuals", [])
    valid_actuals = [a for a in actuals if a is not None] if actuals else []
    if len(valid_actuals) >= 3:
        recent_growth = valid_actuals[-1] - valid_actuals[-2] if valid_actuals[-2] != 0 else 0
        prior_growth = valid_actuals[-2] - valid_actuals[-3] if valid_actuals[-3] != 0 else 0
        if recent_growth > prior_growth and recent_growth > 0:
            score += 1
        elif recent_growth < prior_growth and recent_growth < 0:
            score -= 1

    # 3. Revenue Acceleration
    rev_trend = detail.get("revenue_growth_trend", [])
    valid_rev = [r for r in rev_trend if r is not None] if rev_trend else []
    if len(valid_rev) >= 2:
        if valid_rev[-1] > valid_rev[-2] and valid_rev[-1] > 0:
            score += 1
        elif valid_rev[-1] < valid_rev[-2] and len(valid_rev) >= 3:
            if all(valid_rev[i] < valid_rev[i - 1] for i in range(1, len(valid_rev))):
                score -= 1

    # 4. Margins expanding/contracting
    if detail.get("margins_expanding"):
        score += 1
    elif detail.get("margins_expanding") is False:
        gross_margins = detail.get("gross_margins", [])
        if gross_margins and len(gross_margins) >= 2:
            if gross_margins[-1] < gross_margins[0] - 2:
                score -= 1

    # 5. Analyst Revision Momentum (forward EPS vs trailing EPS)
    fwd = detail.get("forwardEps")
    trail = detail.get("trailingEps")
    if fwd is not None and trail is not None and trail != 0:
        if fwd > trail * 1.05:
            score += 1
        elif fwd < trail * 0.90:
            score -= 1

    return max(-5, min(5, score))


def score_fundamental_quality(row: pd.Series) -> int:
    """Fundamental quality score: -5 to +5.

    Signals:
    1. Profit Margin (> 15% good, < 5% bad)
    2. ROE Quality (> 15% good, < 5% bad)
    3. Debt Health (D/E < 100 good, > 200 bad)
    4. PEG-like (forward P/E vs earnings growth)
    5. Analyst Consensus (< 2.0 good, > 3.5 bad)
    """
    score = 0

    # 1. Profit Margin
    pm = row.get("profitMargins")
    if pd.notna(pm):
        if pm > 0.20:
            score += 1
        elif pm < 0.05:
            score -= 1

    # 2. ROE Quality
    roe = row.get("returnOnEquity")
    if pd.notna(roe):
        if roe > 0.15:
            score += 1
        elif roe < 0.05 or roe < 0:
            score -= 1

    # 3. Debt Health
    de = row.get("debtToEquity")
    if pd.notna(de):
        if de < 80:
            score += 1
        elif de > 200:
            score -= 1

    # 4. PEG-like ratio
    fpe = row.get("forwardPE")
    eg = row.get("earningsGrowth")
    if pd.notna(fpe) and pd.notna(eg) and eg > 0 and fpe > 0:
        peg = fpe / (eg * 100)
        if peg < 1.0:
            score += 1
        elif peg > 2.5:
            score -= 1

    # 5. Analyst Consensus
    rec = row.get("recommendationMean")
    if pd.notna(rec):
        if rec < 2.0:
            score += 1
        elif rec > 3.5:
            score -= 1

    return max(-5, min(5, score))


def tag_investment_strategies(
    tech_score: int,
    earnings_score: int,
    fund_score: int,
    detail: dict,
    row: pd.Series,
) -> List[str]:
    """Generate medium/long-term strategy tags."""
    tags = []
    total = tech_score + earnings_score + fund_score

    beats = detail.get("beat_count", 0) or 0
    fwd_eps = detail.get("forwardEps")
    trail_eps = detail.get("trailingEps")
    roe = row.get("returnOnEquity")
    margins_exp = detail.get("margins_expanding")
    eg = row.get("earningsGrowth")
    fpe = row.get("forwardPE")
    rev_trend = detail.get("revenue_growth_trend", [])

    if earnings_score >= 3 and tech_score >= 2:
        tags.append("EARNINGS MOMENTUM")

    if beats >= 2 and fwd_eps is not None and trail_eps is not None and fwd_eps > trail_eps:
        tags.append("BEAT & RAISE")

    valid_rev = [r for r in rev_trend if r is not None] if rev_trend else []
    if len(valid_rev) >= 2 and all(r > 0 for r in valid_rev[-2:]):
        if valid_rev[-1] > valid_rev[-2]:
            tags.append("REVENUE ACCELERATOR")

    if (pd.notna(roe) and roe > 0.15 and
            margins_exp is True and
            pd.notna(eg) and eg > 0):
        tags.append("QUALITY COMPOUNDER")

    if (pd.notna(fpe) and fpe > 0 and fpe < 15 and
            earnings_score >= 1):
        tags.append("VALUE RECOVERY")

    if total >= 12:
        tags.append("HIGH CONVICTION")

    return tags


def conviction_grade(score: int) -> str:
    """Map composite score to conviction grade."""
    if score >= 10:
        return "A"
    if score >= 5:
        return "B"
    if score >= 0:
        return "C"
    if score >= -5:
        return "D"
    return "F"


def build_top_picks_df(
    price_history: Dict[str, pd.DataFrame],
    earnings_df: pd.DataFrame,
    earnings_details: Dict[str, dict],
) -> pd.DataFrame:
    """Build the ranked investment screener dataframe."""
    tech = calc_technicals(price_history)

    df = tech.copy()
    if not earnings_df.empty:
        for col in earnings_df.columns:
            if col not in df.columns:
                df[col] = earnings_df[col]

    df["tech_score"] = df.apply(score_technical, axis=1)

    earnings_scores = {}
    for ticker in df.index:
        detail = earnings_details.get(ticker, {})
        earnings_scores[ticker] = score_earnings(detail)
    df["earnings_score"] = pd.Series(earnings_scores)
    df["earnings_score"] = df["earnings_score"].fillna(0).astype(int)

    df["fund_score"] = df.apply(score_fundamental_quality, axis=1)

    df["composite"] = df["tech_score"] + df["earnings_score"] + df["fund_score"]

    # Strategy tags
    tag_list = {}
    for ticker in df.index:
        detail = earnings_details.get(ticker, {})
        row = df.loc[ticker]
        tags = tag_investment_strategies(
            int(row.get("tech_score", 0)),
            int(row.get("earnings_score", 0)),
            int(row.get("fund_score", 0)),
            detail,
            row,
        )
        tag_list[ticker] = " ".join(tags)
    df["tags"] = pd.Series(tag_list)

    df["conviction"] = df["composite"].apply(conviction_grade)

    # vs analyst target
    tp = df.get("targetMeanPrice")
    if tp is not None:
        df["vs_target"] = ((tp - df["price"]) / df["price"] * 100).round(1)

    df = df.reset_index()
    df = df.sort_values("composite", ascending=False)
    df["rank"] = range(1, len(df) + 1)

    return df
