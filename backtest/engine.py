"""Rebalance engine — walk forward monthly, score, record forward returns."""

from typing import Dict, List, Optional

import pandas as pd

from backtest import config, data, ptscore


def rebalance_dates(
    start: str = config.REBALANCE_START,
    end: str = config.REBALANCE_END,
    freq: str = config.REBALANCE_FREQ,
) -> List[pd.Timestamp]:
    return list(pd.date_range(start=start, end=end, freq=freq))


def forward_return(
    df: pd.DataFrame, asof: pd.Timestamp, months: int
) -> Optional[float]:
    """Return over [asof, asof + months] using first close on/after each date."""
    entry = data.price_on_or_after(df, asof)
    exit_date = asof + pd.DateOffset(months=months)
    exit_px = data.price_on_or_after(df, exit_date)
    if entry is None or exit_px is None or entry <= 0:
        return None
    # Guard against the exit falling past the end of data (no later bar exists).
    if df.index.max() < exit_date - pd.Timedelta(days=10):
        return None
    return (exit_px - entry) / entry


def run(
    panel: Dict[str, pd.DataFrame],
    full_composite: bool = False,
    universe: Optional[List[str]] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Run the rebalance loop. Returns a tidy DataFrame, one row per (date,ticker).

    Columns: asof, ticker, tech_score, earn_score, fund_score, composite, grade,
    mode, ret_1m, ret_3m, ret_6m, bench_ret_1m/3m/6m.
    """
    enricher = None
    if full_composite:
        tickers = universe or list(panel.keys())
        enricher = ptscore.CompositeEnricher(tickers)
        if verbose:
            print("  loading current yfinance fundamentals/earnings (look-ahead snapshot)...")
        enricher.load()

    spy = panel.get(config.BENCHMARK_TICKER)
    dates = rebalance_dates()
    records = []

    for i, asof in enumerate(dates):
        scored = ptscore.score_universe_asof(panel, asof, enricher=enricher)
        if scored.empty:
            continue

        # forward returns per ticker
        for _, r in scored.iterrows():
            df = panel.get(r["ticker"])
            if df is None:
                continue
            rec = {
                "asof": asof,
                "ticker": r["ticker"],
                "tech_score": int(r["tech_score"]),
                "earn_score": int(r["earn_score"]),
                "fund_score": int(r["fund_score"]),
                "composite": int(r["composite"]),
                "grade": r["grade"],
                "mode": r["mode"],
            }
            for m in config.HORIZONS_MONTHS:
                rec[f"ret_{m}m"] = forward_return(df, asof, m)
            records.append(rec)

        if verbose:
            n_a = (scored["grade"] == "A").sum()
            print(
                f"  [{i+1}/{len(dates)}] {asof.date()}  scored={len(scored)}  A-rated={n_a}"
            )

    df = pd.DataFrame(records)

    # attach benchmark (SPY) forward returns per date
    if spy is not None and not df.empty:
        bench = {}
        for asof in df["asof"].unique():
            ts = pd.Timestamp(asof)
            for m in config.HORIZONS_MONTHS:
                bench[(asof, m)] = forward_return(spy, ts, m)
        for m in config.HORIZONS_MONTHS:
            df[f"bench_ret_{m}m"] = df["asof"].map(
                lambda d, mm=m: bench.get((d, mm))
            )

    return df
