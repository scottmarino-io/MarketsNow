"""Price-history data layer for the backtest.

Pulls split-adjusted daily OHLCV from the Massive API (`get_aggs`, one call per
ticker over the full window) and caches each series to a local parquet file so
repeat runs are instant and avoid yfinance rate limits entirely.
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import pandas as pd

from backtest import config


def _client():
    from massive import RESTClient

    api_key = os.getenv("MASSIVE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "MASSIVE_API_KEY is not set — the backtest needs it for price history."
        )
    return RESTClient(api_key=api_key)


def _cache_path(ticker: str) -> str:
    safe = ticker.replace("/", "_")
    return os.path.join(config.CACHE_DIR, f"{safe}.parquet")


def _aggs_to_df(aggs) -> pd.DataFrame:
    rows = []
    for a in aggs:
        rows.append(
            {
                "Date": pd.Timestamp(a.timestamp, unit="ms").normalize(),
                "Open": a.open,
                "High": a.high,
                "Low": a.low,
                "Close": a.close,
                "Volume": a.volume,
            }
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("Date").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def fetch_ticker_history(
    ticker: str,
    client=None,
    start: str = config.DATA_START,
    end: Optional[str] = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Return split-adjusted daily OHLCV for one ticker (cached to parquet)."""
    path = _cache_path(ticker)
    if use_cache and os.path.exists(path):
        try:
            return pd.read_parquet(path)
        except Exception:
            pass

    client = client or _client()
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    try:
        aggs = client.get_aggs(
            ticker, 1, "day", start, end, adjusted=True, limit=50000
        )
    except Exception:
        return pd.DataFrame()

    df = _aggs_to_df(aggs)
    if not df.empty and use_cache:
        os.makedirs(config.CACHE_DIR, exist_ok=True)
        try:
            df.to_parquet(path)
        except Exception:
            pass
    return df


def load_price_panel(
    tickers: List[str],
    use_cache: bool = True,
    verbose: bool = True,
) -> Dict[str, pd.DataFrame]:
    """Load OHLCV history for every ticker in parallel. Returns {ticker: df}."""
    client = _client()
    out: Dict[str, pd.DataFrame] = {}

    def _one(t: str):
        return t, fetch_ticker_history(t, client=client, use_cache=use_cache)

    with ThreadPoolExecutor(max_workers=config.FETCH_WORKERS) as ex:
        futures = [ex.submit(_one, t) for t in tickers]
        done = 0
        for fut in as_completed(futures):
            t, df = fut.result()
            done += 1
            if df is not None and not df.empty:
                out[t] = df
            if verbose and done % 50 == 0:
                print(f"  loaded {done}/{len(tickers)} tickers...")

    if verbose:
        print(f"  price panel ready: {len(out)}/{len(tickers)} tickers with data")
    return out


def slice_history(
    df: pd.DataFrame, asof: pd.Timestamp, lookback_sessions: int = 260
) -> pd.DataFrame:
    """Return history up to and including `asof` (no look-ahead), last N sessions."""
    sub = df.loc[df.index <= asof]
    if lookback_sessions:
        sub = sub.tail(lookback_sessions)
    return sub


def price_on_or_after(df: pd.DataFrame, date: pd.Timestamp) -> Optional[float]:
    """First available close on/after `date` (handles weekends/holidays)."""
    sub = df.loc[df.index >= date]
    if sub.empty:
        return None
    return float(sub["Close"].iloc[0])


def price_on_or_before(df: pd.DataFrame, date: pd.Timestamp) -> Optional[float]:
    """Last available close on/before `date`."""
    sub = df.loc[df.index <= date]
    if sub.empty:
        return None
    return float(sub["Close"].iloc[-1])
