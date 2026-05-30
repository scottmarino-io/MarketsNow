"""Metrics — EV vs random, hit rate, Sharpe, drawdown, grade monotonicity."""

from typing import Dict

import numpy as np
import pandas as pd

from backtest import config


def _annualize_sharpe(returns: pd.Series, periods_per_year: float) -> float:
    r = returns.dropna()
    if len(r) < 2 or r.std(ddof=1) == 0:
        return float("nan")
    return float(r.mean() / r.std(ddof=1) * np.sqrt(periods_per_year))


def _max_drawdown(period_returns: pd.Series) -> float:
    """Max drawdown of a compounded equity curve built from sequential returns."""
    r = period_returns.dropna()
    if r.empty:
        return float("nan")
    equity = (1 + r).cumprod()
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min())


def return_by_grade(df: pd.DataFrame, horizon: str) -> pd.DataFrame:
    """Average forward return + hit rate by conviction grade for one horizon."""
    col = f"ret_{horizon}"
    bench = f"bench_ret_{horizon}"
    sub = df.dropna(subset=[col])
    rows = []
    for grade in ["A", "B", "C", "D", "F"]:
        g = sub[sub["grade"] == grade]
        if g.empty:
            rows.append({"grade": grade, "n": 0})
            continue
        beat = (g[col] > g[bench]).mean() if bench in g else float("nan")
        rows.append(
            {
                "grade": grade,
                "n": len(g),
                "avg_return": g[col].mean(),
                "median_return": g[col].median(),
                "hit_rate_pos": (g[col] > 0).mean(),
                "hit_rate_vs_spy": beat,
            }
        )
    return pd.DataFrame(rows)


def random_baseline(
    df: pd.DataFrame, horizon: str, basket_size: int, draws: int = config.RANDOM_DRAWS
) -> float:
    """Mean forward return of random equal-weight baskets drawn per rebalance date.

    For each date we draw `basket_size` random tickers (matching the A-rated
    count that month) and average their forward return, then average across
    draws and dates. This is the fair EV baseline for the A-rated basket.
    """
    col = f"ret_{horizon}"
    rng = np.random.default_rng(config.RANDOM_SEED)
    per_date_means = []
    for asof, g in df.dropna(subset=[col]).groupby("asof"):
        pool = g[col].values
        if len(pool) == 0:
            continue
        k = min(basket_size, len(pool))
        if k <= 0:
            continue
        draw_means = [rng.choice(pool, size=k, replace=False).mean() for _ in range(draws)]
        per_date_means.append(np.mean(draw_means))
    if not per_date_means:
        return float("nan")
    return float(np.mean(per_date_means))


def summarize(df: pd.DataFrame) -> Dict:
    """Build the full metrics summary across horizons."""
    out: Dict = {"mode": df["mode"].iloc[0] if not df.empty else "unknown"}
    periods_per_year = {"1m": 12, "3m": 4, "6m": 2}

    for m in config.HORIZONS_MONTHS:
        h = f"{m}m"
        col = f"ret_{h}"
        bench = f"bench_ret_{h}"
        a = df[(df["grade"] == "A")].dropna(subset=[col])
        all_picks = df.dropna(subset=[col])

        # average A-rated basket return per rebalance date, then mean
        a_by_date = a.groupby("asof")[col].mean()
        a_avg = float(a_by_date.mean()) if not a_by_date.empty else float("nan")

        # basket size = avg number of A-rated names per date
        basket_size = int(round(a.groupby("asof").size().mean())) if not a.empty else 0
        rand_avg = random_baseline(df, h, max(basket_size, 1))

        spy_by_date = (
            df.dropna(subset=[bench]).groupby("asof")[bench].first()
            if bench in df
            else pd.Series(dtype=float)
        )
        spy_avg = float(spy_by_date.mean()) if not spy_by_date.empty else float("nan")

        out[h] = {
            "n_a_observations": int(len(a)),
            "avg_a_basket_per_date": basket_size,
            "a_avg_return": a_avg,
            "random_avg_return": rand_avg,
            "ev_vs_random": (a_avg - rand_avg)
            if not (np.isnan(a_avg) or np.isnan(rand_avg))
            else float("nan"),
            "spy_avg_return": spy_avg,
            "ev_vs_spy": (a_avg - spy_avg)
            if not (np.isnan(a_avg) or np.isnan(spy_avg))
            else float("nan"),
            "a_hit_rate_pos": float((a[col] > 0).mean()) if not a.empty else float("nan"),
            "a_hit_rate_vs_spy": float((a[col] > a[bench]).mean())
            if (not a.empty and bench in a)
            else float("nan"),
            "a_sharpe": _annualize_sharpe(a_by_date, periods_per_year.get(h, 12)),
            "a_max_drawdown": _max_drawdown(a_by_date.sort_index()),
            "grade_table": return_by_grade(df, h).to_dict(orient="records"),
        }

        # monotonicity check: are avg returns ordered A>=B>=C>=D>=F?
        gt = return_by_grade(df, h)
        gt = gt[gt["n"] > 0]
        if "avg_return" in gt and len(gt) >= 2:
            order = gt.set_index("grade")["avg_return"]
            ideal = [g for g in ["A", "B", "C", "D", "F"] if g in order.index]
            ranks = order.reindex(ideal).values
            # Spearman-style: fraction of adjacent pairs correctly ordered
            pairs = list(zip(ranks[:-1], ranks[1:]))
            correct = sum(1 for hi, lo in pairs if hi >= lo)
            out[h]["grade_monotonic_frac"] = correct / len(pairs) if pairs else float("nan")
        else:
            out[h]["grade_monotonic_frac"] = float("nan")

    return out
