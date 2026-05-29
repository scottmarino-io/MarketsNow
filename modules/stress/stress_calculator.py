"""Stress scoring logic: normalize indicators to 0-100 and compute a composite."""

import pandas as pd
import numpy as np
from typing import Optional

from modules.stress.config import COMPOSITE_INDICATORS, ROLLING_NORM_WINDOW, STRESS_BANDS


def percentile_rank(series: pd.Series, value: float) -> float:
    """Fraction of `series` values strictly below `value`, scaled 0-100."""
    clean = series.dropna()
    if clean.empty or np.isnan(value):
        return 50.0
    return float((clean < value).mean() * 100)


def stress_score_for_value(
    series: pd.Series,
    value: float,
    higher_is_stress: bool,
) -> float:
    """
    Compute a 0-100 stress score for a single value given its historical series.
    """
    pct = percentile_rank(series, value)
    return pct if higher_is_stress else (100.0 - pct)


def current_scores(data: dict[str, pd.Series]) -> dict:
    """
    For each composite indicator return its stress score (0-100) and current value.
    """
    scores: dict[str, float] = {}
    weights: dict[str, float] = {}
    values: dict[str, float] = {}

    for key, cfg in COMPOSITE_INDICATORS.items():
        series = data.get(key, pd.Series(dtype=float)).dropna()
        if series.empty:
            continue
        val = float(series.iloc[-1])
        score = stress_score_for_value(series, val, cfg["higher_is_stress"])
        scores[key] = score
        weights[key] = cfg["weight"]
        values[key] = val

    if not scores:
        return {"composite": 50.0, "indicators": {}, "current_values": {}, "weights": {}}

    total_w = sum(weights[k] for k in scores)
    norm_weights = {k: weights[k] / total_w for k in scores}
    composite = sum(scores[k] * norm_weights[k] for k in scores)

    return {
        "composite": composite,
        "indicators": scores,
        "current_values": values,
        "weights": norm_weights,
    }


def _rolling_stress_series(series: pd.Series, higher_is_stress: bool) -> pd.Series:
    """
    For each point t, compute percentile rank of series[t] within
    the trailing ROLLING_NORM_WINDOW observations.
    """
    window = min(ROLLING_NORM_WINDOW, len(series))
    result = pd.Series(index=series.index, dtype=float)
    arr = series.values

    for i in range(len(arr)):
        if i < 20:
            result.iloc[i] = np.nan
            continue
        start = max(0, i - window)
        hist = arr[start : i + 1]
        val = arr[i]
        pct = float((hist < val).mean() * 100)
        result.iloc[i] = pct if higher_is_stress else (100.0 - pct)

    return result


def historical_composite(data: dict[str, pd.Series]) -> pd.Series:
    """
    Build a daily composite stress series aligned on a common date index.
    """
    score_series: dict[str, pd.Series] = {}
    weight_map: dict[str, float] = {}

    for key, cfg in COMPOSITE_INDICATORS.items():
        series = data.get(key, pd.Series(dtype=float)).dropna()
        if len(series) < 25:
            continue
        score_series[key] = _rolling_stress_series(series, cfg["higher_is_stress"])
        weight_map[key] = cfg["weight"]

    if not score_series:
        return pd.Series(dtype=float)

    df = pd.DataFrame(score_series)

    composite = pd.Series(index=df.index, dtype=float)
    for i in range(len(df)):
        row = df.iloc[i].dropna()
        if row.empty:
            composite.iloc[i] = np.nan
            continue
        w = pd.Series({k: weight_map[k] for k in row.index})
        w = w / w.sum()
        composite.iloc[i] = float((row * w).sum())

    return composite.dropna()


def classify(score: float) -> tuple[str, str]:
    """Return (label, hex_color) for a composite score 0-100."""
    for lo, hi, label, color in STRESS_BANDS:
        if lo <= score <= hi:
            return label, color
    return "Unknown", "#888"
