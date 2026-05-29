"""Options chain analysis for flow-based signals via Massive API."""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import streamlit as st

DTE_MAX          = 60
DTE_MAX_PAIN     = 14
VOL_OI_MIN_RATIO = 1.5
VOL_MIN          = 200
OI_MIN           = 20


def _get_client():
    try:
        from massive import RESTClient
        key = os.getenv("MASSIVE_API_KEY")
        return RESTClient(api_key=key) if key else None
    except ImportError:
        return None


def _dte(exp_str: str) -> int:
    try:
        return (date.fromisoformat(exp_str) - date.today()).days
    except Exception:
        return 9999


def _max_pain(strikes_calls: Dict[float, int], strikes_puts: Dict[float, int]) -> Optional[float]:
    all_strikes = sorted(set(list(strikes_calls.keys()) + list(strikes_puts.keys())))
    if not all_strikes:
        return None

    min_pain    = float("inf")
    pain_strike = None

    for s in all_strikes:
        call_pain = sum(
            max(0.0, s - k) * oi
            for k, oi in strikes_calls.items()
            if k < s
        )
        put_pain = sum(
            max(0.0, k - s) * oi
            for k, oi in strikes_puts.items()
            if k > s
        )
        total = (call_pain + put_pain) * 100
        if total < min_pain:
            min_pain    = total
            pain_strike = s

    return pain_strike


def fetch_chain_metrics(ticker: str, client=None) -> dict:
    """Fetch options chain snapshot for one ticker and compute flow metrics."""
    if client is None:
        client = _get_client()
    if client is None:
        return {}

    try:
        today   = date.today()
        exp_max = (today + timedelta(days=DTE_MAX)).isoformat()

        chain_iter = client.list_snapshot_options_chain(ticker, params={
            "expiration_date_gte": today.isoformat(),
            "expiration_date_lte": exp_max,
        })

        call_vol = put_vol = call_oi = put_oi = 0
        unusual: List[dict] = []
        mp_calls: Dict[float, int] = {}
        mp_puts:  Dict[float, int] = {}
        n = 0

        for c in chain_iter:
            n += 1
            ct     = c.details.contract_type
            strike = c.details.strike_price
            exp    = c.details.expiration_date
            dte    = _dte(exp)
            oi     = c.open_interest or 0
            vol    = (c.day.volume if c.day else None) or 0

            if ct == "call":
                call_vol += vol
                call_oi  += oi
            else:
                put_vol  += vol
                put_oi   += oi

            if oi >= OI_MIN and vol >= VOL_MIN:
                ratio = vol / oi
                if ratio >= VOL_OI_MIN_RATIO:
                    unusual.append({
                        "contract": c.details.ticker,
                        "type":     ct,
                        "strike":   strike,
                        "exp":      exp,
                        "dte":      dte,
                        "vol":      vol,
                        "oi":       oi,
                        "ratio":    round(ratio, 2),
                    })

            if dte <= DTE_MAX_PAIN:
                if ct == "call":
                    mp_calls[strike] = mp_calls.get(strike, 0) + oi
                else:
                    mp_puts[strike]  = mp_puts.get(strike, 0) + oi

        if n == 0:
            return {}

        pc_vol = (put_vol / call_vol) if call_vol > 0 else float("nan")
        pc_oi  = (put_oi  / call_oi)  if call_oi  > 0 else float("nan")

        unusual.sort(key=lambda x: x["ratio"], reverse=True)
        top_unusual = unusual[:5]

        max_pain_strike = _max_pain(mp_calls, mp_puts)

        flow_score = 0

        if not np.isnan(pc_vol):
            if   pc_vol < 0.50:  flow_score += 2
            elif pc_vol < 0.75:  flow_score += 1
            elif pc_vol > 2.00:  flow_score -= 2
            elif pc_vol > 1.25:  flow_score -= 1

        if top_unusual:
            u_calls = sum(1 for u in top_unusual if u["type"] == "call")
            u_puts  = sum(1 for u in top_unusual if u["type"] == "put")
            if u_calls > u_puts:
                flow_score += 1
            elif u_puts > u_calls:
                flow_score -= 1

        return {
            "call_vol":    call_vol,
            "put_vol":     put_vol,
            "call_oi":     call_oi,
            "put_oi":      put_oi,
            "pc_vol":      round(pc_vol, 2) if not np.isnan(pc_vol) else float("nan"),
            "pc_oi":       round(pc_oi,  2) if not np.isnan(pc_oi)  else float("nan"),
            "max_pain":    max_pain_strike,
            "top_unusual": top_unusual,
            "flow_score":  max(-3, min(3, flow_score)),
            "n_contracts": n,
        }

    except Exception:
        return {}


@st.cache_data(ttl=900, show_spinner=False)
def fetch_flow_bulk(tickers: tuple) -> pd.DataFrame:
    """Parallel options flow fetch. Cached 15 min."""
    client = _get_client()
    if client is None:
        return pd.DataFrame()

    rows: dict = {}
    unusual_map: dict = {}

    def _fetch(ticker: str):
        metrics = fetch_chain_metrics(ticker, client=client)
        return ticker, metrics

    with ThreadPoolExecutor(max_workers=8) as exe:
        futures = {exe.submit(_fetch, t): t for t in tickers}
        for f in as_completed(futures):
            ticker, metrics = f.result()
            if metrics:
                unusual_map[ticker] = metrics.pop("top_unusual", [])
                rows[ticker] = metrics

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index.name = "ticker"

    if hasattr(st, "session_state"):
        st.session_state["_unusual_map"] = unusual_map

    return df


def fmt_pc(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    if   v < 0.50: color = "#a6e3a1"
    elif v < 0.75: color = "#94e2d5"
    elif v < 1.25: color = "#f9e2af"
    elif v < 2.00: color = "#fab387"
    else:          color = "#f38ba8"
    return f"<span style='color:{color}'>{v:.2f}</span>"


def fmt_flow_score(score) -> str:
    if score is None or (isinstance(score, float) and np.isnan(score)):
        return "—"
    score = int(score)
    pct   = (score + 3) / 6 * 100
    color = "#a6e3a1" if score > 0 else "#f38ba8" if score < 0 else "#f9e2af"
    return (
        f"<div style='background:#313244;border-radius:4px;height:14px;width:100%'>"
        f"<div style='background:{color};border-radius:4px;height:14px;width:{pct:.0f}%'></div>"
        f"</div>"
        f"<span style='font-size:.72rem;color:{color}'>{score:+d}</span>"
    )


def render_unusual_table(unusual: list) -> str:
    if not unusual:
        return "<span style='color:#6c7086;font-size:.8rem'>No unusual activity detected</span>"
    rows_html = ""
    for u in unusual:
        ct_color = "#a6e3a1" if u["type"] == "call" else "#f38ba8"
        rows_html += (
            f"<tr>"
            f"<td style='color:{ct_color};font-weight:600'>{u['type'].upper()}</td>"
            f"<td>${u['strike']:.0f}</td>"
            f"<td>{u['exp']} ({u['dte']}d)</td>"
            f"<td style='color:#cdd6f4'>{u['vol']:,}</td>"
            f"<td style='color:#6c7086'>{u['oi']:,}</td>"
            f"<td style='color:{ct_color};font-weight:600'>{u['ratio']:.1f}×</td>"
            f"</tr>"
        )
    return (
        "<table style='width:100%;font-size:.78rem;border-collapse:collapse'>"
        "<thead><tr style='color:#a6adc8;border-bottom:1px solid #313244'>"
        "<th align='left'>Type</th><th align='left'>Strike</th>"
        "<th align='left'>Expiry (DTE)</th>"
        "<th align='left'>Volume</th><th align='left'>OI</th>"
        "<th align='left'>Vol/OI</th></tr></thead>"
        f"<tbody>{rows_html}</tbody></table>"
    )
