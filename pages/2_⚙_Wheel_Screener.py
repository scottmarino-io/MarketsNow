"""Wheel Strategy Options Screener page."""

import os
from collections import deque
from datetime import date, timedelta
from math import sqrt
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from modules.market.breadth import BreadthFetcher, BreadthSnapshot
from modules.shared.theme import inject_css

load_dotenv()

st.set_page_config(
    page_title="Wheel Screener · MarketsNow",
    page_icon="⚙",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_css()


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


@st.cache_data(ttl=120)
def fetch_snapshot(ticker: str) -> dict:
    client = get_client()
    try:
        snap = client.get_snapshot_ticker("stocks", ticker)
        d, p = snap.day, snap.prev_day
        return {
            "price":      d.close or d.vwap or 0,
            "open":       d.open,
            "high":       d.high,
            "low":        d.low,
            "volume":     d.volume,
            "vwap":       d.vwap,
            "prev_close": p.close if p else None,
        }
    except Exception as e:
        st.warning(f"Snapshot error: {e}")
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


@st.cache_data(ttl=120)
def fetch_atr(ticker: str, period: int = 14) -> Optional[float]:
    client = get_client()
    try:
        bars = list(client.list_aggs(
            ticker=ticker, multiplier=1, timespan="day",
            from_=(date.today() - timedelta(days=period * 3)).isoformat(),
            to=date.today().isoformat(),
            adjusted=True, sort="asc", limit=period + 5,
        ))
        if len(bars) < 2:
            return None
        true_ranges = []
        for i in range(1, len(bars)):
            h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
            true_ranges.append(max(h - l, abs(h - pc), abs(l - pc)))
        return sum(true_ranges[-period:]) / min(period, len(true_ranges))
    except Exception:
        return None


@st.cache_data(ttl=120)
def fetch_options_chain(ticker: str, contract_type: str,
                         dte_min: int, dte_max: int) -> pd.DataFrame:
    client = get_client()
    today     = date.today()
    exp_min   = (today + timedelta(days=dte_min)).isoformat()
    exp_max   = (today + timedelta(days=dte_max)).isoformat()

    try:
        raw = list(client.list_snapshot_options_chain(
            ticker,
            params={
                "contract_type":        contract_type,
                "expiration_date.gte":  exp_min,
                "expiration_date.lte":  exp_max,
            },
        ))
    except Exception as e:
        st.warning(f"Options chain error: {e}")
        return pd.DataFrame()

    rows = []
    for o in raw:
        d   = o.details
        g   = o.greeks
        day = o.day
        if not g or g.delta is None:
            continue
        exp_date = date.fromisoformat(d.expiration_date)
        dte      = (exp_date - today).days
        premium  = day.close or 0

        rows.append({
            "ticker":       d.ticker,
            "exp_date":     d.expiration_date,
            "dte":          dte,
            "strike":       d.strike_price,
            "type":         d.contract_type.upper(),
            "delta":        round(g.delta, 3),
            "gamma":        round(g.gamma, 4) if g.gamma else None,
            "theta":        round(g.theta, 4) if g.theta else None,
            "vega":         round(g.vega,  4) if g.vega  else None,
            "iv":           round(o.implied_volatility * 100, 1) if o.implied_volatility else None,
            "premium":      round(premium, 2),
            "oi":           o.open_interest or 0,
            "volume":       day.volume or 0,
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.sort_values(["exp_date", "strike"]).reset_index(drop=True)
    return df


def enrich_chain(df: pd.DataFrame, spot: float,
                  dte_sweet: tuple, delta_sweet: tuple) -> pd.DataFrame:
    if df.empty or spot == 0:
        return df

    df = df.copy()
    df["distance_pct"] = ((df["strike"] - spot) / spot * 100).round(2)
    df["ann_yield_pct"] = (
        df.apply(lambda r: round((r["premium"] / r["strike"]) * (365 / r["dte"]) * 100, 1)
                 if r["dte"] > 0 and r["strike"] > 0 else 0, axis=1)
    )
    df["abs_delta"] = df["delta"].abs()
    df["exp_move"] = df.apply(
        lambda r: round(spot * (r["iv"] / 100) * sqrt(r["dte"] / 365), 2)
        if r["iv"] and r["dte"] > 0 else None, axis=1
    )
    df["exp_move_pct"] = df.apply(
        lambda r: round(r["exp_move"] / spot * 100, 1)
        if r["exp_move"] else None, axis=1
    )
    df["vs_move"] = df.apply(
        lambda r: round(abs(r["distance_pct"]) / r["exp_move_pct"] * 100, 0)
        if r["exp_move_pct"] else None, axis=1
    )

    d_lo, d_hi   = delta_sweet
    dte_lo, dte_hi = dte_sweet

    def score(r):
        s = 0
        if d_lo  <= r["abs_delta"] <= d_hi:   s += 2
        if dte_lo <= r["dte"]       <= dte_hi: s += 1
        if r["oi"]          >= 100:            s += 1
        if r["ann_yield_pct"] >= 10:           s += 1
        return s

    df["wheel_score"] = df.apply(score, axis=1)
    df["in_zone"]     = (
        (df["abs_delta"].between(d_lo, d_hi)) &
        (df["dte"].between(dte_lo, dte_hi))
    )

    return df.sort_values(["wheel_score", "abs_delta"], ascending=[False, True])


def trend_badge(indicators: dict, snap: dict) -> tuple:
    price = snap.get("price", 0)
    score = 0
    if price and indicators.get("sma20"):
        score += 1 if price > indicators["sma20"] else -1
    if price and indicators.get("sma50"):
        score += 1 if price > indicators["sma50"] else -1
    rsi = indicators.get("rsi14")
    if rsi: score += 1 if rsi > 50 else -1
    macd = indicators.get("macd"); sig = indicators.get("macd_sig")
    if macd is not None and sig is not None:
        score += 1 if macd > sig else -1

    labels = {3:"STRONG UP", 2:"UP", 1:"SLIGHT UP", 0:"NEUTRAL",
              -1:"SLIGHT DN", -2:"DOWN", -3:"STRONG DN"}
    colors = {3:"#a6e3a1", 2:"#a6e3a1", 1:"#a6e3a1",
              0:"#f9e2af", -1:"#f38ba8", -2:"#f38ba8", -3:"#f38ba8"}
    return labels.get(score, "NEUTRAL"), colors.get(score, "#f9e2af"), score


# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("⚙ Wheel Screener")
    st.caption("Massive API · 15-min delayed")
    st.divider()

    ticker = st.text_input("Ticker", value="QQQI").upper().strip()

    strategy = st.radio("Strategy leg", ["Cash-Secured Put", "Covered Call"],
                        index=0, horizontal=True)
    contract_type = "put" if strategy == "Cash-Secured Put" else "call"

    st.divider()
    st.subheader("Filters")
    dte_range = st.slider("DTE range", 1, 120, (21, 60), step=1,
                           help="Days to expiration")
    delta_range = st.slider("|Delta| range", 0.05, 0.60, (0.15, 0.35), step=0.01,
                            help="Absolute delta — wheel sweet spot is typically 0.20-0.30")
    min_oi = st.number_input("Min open interest", min_value=0, value=25, step=25)
    min_yield = st.number_input("Min annualized yield %", min_value=0.0, value=5.0, step=1.0)

    st.divider()
    st.subheader("Wheel zone")
    sweet_delta = st.slider("|Delta| sweet spot", 0.05, 0.50, (0.20, 0.30), step=0.01)
    sweet_dte   = st.slider("DTE sweet spot",     1,   90,  (30,  45),  step=1)

    st.divider()
    if st.button("🔄  Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Auto-cached 2 min · indicators 1 min")


# ── main ─────────────────────────────────────────────────────────────────────

if "breadth_history" not in st.session_state:
    st.session_state.breadth_history = deque(maxlen=60)

snap   = fetch_snapshot(ticker)
indic  = fetch_indicators(ticker)
atr    = fetch_atr(ticker)
trend_label, trend_color, trend_score = trend_badge(indic, snap)

price      = snap.get("price", 0)
prev_close = snap.get("prev_close")
day_chg    = price - prev_close if price and prev_close else None
day_chg_pct = (day_chg / prev_close * 100) if day_chg and prev_close else None

# ── header row ───────────────────────────────────────────────────────────────

col_title, col_price, col_chg, col_trend, col_rsi, col_macd, col_atr = st.columns([2, 1.5, 1.5, 1.5, 1.2, 1.5, 1.5])

with col_title:
    st.markdown(f"## {ticker}")
    st.caption(f"{strategy}  ·  DTE {dte_range[0]}–{dte_range[1]}")

with col_price:
    st.metric("Price", f"${price:.2f}" if price else "--")

with col_chg:
    chg_str = f"{'+'if day_chg and day_chg>0 else ''}{day_chg:.2f}" if day_chg else "--"
    pct_str = f"{'+'if day_chg_pct and day_chg_pct>0 else ''}{day_chg_pct:.2f}%" if day_chg_pct else ""
    st.metric("Day Change", chg_str, pct_str)

with col_trend:
    st.markdown(
        f"<div style='padding-top:4px'><span style='font-size:0.75rem;color:#a6adc8;text-transform:uppercase'>Trend</span><br>"
        f"<span style='font-size:1.2rem;font-weight:600;color:{trend_color}'>{trend_label}</span>"
        f"<span style='color:#6c7086;font-size:0.8rem'> ({trend_score:+d}/3)</span></div>",
        unsafe_allow_html=True,
    )

with col_rsi:
    rsi = indic.get("rsi14")
    rsi_color = "#f38ba8" if rsi and rsi > 70 else ("#a6e3a1" if rsi and rsi < 30 else "#f9e2af")
    st.markdown(
        f"<div style='padding-top:4px'><span style='font-size:0.75rem;color:#a6adc8;text-transform:uppercase'>RSI-14</span><br>"
        f"<span style='font-size:1.2rem;font-weight:600;color:{rsi_color}'>{rsi:.1f}</span></div>"
        if rsi else "<div style='padding-top:4px'>RSI: --</div>",
        unsafe_allow_html=True,
    )

with col_macd:
    mval  = indic.get("macd")
    mhist = indic.get("macd_hist")
    macd_color = "#a6e3a1" if mhist and mhist > 0 else "#f38ba8"
    macd_label = "Bullish ▲" if mhist and mhist > 0 else "Bearish ▼"
    st.markdown(
        f"<div style='padding-top:4px'><span style='font-size:0.75rem;color:#a6adc8;text-transform:uppercase'>MACD</span><br>"
        f"<span style='font-size:1.2rem;font-weight:600;color:{macd_color}'>{macd_label}</span>"
        f"<span style='color:#6c7086;font-size:0.8rem'> hist {mhist:+.3f}</span></div>"
        if mval is not None else "<div style='padding-top:4px'>MACD: --</div>",
        unsafe_allow_html=True,
    )

with col_atr:
    if atr is not None:
        atr_pct = atr / price * 100 if price else 0
        atr_color = "#f38ba8" if atr_pct > 3 else ("#f9e2af" if atr_pct > 1.5 else "#a6e3a1")
        st.markdown(
            f"<div style='padding-top:4px'>"
            f"<span style='font-size:0.75rem;color:#a6adc8;text-transform:uppercase'>ATR-14</span><br>"
            f"<span style='font-size:1.2rem;font-weight:600;color:{atr_color}'>${atr:.2f}</span>"
            f"<span style='color:#6c7086;font-size:0.8rem'> ({atr_pct:.1f}%/day)</span></div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown("<div style='padding-top:4px'>ATR: --</div>", unsafe_allow_html=True)

# ── market breadth ───────────────────────────────────────────────────────────

bsnap = fetch_breadth_data(id(get_breadth_fetcher()))
if bsnap:
    st.session_state.breadth_history.append(bsnap)

def _tick_color_hex(val: int) -> str:
    if val >= 1000:  return "#a6e3a1"
    if val >= 500:   return "#94e2d5"
    if val >= -499:  return "#f9e2af"
    if val >= -999:  return "#fab387"
    return "#f38ba8"

def _breadth_card(label: str, tick: int, breadth_pct: Optional[float]) -> str:
    color = _tick_color_hex(tick)
    pct   = f"{breadth_pct:.1f}%" if breadth_pct is not None else "N/A"
    sign  = "+" if tick >= 0 else ""
    return (
        f"<div class='metric-card'>"
        f"<div class='metric-label'>{label}</div>"
        f"<div class='metric-value' style='color:{color}'>{sign}{tick:,}</div>"
        f"<div class='metric-sub'>{pct} advancing</div>"
        f"</div>"
    )

with st.container():
    st.markdown(
        "<p style='font-size:0.8rem;color:#6c7086;margin-bottom:4px'>"
        "Market Breadth Proxy · advance/decline count · not true $TICK · 15-min delayed</p>",
        unsafe_allow_html=True,
    )
    bc1, bc2, bc3, bc4 = st.columns(4)
    if bsnap:
        bc1.markdown(_breadth_card("All-Market TICK", bsnap.tick_all,  bsnap.breadth_all_pct),  unsafe_allow_html=True)
        bc2.markdown(_breadth_card("NYSE TICK",       bsnap.tick_nyse, bsnap.breadth_nyse_pct), unsafe_allow_html=True)
        bc3.markdown(_breadth_card("Nasdaq TICK",     bsnap.tick_nq,   bsnap.breadth_nq_pct),   unsafe_allow_html=True)
        total = bsnap.up_all + bsnap.dn_all
        bc4.markdown(
            f"<div class='metric-card'>"
            f"<div class='metric-label'>A / D Ratio</div>"
            f"<div class='metric-value'>{bsnap.up_all:,} / {bsnap.dn_all:,}</div>"
            f"<div class='metric-sub'>of {total:,} stocks  ·  age {bsnap.age_secs}s</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        bc1.info("Breadth loading...")

    history = list(st.session_state.breadth_history)
    if len(history) >= 2:
        with st.expander("TICK History  (last 60 readings)", expanded=False):
            fig_tick = go.Figure()
            x = list(range(len(history)))
            for series, label, color in [
                ([s.tick_all  for s in history], "All-Market", "#89b4fa"),
                ([s.tick_nyse for s in history], "NYSE",       "#a6e3a1"),
                ([s.tick_nq   for s in history], "Nasdaq",     "#cba6f7"),
            ]:
                fig_tick.add_trace(go.Scatter(
                    x=x, y=series, mode="lines", name=label,
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

st.divider()

# ── options chain ─────────────────────────────────────────────────────────────

with st.spinner(f"Loading {ticker} {contract_type}s..."):
    df_raw = fetch_options_chain(ticker, contract_type, dte_range[0], dte_range[1])

if df_raw.empty:
    st.warning(f"No options data returned for {ticker}. Check ticker or DTE range.")
    st.stop()

df = enrich_chain(df_raw, price, sweet_dte, sweet_delta)

mask = (
    (df["abs_delta"].between(delta_range[0], delta_range[1])) &
    (df["oi"] >= min_oi) &
    (df["ann_yield_pct"] >= min_yield)
)
df_filtered = df[mask].copy()

zone_count  = df_filtered["in_zone"].sum()
total_count = len(df_filtered)

st.markdown(
    f"**{total_count}** contracts match filters  ·  "
    f"**{zone_count}** in wheel zone (delta {sweet_delta[0]:.2f}–{sweet_delta[1]:.2f}, DTE {sweet_dte[0]}–{sweet_dte[1]})"
)

# ── main table ────────────────────────────────────────────────────────────────

display_cols = {
    "exp_date": "Expiry", "dte": "DTE", "strike": "Strike", "distance_pct": "Dist %",
    "delta": "Delta", "theta": "Theta/day", "iv": "IV %",
    "exp_move": "Exp Move $", "exp_move_pct": "Exp Move %", "vs_move": "vs Move %",
    "premium": "Premium", "ann_yield_pct": "Ann Yield %",
    "oi": "OI", "volume": "Volume", "wheel_score": "Score",
}

if not df_filtered.empty:
    display_df = df_filtered[list(display_cols.keys())].rename(columns=display_cols)

    def style_table(df_in):
        def row_style(row):
            in_zone = (
                sweet_delta[0] <= abs(row["Delta"]) <= sweet_delta[1] and
                sweet_dte[0]   <= row["DTE"]        <= sweet_dte[1]
            )
            base = "background-color: rgba(166,227,161,0.10); " if in_zone else ""
            return [base] * len(row)

        def score_color(val):
            if val >= 4: return "color: #a6e3a1; font-weight: bold"
            if val >= 3: return "color: #a6e3a1"
            if val >= 2: return "color: #f9e2af"
            return "color: #6c7086"

        def yield_color(val):
            if val >= 20: return "color: #a6e3a1; font-weight: bold"
            if val >= 12: return "color: #a6e3a1"
            if val >= 7:  return "color: #f9e2af"
            return ""

        def delta_color(val):
            av = abs(val)
            if sweet_delta[0] <= av <= sweet_delta[1]:
                return "color: #a6e3a1"
            return "color: #6c7086"

        def vs_move_color(val):
            if pd.isna(val):  return "color: #6c7086"
            if val >= 150:    return "color: #a6e3a1; font-weight: bold"
            if val >= 100:    return "color: #a6e3a1"
            if val >= 75:     return "color: #f9e2af"
            return "color: #f38ba8"

        return (
            df_in.style
            .apply(row_style, axis=1)
            .map(score_color,    subset=["Score"])
            .map(yield_color,    subset=["Ann Yield %"])
            .map(delta_color,    subset=["Delta"])
            .map(vs_move_color,  subset=["vs Move %"])
            .format({
                "Strike": "${:.2f}", "Dist %": "{:+.1f}%",
                "Delta": "{:.3f}", "Theta/day": "{:.4f}",
                "IV %": "{:.1f}%", "Exp Move $": "${:.2f}",
                "Exp Move %": "{:.1f}%", "vs Move %": "{:.0f}%",
                "Premium": "${:.2f}", "Ann Yield %": "{:.1f}%",
                "OI": "{:,.0f}", "Volume": "{:,.0f}",
            }, na_rep="--")
        )

    st.dataframe(
        style_table(display_df),
        use_container_width=True,
        height=min(600, 36 + len(display_df) * 35),
        hide_index=True,
    )
else:
    st.info("No contracts match current filters. Try widening the delta or DTE range.")

st.divider()

# ── charts ────────────────────────────────────────────────────────────────────

if not df_filtered.empty:
    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.subheader("IV Smile")
        exps = sorted(df_filtered["exp_date"].unique())
        selected_exp = st.selectbox("Expiration", exps, key="iv_smile_exp")
        df_exp = df_filtered[df_filtered["exp_date"] == selected_exp]
        if not df_exp.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df_exp["strike"], y=df_exp["iv"], mode="lines+markers",
                marker=dict(color=df_exp["abs_delta"], colorscale="RdYlGn_r",
                            size=8, colorbar=dict(title="|Delta|", thickness=12), showscale=True),
                line=dict(color="#89b4fa"),
                hovertemplate="Strike: $%{x}<br>IV: %{y:.1f}%<extra></extra>",
            ))
            if price:
                fig.add_vline(x=price, line_dash="dash", line_color="#f9e2af",
                              annotation_text=f"Spot ${price:.2f}", annotation_position="top right")
            zone_df = df_exp[df_exp["in_zone"]]
            if not zone_df.empty:
                fig.add_vrect(
                    x0=zone_df["strike"].min() - 0.5, x1=zone_df["strike"].max() + 0.5,
                    fillcolor="rgba(166,227,161,0.08)", line_width=0,
                    annotation_text="wheel zone", annotation_position="top left",
                )
            fig.update_layout(
                xaxis_title="Strike", yaxis_title="Implied Volatility %",
                plot_bgcolor="#1e1e2e", paper_bgcolor="#1e1e2e",
                font=dict(color="#cdd6f4"), height=360,
                margin=dict(l=40, r=20, t=20, b=40),
            )
            st.plotly_chart(fig, use_container_width=True)

    with chart_col2:
        st.subheader("Yield vs Delta")
        fig2 = go.Figure()
        for exp in sorted(df_filtered["exp_date"].unique()):
            d = df_filtered[df_filtered["exp_date"] == exp]
            fig2.add_trace(go.Scatter(
                x=d["abs_delta"], y=d["ann_yield_pct"], mode="markers",
                name=f"{exp} ({d['dte'].iloc[0]}d)",
                marker=dict(size=9, opacity=0.85),
                customdata=d[["strike", "premium", "oi"]].values,
                hovertemplate=(
                    "Strike: $%{customdata[0]}<br>Delta: %{x:.2f}<br>"
                    "Ann Yield: %{y:.1f}%<br>Premium: $%{customdata[1]}<br>"
                    "OI: %{customdata[2]:,.0f}<extra></extra>"
                ),
            ))
        fig2.add_vrect(x0=sweet_delta[0], x1=sweet_delta[1],
                       fillcolor="rgba(166,227,161,0.08)", line_width=0)
        fig2.add_hline(y=min_yield, line_dash="dot", line_color="#6c7086",
                       annotation_text=f"min yield {min_yield}%",
                       annotation_position="bottom right")
        fig2.update_layout(
            xaxis_title="|Delta|", yaxis_title="Annualized Yield %",
            plot_bgcolor="#1e1e2e", paper_bgcolor="#1e1e2e",
            font=dict(color="#cdd6f4"), height=360,
            margin=dict(l=40, r=20, t=20, b=40),
            legend=dict(bgcolor="#313244", borderwidth=0),
        )
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("Theta Decay by Strike")
    fig3 = px.bar(
        df_filtered.sort_values("strike"),
        x="strike", y="theta", color="exp_date",
        labels={"strike": "Strike", "theta": "Theta ($/day)", "exp_date": "Expiry"},
        barmode="group", color_discrete_sequence=px.colors.qualitative.Pastel,
    )
    if price:
        fig3.add_vline(x=price, line_dash="dash", line_color="#f9e2af")
    fig3.update_layout(
        plot_bgcolor="#1e1e2e", paper_bgcolor="#1e1e2e",
        font=dict(color="#cdd6f4"), height=300,
        margin=dict(l=40, r=20, t=10, b=40),
    )
    st.plotly_chart(fig3, use_container_width=True)

# ── top picks ─────────────────────────────────────────────────────────────────

st.divider()
st.subheader("Top Wheel Picks")

top = df_filtered[df_filtered["wheel_score"] >= 3].head(10)
if top.empty:
    top = df_filtered.head(5)

if not top.empty:
    for _, r in top.iterrows():
        in_z = "🟢" if r["in_zone"] else "⚪"
        col1, col2, col3, col4, col5, col6, col7, col8 = st.columns([0.3, 1, 1, 1, 1, 1.2, 1.2, 1.2])
        col1.markdown(in_z)
        col2.metric("Strike",     f"${r['strike']:.2f}")
        col3.metric("Expiry",     f"{r['exp_date']} ({int(r['dte'])}d)")
        col4.metric("Premium",    f"${r['premium']:.2f}")
        col5.metric("Ann Yield",  f"{r['ann_yield_pct']:.1f}%")
        col6.metric("Delta / IV", f"{r['delta']:.3f} / {r['iv']:.1f}%")
        vs = r.get("vs_move")
        col7.metric("vs Exp Move", f"{vs:.0f}%" if vs and not pd.isna(vs) else "--",
                    help=">100% = strike is beyond the 1σ expected move")
        col8.metric("Score / OI",  f"{int(r['wheel_score'])}/5  ·  OI {int(r['oi']):,}")
