"""CLI entrypoint for the Top Picks backtest.

Examples:
    python -m backtest.run_backtest                 # technical-only (rigorous)
    python -m backtest.run_backtest --full-composite # + yfinance fundamentals (biased)
    python -m backtest.run_backtest --universe combined --no-plots
"""

import argparse
import json
import os
from datetime import datetime

import pandas as pd

from backtest import config, data, engine, metrics


def _print_summary(summary: dict):
    print("\n" + "=" * 70)
    print(f"BACKTEST SUMMARY  —  mode: {summary['mode']}")
    print("=" * 70)
    for m in config.HORIZONS_MONTHS:
        h = f"{m}m"
        s = summary[h]
        print(f"\n— {m}-month horizon —")
        print(f"  A-rated observations:     {s['n_a_observations']}")
        print(f"  Avg A-basket / rebalance: {s['avg_a_basket_per_date']}")
        print(f"  A avg return:             {_pct(s['a_avg_return'])}")
        print(f"  Random avg return:        {_pct(s['random_avg_return'])}")
        print(f"  SPY avg return:           {_pct(s['spy_avg_return'])}")
        print(f"  EV vs random:             {_pct(s['ev_vs_random'])}")
        print(f"  EV vs SPY:                {_pct(s['ev_vs_spy'])}")
        print(f"  A hit rate (positive):    {_pct(s['a_hit_rate_pos'])}")
        print(f"  A hit rate (beat SPY):    {_pct(s['a_hit_rate_vs_spy'])}")
        print(f"  A Sharpe (annualized):    {_num(s['a_sharpe'])}")
        print(f"  A max drawdown:           {_pct(s['a_max_drawdown'])}")
        print(f"  Grade monotonic fraction: {_num(s['grade_monotonic_frac'])}")
        print(f"  Return by grade:")
        for g in s["grade_table"]:
            if g.get("n", 0) > 0:
                print(
                    f"     {g['grade']}: n={g['n']:<5} avg={_pct(g.get('avg_return'))}"
                    f"  hit_pos={_pct(g.get('hit_rate_pos'))}"
                    f"  beat_spy={_pct(g.get('hit_rate_vs_spy'))}"
                )


def _pct(x):
    try:
        if x is None or pd.isna(x):
            return "  n/a"
        return f"{x*100:+.2f}%"
    except Exception:
        return "  n/a"


def _num(x):
    try:
        if x is None or pd.isna(x):
            return " n/a"
        return f"{x:+.2f}"
    except Exception:
        return " n/a"


def _make_plots(df: pd.DataFrame, summary: dict, tag: str):
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"  (skipping plots — matplotlib unavailable: {e})")
        return []

    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    paths = []

    # 1. Cumulative equity: A-rated vs universe vs SPY using the 3m horizon.
    # Subsample every 3rd rebalance so the periods are truly NON-OVERLAPPING
    # (chaining monthly 3m returns would triple-count and inflate the curve).
    h = "3m"
    step = 3
    col = f"ret_{h}"
    bench = f"bench_ret_{h}"
    a_by_date = df[df["grade"] == "A"].dropna(subset=[col]).groupby("asof")[col].mean()
    spy_by_date = df.dropna(subset=[bench]).groupby("asof")[bench].first()
    all_by_date = df.dropna(subset=[col]).groupby("asof")[col].mean()

    if not a_by_date.empty:
        idx = a_by_date.sort_index().index[::step]
        a_seq = a_by_date.reindex(idx)
        all_seq = all_by_date.reindex(idx)
        spy_seq = spy_by_date.reindex(idx)
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(idx, (1 + a_seq).cumprod().values, label="A-rated basket", lw=2, marker="o", ms=3)
        ax.plot(idx, (1 + all_seq).cumprod().values, label="Universe avg", lw=1.5, ls="--")
        ax.plot(idx, (1 + spy_seq).cumprod().values, label="SPY", lw=1.5, ls=":")
        ax.set_title(f"Cumulative growth of $1 — non-overlapping {h} holds ({tag})")
        ax.set_xlabel("Rebalance date")
        ax.set_ylabel("Growth multiple")
        ax.legend()
        ax.grid(alpha=0.3)
        p = os.path.join(config.RESULTS_DIR, f"equity_{tag}.png")
        fig.tight_layout()
        fig.savefig(p, dpi=110)
        plt.close(fig)
        paths.append(p)

    # 2. Avg return by grade (1m/3m/6m grouped bars)
    grades = ["A", "B", "C", "D", "F"]
    fig, ax = plt.subplots(figsize=(10, 5))
    width = 0.25
    import numpy as np

    x = np.arange(len(grades))
    for i, m in enumerate(config.HORIZONS_MONTHS):
        gt = {r["grade"]: r.get("avg_return") for r in summary[f"{m}m"]["grade_table"]}
        vals = [(gt.get(g) or 0) * 100 for g in grades]
        ax.bar(x + i * width, vals, width, label=f"{m}m")
    ax.set_xticks(x + width)
    ax.set_xticklabels(grades)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_title(f"Average forward return by conviction grade ({tag})")
    ax.set_ylabel("Avg return %")
    ax.legend()
    ax.grid(alpha=0.3, axis="y")
    p = os.path.join(config.RESULTS_DIR, f"by_grade_{tag}.png")
    fig.tight_layout()
    fig.savefig(p, dpi=110)
    plt.close(fig)
    paths.append(p)

    print(f"  plots written: {', '.join(os.path.basename(p) for p in paths)}")
    return paths


def main():
    ap = argparse.ArgumentParser(description="Top Picks backtest")
    ap.add_argument("--full-composite", action="store_true",
                    help="Add yfinance earnings+fundamental pillars (LOOK-AHEAD BIASED)")
    ap.add_argument("--universe", default=None, choices=["sp500", "combined"],
                    help="Override BACKTEST_UNIVERSE")
    ap.add_argument("--no-cache", action="store_true", help="Ignore the parquet cache")
    ap.add_argument("--no-plots", action="store_true", help="Skip plot generation")
    args = ap.parse_args()

    if args.universe:
        config.UNIVERSE = args.universe

    tickers = config.get_universe()
    if config.BENCHMARK_TICKER not in tickers:
        tickers = tickers + [config.BENCHMARK_TICKER]

    print(f"Universe: {config.UNIVERSE} ({len(tickers)} tickers incl. benchmark)")
    print(f"Window:   {config.DATA_START} → today  |  rebalance "
          f"{config.REBALANCE_START}..{config.REBALANCE_END} ({config.REBALANCE_FREQ})")
    print("Loading price panel from Massive (cached to parquet)...")
    panel = data.load_price_panel(tickers, use_cache=not args.no_cache)

    if config.BENCHMARK_TICKER not in panel:
        print(f"WARNING: benchmark {config.BENCHMARK_TICKER} missing — SPY metrics will be n/a")

    mode_tag = "full" if args.full_composite else "technical"
    print(f"\nRunning rebalance loop (mode={mode_tag})...")
    results = engine.run(
        panel, full_composite=args.full_composite, universe=tickers, verbose=True
    )

    if results.empty:
        print("No results produced — aborting.")
        return

    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    csv_path = os.path.join(config.RESULTS_DIR, f"results_{mode_tag}.csv")
    results.to_csv(csv_path, index=False)
    print(f"\nPer-pick results written: {csv_path}  ({len(results)} rows)")

    summary = metrics.summarize(results)
    summary["generated_at"] = datetime.utcnow().isoformat() + "Z"
    summary["universe"] = config.UNIVERSE
    summary["n_tickers"] = len(panel)
    json_path = os.path.join(config.RESULTS_DIR, f"summary_{mode_tag}.json")
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"Summary written:          {json_path}")

    _print_summary(summary)

    if not args.no_plots:
        _make_plots(results, summary, mode_tag)

    print("\nDone.")


if __name__ == "__main__":
    main()
