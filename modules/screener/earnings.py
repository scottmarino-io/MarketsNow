"""Earnings data fetching and analysis for medium/long-term investment scoring."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

from modules.screener.universe import SP500_COMBINED


def _fetch_earnings_one(ticker: str) -> tuple:
    """Fetch earnings data for a single ticker."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}

        result = {
            "forwardEps": info.get("forwardEps"),
            "trailingEps": info.get("trailingEps"),
            "earningsGrowth": info.get("earningsGrowth"),
            "revenueGrowth": info.get("revenueGrowth"),
            "profitMargins": info.get("profitMargins"),
            "returnOnEquity": info.get("returnOnEquity"),
            "debtToEquity": info.get("debtToEquity"),
            "forwardPE": info.get("forwardPE"),
            "trailingPE": info.get("trailingPE"),
            "priceToBook": info.get("priceToBook"),
            "recommendationMean": info.get("recommendationMean"),
            "targetMeanPrice": info.get("targetMeanPrice"),
            "marketCap": info.get("marketCap"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "shortName": info.get("shortName"),
            "regularMarketPrice": info.get("regularMarketPrice"),
            "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow"),
            "dividendYield": info.get("dividendYield"),
            "beta": info.get("beta"),
        }

        # Earnings history (last 4 quarters actual vs estimate)
        try:
            eh = t.earnings_history
            if eh is not None and not eh.empty:
                eh = eh.sort_index(ascending=True).tail(4)
                surprises = []
                eps_actuals = []
                eps_estimates = []
                for _, row in eh.iterrows():
                    actual = row.get("epsActual")
                    estimate = row.get("epsEstimate")
                    surprise_pct = row.get("surprisePercent")
                    eps_actuals.append(actual)
                    eps_estimates.append(estimate)
                    if surprise_pct is not None and pd.notna(surprise_pct):
                        surprises.append(float(surprise_pct))
                    elif actual is not None and estimate is not None and estimate != 0:
                        surprises.append(float((actual - estimate) / abs(estimate) * 100))
                    else:
                        surprises.append(None)

                result["eps_surprises"] = surprises
                result["eps_actuals"] = [float(x) if pd.notna(x) else None for x in eps_actuals]
                result["eps_estimates"] = [float(x) if pd.notna(x) else None for x in eps_estimates]
                beats = sum(1 for s in surprises if s is not None and s > 0)
                misses = sum(1 for s in surprises if s is not None and s < 0)
                result["beat_count"] = beats
                result["miss_count"] = misses
                result["beat_streak"] = beats
        except Exception:
            pass

        # Quarterly financials for revenue trend
        try:
            qf = t.quarterly_financials
            if qf is not None and not qf.empty:
                rev_row = None
                for label in ["Total Revenue", "Revenue", "Operating Revenue"]:
                    if label in qf.index:
                        rev_row = qf.loc[label]
                        break
                if rev_row is not None:
                    rev_vals = rev_row.dropna().sort_index(ascending=True).tail(4)
                    result["quarterly_revenue"] = [float(x) for x in rev_vals.values]
                    result["quarterly_revenue_dates"] = [
                        d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
                        for d in rev_vals.index
                    ]
                    if len(rev_vals) >= 2:
                        rev_changes = []
                        vals = rev_vals.values
                        for i in range(1, len(vals)):
                            if vals[i - 1] != 0:
                                rev_changes.append(float((vals[i] - vals[i - 1]) / abs(vals[i - 1]) * 100))
                            else:
                                rev_changes.append(None)
                        result["revenue_growth_trend"] = rev_changes

                # Gross / operating margins trend
                gp_row = None
                for label in ["Gross Profit"]:
                    if label in qf.index:
                        gp_row = qf.loc[label]
                        break
                if gp_row is not None and rev_row is not None:
                    gp_vals = gp_row.dropna().sort_index(ascending=True).tail(4)
                    rev_aligned = rev_row.reindex(gp_vals.index).dropna()
                    common = gp_vals.index.intersection(rev_aligned.index)
                    if len(common) >= 2:
                        margins = (gp_vals[common] / rev_aligned[common] * 100).values
                        result["gross_margins"] = [float(m) for m in margins]
                        result["margins_expanding"] = float(margins[-1]) > float(margins[0])
        except Exception:
            pass

        # Earnings dates
        try:
            ed = t.earnings_dates
            if ed is not None and not ed.empty:
                now = pd.Timestamp.now(tz="UTC")
                future = ed[ed.index > now]
                if not future.empty:
                    next_date = future.index.min()
                    result["next_earnings_date"] = next_date.strftime("%Y-%m-%d")
                    result["days_to_earnings"] = (next_date - now).days
        except Exception:
            pass

        return ticker, result
    except Exception:
        return ticker, {}


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_earnings_data(tickers: tuple) -> pd.DataFrame:
    """Fetch earnings data for all tickers in universe. Cached 6 hours."""
    rows = {}

    def _fetch(ticker: str) -> tuple:
        return _fetch_earnings_one(ticker)

    with ThreadPoolExecutor(max_workers=20) as exe:
        futures = {exe.submit(_fetch, t): t for t in tickers}
        for f in as_completed(futures):
            ticker, data = f.result()
            if data:
                flat = {}
                for k, v in data.items():
                    if isinstance(v, list):
                        continue
                    flat[k] = v
                rows[ticker] = flat

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index.name = "ticker"
    return df


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_earnings_details(tickers: tuple) -> Dict[str, dict]:
    """Fetch detailed earnings data (including lists) for all tickers. Cached 6 hours."""
    details = {}

    def _fetch(ticker: str) -> tuple:
        return _fetch_earnings_one(ticker)

    with ThreadPoolExecutor(max_workers=20) as exe:
        futures = {exe.submit(_fetch, t): t for t in tickers}
        for f in as_completed(futures):
            ticker, data = f.result()
            if data:
                details[ticker] = data

    return details


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_price_history_extended(tickers: tuple, period: str = "1y") -> Dict[str, pd.DataFrame]:
    """Fetch 1-year daily OHLCV for all tickers. Cached 1 hour."""
    results: Dict[str, pd.DataFrame] = {}

    def _fetch(ticker: str) -> tuple:
        try:
            t = yf.Ticker(ticker)
            h = t.history(period=period, interval="1d", auto_adjust=True)
            if h.empty:
                return ticker, None
            return ticker, h[["Open", "High", "Low", "Close", "Volume"]]
        except Exception:
            return ticker, None

    with ThreadPoolExecutor(max_workers=20) as exe:
        futures = {exe.submit(_fetch, t): t for t in tickers}
        for f in as_completed(futures):
            ticker, df = f.result()
            if df is not None and len(df) >= 20:
                results[ticker] = df

    return results
