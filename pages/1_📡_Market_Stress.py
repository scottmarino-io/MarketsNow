"""Market Stress Monitor page."""

import os
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from modules.stress.config import (
    CATEGORY_ORDER,
    COMPOSITE_INDICATORS,
    EXPANDED_BY_DEFAULT,
    INFORMATIONAL_INDICATORS,
    STRESS_BANDS,
)
from modules.stress.ai_analysis import stream_analysis
from modules.stress.data_fetchers import daily_change, fetch_all, latest_value
from modules.stress.direction_indicator import (
    compute_signals,
    fetch_market_headlines,
    generate_direction_forecast,
)
from modules.stress.economic_calendar import build_calendar
from modules.stress.news_ticker import render_ticker
from modules.stress.stress_calculator import classify, current_scores, historical_composite
from modules.market.uw_fetchers import fetch_market_tide_eod, fetch_gex_levels
from modules.shared.theme import hex_to_rgba, inject_css

load_dotenv()

st.set_page_config(
    page_title="Market Stress · MarketsNow",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_css()

# Additional CSS specific to this page
st.markdown("""
<style>
    .dir-prob { font-size: 36px; font-weight: 800; line-height: 1.1; }
    .dir-sub  { font-size: 13px; color: #888; margin-top: 2px; }
    .dir-confidence {
        display: inline-block; padding: 2px 10px; border-radius: 12px;
        font-size: 11px; font-weight: 600; text-transform: uppercase;
        letter-spacing: .06em; margin-top: 6px;
    }
    .signal-item { padding: 5px 0; font-size: 13px; border-bottom: 1px solid #1e2025; }
    .signal-item:last-child { border-bottom: none; }
    .key-risk-box {
        background: #1a1409; border-left: 4px solid #e67e22;
        border-radius: 0 6px 6px 0; padding: 10px 14px;
        font-size: 13px; color: #ccc; margin-top: 10px;
    }
    .disclaimer { font-size: 11px; color: #444; margin-top: 12px; text-align: center; }
    .cal-row {
        display: flex; align-items: center; padding: 7px 10px;
        border-radius: 6px; margin-bottom: 4px; background: #16181d;
        gap: 10px; font-size: 13px;
    }
    .cal-row:hover { background: #1c1f27; }
    .cal-time   { width: 70px; color: #666; font-size: 11px; flex-shrink: 0; }
    .cal-impact { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
    .cal-title  { flex: 1; color: #ddd; font-weight: 500; }
    .cal-title a { color: #ddd; text-decoration: none; }
    .cal-title a:hover { color: #4a90d9; text-decoration: underline; }
    .cal-val    { width: 90px; text-align: right; font-size: 12px; color: #aaa; flex-shrink: 0; }
    .cal-actual { color: #f0f0f0; font-weight: 600; }
    .cal-date-header {
        font-size: 11px; font-weight: 700; text-transform: uppercase;
        letter-spacing: .08em; color: #555; margin: 14px 0 4px 0;
        padding-bottom: 3px; border-bottom: 1px solid #1e2025;
    }
</style>
""", unsafe_allow_html=True)

# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Settings")

    api_key: str = ""
    try:
        api_key = st.secrets["FRED_API_KEY"]
    except (KeyError, FileNotFoundError):
        api_key = os.environ.get("FRED_API_KEY", "")

    if not api_key:
        api_key = st.text_input(
            "FRED API Key", type="password",
            placeholder="Paste your free FRED API key…",
            help="Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html",
        )
        if not api_key:
            st.info(
                "A **free** FRED API key is required.\n\n"
                "1. Register at [fred.stlouisfed.org](https://fred.stlouisfed.org)\n"
                "2. Copy your key and paste it above, or set `FRED_API_KEY` in a `.env` file."
            )
            st.stop()
    else:
        st.success("FRED API key loaded ✓", icon="🔑")

    anthropic_key: str = ""
    try:
        anthropic_key = st.secrets["ANTHROPIC_API_KEY"]
    except (KeyError, FileNotFoundError):
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if not anthropic_key:
        anthropic_key = st.text_input(
            "Anthropic API Key", type="password",
            placeholder="For AI analysis (optional)…",
            help="Get a key at https://console.anthropic.com",
        )
    else:
        st.success("Anthropic API key loaded ✓", icon="🤖")

    unusual_whales_key: str = ""
    try:
        unusual_whales_key = st.secrets["UNUSUAL_WHALES_API_KEY"]
    except (KeyError, FileNotFoundError):
        unusual_whales_key = os.environ.get("UNUSUAL_WHALES_API_KEY", "")

    if not unusual_whales_key:
        unusual_whales_key = st.text_input(
            "Unusual Whales API Key", type="password",
            placeholder="For Gamma/Tide in the direction forecast (optional)…",
            help="Get a key at https://unusualwhales.com/api",
        )
    else:
        st.success("Unusual Whales API key loaded ✓", icon="🐋")

    st.divider()
    chart_years = st.select_slider("Chart history", options=[1, 2, 3, 5, 7], value=3,
                                   format_func=lambda y: f"{y}Y")

    if st.button("🔄  Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.markdown(
        "<small style='color:#555'>Data: FRED API · Yahoo Finance<br>"
        "Scores: rolling 2-year percentile rank<br>"
        "CDS proxied by ICE BofA OAS spreads</small>",
        unsafe_allow_html=True,
    )

# ─── Data fetch ───────────────────────────────────────────────────────────────
with st.spinner("Fetching market data…"):
    data = fetch_all(api_key)

scores_result = current_scores(data)
composite = scores_result["composite"]
ind_scores = scores_result["indicators"]
ind_values = scores_result["current_values"]
stress_label, stress_color = classify(composite)

# ─── Header ───────────────────────────────────────────────────────────────────
h_left, h_right = st.columns([3, 1])
with h_left:
    st.markdown(
        f"## 📡 Market Stress Monitor"
        f"&nbsp;&nbsp;<span class='stress-badge' style='background:{stress_color}22; color:{stress_color};'>"
        f"{stress_label.upper()}</span>",
        unsafe_allow_html=True,
    )
with h_right:
    now = datetime.now().strftime("%b %d, %Y  %H:%M")
    st.markdown(
        f"<div style='text-align:right; color:#666; padding-top:14px; font-size:13px;'>"
        f"Last updated<br><b>{now}</b></div>",
        unsafe_allow_html=True,
    )

# ─── News ticker ──────────────────────────────────────────────────────────────
render_ticker()
st.divider()

# ─── Gauge + indicator cards ─────────────────────────────────────────────────
col_gauge, col_cards = st.columns([1, 2], gap="large")

with col_gauge:
    gauge_steps = [
        {"range": [lo, hi], "color": hex_to_rgba(color)}
        for lo, hi, _, color in STRESS_BANDS
    ]
    fig_gauge = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=composite,
            number={"suffix": "", "font": {"size": 42, "color": stress_color}},
            domain={"x": [0, 1], "y": [0.05, 1]},
            gauge={
                "axis": {
                    "range": [0, 100],
                    "tickvals": [0, 25, 45, 65, 85, 100],
                    "ticktext": ["0", "25", "45", "65", "85", "100"],
                    "tickfont": {"size": 10, "color": "#888"},
                    "tickwidth": 1, "tickcolor": "#444",
                },
                "bar": {"color": stress_color, "thickness": 0.25},
                "bgcolor": "#0e1117", "borderwidth": 0,
                "steps": gauge_steps,
                "threshold": {
                    "line": {"color": stress_color, "width": 3},
                    "thickness": 0.75, "value": composite,
                },
            },
        )
    )
    fig_gauge.update_layout(
        height=280, margin=dict(t=10, b=0, l=20, r=20),
        paper_bgcolor="rgba(0,0,0,0)", font_color="#ccc",
        annotations=[dict(
            text=f"<b>{stress_label}</b>", x=0.5, y=0.0, showarrow=False,
            font=dict(size=16, color=stress_color), xanchor="center",
        )],
    )
    st.plotly_chart(fig_gauge, use_container_width=True, config={"displayModeBar": False})

    st.markdown("<div class='section-header'>Score Breakdown</div>", unsafe_allow_html=True)
    for key, cfg in COMPOSITE_INDICATORS.items():
        if key not in ind_scores:
            continue
        s = ind_scores[key]
        _, bar_color = classify(s)
        bar_pct = int(s)
        val = ind_values.get(key)
        val_str = f"{val:{cfg['fmt']}}" if val is not None else "—"
        st.markdown(
            f"<div style='margin-bottom:6px;'>"
            f"<div style='display:flex; justify-content:space-between; font-size:11px; color:#888; margin-bottom:2px;'>"
            f"<span>{cfg['name']}</span><span style='color:{bar_color}'>{s:.0f}</span></div>"
            f"<div style='background:#222; border-radius:3px; height:5px;'>"
            f"<div style='width:{bar_pct}%; background:{bar_color}; height:5px; border-radius:3px;'></div>"
            f"</div></div>",
            unsafe_allow_html=True,
        )

with col_cards:
    st.markdown("<div class='section-header'>Composite Indicators</div>", unsafe_allow_html=True)
    keys = list(COMPOSITE_INDICATORS.keys())
    card_cols = st.columns(2, gap="small")

    for i, key in enumerate(keys):
        cfg = COMPOSITE_INDICATORS[key]
        col = card_cols[i % 2]
        with col:
            val = ind_values.get(key)
            score = ind_scores.get(key, 50.0)
            _, card_color = classify(score)
            series = data.get(key, pd.Series(dtype=float))
            chg = daily_change(series)
            val_str = f"{val:{cfg['fmt']}} {cfg['unit']}" if val is not None else "No data"

            if chg is not None:
                arrow = "▲" if chg > 0 else "▼"
                delta_color = "#e74c3c" if cfg["higher_is_stress"] == (chg > 0) else "#27ae60"
                delta_html = (
                    f"<div class='ind-delta' style='color:{delta_color};'>"
                    f"{arrow} {abs(chg):{cfg['fmt']}} {cfg['unit']}</div>"
                )
            else:
                delta_html = "<div class='ind-delta' style='color:#555;'>—</div>"

            st.markdown(
                f"<div class='ind-card' style='border-left-color:{card_color};'>"
                f"<div class='ind-label'>{cfg['name']}</div>"
                f"<div class='ind-value'>{val_str}</div>"
                f"{delta_html}"
                f"<div class='ind-score'>Stress score: {score:.0f}/100 · {cfg['category']}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

# ─── Historical composite chart ──────────────────────────────────────────────
st.markdown("<div class='section-header'>Composite Stress Index — Historical</div>", unsafe_allow_html=True)

hist = historical_composite(data)

if not hist.empty:
    cutoff = pd.Timestamp.now() - pd.DateOffset(years=chart_years)
    hist_view = hist[hist.index >= cutoff]

    fig_hist = go.Figure()
    for lo, hi, label, color in STRESS_BANDS:
        fig_hist.add_hrect(
            y0=lo, y1=hi, fillcolor=color, opacity=0.06, layer="below",
            line_width=0, annotation_text=label, annotation_position="right",
            annotation_font_size=9, annotation_font_color=color,
        )

    fig_hist.add_trace(go.Scatter(
        x=hist_view.index, y=hist_view.values, mode="lines",
        line=dict(width=2, color=stress_color), name="Composite Stress",
        hovertemplate="<b>%{x|%b %d, %Y}</b><br>Stress: %{y:.1f}<extra></extra>",
    ))
    fig_hist.add_trace(go.Scatter(
        x=[hist_view.index[-1]], y=[hist_view.values[-1]], mode="markers",
        marker=dict(size=9, color=stress_color, symbol="circle"),
        showlegend=False, hoverinfo="skip",
    ))
    fig_hist.update_layout(
        height=320, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=10, b=30, l=40, r=80),
        yaxis=dict(range=[0, 100], tickvals=[0, 25, 45, 65, 85, 100],
                   gridcolor="#222", tickfont=dict(size=10, color="#666"),
                   title="Stress Score", title_font=dict(size=11, color="#666")),
        xaxis=dict(gridcolor="#1a1a1a", tickfont=dict(size=10, color="#666")),
        showlegend=False, hovermode="x unified",
    )
    st.plotly_chart(fig_hist, use_container_width=True, config={"displayModeBar": False})
else:
    st.info("Not enough historical data to render the composite chart yet.")

# ─── AI Analysis ─────────────────────────────────────────────────────────────
st.markdown("<div class='section-header'>🤖 AI Market Stress Analysis</div>", unsafe_allow_html=True)

if not anthropic_key:
    st.info("Add an **Anthropic API key** in the sidebar to enable AI-powered analysis.")
else:
    btn_col, status_col = st.columns([1, 3])
    with btn_col:
        generate_btn = st.button("⚡ Generate Analysis", use_container_width=True, type="primary")
    with status_col:
        if "ai_analysis_time" in st.session_state:
            st.markdown(
                f"<div style='padding-top:8px; color:#555; font-size:13px;'>"
                f"Generated {st.session_state['ai_analysis_time']} · powered by Claude Opus</div>",
                unsafe_allow_html=True,
            )

    if generate_btn:
        with st.container():
            analysis_text = st.write_stream(
                stream_analysis(
                    anthropic_key, composite, stress_label, ind_scores, ind_values,
                    hist if not hist.empty else pd.Series(dtype=float),
                )
            )
        st.session_state["ai_analysis"] = analysis_text
        st.session_state["ai_analysis_time"] = datetime.now().strftime("%b %d, %Y %H:%M")
    elif "ai_analysis" in st.session_state:
        st.markdown(st.session_state["ai_analysis"])

# ─── Direction Indicator ─────────────────────────────────────────────────────
st.markdown("<div class='section-header'>🎯 Next-Day S&P 500 Direction</div>", unsafe_allow_html=True)

if not anthropic_key:
    st.info("Add an **Anthropic API key** in the sidebar to enable the direction indicator.")
else:
    dir_btn_col, dir_status_col = st.columns([1, 3])
    with dir_btn_col:
        dir_btn = st.button("🎯 Generate Forecast", use_container_width=True, type="primary")
    with dir_status_col:
        if "dir_forecast_time" in st.session_state:
            st.markdown(
                f"<div style='padding-top:8px; color:#555; font-size:13px;'>"
                f"Generated {st.session_state['dir_forecast_time']} · powered by Claude Opus</div>",
                unsafe_allow_html=True,
            )

    if dir_btn:
        with st.spinner("Fetching headlines & computing forecast…"):
            tide = fetch_market_tide_eod(unusual_whales_key) if unusual_whales_key else None
            gamma = (
                fetch_gex_levels(unusual_whales_key, "SPY", source="vol")
                if unusual_whales_key else None
            )
            signals = compute_signals(
                data, hist if not hist.empty else pd.Series(dtype=float), tide, gamma,
            )
            headlines = fetch_market_headlines()
            forecast = generate_direction_forecast(anthropic_key, signals, headlines)
        st.session_state["dir_forecast"] = forecast
        st.session_state["dir_forecast_time"] = datetime.now().strftime("%b %d, %Y %H:%M")

    if "dir_forecast" in st.session_state:
        forecast = st.session_state["dir_forecast"]
        if "error" in forecast:
            st.error(f"Forecast failed: {forecast['error']}")
        else:
            prob_up = forecast["probability_up"]
            prob_dn = 100 - prob_up
            lean = forecast["lean"]
            confidence = forecast["confidence"]
            is_bullish = lean == "bullish"
            main_color = "#27ae60" if is_bullish else "#e74c3c"
            prob_display = prob_up if is_bullish else prob_dn
            direction_word = "UP" if is_bullish else "DOWN"
            conf_bg = {"low": "#2a2010", "moderate": "#1a2a10", "high": "#0f2a0f"}.get(confidence, "#2a2010")
            conf_color = {"low": "#e67e22", "moderate": "#f1c40f", "high": "#27ae60"}.get(confidence, "#888")

            dir_left, dir_right = st.columns([1, 2], gap="large")

            with dir_left:
                st.markdown(
                    f"<div class='dir-prob' style='color:{main_color};'>{prob_display}%</div>"
                    f"<div class='dir-sub'>probability market moves <b>{direction_word}</b> tomorrow</div>"
                    f"<span class='dir-confidence' style='background:{conf_bg}; color:{conf_color};'>"
                    f"{confidence} confidence</span>",
                    unsafe_allow_html=True,
                )
                st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
                st.markdown(
                    f"<div style='display:flex; border-radius:6px; overflow:hidden; height:18px; margin-top:4px;'>"
                    f"<div style='width:{prob_up}%; background:#27ae60; display:flex; align-items:center; "
                    f"justify-content:center; font-size:10px; color:#fff; font-weight:700;'>"
                    f"{'▲ ' + str(prob_up) + '%' if prob_up >= 20 else ''}</div>"
                    f"<div style='width:{prob_dn}%; background:#e74c3c; display:flex; align-items:center; "
                    f"justify-content:center; font-size:10px; color:#fff; font-weight:700;'>"
                    f"{'▼ ' + str(prob_dn) + '%' if prob_dn >= 20 else ''}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            with dir_right:
                sig_col_bull, sig_col_bear = st.columns(2, gap="small")
                with sig_col_bull:
                    st.markdown(
                        "<div style='font-size:12px; font-weight:700; color:#27ae60; "
                        "text-transform:uppercase; letter-spacing:.05em; margin-bottom:4px;'>▲ Bull Signals</div>",
                        unsafe_allow_html=True,
                    )
                    for s in forecast.get("bull_signals", []):
                        st.markdown(f"<div class='signal-item' style='color:#ccc;'>▲ {s}</div>", unsafe_allow_html=True)
                with sig_col_bear:
                    st.markdown(
                        "<div style='font-size:12px; font-weight:700; color:#e74c3c; "
                        "text-transform:uppercase; letter-spacing:.05em; margin-bottom:4px;'>▼ Bear Signals</div>",
                        unsafe_allow_html=True,
                    )
                    for s in forecast.get("bear_signals", []):
                        st.markdown(f"<div class='signal-item' style='color:#ccc;'>▼ {s}</div>", unsafe_allow_html=True)

            with st.expander("📊 Reasoning", expanded=False):
                st.markdown(forecast.get("reasoning", ""))

            st.markdown(
                f"<div class='key-risk-box'>"
                f"<b style='color:#e67e22;'>⚠ Key Risk:</b> {forecast.get('key_risk', '—')}</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "<div class='disclaimer'>Experimental · Uses ~10 quantitative signals + recent headlines · "
                "Not investment advice · Past performance does not predict future results</div>",
                unsafe_allow_html=True,
            )

# ─── Economic Calendar ───────────────────────────────────────────────────────
st.markdown("<div class='section-header'>📅 Economic Calendar — This Week (USD)</div>", unsafe_allow_html=True)

cal_filter_col, cal_legend_col = st.columns([2, 3])
with cal_filter_col:
    show_low = st.checkbox("Show low-impact events", value=False)

impact_filter = ["High", "Medium"] + (["Low"] if show_low else [])

with st.spinner("Loading calendar…"):
    cal_events = build_calendar(api_key, impact_filter=impact_filter)

if not cal_events:
    st.info("Economic calendar unavailable — Forex Factory RSS may be temporarily unreachable.")
else:
    impact_colors = {"High": "#e74c3c", "Medium": "#f1c40f", "Low": "#555"}
    impact_dot    = {"High": "#e74c3c", "Medium": "#e67e22", "Low": "#444"}

    with cal_legend_col:
        st.markdown(
            "<div style='padding-top:6px; font-size:11px; color:#555;'>"
            "<span style='color:#e74c3c;'>● High</span> &nbsp;"
            "<span style='color:#e67e22;'>● Medium</span> &nbsp;"
            "<span style='color:#444;'>● Low</span> &nbsp;&nbsp;|&nbsp;&nbsp;"
            "Actual values sourced from FRED where available · "
            "Forecast/Previous from <a href='https://www.forexfactory.com/calendar' "
            "target='_blank' style='color:#555;'>Forex Factory</a></div>",
            unsafe_allow_html=True,
        )

    st.markdown(
        "<div style='display:flex; gap:10px; padding:0 10px; "
        "font-size:10px; font-weight:700; text-transform:uppercase; "
        "letter-spacing:.06em; color:#444; margin-bottom:2px;'>"
        "<span style='width:70px;'>Time</span>"
        "<span style='width:8px;'></span>"
        "<span style='flex:1;'>Event</span>"
        "<span style='width:90px; text-align:right;'>Actual</span>"
        "<span style='width:90px; text-align:right;'>Forecast</span>"
        "<span style='width:90px; text-align:right;'>Previous</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    current_date = None
    for ev in cal_events:
        if ev["date_str"] != current_date:
            current_date = ev["date_str"]
            today_str = datetime.now().strftime("%a %b %-d")
            label = "TODAY" if ev["date_str"] == today_str else ev["date_str"].upper()
            st.markdown(f"<div class='cal-date-header'>{label}</div>", unsafe_allow_html=True)

        dot_color = impact_dot.get(ev["impact"], "#444")
        actual_html = (
            f"<span class='cal-actual'>{ev['actual']}</span>"
            if ev["is_past"] and ev["actual"] not in ("—", "Released")
            else f"<span style='color:#555;'>{ev['actual']}</span>"
        )
        title_html = (
            f"<a href='{ev['url']}' target='_blank'>{ev['title']}</a>"
            if ev.get("url") else ev["title"]
        )
        st.markdown(
            f"<div class='cal-row'>"
            f"<span class='cal-time'>{ev['time_str']}</span>"
            f"<span class='cal-impact' style='background:{dot_color};'></span>"
            f"<span class='cal-title'>{title_html}</span>"
            f"<span class='cal-val'>{actual_html}</span>"
            f"<span class='cal-val' style='color:#888;'>{ev['forecast']}</span>"
            f"<span class='cal-val' style='color:#555;'>{ev['previous']}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

# ─── Indicator detail charts ─────────────────────────────────────────────────
st.markdown("<div class='section-header'>Indicator Detail Charts</div>", unsafe_allow_html=True)

all_cfgs = {**COMPOSITE_INDICATORS, **INFORMATIONAL_INDICATORS}

grouped: dict[str, list[tuple[str, dict]]] = {}
for key, cfg in all_cfgs.items():
    cat = cfg["category"]
    grouped.setdefault(cat, []).append((key, cfg))

for category in CATEGORY_ORDER:
    if category not in grouped:
        continue

    items = grouped[category]
    with st.expander(f"**{category}**  ({len(items)} indicators)", expanded=category in EXPANDED_BY_DEFAULT):
        n_cols = min(len(items), 3)
        det_cols = st.columns(n_cols, gap="small")

        for idx, (key, cfg) in enumerate(items):
            series = data.get(key, pd.Series(dtype=float)).dropna()
            series.index = pd.to_datetime(series.index).tz_localize(None)
            cutoff = pd.Timestamp.now() - pd.DateOffset(years=chart_years)
            series_view = series[series.index >= cutoff]

            with det_cols[idx % n_cols]:
                if series_view.empty:
                    st.markdown(f"**{cfg['name']}**\n\n_No data available_")
                    continue

                in_composite = key in COMPOSITE_INDICATORS
                score = ind_scores.get(key)
                _, line_color = classify(score) if score is not None else (None, "#4a90d9")

                val = latest_value(series_view)
                val_str = f"{val:{cfg['fmt']}} {cfg['unit']}" if val is not None else "—"
                score_label = f"  ·  Stress: {score:.0f}/100" if score is not None else ""

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=series_view.index, y=series_view.values, mode="lines",
                    line=dict(width=1.5, color=line_color),
                    fill="tozeroy", fillcolor=hex_to_rgba(line_color, alpha=0.1),
                    hovertemplate=f"<b>%{{x|%b %d, %Y}}</b><br>{cfg['name']}: %{{y:{cfg['fmt']}}} {cfg['unit']}<extra></extra>",
                ))
                fig.update_layout(
                    title=dict(
                        text=f"<b>{cfg['name']}</b><br>"
                             f"<span style='font-size:11px; color:#888;'>{val_str}{score_label}</span>",
                        font=dict(size=13), x=0,
                    ),
                    height=200, margin=dict(t=50, b=20, l=40, r=10),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(gridcolor="#1a1a1a", tickfont=dict(size=9, color="#666"), showgrid=False),
                    yaxis=dict(gridcolor="#222", tickfont=dict(size=9, color="#666")),
                    showlegend=False, hovermode="x unified",
                )
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

                desc = cfg.get("description", "")
                if desc:
                    st.markdown(f"<small style='color:#555;'>{desc}</small>", unsafe_allow_html=True)

# ─── Footer ──────────────────────────────────────────────────────────────────
st.divider()
st.markdown(
    "<div style='text-align:center; color:#444; font-size:11px; padding:8px 0;'>"
    "Data sourced from FRED (Federal Reserve Bank of St. Louis) and Yahoo Finance. "
    "CDS market stress is proxied by ICE BofA Option-Adjusted Spreads. "
    "Stress scores are rolling 2-year historical percentile ranks — not investment advice."
    "</div>",
    unsafe_allow_html=True,
)
