"""Market Monitor — Streamlit adaptation of the terminal Bloomberg-style dashboard."""

import os
from collections import deque
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from modules.market.breadth import BreadthFetcher, BreadthSnapshot
from modules.market.uw_fetchers import fetch_market_tide_eod, fetch_gex_levels, fetch_market_tide
from modules.market.uw_display import render_tide_card, render_gamma_card, render_tide_history_chart
from modules.shared.theme import inject_css

load_dotenv()

# Optional — no sidebar prompt for this one (unlike Massive, which the page
# requires and hard-stops without). UW is purely additive here.
UNUSUAL_WHALES_KEY = os.environ.get("UNUSUAL_WHALES_API_KEY", "")

st.set_page_config(
    page_title="Market Monitor · MarketsNow",
    page_icon="📺",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_css()

DEFAULT_TICKERS = ["SPY", "QQQ", "IWM", "DIA", "QQQI"]
SPARK_CHARS     = "▁▂▃▄▅▆▇█"


def sparkline(values: list) -> str:
    if not values:
        return "─" * 10
    mn, mx = min(values), max(values)
    if mx == mn:
        return "─" * len(values)
    n = len(SPARK_CHARS) - 1
    return "".join(SPARK_CHARS[round((v - mn) / (mx - mn) * n)] for v in values)


# ── helpers ───────────────────────────────────────────────────────────────────

@st.cache_resource
def get_client():
    from massive import RESTClient
    key = os.getenv("MASSIVE_API_KEY")
    if not key:
        st.error("MASSIVE_API_KEY not set. Add it to .env or export it.")
        st.stop()
    return RESTClient(api_key=key)


@st.cache_resource
def get_breadth_fetcher() -> BreadthFetcher:
    bf = BreadthFetcher(get_client())
    bf.load_exchange_tickers()
    return bf


@st.cache_data(ttl=30)
def fetch_breadth_data(_fetcher_id: int) -> Optional[BreadthSnapshot]:
    try:
        return get_breadth_fetcher().fetch()
    except Exception:
        return None


@st.cache_data(ttl=60)
def fetch_snapshot(ticker: str) -> dict:
    client = get_client()
    try:
        snap = client.get_snapshot_ticker("stocks", ticker)
        d, p = snap.day, snap.prev_day
        session = getattr(snap, "session", None)
        return {
            "price":       d.close or d.vwap or 0,
            "open":        d.open,
            "high":        d.high,
            "low":         d.low,
            "volume":      d.volume,
            "vwap":        d.vwap,
            "prev_close":  p.close if p else None,
            "session":     session,
        }
    except Exception:
        return {}


@st.cache_data(ttl=60)
def fetch_indicators(ticker: str) -> dict:
    client = get_client()
    result = {}
    try:
        r = client.get_sma(ticker, timespan="day", window=20, series_type="close", order="desc", limit=1)
        if r.values: result["sma20"] = r.values[0].value
    except Exception: pass
    try:
        r = client.get_sma(ticker, timespan="day", window=50, series_type="close", order="desc", limit=1)
        if r.values: result["sma50"] = r.values[0].value
    except Exception: pass
    try:
        r = client.get_sma(ticker, timespan="day", window=200, series_type="close", order="desc", limit=1)
        if r.values: result["sma200"] = r.values[0].value
    except Exception: pass
    try:
        r = client.get_rsi(ticker, timespan="day", window=14, series_type="close", order="desc", limit=1)
        if r.values: result["rsi14"] = r.values[0].value
    except Exception: pass
    try:
        r = client.get_macd(ticker, timespan="day", short_window=12, long_window=26,
                            signal_window=9, series_type="close", order="desc", limit=1)
        if r.values:
            v = r.values[0]
            result["macd"] = v.value
            result["macd_sig"] = v.signal
            result["macd_hist"] = v.histogram
    except Exception: pass
    return result


@st.cache_data(ttl=3600)
def fetch_history(ticker: str, days: int = 20) -> list:
    client = get_client()
    try:
        bars = list(client.list_aggs(
            ticker=ticker, multiplier=1, timespan="day",
            from_=(date.today() - timedelta(days=days * 2)).isoformat(),
            to=date.today().isoformat(),
            adjusted=True, sort="asc", limit=days + 5,
        ))
        return [b.close for b in bars if b.close][-days:]
    except Exception:
        return []


@st.cache_data(ttl=3600)
def fetch_avg_volume(ticker: str) -> Optional[float]:
    client = get_client()
    try:
        bars = list(client.list_aggs(
            ticker=ticker, multiplier=1, timespan="day",
            from_=(date.today() - timedelta(days=45)).isoformat(),
            to=date.today().isoformat(),
            adjusted=True, sort="asc", limit=35,
        ))
        vols = [b.volume for b in bars if b.volume]
        return sum(vols) / len(vols) if vols else None
    except Exception:
        return None


def trend_score(price, indic):
    score = 0
    if price and indic.get("sma20"):
        score += 1 if price > indic["sma20"] else -1
    if price and indic.get("sma50"):
        score += 1 if price > indic["sma50"] else -1
    if price and indic.get("sma200"):
        score += 1 if price > indic["sma200"] else -1
    rsi = indic.get("rsi14")
    if rsi:
        score += 1 if rsi > 50 else -1
    m = indic.get("macd"); s = indic.get("macd_sig")
    if m is not None and s is not None:
        score += 1 if m > s else -1
    return score


# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("📺 Market Monitor")
    st.caption("Massive API · 15-min delayed")
    st.divider()

    ticker_input = st.text_input("Tickers (comma-separated)", value=", ".join(DEFAULT_TICKERS))
    tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]

    st.divider()
    if st.button("🔄  Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Auto-cached 1 min")


# ── data fetch ────────────────────────────────────────────────────────────────

all_data = {}
for t in tickers:
    snap = fetch_snapshot(t)
    indic = fetch_indicators(t)
    hist = fetch_history(t)
    avg_vol = fetch_avg_volume(t)
    price = snap.get("price", 0)
    prev = snap.get("prev_close")
    chg = price - prev if price and prev else None
    chg_pct = (chg / prev * 100) if chg and prev else None

    all_data[t] = {
        "snap": snap, "indic": indic, "hist": hist, "avg_vol": avg_vol,
        "price": price, "prev": prev, "chg": chg, "chg_pct": chg_pct,
        "trend": trend_score(price, indic),
    }

# ── header ────────────────────────────────────────────────────────────────────

st.markdown("## 📺 Market Monitor")
st.caption(f"Tracking {len(tickers)} tickers · 15-min delayed via Massive API")
st.divider()

# ── price cards ──────────────────────────────────────────────────────────────

cols = st.columns(min(len(tickers), 5))
for i, t in enumerate(tickers):
    d = all_data[t]
    col = cols[i % len(cols)]
    chg_str = f"{d['chg']:+.2f}" if d['chg'] is not None else "—"
    pct_str = f"{d['chg_pct']:+.2f}%" if d['chg_pct'] is not None else ""
    color = "#a6e3a1" if (d['chg'] or 0) >= 0 else "#f38ba8"
    spark = sparkline(d["hist"])

    trend = d["trend"]
    trend_labels = {5:"STRONG UP", 4:"STRONG UP", 3:"UP", 2:"UP", 1:"SLIGHT UP",
                    0:"NEUTRAL", -1:"SLIGHT DN", -2:"DN", -3:"DN", -4:"STRONG DN", -5:"STRONG DN"}
    trend_colors = {k: "#a6e3a1" if k > 0 else "#f38ba8" if k < 0 else "#f9e2af"
                    for k in range(-5, 6)}
    tl = trend_labels.get(trend, "—")
    tc = trend_colors.get(trend, "#f9e2af")

    with col:
        st.markdown(
            f"<div class='metric-card' style='border-top:3px solid {color}'>"
            f"<div style='font-size:1.1rem;font-weight:700;color:#cdd6f4'>{t}</div>"
            f"<div style='font-size:1.6rem;font-weight:700;color:{color}'>${d['price']:.2f}</div>"
            f"<div style='font-size:.85rem;color:{color}'>{chg_str}  {pct_str}</div>"
            f"<div style='font-family:monospace;font-size:1rem;letter-spacing:1px;color:#89b4fa;margin:4px 0'>{spark}</div>"
            f"<div style='font-size:.72rem;color:{tc};font-weight:600'>{tl} ({trend:+d}/5)</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

st.divider()

# ── detail table ─────────────────────────────────────────────────────────────

st.subheader("Detailed View")

rows = []
for t in tickers:
    d = all_data[t]
    snap = d["snap"]
    indic = d["indic"]
    rsi = indic.get("rsi14")
    vol = snap.get("volume")
    avg_vol = d["avg_vol"]
    vol_pct = (vol / avg_vol * 100) if vol and avg_vol and avg_vol > 0 else None

    rows.append({
        "Ticker": t,
        "Price": f"${d['price']:.2f}" if d['price'] else "—",
        "Chg $": f"{d['chg']:+.2f}" if d['chg'] is not None else "—",
        "Chg %": f"{d['chg_pct']:+.2f}%" if d['chg_pct'] is not None else "—",
        "Open": f"${snap.get('open', 0):.2f}" if snap.get('open') else "—",
        "High": f"${snap.get('high', 0):.2f}" if snap.get('high') else "—",
        "Low": f"${snap.get('low', 0):.2f}" if snap.get('low') else "—",
        "VWAP": f"${snap.get('vwap', 0):.2f}" if snap.get('vwap') else "—",
        "Volume": f"{vol:,.0f}" if vol else "—",
        "Vol/Avg": f"{vol_pct:.0f}%" if vol_pct else "—",
        "SMA20": f"${indic.get('sma20', 0):.2f}" if indic.get('sma20') else "—",
        "SMA50": f"${indic.get('sma50', 0):.2f}" if indic.get('sma50') else "—",
        "SMA200": f"${indic.get('sma200', 0):.2f}" if indic.get('sma200') else "—",
        "RSI-14": f"{rsi:.1f}" if rsi else "—",
        "MACD Hist": f"{indic.get('macd_hist', 0):+.3f}" if indic.get('macd_hist') is not None else "—",
        "Trend": f"{d['trend']:+d}/5",
    })

detail_df = pd.DataFrame(rows)
st.dataframe(detail_df, use_container_width=True, hide_index=True)

st.divider()

# ── market breadth panel ─────────────────────────────────────────────────────

st.subheader("Market Breadth")

if "monitor_breadth_history" not in st.session_state:
    st.session_state.monitor_breadth_history = deque(maxlen=60)

bsnap = fetch_breadth_data(id(get_breadth_fetcher()))
if bsnap:
    st.session_state.monitor_breadth_history.append(bsnap)

def _tick_color(val: int) -> str:
    if val >= 1000:  return "#a6e3a1"
    if val >= 500:   return "#94e2d5"
    if val >= -499:  return "#f9e2af"
    if val >= -999:  return "#fab387"
    return "#f38ba8"

if bsnap:
    bc1, bc2, bc3, bc4 = st.columns(4)
    for col, label, tick_val, pct_val in [
        (bc1, "All-Market TICK", bsnap.tick_all,  bsnap.breadth_all_pct),
        (bc2, "NYSE TICK",       bsnap.tick_nyse, bsnap.breadth_nyse_pct),
        (bc3, "Nasdaq TICK",     bsnap.tick_nq,   bsnap.breadth_nq_pct),
    ]:
        color = _tick_color(tick_val)
        pct_str = f"{pct_val:.1f}%" if pct_val is not None else "N/A"
        sign = "+" if tick_val >= 0 else ""
        col.markdown(
            f"<div class='metric-card'>"
            f"<div class='metric-label'>{label}</div>"
            f"<div class='metric-value' style='color:{color}'>{sign}{tick_val:,}</div>"
            f"<div class='metric-sub'>{pct_str} advancing</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    total = bsnap.up_all + bsnap.dn_all
    bc4.markdown(
        f"<div class='metric-card'>"
        f"<div class='metric-label'>A / D Ratio</div>"
        f"<div class='metric-value'>{bsnap.up_all:,} / {bsnap.dn_all:,}</div>"
        f"<div class='metric-sub'>of {total:,} stocks  ·  age {bsnap.age_secs}s</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    history = list(st.session_state.monitor_breadth_history)
    if len(history) >= 2:
        with st.expander("TICK History  (last 60 readings)", expanded=False):
            fig_tick = go.Figure()
            x = list(range(len(history)))
            for series_data, label, color in [
                ([s.tick_all  for s in history], "All-Market", "#89b4fa"),
                ([s.tick_nyse for s in history], "NYSE",       "#a6e3a1"),
                ([s.tick_nq   for s in history], "Nasdaq",     "#cba6f7"),
            ]:
                fig_tick.add_trace(go.Scatter(
                    x=x, y=series_data, mode="lines", name=label,
                    line=dict(color=color, width=2),
                ))
            for y_val, lbl in [(1000, "+1000 extreme"), (-1000, "-1000 extreme"), (0, "zero")]:
                fig_tick.add_hline(
                    y=y_val, line_dash="dot",
                    line_color="#585b70" if y_val == 0 else "#f38ba8" if y_val < 0 else "#a6e3a1",
                    line_width=1, annotation_text=lbl, annotation_position="right",
                    annotation_font_color="#6c7086",
                )
            fig_tick.update_layout(
                plot_bgcolor="#1e1e2e", paper_bgcolor="#1e1e2e",
                font=dict(color="#cdd6f4"), height=260,
                margin=dict(l=40, r=80, t=10, b=30),
                legend=dict(bgcolor="#313244", borderwidth=0),
                xaxis=dict(showticklabels=False, gridcolor="#313244"),
                yaxis=dict(gridcolor="#313244"),
            )
            st.plotly_chart(fig_tick, use_container_width=True)
else:
    st.info("Breadth data loading... Set MASSIVE_API_KEY in .env to enable.")

# ── unusual whales: gamma exposure + market tide (SPY proxy) ────────────────

if UNUSUAL_WHALES_KEY:
    st.subheader("Market Tide & Gamma Exposure (SPY)")
    try:
        spy_price = all_data.get("SPY", {}).get("price") or None

        gamma_vol = fetch_gex_levels(UNUSUAL_WHALES_KEY, "SPY", source="vol")
        gamma_oi = fetch_gex_levels(UNUSUAL_WHALES_KEY, "SPY", source="oi")
        tide = fetch_market_tide_eod(UNUSUAL_WHALES_KEY)

        uw_c1, uw_c2 = st.columns(2)
        render_gamma_card(gamma_vol, levels_oi=gamma_oi, spot_price=spy_price, col=uw_c1)
        render_tide_card(tide, col=uw_c2)

        with st.expander("Tide History (today)", expanded=False):
            tide_df = fetch_market_tide(UNUSUAL_WHALES_KEY)
            if not tide_df.empty:
                st.plotly_chart(
                    render_tide_history_chart(tide_df),
                    use_container_width=True, config={"displayModeBar": False},
                )
            else:
                st.caption("No tide history available yet today.")
    except Exception as e:
        st.warning(f"Could not load Unusual Whales data: {e}")

# ── intraday charts ──────────────────────────────────────────────────────────

st.divider()
st.subheader("Intraday Price Charts")

chart_cols = st.columns(min(len(tickers), 3))
for i, t in enumerate(tickers):
    with chart_cols[i % len(chart_cols)]:
        d = all_data[t]
        hist_vals = d["hist"]
        if hist_vals:
            color = "#a6e3a1" if (d['chg'] or 0) >= 0 else "#f38ba8"
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                y=hist_vals, mode="lines",
                line=dict(color=color, width=2),
                fill="tozeroy", fillcolor=f"rgba{tuple(int(color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)) + (0.1,)}",
            ))
            fig.update_layout(
                title=dict(text=f"<b>{t}</b> — ${d['price']:.2f}", font=dict(size=13)),
                height=200, margin=dict(t=35, b=20, l=40, r=10),
                plot_bgcolor="#1e1e2e", paper_bgcolor="#1e1e2e",
                font=dict(color="#cdd6f4"),
                xaxis=dict(showticklabels=False, gridcolor="#313244"),
                yaxis=dict(gridcolor="#313244"),
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.markdown(f"**{t}** — No history data")
