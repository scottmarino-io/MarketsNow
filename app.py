"""MarketsNow — Home Dashboard."""

import os
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from modules.shared.theme import hex_to_rgba, inject_css

load_dotenv()

st.set_page_config(
    page_title="MarketsNow",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_css()

# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## MarketsNow")
    st.caption("Unified market intelligence dashboard")
    st.divider()

    st.markdown("### API Keys")

    fred_key = ""
    try:
        fred_key = st.secrets["FRED_API_KEY"]
    except (KeyError, FileNotFoundError):
        fred_key = os.environ.get("FRED_API_KEY", "")
    if not fred_key:
        fred_key = st.text_input("FRED API Key", type="password",
                                  placeholder="Required for stress monitor…",
                                  help="https://fred.stlouisfed.org/docs/api/api_key.html")
    else:
        st.success("FRED API key loaded", icon="🔑")

    massive_key = os.environ.get("MASSIVE_API_KEY", "")
    if not massive_key:
        try:
            massive_key = st.secrets.get("MASSIVE_API_KEY", "")
        except (KeyError, FileNotFoundError):
            pass
    if not massive_key:
        massive_key = st.text_input("Massive API Key", type="password",
                                     placeholder="Required for screener + monitor…")
    else:
        st.success("Massive API key loaded", icon="📡")

    anthropic_key = ""
    try:
        anthropic_key = st.secrets["ANTHROPIC_API_KEY"]
    except (KeyError, FileNotFoundError):
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        st.success("Anthropic API key loaded", icon="🤖")

    st.divider()
    if st.button("🔄  Refresh all data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.markdown(
        "<small style='color:#555'>Data: FRED API · Yahoo Finance · Massive API<br>"
        "Not investment advice</small>",
        unsafe_allow_html=True,
    )

# ── header ────────────────────────────────────────────────────────────────────

h_left, h_right = st.columns([3, 1])
with h_left:
    st.markdown("## 📊 MarketsNow")
    st.caption("Unified market intelligence — stress monitoring, stock screening, and options analysis")
with h_right:
    now = datetime.now().strftime("%b %d, %Y  %H:%M")
    st.markdown(
        f"<div style='text-align:right; color:#666; padding-top:14px; font-size:13px;'>"
        f"<b>{now}</b></div>",
        unsafe_allow_html=True,
    )

# ── news ticker ──────────────────────────────────────────────────────────────
try:
    from modules.stress.news_ticker import render_ticker
    render_ticker()
except Exception:
    pass

st.divider()

# ── stress gauge (compact) ───────────────────────────────────────────────────

stress_col, breadth_col = st.columns(2, gap="large")

with stress_col:
    st.markdown("<div class='section-header'>Market Stress</div>", unsafe_allow_html=True)

    if fred_key:
        try:
            from modules.stress.data_fetchers import fetch_all
            from modules.stress.stress_calculator import classify, current_scores
            from modules.stress.config import STRESS_BANDS

            with st.spinner("Loading stress data…"):
                data = fetch_all(fred_key)
            scores_result = current_scores(data)
            composite = scores_result["composite"]
            stress_label, stress_color = classify(composite)

            gauge_steps = [
                {"range": [lo, hi], "color": hex_to_rgba(color)}
                for lo, hi, _, color in STRESS_BANDS
            ]
            fig_gauge = go.Figure(
                go.Indicator(
                    mode="gauge+number",
                    value=composite,
                    number={"suffix": "", "font": {"size": 36, "color": stress_color}},
                    domain={"x": [0, 1], "y": [0.05, 1]},
                    gauge={
                        "axis": {"range": [0, 100], "tickfont": {"size": 9, "color": "#888"}},
                        "bar": {"color": stress_color, "thickness": 0.25},
                        "bgcolor": "#0e1117", "borderwidth": 0,
                        "steps": gauge_steps,
                    },
                )
            )
            fig_gauge.update_layout(
                height=220, margin=dict(t=10, b=0, l=20, r=20),
                paper_bgcolor="rgba(0,0,0,0)", font_color="#ccc",
                annotations=[dict(
                    text=f"<b>{stress_label}</b>", x=0.5, y=0.0, showarrow=False,
                    font=dict(size=14, color=stress_color), xanchor="center",
                )],
            )
            st.plotly_chart(fig_gauge, use_container_width=True, config={"displayModeBar": False})
            st.markdown(
                f"<div style='text-align:center;color:#888;font-size:.85rem'>"
                f"Composite score: <b style='color:{stress_color}'>{composite:.0f}/100</b> — "
                f"<a href='#' style='color:#4a90d9'>View full stress monitor →</a></div>",
                unsafe_allow_html=True,
            )
        except Exception as e:
            st.warning(f"Could not load stress data: {e}")
    else:
        st.info("Add a **FRED API key** in the sidebar to see the stress gauge.")

# ── market breadth (compact) ─────────────────────────────────────────────────

with breadth_col:
    st.markdown("<div class='section-header'>Market Breadth</div>", unsafe_allow_html=True)

    if massive_key:
        try:
            from modules.market.breadth import BreadthFetcher
            from massive import RESTClient

            @st.cache_data(ttl=30)
            def _home_breadth():
                client = RESTClient(api_key=massive_key)
                bf = BreadthFetcher(client)
                bf.load_exchange_tickers()
                return bf.fetch()

            bsnap = _home_breadth()
            if bsnap:
                def _tick_color(val):
                    if val >= 1000:  return "#a6e3a1"
                    if val >= 500:   return "#94e2d5"
                    if val >= -499:  return "#f9e2af"
                    if val >= -999:  return "#fab387"
                    return "#f38ba8"

                b1, b2, b3 = st.columns(3)
                for col, label, tick_val, pct_val in [
                    (b1, "All-Market", bsnap.tick_all,  bsnap.breadth_all_pct),
                    (b2, "NYSE",       bsnap.tick_nyse, bsnap.breadth_nyse_pct),
                    (b3, "Nasdaq",     bsnap.tick_nq,   bsnap.breadth_nq_pct),
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
                st.markdown(
                    f"<div style='text-align:center;color:#888;font-size:.85rem;margin-top:8px'>"
                    f"{bsnap.up_all:,} advancing / {bsnap.dn_all:,} declining of {total:,} stocks"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        except Exception as e:
            st.warning(f"Could not load breadth data: {e}")
    else:
        st.info("Add a **Massive API key** to see market breadth.")

st.divider()

# ── economic calendar (compact) ──────────────────────────────────────────────

if fred_key:
    st.markdown("<div class='section-header'>📅 Upcoming Economic Events</div>", unsafe_allow_html=True)
    try:
        from modules.stress.economic_calendar import build_calendar

        cal_events = build_calendar(fred_key, impact_filter=["High", "Medium"])
        if cal_events:
            upcoming = [e for e in cal_events if not e.get("is_past", True)][:5]
            if not upcoming:
                upcoming = cal_events[:5]

            impact_dot = {"High": "#e74c3c", "Medium": "#e67e22", "Low": "#444"}
            for ev in upcoming:
                dot_color = impact_dot.get(ev["impact"], "#444")
                st.markdown(
                    f"<div style='display:flex;align-items:center;gap:8px;padding:5px 0;"
                    f"border-bottom:1px solid #1e2025;font-size:.85rem'>"
                    f"<span style='width:8px;height:8px;border-radius:50%;"
                    f"background:{dot_color};flex-shrink:0'></span>"
                    f"<span style='color:#888;width:65px;flex-shrink:0'>{ev['time_str']}</span>"
                    f"<span style='color:#ddd;flex:1'>{ev['title']}</span>"
                    f"<span style='color:#888;font-size:.75rem'>{ev['date_str']}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Calendar unavailable.")
    except Exception:
        st.caption("Calendar unavailable.")

    st.divider()

# ── quick navigation cards ───────────────────────────────────────────────────

st.markdown("<div class='section-header'>Pages</div>", unsafe_allow_html=True)

nav_cols = st.columns(4)

pages = [
    ("📡", "Market Stress", "25+ macro indicators, composite stress score, AI analysis, direction forecast, economic calendar"),
    ("⚙", "Wheel Screener", "Options wheel strategy screener with IV smile, yield vs delta, theta decay analysis"),
    ("📈", "Momentum Screener", "S&P 100 + Nasdaq 100 ranked by composite score — technical, fundamental, sentiment"),
    ("📺", "Market Monitor", "Bloomberg-style price dashboard with trend scoring and market breadth"),
]

for i, (icon, title, desc) in enumerate(pages):
    with nav_cols[i]:
        st.markdown(
            f"<div class='metric-card' style='min-height:120px'>"
            f"<div style='font-size:1.5rem;margin-bottom:6px'>{icon}</div>"
            f"<div style='font-size:1rem;font-weight:600;color:#cdd6f4;margin-bottom:4px'>{title}</div>"
            f"<div style='font-size:.75rem;color:#6c7086'>{desc}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

# ── footer ────────────────────────────────────────────────────────────────────

st.divider()
st.markdown(
    "<div style='text-align:center; color:#444; font-size:11px; padding:8px 0;'>"
    "MarketsNow — Data from FRED, Yahoo Finance, Massive API, CNN, Forex Factory. "
    "Not investment advice."
    "</div>",
    unsafe_allow_html=True,
)
