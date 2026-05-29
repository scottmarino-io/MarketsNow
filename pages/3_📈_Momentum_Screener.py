"""Momentum Screener — S&P 100 + Nasdaq 100 page."""

import os
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from dotenv import load_dotenv

from modules.screener.universe import COMBINED, SECTOR_OVERRIDES
from modules.screener.fetchers import (
    fetch_price_history, fetch_fundamentals,
    fetch_realtime_snapshots, fetch_short_interest_bulk,
)
from modules.screener.signals import build_screener_df
from modules.screener.options_flow import (
    fetch_flow_bulk, fmt_pc, fmt_flow_score, render_unusual_table,
)
from modules.shared.theme import inject_css

load_dotenv()

st.set_page_config(
    page_title="Momentum Screener · MarketsNow",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_css()

st.markdown("""
<style>
    .tag-chip {
        display:inline-block; padding:1px 7px; margin:1px; border-radius:9px;
        font-size:0.68rem; font-weight:600; letter-spacing:.04em;
    }
    .tag-MOMENTUM  { background:#1e3a2f; color:#a6e3a1 }
    .tag-BREAKOUT  { background:#2a2a1e; color:#f9e2af }
    .tag-SQUEEZE   { background:#3a1e2f; color:#cba6f7 }
    .tag-OVERSOLD  { background:#2a1e1e; color:#f38ba8 }
    .tag-VALUE\\+MO { background:#1e2a3a; color:#89b4fa }
    .tag-TRENDING  { background:#1e3a3a; color:#94e2d5 }
    div[data-testid="stMetric"] label { font-size:.7rem !important }
</style>
""", unsafe_allow_html=True)


# ── rendering helpers ─────────────────────────────────────────────────────────

TAG_COLORS = {
    "MOMENTUM": ("#a6e3a1", "#1e3a2f"),
    "BREAKOUT": ("#f9e2af", "#2a2a1e"),
    "SQUEEZE":  ("#cba6f7", "#3a1e2f"),
    "OVERSOLD": ("#f38ba8", "#2a1e1e"),
    "VALUE+MO": ("#89b4fa", "#1e2a3a"),
    "TRENDING": ("#94e2d5", "#1e3a3a"),
}

def render_tags(tag_str: str) -> str:
    if not tag_str or pd.isna(tag_str):
        return ""
    html = ""
    for tag in tag_str.split():
        fg, bg = TAG_COLORS.get(tag, ("#cdd6f4", "#313244"))
        html += (f"<span style='background:{bg};color:{fg};padding:1px 6px;border-radius:8px;"
                 f"font-size:.68rem;font-weight:600;margin:1px;display:inline-block'>{tag}</span>")
    return html

def score_bar(score: int, max_score: int = 14) -> str:
    pct   = (score + max_score) / (2 * max_score) * 100
    pct   = max(0, min(100, pct))
    color = "#a6e3a1" if score >= 3 else ("#f9e2af" if score >= 0 else "#f38ba8")
    return (
        f"<div style='background:#313244;border-radius:4px;height:16px;width:100%'>"
        f"<div style='background:{color};border-radius:4px;height:16px;width:{pct:.0f}%'></div>"
        f"</div>"
        f"<span style='font-size:.75rem;color:{color}'>{score:+d}</span>"
    )

def fmt_pct(v, decimals=1) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    color = "#a6e3a1" if v > 0 else ("#f38ba8" if v < 0 else "#cdd6f4")
    arrow = "▲" if v > 0 else ("▼" if v < 0 else "─")
    return f"<span style='color:{color}'>{arrow}{abs(v):.{decimals}f}%</span>"

def fmt_rsi(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    color = ("#f38ba8" if v >= 70 else "#a6e3a1" if v <= 30 else
             "#a6e3a1" if v >= 55 else "#f38ba8" if v <= 45 else "#f9e2af")
    return f"<span style='color:{color}'>{v:.1f}</span>"

def fmt_rec(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    label = ("Strong Buy" if v < 1.5 else "Buy" if v < 2.5 else
             "Hold" if v < 3.5 else "Sell" if v < 4.5 else "Strong Sell")
    color = ("#a6e3a1" if v < 2 else "#f9e2af" if v < 3 else "#f38ba8")
    return f"<span style='color:{color}'>{label} ({v:.1f})</span>"


# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("📈 Momentum Screener")
    st.caption("S&P 100 + Nasdaq 100  ·  ~170 stocks")
    st.caption("yfinance + Massive API  ·  15-min delayed")
    st.divider()

    st.subheader("Strategy Preset")
    preset = st.radio(
        "", ["All", "Momentum", "Breakout", "Short Squeeze",
             "Oversold Bounce", "Value + Momentum"],
        index=0, label_visibility="collapsed",
    )

    st.divider()
    st.subheader("Filters")
    all_sectors = ["All Sectors", "Technology", "Financials", "Healthcare",
                   "Consumer Cyclical", "Consumer Defensive", "Industrials",
                   "Energy", "Communication Services", "Utilities",
                   "Real Estate", "Basic Materials"]
    sector_filter = st.selectbox("Sector", all_sectors)
    min_score  = st.slider("Min composite score", -11, 11, -11)
    min_mktcap = st.selectbox("Min market cap", ["Any", "$10B+", "$50B+", "$100B+", "$500B+"], index=0)

    st.divider()
    st.subheader("Options Flow")
    flow_enabled = st.toggle("Enable options flow analysis", value=False,
                             help="Fetches live options chains via Massive API. ~20-40s for full universe.")
    if flow_enabled:
        st.caption("P/C ratio · Unusual sweeps · Max pain  ·  Cached 15 min")

    st.divider()
    if st.button("🔄  Refresh all data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Prices: 5 min  ·  Indicators: 1 hr  ·  Fundamentals: 6 hr  ·  Flow: 15 min")


# ── load data ─────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _load_base():
    prices  = fetch_price_history("6mo")
    funds   = fetch_fundamentals()
    rt      = fetch_realtime_snapshots()
    si      = fetch_short_interest_bulk()
    return build_screener_df(prices, funds, rt, si)

with st.spinner("Loading screener data… first run takes ~30s, then cached."):
    df = _load_base()

if df.empty:
    st.error("No data loaded. Check your connection and try refreshing.")
    st.stop()

# ── options flow overlay ─────────────────────────────────────────────────────

flow_df = pd.DataFrame()
if flow_enabled:
    with st.spinner("Loading options flow data… fetching chains for full universe (~30s)…"):
        flow_df = fetch_flow_bulk(tuple(COMBINED))
    if not flow_df.empty:
        for col in ["pc_vol", "pc_oi", "max_pain", "flow_score", "call_vol", "put_vol"]:
            if col in flow_df.columns:
                df = df.copy()
                df = df.set_index("ticker") if "ticker" in df.columns else df
                df[col] = flow_df[col]
                df = df.reset_index()
        if "flow_score" in df.columns:
            df["flow_score"] = df["flow_score"].fillna(0).astype(int)
            df["composite"]  = df["tech_score"] + df["fund_score"] + df["flow_score"]
            df = df.sort_values("composite", ascending=False)
            df["rank"] = range(1, len(df) + 1)

# ── apply sidebar filters ────────────────────────────────────────────────────

fdf = df.copy()

if preset != "All":
    tag_map = {
        "Momentum": "MOMENTUM", "Breakout": "BREAKOUT",
        "Short Squeeze": "SQUEEZE", "Oversold Bounce": "OVERSOLD",
        "Value + Momentum": "VALUE+MO",
    }
    tag = tag_map.get(preset, "")
    fdf = fdf[fdf["tags"].str.contains(tag, na=False)]

if sector_filter != "All Sectors":
    fdf = fdf[fdf["sector"] == sector_filter]

fdf = fdf[fdf["composite"] >= min_score]

cap_map = {"$10B+": 1e10, "$50B+": 5e10, "$100B+": 1e11, "$500B+": 5e11}
if min_mktcap != "Any" and "marketCap" in fdf.columns:
    fdf = fdf[fdf["marketCap"].fillna(0) >= cap_map[min_mktcap]]

# ── summary cards ─────────────────────────────────────────────────────────────

total     = len(fdf)
bullish   = (fdf["composite"] > 2).sum()
bearish   = (fdf["composite"] < -2).sum()
avg_score = fdf["composite"].mean()
tagged    = (fdf["tags"].str.len() > 0).sum()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Tickers shown",   total)
c2.metric("Bullish (>+2)",   f"{bullish}  ({bullish/total*100:.0f}%)" if total else "—")
c3.metric("Bearish (<−2)",   f"{bearish}  ({bearish/total*100:.0f}%)" if total else "—")
c4.metric("Avg composite",   f"{avg_score:+.1f}" if total else "—")
c5.metric("Tagged setups",   tagged)

st.divider()

# ── tabs ──────────────────────────────────────────────────────────────────────

tab_screen, tab_sectors, tab_dive = st.tabs(
    ["📊 Screener", "🗺️ Sector View", "🔍 Deep Dive"]
)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1: SCREENER
# ═══════════════════════════════════════════════════════════════════════════════

with tab_screen:
    sort_col, sort_dir_col, _ = st.columns([2, 1, 5])
    flow_sort_opts = ["flow_score", "pc_vol"] if flow_enabled else []
    sort_by  = sort_col.selectbox("Sort by", [
        "composite", "tech_score", "fund_score",
        "ret_1d", "ret_1w", "ret_1m", "ret_3m",
        "rsi14", "vol_ratio", "shortPercentOfFloat",
        "forwardPE", "earningsGrowth", "revenueGrowth",
    ] + flow_sort_opts, index=0, label_visibility="collapsed")
    asc = sort_dir_col.radio("", ["↓ Desc", "↑ Asc"], horizontal=True,
                             label_visibility="collapsed") == "↑ Asc"
    fdf_sorted = fdf.sort_values(sort_by, ascending=asc, na_position="last")

    display_rows = []
    for _, r in fdf_sorted.iterrows():
        def _f(k, fmt="{:.2f}", fallback="—"):
            v = r.get(k)
            return fallback if (v is None or (isinstance(v, float) and np.isnan(v))) else fmt.format(v)

        mktcap = r.get("marketCap")
        cap_str = ("—" if not mktcap or np.isnan(mktcap) else
                   f"${mktcap/1e12:.1f}T" if mktcap >= 1e12 else f"${mktcap/1e9:.0f}B")

        row_dict = {
            "Ticker":    r["ticker"],
            "Company":   (r.get("shortName") or "")[:22],
            "Sector":    (r.get("sector") or "—")[:18],
            "Cap":        cap_str,
            "Price":     f"${r['price']:.2f}" if pd.notna(r.get("price")) else "—",
            "1D %":      fmt_pct(r.get("ret_1d")),
            "1W %":      fmt_pct(r.get("ret_1w")),
            "1M %":      fmt_pct(r.get("ret_1m")),
            "RSI-14":    fmt_rsi(r.get("rsi14")),
            "vs SMA50":  fmt_pct(r.get("vs_sma50")),
            "vs SMA200": fmt_pct(r.get("vs_sma200")),
            "Vol/Avg":   "—" if pd.isna(r.get("vol_ratio", float("nan"))) else f"{r['vol_ratio']:.1f}×",
            "Fwd P/E":   _f("forwardPE", "{:.1f}"),
            "EPS Gr":    "—" if pd.isna(r.get("earningsGrowth", float("nan"))) else f"{r['earningsGrowth']*100:+.0f}%",
            "Rev Gr":    "—" if pd.isna(r.get("revenueGrowth",  float("nan"))) else f"{r['revenueGrowth']*100:+.0f}%",
            "Analyst":   fmt_rec(r.get("recommendationMean")),
            "vs Target": fmt_pct(r.get("vs_target")),
            "Short %":   "—" if pd.isna(r.get("shortPercentOfFloat", float("nan"))) else f"{r['shortPercentOfFloat']*100:.1f}%",
            "Tags":      render_tags(r.get("tags", "")),
            "Score":     score_bar(int(r.get("composite", 0))),
        }
        if flow_enabled and not flow_df.empty:
            row_dict["Flow"] = fmt_flow_score(r.get("flow_score"))
            row_dict["P/C"]  = fmt_pc(r.get("pc_vol"))
            mp = r.get("max_pain")
            row_dict["Max Pain"] = f"${mp:.0f}" if mp and not np.isnan(float(mp)) else "—"
        display_rows.append(row_dict)

    display_df = pd.DataFrame(display_rows)
    st.markdown(
        display_df.to_html(escape=False, index=False, classes="screener-table", border=0),
        unsafe_allow_html=True,
    )
    st.caption(f"{len(fdf_sorted)} tickers shown · sorted by {sort_by}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2: SECTOR VIEW
# ═══════════════════════════════════════════════════════════════════════════════

with tab_sectors:
    if "sector" not in fdf.columns or fdf["sector"].isna().all():
        st.info("Sector data not yet loaded.")
    else:
        sec = (
            fdf.dropna(subset=["sector"])
               .groupby("sector")
               .agg(
                   count      = ("composite", "count"),
                   avg_score  = ("composite", "mean"),
                   bullish    = ("composite", lambda x: (x > 2).sum()),
                   bearish    = ("composite", lambda x: (x < -2).sum()),
                   avg_1m_ret = ("ret_1m",    "mean"),
                   avg_rsi    = ("rsi14",     "mean"),
               )
               .reset_index()
               .sort_values("avg_score", ascending=False)
        )
        sec["breadth"] = (sec["bullish"] / sec["count"] * 100).round(0)

        col_heat, col_bar = st.columns([1.2, 1])

        with col_heat:
            st.subheader("Avg Composite Score by Sector")
            fig = go.Figure(go.Bar(
                y=sec["sector"], x=sec["avg_score"], orientation="h",
                marker_color=[
                    "#a6e3a1" if v >= 2 else "#f9e2af" if v >= 0 else "#f38ba8"
                    for v in sec["avg_score"]
                ],
                text=sec["avg_score"].round(1), textposition="outside",
                hovertemplate="<b>%{y}</b><br>Avg Score: %{x:.1f}<extra></extra>",
            ))
            fig.update_layout(
                plot_bgcolor="#1e1e2e", paper_bgcolor="#1e1e2e",
                font=dict(color="#cdd6f4"), height=400,
                margin=dict(l=10, r=60, t=10, b=10),
                xaxis=dict(gridcolor="#313244", zeroline=True, zerolinecolor="#585b70"),
                yaxis=dict(autorange="reversed"),
            )
            st.plotly_chart(fig, use_container_width=True)

        with col_bar:
            st.subheader("Sector Breadth (% Bullish)")
            fig2 = go.Figure()
            for _, row in sec.iterrows():
                bull_pct = row["bullish"] / row["count"] * 100
                bear_pct = row["bearish"] / row["count"] * 100
                neut_pct = 100 - bull_pct - bear_pct
                fig2.add_trace(go.Bar(
                    name="Bullish", y=[row["sector"]], x=[bull_pct],
                    orientation="h", marker_color="#a6e3a1",
                    showlegend=(row["sector"] == sec["sector"].iloc[0]),
                ))
                fig2.add_trace(go.Bar(
                    name="Neutral", y=[row["sector"]], x=[neut_pct],
                    orientation="h", marker_color="#585b70",
                    showlegend=(row["sector"] == sec["sector"].iloc[0]),
                ))
                fig2.add_trace(go.Bar(
                    name="Bearish", y=[row["sector"]], x=[bear_pct],
                    orientation="h", marker_color="#f38ba8",
                    showlegend=(row["sector"] == sec["sector"].iloc[0]),
                ))
            fig2.update_layout(
                barmode="stack",
                plot_bgcolor="#1e1e2e", paper_bgcolor="#1e1e2e",
                font=dict(color="#cdd6f4"), height=400,
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis=dict(range=[0, 100], ticksuffix="%", gridcolor="#313244"),
                yaxis=dict(autorange="reversed"),
                legend=dict(bgcolor="#313244", orientation="h", x=0, y=-0.15),
            )
            st.plotly_chart(fig2, use_container_width=True)

        st.subheader("Sector Summary Table")
        sec_display = sec.copy()
        sec_display["avg_score"]  = sec_display["avg_score"].round(2)
        sec_display["avg_1m_ret"] = sec_display["avg_1m_ret"].round(1)
        sec_display["avg_rsi"]    = sec_display["avg_rsi"].round(1)
        sec_display["breadth"]    = sec_display["breadth"].astype(int).astype(str) + "%"
        sec_display.columns       = ["Sector","# Stocks","Avg Score",
                                     "Bullish","Bearish","Avg 1M%","Avg RSI","Breadth"]
        st.dataframe(sec_display, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3: DEEP DIVE
# ═══════════════════════════════════════════════════════════════════════════════

with tab_dive:
    ticker_list = sorted(fdf["ticker"].tolist()) if not fdf.empty else COMBINED
    sel = st.selectbox("Select ticker", ticker_list, index=0)

    row_data = df[df["ticker"] == sel]
    if row_data.empty:
        st.warning(f"No data for {sel}.")
        st.stop()
    r = row_data.iloc[0]

    name = r.get("shortName") or sel
    sect = r.get("sector") or "—"
    comp = int(r.get("composite", 0))
    comp_color = "#a6e3a1" if comp >= 3 else "#f9e2af" if comp >= 0 else "#f38ba8"

    st.markdown(
        f"### {sel} — {name}"
        f"&nbsp;&nbsp;<span style='color:{comp_color};font-size:1rem'>Score: {comp:+d} / 11</span>"
        f"&nbsp;&nbsp;<span style='color:#6c7086;font-size:.85rem'>{sect}</span>",
        unsafe_allow_html=True,
    )
    if r.get("tags"):
        st.markdown(render_tags(r["tags"]), unsafe_allow_html=True)
    st.write("")

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Price",      f"${r['price']:.2f}"   if pd.notna(r.get("price"))     else "—")
    m2.metric("1M Return",  f"{r['ret_1m']:+.1f}%" if pd.notna(r.get("ret_1m"))    else "—")
    m3.metric("RSI-14",     f"{r['rsi14']:.1f}"    if pd.notna(r.get("rsi14"))     else "—")
    m4.metric("Fwd P/E",    f"{r['forwardPE']:.1f}" if pd.notna(r.get("forwardPE")) else "—")
    m5.metric("EPS Growth", f"{r['earningsGrowth']*100:+.0f}%" if pd.notna(r.get("earningsGrowth")) else "—")
    m6.metric("vs Target",  f"{r['vs_target']:+.1f}%" if pd.notna(r.get("vs_target")) else "—")

    st.divider()
    chart_col, stats_col = st.columns([2, 1])

    with chart_col:
        @st.cache_data(ttl=3600, show_spinner=False)
        def _load_chart(ticker):
            t = yf.Ticker(ticker)
            return t.history(period="6mo", interval="1d", auto_adjust=True)

        hist = _load_chart(sel)
        if not hist.empty:
            fig_chart = go.Figure()
            fig_chart.add_trace(go.Candlestick(
                x=hist.index,
                open=hist["Open"], high=hist["High"],
                low=hist["Low"],   close=hist["Close"],
                name=sel,
                increasing_line_color="#a6e3a1",
                decreasing_line_color="#f38ba8",
            ))
            close = hist["Close"]
            for period, color, label in [(20,"#89b4fa","SMA20"), (50,"#f9e2af","SMA50"), (200,"#cba6f7","SMA200")]:
                if len(close) >= period:
                    sma = close.rolling(period).mean()
                    fig_chart.add_trace(go.Scatter(
                        x=hist.index, y=sma, mode="lines",
                        name=label, line=dict(color=color, width=1.2, dash="dot"),
                    ))
            fig_chart.update_layout(
                plot_bgcolor="#1e1e2e", paper_bgcolor="#1e1e2e",
                font=dict(color="#cdd6f4"), height=380,
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis_rangeslider_visible=False,
                legend=dict(bgcolor="#313244", borderwidth=0),
                xaxis=dict(gridcolor="#313244"), yaxis=dict(gridcolor="#313244"),
            )
            st.plotly_chart(fig_chart, use_container_width=True)

            vol_avg = hist["Volume"].rolling(20).mean()
            vol_colors = ["#a6e3a1" if hist["Close"].iloc[i] >= hist["Open"].iloc[i]
                          else "#f38ba8" for i in range(len(hist))]
            fig_vol = go.Figure()
            fig_vol.add_trace(go.Bar(
                x=hist.index, y=hist["Volume"],
                marker_color=vol_colors, name="Volume", opacity=0.7,
            ))
            fig_vol.add_trace(go.Scatter(
                x=hist.index, y=vol_avg, mode="lines",
                name="20d Avg", line=dict(color="#f9e2af", width=1.5),
            ))
            fig_vol.update_layout(
                plot_bgcolor="#1e1e2e", paper_bgcolor="#1e1e2e",
                font=dict(color="#cdd6f4"), height=150,
                margin=dict(l=10, r=10, t=4, b=10),
                showlegend=False,
                xaxis=dict(gridcolor="#313244"), yaxis=dict(gridcolor="#313244"),
            )
            st.plotly_chart(fig_vol, use_container_width=True)

    with stats_col:
        st.subheader("Technical")
        tbl = {
            "vs SMA 20":  f"{r.get('vs_sma20',  float('nan')):+.1f}%" if pd.notna(r.get("vs_sma20"))  else "—",
            "vs SMA 50":  f"{r.get('vs_sma50',  float('nan')):+.1f}%" if pd.notna(r.get("vs_sma50"))  else "—",
            "vs SMA 200": f"{r.get('vs_sma200', float('nan')):+.1f}%" if pd.notna(r.get("vs_sma200")) else "—",
            "RSI-14":     f"{r.get('rsi14', float('nan')):.1f}"        if pd.notna(r.get("rsi14"))     else "—",
            "MACD hist":  f"{r.get('macd_hist', float('nan')):+.3f}"   if pd.notna(r.get("macd_hist")) else "—",
            "ATR-14":     f"${r.get('atr14', float('nan')):.2f} ({r.get('atr_pct', float('nan')):.1f}%)" if pd.notna(r.get("atr14")) else "—",
            "52wk pos":   f"{r.get('pos_52wk', float('nan')):.0f}%"    if pd.notna(r.get("pos_52wk")) else "—",
            "Vol/Avg":    f"{r.get('vol_ratio', float('nan')):.1f}×"   if pd.notna(r.get("vol_ratio")) else "—",
        }
        for k, v in tbl.items():
            st.markdown(f"<div style='display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #313244'>"
                        f"<span style='color:#a6adc8;font-size:.8rem'>{k}</span>"
                        f"<span style='color:#cdd6f4;font-size:.8rem;font-weight:600'>{v}</span>"
                        f"</div>", unsafe_allow_html=True)

        st.subheader("Fundamental")
        def _pct_str(v, mult=100):
            return f"{v*mult:+.1f}%" if pd.notna(v) else "—"
        fund_tbl = {
            "Trailing P/E":  f"{r.get('trailingPE', float('nan')):.1f}" if pd.notna(r.get("trailingPE")) else "—",
            "Forward P/E":   f"{r.get('forwardPE',  float('nan')):.1f}" if pd.notna(r.get("forwardPE"))  else "—",
            "P/Book":        f"{r.get('priceToBook', float('nan')):.1f}" if pd.notna(r.get("priceToBook")) else "—",
            "EPS Growth":    _pct_str(r.get("earningsGrowth")),
            "Rev Growth":    _pct_str(r.get("revenueGrowth")),
            "Profit Margin": _pct_str(r.get("profitMargins")),
            "ROE":           _pct_str(r.get("returnOnEquity")),
            "Debt/Equity":   f"{r.get('debtToEquity', float('nan')):.0f}%" if pd.notna(r.get("debtToEquity")) else "—",
            "Beta":          f"{r.get('beta', float('nan')):.2f}"  if pd.notna(r.get("beta"))  else "—",
            "Short % Float": f"{r.get('shortPercentOfFloat', float('nan'))*100:.1f}%" if pd.notna(r.get("shortPercentOfFloat")) else "—",
            "Analyst":       fmt_rec(r.get("recommendationMean")),
        }
        for k, v in fund_tbl.items():
            st.markdown(f"<div style='display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #313244'>"
                        f"<span style='color:#a6adc8;font-size:.8rem'>{k}</span>"
                        f"<span style='color:#cdd6f4;font-size:.8rem;font-weight:600'>{v}</span>"
                        f"</div>", unsafe_allow_html=True)

    # options flow section
    if flow_enabled:
        st.divider()
        st.subheader(f"Options Flow — {sel}")

        ticker_flow = {}
        if not flow_df.empty and sel in flow_df.index:
            ticker_flow = flow_df.loc[sel].to_dict()

        if ticker_flow:
            fo1, fo2, fo3, fo4 = st.columns(4)
            pc_v  = ticker_flow.get("pc_vol", float("nan"))
            pc_o  = ticker_flow.get("pc_oi",  float("nan"))
            mp    = ticker_flow.get("max_pain")
            fs    = ticker_flow.get("flow_score", 0)
            cv    = ticker_flow.get("call_vol", 0)
            pv    = ticker_flow.get("put_vol",  0)

            fo1.metric("P/C Vol Ratio", f"{pc_v:.2f}" if pd.notna(pc_v) else "—",
                       help="< 0.75 = call-heavy (bullish)  ·  > 1.25 = put-heavy (bearish)")
            fo2.metric("P/C OI Ratio",  f"{pc_o:.2f}" if pd.notna(pc_o) else "—")
            fo3.metric("Max Pain",      f"${mp:.0f}"  if mp else "—")
            fo4.metric("Flow Score",    f"{fs:+d}")

            if cv + pv > 0:
                call_pct = cv / (cv + pv) * 100
                put_pct  = pv / (cv + pv) * 100
                st.markdown(
                    f"<div style='margin:8px 0 4px 0;font-size:.75rem;color:#a6adc8'>Call vs Put Volume</div>"
                    f"<div style='display:flex;border-radius:6px;overflow:hidden;height:20px'>"
                    f"<div style='background:#1e3a2f;width:{call_pct:.0f}%;display:flex;align-items:center;"
                    f"justify-content:center;color:#a6e3a1;font-size:.7rem;font-weight:600'>"
                    f"{'Calls ' + str(int(cv/1000))+'K' if cv >= 1000 else 'Calls'}</div>"
                    f"<div style='background:#3a1e1e;width:{put_pct:.0f}%;display:flex;align-items:center;"
                    f"justify-content:center;color:#f38ba8;font-size:.7rem;font-weight:600'>"
                    f"{'Puts ' + str(int(pv/1000))+'K' if pv >= 1000 else 'Puts'}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            st.markdown("**Unusual Volume (vol/OI >= 1.5x)**")
            unusual = st.session_state.get("_unusual_map", {}).get(sel, [])
            st.markdown(render_unusual_table(unusual), unsafe_allow_html=True)
        else:
            st.info(f"Options flow data not available for {sel}. "
                    "Enable the flow toggle and ensure Massive API key is set.")

    # recent news
    st.divider()
    st.subheader(f"Recent News — {sel}")
    @st.cache_data(ttl=1800, show_spinner=False)
    def _load_news(ticker):
        try:
            from massive import RESTClient
            key = os.getenv("MASSIVE_API_KEY")
            if not key: return []
            return list(RESTClient(api_key=key).list_ticker_news(
                ticker=ticker, limit=8,
                params={"order": "desc", "sort": "published_utc"}
            ))
        except Exception:
            return []

    news = _load_news(sel)
    if news:
        for n in news:
            pub  = (getattr(n, "published_utc", "") or "")[:10]
            title = getattr(n, "title", "")
            url   = getattr(n, "article_url", "#") or "#"
            pub_name = getattr(n, "publisher", {})
            pub_name = pub_name.get("name", "") if isinstance(pub_name, dict) else ""
            st.markdown(
                f"<div style='padding:6px 0;border-bottom:1px solid #313244'>"
                f"<a href='{url}' target='_blank' style='color:#89b4fa;text-decoration:none'>{title}</a>"
                f"<span style='color:#6c7086;font-size:.75rem;margin-left:8px'>{pub_name} · {pub}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("News unavailable (Massive API key not configured or no recent articles).")
