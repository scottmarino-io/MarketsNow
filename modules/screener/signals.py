"""Technical indicator calculation and composite scoring."""

import numpy as np
import pandas as pd
from typing import Dict, Optional


def _sma(series: pd.Series, period: int) -> float:
    if len(series) < period:
        return float("nan")
    return series.iloc[-period:].mean()

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def _rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return float("nan")
    delta = close.diff().dropna()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs    = gain / loss.replace(0, float("nan"))
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])

def _macd(close: pd.Series, fast=12, slow=26, signal=9) -> tuple:
    if len(close) < slow + signal:
        return float("nan"), float("nan"), float("nan")
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd     = ema_fast - ema_slow
    sig      = _ema(macd, signal)
    hist     = macd - sig
    return float(macd.iloc[-1]), float(sig.iloc[-1]), float(hist.iloc[-1])

def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period=14) -> float:
    if len(close) < period + 1:
        return float("nan")
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return float(tr.iloc[-period:].mean())

def _pct_return(close: pd.Series, days: int) -> float:
    if len(close) < days + 1:
        return float("nan")
    return float((close.iloc[-1] / close.iloc[-days - 1] - 1) * 100)

def _vol_ratio(volume: pd.Series, days=20) -> float:
    if len(volume) < days + 1:
        return float("nan")
    avg = volume.iloc[-days - 1:-1].mean()
    if avg == 0:
        return float("nan")
    return float(volume.iloc[-1] / avg)

def _52wk_position(close: pd.Series, high_52: float, low_52: float) -> float:
    rng = high_52 - low_52
    if rng == 0:
        return 50.0
    return float((close.iloc[-1] - low_52) / rng * 100)


def calc_technicals(price_history: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Compute all technical indicators for every ticker."""
    rows = []
    for ticker, df in price_history.items():
        if df is None or len(df) < 30:
            continue
        c = df["Close"]
        h = df["High"]
        l = df["Low"]
        v = df["Volume"]

        sma20  = _sma(c, 20)
        sma50  = _sma(c, 50)
        sma200 = _sma(c, 200) if len(c) >= 200 else float("nan")
        price  = float(c.iloc[-1])

        macd_v, macd_s, macd_h = _macd(c)
        rsi14  = _rsi(c, 14)
        atr14  = _atr(h, l, c, 14)

        rows.append({
            "ticker":      ticker,
            "price":       price,
            "sma20":       sma20,
            "sma50":       sma50,
            "sma200":      sma200,
            "vs_sma20":    (price - sma20)  / sma20  * 100 if not np.isnan(sma20)  else float("nan"),
            "vs_sma50":    (price - sma50)  / sma50  * 100 if not np.isnan(sma50)  else float("nan"),
            "vs_sma200":   (price - sma200) / sma200 * 100 if not np.isnan(sma200) else float("nan"),
            "rsi14":       rsi14,
            "macd":        macd_v,
            "macd_signal": macd_s,
            "macd_hist":   macd_h,
            "atr14":       atr14,
            "atr_pct":     atr14 / price * 100 if not np.isnan(atr14) else float("nan"),
            "ret_1d":      _pct_return(c, 1),
            "ret_1w":      _pct_return(c, 5),
            "ret_1m":      _pct_return(c, 21),
            "ret_3m":      _pct_return(c, 63),
            "vol_ratio":   _vol_ratio(v, 20),
            "pos_52wk":    _52wk_position(c, float(h.max()), float(l.min())),
        })

    df = pd.DataFrame(rows).set_index("ticker")
    return df


def score_technical(row: pd.Series) -> int:
    """Technical score: -7 to +7"""
    score = 0
    def _s(val, bullish: bool):
        nonlocal score
        if pd.notna(val):
            score += 1 if bullish else -1

    _s(row.get("vs_sma20"),  row.get("vs_sma20",  0) > 0)
    _s(row.get("vs_sma50"),  row.get("vs_sma50",  0) > 0)
    _s(row.get("vs_sma200"), row.get("vs_sma200", 0) > 0)
    _s(row.get("rsi14"),     row.get("rsi14", 50) > 50)
    _s(row.get("macd_hist"), row.get("macd_hist", 0) > 0)
    _s(row.get("ret_1m"),    row.get("ret_1m",   0) > 0)

    vr = row.get("vol_ratio")
    if pd.notna(vr) and vr >= 1.5:
        score += 1

    return score


def score_fundamental(row: pd.Series) -> int:
    """Fundamental score: -4 to +4"""
    score = 0

    fpe = row.get("forwardPE")
    if pd.notna(fpe) and fpe > 0:
        score += 1 if fpe < 25 else (-1 if fpe > 40 else 0)

    eg = row.get("earningsGrowth")
    if pd.notna(eg):
        score += 1 if eg > 0.10 else (-1 if eg < -0.10 else 0)

    rg = row.get("revenueGrowth")
    if pd.notna(rg):
        score += 1 if rg > 0.08 else 0

    rec = row.get("recommendationMean")
    if pd.notna(rec):
        score += 1 if rec < 2.2 else (-1 if rec > 3.5 else 0)

    return score


def tag_strategies(row: pd.Series) -> list:
    """Return list of strategy tag strings for a ticker row."""
    tags = []

    ts = row.get("tech_score", 0)
    fs = row.get("fund_score", 0)
    r1m    = row.get("ret_1m",   0) or 0
    r1w    = row.get("ret_1w",   0) or 0
    rsi    = row.get("rsi14",   50) or 50
    pos52  = row.get("pos_52wk", 50) or 50
    vr     = row.get("vol_ratio", 1) or 1
    short  = row.get("shortPercentOfFloat", 0) or 0
    fpe    = row.get("forwardPE", 99) or 99
    macd_h = row.get("macd_hist", 0) or 0
    vs200  = row.get("vs_sma200", 0) or 0

    if ts >= 4 and r1m > 3:
        tags.append("MOMENTUM")
    if pos52 >= 90 and vr >= 1.3:
        tags.append("BREAKOUT")
    if short > 0.08 and r1w > 2:
        tags.append("SQUEEZE")
    if rsi < 35 and vs200 > -20:
        tags.append("OVERSOLD")
    if fpe > 0 and fpe < 20 and ts >= 2:
        tags.append("VALUE+MO")
    if ts >= 3 and macd_h > 0 and r1m > 0:
        tags.append("TRENDING")

    return tags


def build_screener_df(
    price_history: Dict[str, pd.DataFrame],
    fundamentals:  pd.DataFrame,
    rt_snapshots:  pd.DataFrame,
    short_interest: pd.DataFrame,
    flow_data:      Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Merge all data sources and compute composite scores."""
    tech = calc_technicals(price_history)

    df = tech.join(fundamentals, how="left")

    if not rt_snapshots.empty:
        for col in ["rt_price", "rt_volume", "rt_chg_pct"]:
            if col in rt_snapshots.columns:
                df[col] = rt_snapshots[col]
        mask = df["rt_price"].notna()
        df.loc[mask, "price"] = df.loc[mask, "rt_price"]

    if not short_interest.empty:
        for col in short_interest.columns:
            if col not in df.columns:
                df[col] = short_interest[col]

    if flow_data is not None and not flow_data.empty:
        flow_cols = ["pc_vol", "pc_oi", "max_pain", "flow_score", "call_vol", "put_vol"]
        for col in flow_cols:
            if col in flow_data.columns:
                df[col] = flow_data[col]

    df["tech_score"] = df.apply(score_technical,   axis=1)
    df["fund_score"] = df.apply(score_fundamental, axis=1)
    df["flow_score"] = df.get("flow_score", pd.Series(0, index=df.index)).fillna(0).astype(int)
    df["composite"]  = df["tech_score"] + df["fund_score"] + df["flow_score"]

    df["tags"] = df.apply(lambda r: " ".join(tag_strategies(r)), axis=1)

    tp = df.get("targetMeanPrice")
    if tp is not None:
        df["vs_target"] = ((tp - df["price"]) / df["price"] * 100).round(1)

    df = df.reset_index()
    df = df.sort_values("composite", ascending=False)
    df["rank"] = range(1, len(df) + 1)

    return df
