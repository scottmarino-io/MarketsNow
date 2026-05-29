"""Data fetching from FRED API and Yahoo Finance."""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional
import streamlit as st

from modules.stress.config import COMPOSITE_INDICATORS, INFORMATIONAL_INDICATORS, LOOKBACK_YEARS


def _fred_client(api_key: str):
    from fredapi import Fred
    return Fred(api_key=api_key)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_fred_series(api_key: str, series_id: str, years: int = LOOKBACK_YEARS) -> pd.Series:
    """Fetch a single FRED series going back `years` years."""
    try:
        fred = _fred_client(api_key)
        start = datetime.now() - timedelta(days=years * 365 + 60)
        data = fred.get_series(series_id, observation_start=start.strftime("%Y-%m-%d"))
        return data.dropna().rename(series_id)
    except Exception as exc:
        st.warning(f"FRED [{series_id}]: {exc}")
        return pd.Series(dtype=float, name=series_id)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_sp500(api_key: str, years: int = LOOKBACK_YEARS) -> pd.Series:
    """
    Fetch S&P 500 prices.  Tries Yahoo Finance first; falls back to FRED SP500
    series if Yahoo is unavailable (e.g. SSL issues on some macOS versions).
    """
    # --- Try Yahoo Finance ---
    try:
        import yfinance as yf
        start = (datetime.now() - timedelta(days=years * 365 + 60)).strftime("%Y-%m-%d")
        ticker = yf.Ticker("^GSPC")
        hist = ticker.history(start=start, auto_adjust=True)
        if not hist.empty:
            close = hist["Close"].copy()
            idx = pd.to_datetime(close.index)
            if idx.tz is not None:
                idx = idx.tz_convert(None)
            close.index = idx
            return close.dropna().rename("^GSPC")
    except Exception:
        pass

    # --- Fallback: FRED SP500 series ---
    try:
        series = fetch_fred_series(api_key, "SP500", years)
        if not series.empty:
            return series.rename("^GSPC")
    except Exception as exc:
        st.warning(f"S&P 500 fallback (FRED SP500): {exc}")

    return pd.Series(dtype=float, name="^GSPC")


def compute_drawdown(prices: pd.Series, window: int = 252) -> pd.Series:
    """Rolling drawdown from the trailing `window`-day high, as a percentage."""
    rolling_high = prices.rolling(window=window, min_periods=1).max()
    return ((prices / rolling_high) - 1) * 100


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_cnn_fear_greed() -> pd.Series:
    """Fetch CNN Fear & Greed Index history from CNN's public data endpoint."""
    import urllib.request
    import json
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.cnn.com/markets/fear-and-greed",
            "Origin": "https://www.cnn.com",
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read())
        records = payload["fear_and_greed_historical"]["data"]
        series = pd.Series(
            {pd.Timestamp(r["x"], unit="ms"): float(r["y"]) for r in records}
        ).sort_index()
        return series.dropna().rename("fear_greed")
    except Exception as exc:
        st.warning(f"CNN Fear & Greed: {exc}")
        return pd.Series(dtype=float, name="fear_greed")


def _yahoo_close_urllib(ticker: str, years: int) -> pd.Series:
    """
    Fetch daily closing prices from Yahoo Finance's JSON API using only
    urllib (no yfinance).
    """
    import urllib.request
    import json
    import time

    end_ts   = int(time.time())
    start_ts = end_ts - (years * 365 + 60) * 86400
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?period1={start_ts}&period2={end_ts}&interval=1d&events=history"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read())

    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes     = result["indicators"]["quote"][0]["close"]
    series = pd.Series(
        {pd.Timestamp(ts, unit="s"): float(c)
         for ts, c in zip(timestamps, closes) if c is not None}
    ).sort_index()
    series.index = pd.to_datetime(series.index).tz_localize(None)
    return series


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_copper_gold_ratio(api_key: str, years: int = LOOKBACK_YEARS) -> pd.Series:
    """
    Compute the Copper/Gold ratio.

    Copper: FRED PCOPPUSDM (IMF monthly, USD/metric ton) — reliable FRED series
    Gold:   Yahoo Finance JSON API via urllib for GLD ETF
    """
    try:
        copper = fetch_fred_series(api_key, "PCOPPUSDM", years)

        try:
            gold = _yahoo_close_urllib("GLD", years)
        except Exception:
            try:
                gold = _yahoo_close_urllib("IAU", years)
            except Exception:
                gold = pd.Series(dtype=float)

        if copper.empty or gold.empty:
            return pd.Series(dtype=float, name="copper_gold_ratio")

        def _strip_tz(s):
            idx = pd.to_datetime(s.index)
            if idx.tz is not None:
                idx = idx.tz_convert(None)
            out = s.copy()
            out.index = idx
            return out

        copper = _strip_tz(copper)
        gold   = _strip_tz(gold)

        combined = pd.DataFrame({"copper": copper, "gold": gold})
        bday_range = pd.date_range(combined.index.min(), combined.index.max(), freq="B")
        combined = combined.reindex(bday_range).ffill().dropna()

        if combined.empty:
            return pd.Series(dtype=float, name="copper_gold_ratio")

        return (combined["copper"] / combined["gold"]).rename("copper_gold_ratio")

    except Exception as exc:
        st.warning(f"Copper/Gold ratio: {exc}")
        return pd.Series(dtype=float, name="copper_gold_ratio")


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_yf_price(ticker: str, years: int = LOOKBACK_YEARS) -> pd.Series:
    """Fetch a daily closing-price series from Yahoo Finance."""
    try:
        import yfinance as yf
        start = (datetime.now() - timedelta(days=years * 365 + 60)).strftime("%Y-%m-%d")
        t = yf.Ticker(ticker)
        hist = t.history(start=start, auto_adjust=True)
        if not hist.empty:
            close = hist["Close"].copy()
            idx = pd.to_datetime(close.index)
            if idx.tz is not None:
                idx = idx.tz_convert(None)
            close.index = idx
            return close.dropna().rename(ticker)
    except Exception as exc:
        st.warning(f"Yahoo Finance [{ticker}]: {exc}")
    return pd.Series(dtype=float, name=ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_all(api_key: str) -> dict[str, pd.Series]:
    """
    Fetch every series (composite + informational) and return a dict of
    {indicator_key: pd.Series}.
    """
    data: dict[str, pd.Series] = {}

    all_indicators = {**COMPOSITE_INDICATORS, **INFORMATIONAL_INDICATORS}

    for key, cfg in all_indicators.items():
        series_id = cfg.get("fred_series")
        if series_id:
            data[key] = fetch_fred_series(api_key, series_id)

    # S&P 500 drawdown
    sp500 = fetch_sp500(api_key)
    data["_sp500_price"] = sp500
    if not sp500.empty:
        data["sp500_drawdown"] = compute_drawdown(sp500)

    # CNN Fear & Greed Index
    data["fear_greed"] = fetch_cnn_fear_greed()

    # M2 YoY growth rate (computed from M2SL)
    m2 = data.get("m2", pd.Series(dtype=float))
    if not m2.empty and len(m2) >= 13:
        m2_yoy = m2.pct_change(12) * 100
        data["m2_yoy"] = m2_yoy.dropna().rename("m2_yoy")

    # Copper / Gold ratio
    data["copper_gold_ratio"] = fetch_copper_gold_ratio(api_key)

    return data


def latest_value(series: pd.Series) -> Optional[float]:
    """Return the most recent non-NaN value, or None."""
    s = series.dropna()
    return float(s.iloc[-1]) if not s.empty else None


def prev_value(series: pd.Series, n: int = 1) -> Optional[float]:
    """Return the value n observations before the latest."""
    s = series.dropna()
    if len(s) <= n:
        return None
    return float(s.iloc[-(n + 1)])


def daily_change(series: pd.Series) -> Optional[float]:
    """Latest minus previous observation."""
    curr = latest_value(series)
    prev = prev_value(series)
    if curr is None or prev is None:
        return None
    return curr - prev
