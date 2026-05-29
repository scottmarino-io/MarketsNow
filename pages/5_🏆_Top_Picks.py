"""Top Picks — Medium/long-term investment opportunity screener."""

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

from modules.screener.universe import SP500_COMBINED, SECTOR_OVERRIDES
from modules.screener.earnings import (
    fetch_earnings_data,
    fetch_earnings_details,
    fetch_price_history_extended,
)
from modules.screener.investment_signals import (
    build_top_picks_df,
    conviction_grade,
)
from modules.shared.theme import inject_css

load_dotenv()

st.set_page_config(
    page_title="Top Picks · MarketsNow",
    page_icon="🏆",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_css()

st.markdown("""
<style>
    .tag-chip {
        display:inline-block; padding:1px 7px; margin:1px; border-radius:9px;
        font-size:0.65rem; font-weight:600; letter-spacing:.04em;
    }
    div[data-testid="stMetric"] label { font-size:.7rem !important }
    .conviction-A { color:#a6e3a1; font-weight:700 }
    .conviction-B { color:#94e2d5; font-weight:700 }
    .conviction-C { color:#f9e2af; font-weight:600 }
    .conviction-D { color:#fab387; font-weight:600 }
    .conviction-F { color:#f38ba8; font-weight:600 }
    .beat-dot { display:inline-block; width:12px; height:12px; border-radius:50%; margin:1px }
    .beat-hit { background:#a6e3a1 }
    .beat-miss { background:#f38ba8 }
    .beat-na { background:#45475a }
</style>
""", unsafe_allow_html=True)


# ── rendering helpers ─────────────────────────────────────────────────────────

TAG_COLORS = {
    "EARNINGS MOMENTUM": ("#a6e3a1", "#1e3a2f"),
    "BEAT & RAISE":      ("#94e2d5", "#1e3a3a"),
    "REVENUE ACCELERATOR": ("#89b4fa", "#1e2a3a"),
    "QUALITY COMPOUNDER":  ("#cba6f7", "#3a1e3a"),
    "VALUE RECOVERY":      ("#f9e2af", "#2a2a1e"),
    "HIGH CONVICTION":     ("#f5c2e7", "#3a1e2f"),
}

def render_tags(tag_str: str) -> str:
    if not tag_str or pd.isna(tag_str):
        return ""
    html = ""
    for tag in tag_str.split("  "):
        for t in tag.strip().split("  "):
            t = t.strip()
            if not t:
                continue
            fg, bg = TAG_COLORS.get(t, ("#cdd6f4", "#313244"))
            html += (f"<span style='background:{bg};color:{fg};padding:1px 6px;border-radius:8px;"
                     f"font-size:.65rem;font-weight:600;margin:1px;display:inline-block'>{t}</span>")
    return html

def render_tags_from_list(tags: str) -> str:
    if not tags:
        return ""
    parts = []
    for t in tags.split():
        combined = t
        parts.append(combined)
    result_tags = []
    i = 0
    raw = tags.strip()
    for tag_name in TAG_COLORS:
        if tag_name in raw:
            result_tags.append(tag_name)
    remaining = raw
    for t in result_tags:
        remaining = remaining.replace(t, "")
    for word in remaining.split():
        if word.strip():
            found = False
            for t in result_tags:
                if word in t:
                    found = True
                    break
            if not found:
                result_tags.append(word)
    html = ""
    for t in result_tags:
        fg, bg = TAG_COLORS.get(t, ("#cdd6f4", "#313244"))
        html += (f"<span style='background:{bg};color:{fg};padding:1px 6px;border-radius:8px;"
                 f"font-size:.65rem;font-weight:600;margin:1px;display:inline-block'>{t}</span>")
    return html


def conviction_html(grade: str) -> str:
    return f"<span class='conviction-{grade}'>{grade}</span>"


def fmt_pct(v, decimals=1) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    color = "#a6e3a1" if v > 0 else ("#f38ba8" if v < 0 else "#cdd6f4")
    arrow = "▲" if v > 0 else ("▼" if v < 0 else "─")
    return f"<span style='color:{color}'>{arrow}{abs(v):.{decimals}f}%</span>"


def beat_dots(detail: dict) -> str:
    surprises = detail.get("eps_surprises", [])
    if not surprises:
        return "—"
    html = ""
    for s in surprises:
        if s is None:
            html += "<span class='beat-dot beat-na'></span>"
        elif s > 0:
            html += "<span class='beat-dot beat-hit'></span>"
        else:
            html += "<span class='beat-dot beat-miss'></span>"
    return html


# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🏆 Top Picks")
    st.caption("S&P 500 + Nasdaq 100  ·  ~500 stocks")
    st.caption("Medium/long-term investment opportunities")
    st.divider()

    st.subheader("Filters")
    top_n = st.slider("Show top N picks", 10, 100, 20, step=5)

    conviction_filter = st.multiselect(
        "Conviction grade",
        ["A", "B", "C", "D", "F"],
        default=["A", "B"],
    )

    all_sectors = ["All Sectors", "Technology", "Financials", "Healthcare",
                   "Consumer Cyclical", "Consumer Defensive", "Industrials",
                   "Energy", "Communication Services", "Utilities",
                   "Real Estate", "Basic Materials"]
    sector_filter = st.selectbox("Sector", all_sectors)

    min_mktcap = st.selectbox("Min market cap", ["Any", "$10B+", "$50B+", "$100B+", "$500B+"], index=0)

    st.divider()
    st.subheader("Strategy Filter")
    strat_filter = st.multiselect(
        "Show only",
        list(TAG_COLORS.keys()),
        default=[],
        help="Leave empty to show all strategies",
    )

    st.divider()
    if st.button("🔄  Refresh all data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Prices: 1 hr  ·  Earnings: 6 hr  ·  First load ~2-3 min")


# ── load data ─────────────────────────────────────────────────────────────────

universe = tuple(SP500_COMBINED)

@st.cache_data(ttl=3600, show_spinner=False)
def _load_top_picks():
    prices = fetch_price_history_extended(universe, period="1y")
    earnings_df = fetch_earnings_data(universe)
    earnings_det = fetch_earnings_details(universe)
    return build_top_picks_df(prices, earnings_df, earnings_det), earnings_det

with st.spinner("Loading investment data… first run takes ~2-3 minutes for 500 tickers, then cached."):
    df, earnings_details = _load_top_picks()

if df.empty:
    st.error("No data loaded. Check your connection and try refreshing.")
    st.stop()

# ── apply filters ─────────────────────────────────────────────────────────────

fdf = df.copy()

if conviction_filter:
    fdf = fdf[fdf["conviction"].isin(conviction_filter)]

if sector_filter != "All Sectors":
    fdf = fdf[fdf["sector"] == sector_filter]

cap_map = {"$10B+": 1e10, "$50B+": 5e10, "$100B+": 1e11, "$500B+": 5e11}
if min_mktcap != "Any" and "marketCap" in fdf.columns:
    fdf = fdf[fdf["marketCap"].fillna(0) >= cap_map[min_mktcap]]

if strat_filter:
    mask = fdf["tags"].apply(lambda t: any(s in (t or "") for s in strat_filter))
    fdf = fdf[mask]

fdf = fdf.head(top_n)

# ── summary cards ─────────────────────────────────────────────────────────────

total = len(fdf)
a_count = (fdf["conviction"] == "A").sum()
b_count = (fdf["conviction"] == "B").sum()
avg_score = fdf["composite"].mean() if total > 0 else 0
tagged = (fdf["tags"].str.len() > 0).sum() if total > 0 else 0

best_sector = fdf["sector"].mode().iloc[0] if total > 0 and "sector" in fdf.columns and not fdf["sector"].isna().all() else "—"

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Picks shown", total)
c2.metric("A-Rated", a_count)
c3.metric("B-Rated", b_count)
c4.metric("Avg score", f"{avg_score:+.1f}" if total else "—")
c5.metric("Top sector", best_sector)

st.divider()

# ── tabs ──────────────────────────────────────────────────────────────────────

tab_picks, tab_earnings, tab_dive = st.tabs(
    ["🏆 Top Picks", "📊 Earnings Dashboard", "🔍 Deep Dive"]
)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1: TOP PICKS TABLE
# ═══════════════════════════════════════════════════════════════════════════════

with tab_picks:
    if fdf.empty:
        st.info("No stocks match the current filters. Try adjusting conviction grade or sector.")
    else:
        display_cols = ["rank", "ticker", "shortName", "sector", "price",
                        "composite", "conviction", "tech_score", "earnings_score", "fund_score",
                        "ret_1m", "ret_3m", "rsi14", "tags"]

        existing = [c for c in display_cols if c in fdf.columns]
        tbl = fdf[existing].copy()

        # Build HTML table
        header_labels = {
            "rank": "#", "ticker": "Ticker", "shortName": "Name", "sector": "Sector",
            "price": "Price", "composite": "Score", "conviction": "Conv",
            "tech_score": "Tech", "earnings_score": "Earn", "fund_score": "Fund",
            "ret_1m": "1M", "ret_3m": "3M", "rsi14": "RSI", "tags": "Signals",
        }

        html = "<table style='width:100%;border-collapse:collapse;font-size:.82rem'>"
        html += "<thead><tr style='border-bottom:2px solid #45475a'>"
        for col in existing:
            html += f"<th style='text-align:left;padding:6px 8px;color:#a6adc8;font-weight:600'>{header_labels.get(col, col)}</th>"
        html += "</tr></thead><tbody>"

        for _, row in tbl.iterrows():
            conv = row.get("conviction", "C")
            bg = "#1e3a2f" if conv == "A" else "#1e2a3a" if conv == "B" else "transparent"
            html += f"<tr style='border-bottom:1px solid #313244;background:{bg}'>"
            for col in existing:
                val = row.get(col)
                if col == "rank":
                    html += f"<td style='padding:5px 8px;color:#6c7086'>{int(val)}</td>"
                elif col == "ticker":
                    html += f"<td style='padding:5px 8px;font-weight:700;color:#cdd6f4'>{val}</td>"
                elif col == "shortName":
                    name = str(val)[:25] if pd.notna(val) else "—"
                    html += f"<td style='padding:5px 8px;color:#a6adc8'>{name}</td>"
                elif col == "sector":
                    html += f"<td style='padding:5px 8px;color:#a6adc8;font-size:.75rem'>{val if pd.notna(val) else '—'}</td>"
                elif col == "price":
                    html += f"<td style='padding:5px 8px;color:#cdd6f4'>${val:.2f}</td>" if pd.notna(val) else "<td style='padding:5px 8px'>—</td>"
                elif col == "composite":
                    color = "#a6e3a1" if val >= 5 else "#94e2d5" if val >= 0 else "#f38ba8"
                    html += f"<td style='padding:5px 8px;color:{color};font-weight:700'>{val:+d}</td>"
                elif col == "conviction":
                    html += f"<td style='padding:5px 8px'>{conviction_html(val)}</td>"
                elif col in ("tech_score", "earnings_score", "fund_score"):
                    v = int(val) if pd.notna(val) else 0
                    color = "#a6e3a1" if v > 0 else "#f38ba8" if v < 0 else "#6c7086"
                    html += f"<td style='padding:5px 8px;color:{color}'>{v:+d}</td>"
                elif col in ("ret_1m", "ret_3m"):
                    html += f"<td style='padding:5px 8px'>{fmt_pct(val)}</td>"
                elif col == "rsi14":
                    if pd.notna(val):
                        color = "#f38ba8" if val >= 70 else "#a6e3a1" if val <= 30 else "#f9e2af"
                        html += f"<td style='padding:5px 8px;color:{color}'>{val:.0f}</td>"
                    else:
                        html += "<td style='padding:5px 8px'>—</td>"
                elif col == "tags":
                    html += f"<td style='padding:5px 8px'>{render_tags_from_list(str(val) if pd.notna(val) else '')}</td>"
                else:
                    html += f"<td style='padding:5px 8px'>{val}</td>"
            html += "</tr>"

        html += "</tbody></table>"
        st.markdown(html, unsafe_allow_html=True)

        st.caption(f"Showing top {total} of {len(df)} stocks · Scored by technical + earnings + fundamental quality")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2: EARNINGS DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

with tab_earnings:
    if fdf.empty:
        st.info("No stocks to display. Adjust filters.")
    else:
        st.subheader("Earnings Beat/Miss Heatmap (Last 4 Quarters)")
        st.caption("🟢 = Beat  🔴 = Miss  ⚫ = N/A")

        heatmap_html = "<table style='width:100%;border-collapse:collapse;font-size:.82rem'>"
        heatmap_html += "<thead><tr style='border-bottom:2px solid #45475a'>"
        heatmap_html += "<th style='text-align:left;padding:6px 8px;color:#a6adc8'>Ticker</th>"
        heatmap_html += "<th style='text-align:left;padding:6px 8px;color:#a6adc8'>Name</th>"
        heatmap_html += "<th style='text-align:center;padding:6px 8px;color:#a6adc8'>Q1</th>"
        heatmap_html += "<th style='text-align:center;padding:6px 8px;color:#a6adc8'>Q2</th>"
        heatmap_html += "<th style='text-align:center;padding:6px 8px;color:#a6adc8'>Q3</th>"
        heatmap_html += "<th style='text-align:center;padding:6px 8px;color:#a6adc8'>Q4</th>"
        heatmap_html += "<th style='text-align:center;padding:6px 8px;color:#a6adc8'>Beat %</th>"
        heatmap_html += "<th style='text-align:left;padding:6px 8px;color:#a6adc8'>Earn Score</th>"
        heatmap_html += "<th style='text-align:left;padding:6px 8px;color:#a6adc8'>Next Earnings</th>"
        heatmap_html += "</tr></thead><tbody>"

        for _, row in fdf.iterrows():
            ticker = row["ticker"]
            detail = earnings_details.get(ticker, {})
            surprises = detail.get("eps_surprises", [])
            name = str(row.get("shortName", ""))[:20] if pd.notna(row.get("shortName")) else "—"
            es = int(row.get("earnings_score", 0))
            es_color = "#a6e3a1" if es > 0 else "#f38ba8" if es < 0 else "#6c7086"
            next_earn = detail.get("next_earnings_date", "—")
            days_to = detail.get("days_to_earnings")
            next_str = f"{next_earn}"
            if days_to is not None:
                next_str += f" ({days_to}d)"

            # Pad surprises to 4
            while len(surprises) < 4:
                surprises.insert(0, None)
            surprises = surprises[-4:]

            beat_count = sum(1 for s in surprises if s is not None and s > 0)
            total_known = sum(1 for s in surprises if s is not None)
            beat_pct = f"{beat_count}/{total_known}" if total_known > 0 else "—"

            heatmap_html += f"<tr style='border-bottom:1px solid #313244'>"
            heatmap_html += f"<td style='padding:5px 8px;font-weight:700;color:#cdd6f4'>{ticker}</td>"
            heatmap_html += f"<td style='padding:5px 8px;color:#a6adc8;font-size:.75rem'>{name}</td>"

            for s in surprises:
                if s is None:
                    heatmap_html += "<td style='text-align:center;padding:5px 8px'><span class='beat-dot beat-na'></span></td>"
                elif s > 0:
                    heatmap_html += f"<td style='text-align:center;padding:5px 8px'><span class='beat-dot beat-hit' title='+{s:.1f}%'></span></td>"
                else:
                    heatmap_html += f"<td style='text-align:center;padding:5px 8px'><span class='beat-dot beat-miss' title='{s:.1f}%'></span></td>"

            heatmap_html += f"<td style='text-align:center;padding:5px 8px;color:#cdd6f4'>{beat_pct}</td>"
            heatmap_html += f"<td style='padding:5px 8px;color:{es_color}'>{es:+d}</td>"
            heatmap_html += f"<td style='padding:5px 8px;color:#a6adc8;font-size:.75rem'>{next_str}</td>"
            heatmap_html += "</tr>"

        heatmap_html += "</tbody></table>"
        st.markdown(heatmap_html, unsafe_allow_html=True)

        # EPS Trend sparklines for top 10
        st.divider()
        st.subheader("EPS Trend — Top 10")

        top10 = fdf.head(10)
        cols_per_row = 5
        for start in range(0, len(top10), cols_per_row):
            chunk = top10.iloc[start:start + cols_per_row]
            cols = st.columns(cols_per_row)
            for idx, (_, row) in enumerate(chunk.iterrows()):
                ticker = row["ticker"]
                detail = earnings_details.get(ticker, {})
                actuals = detail.get("eps_actuals", [])
                estimates = detail.get("eps_estimates", [])

                with cols[idx]:
                    st.markdown(f"**{ticker}**")
                    if actuals and any(a is not None for a in actuals):
                        fig = go.Figure()
                        q_labels = [f"Q{i+1}" for i in range(len(actuals))]

                        act_vals = [a if a is not None else 0 for a in actuals]
                        est_vals = [e if e is not None else 0 for e in estimates] if estimates else []

                        fig.add_trace(go.Bar(
                            x=q_labels, y=act_vals, name="Actual",
                            marker_color="#a6e3a1", opacity=0.85,
                        ))
                        if est_vals and len(est_vals) == len(act_vals):
                            fig.add_trace(go.Scatter(
                                x=q_labels, y=est_vals, name="Estimate",
                                mode="lines+markers",
                                line=dict(color="#f9e2af", width=2, dash="dot"),
                                marker=dict(size=6),
                            ))

                        fig.update_layout(
                            height=150, margin=dict(l=5, r=5, t=5, b=5),
                            plot_bgcolor="#1e1e2e", paper_bgcolor="#1e1e2e",
                            font=dict(color="#cdd6f4", size=10),
                            showlegend=False,
                            xaxis=dict(gridcolor="#313244"),
                            yaxis=dict(gridcolor="#313244"),
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.caption("No EPS data")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3: DEEP DIVE
# ═══════════════════════════════════════════════════════════════════════════════

with tab_dive:
    if fdf.empty:
        st.info("No stocks to display.")
        st.stop()

    tickers_list = fdf["ticker"].tolist()
    sel = st.selectbox("Select ticker for deep dive", tickers_list,
                       format_func=lambda t: f"{t} — {fdf[fdf['ticker']==t]['shortName'].values[0]}"
                       if t in fdf["ticker"].values and pd.notna(fdf[fdf['ticker']==t]['shortName'].values[0])
                       else t)

    r = fdf[fdf["ticker"] == sel].iloc[0]
    detail = earnings_details.get(sel, {})
    name = str(r.get("shortName", "")) if pd.notna(r.get("shortName")) else sel
    sect = str(r.get("sector", "")) if pd.notna(r.get("sector")) else "—"
    conv = r.get("conviction", "C")

    comp = int(r.get("composite", 0))
    comp_color = "#a6e3a1" if comp >= 10 else "#94e2d5" if comp >= 5 else "#f9e2af" if comp >= 0 else "#f38ba8"

    st.markdown(
        f"### {sel} — {name}"
        f"&nbsp;&nbsp;<span class='conviction-{conv}'>Grade {conv}</span>"
        f"&nbsp;&nbsp;<span style='color:{comp_color};font-size:1rem'>Score: {comp:+d} / 17</span>"
        f"&nbsp;&nbsp;<span style='color:#6c7086;font-size:.85rem'>{sect}</span>",
        unsafe_allow_html=True,
    )
    if r.get("tags"):
        st.markdown(render_tags_from_list(str(r["tags"])), unsafe_allow_html=True)
    st.write("")

    # Score breakdown
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Price", f"${r['price']:.2f}" if pd.notna(r.get("price")) else "—")
    m2.metric("Technical", f"{int(r.get('tech_score', 0)):+d} / 7")
    m3.metric("Earnings", f"{int(r.get('earnings_score', 0)):+d} / 5")
    m4.metric("Fundamental", f"{int(r.get('fund_score', 0)):+d} / 5")
    m5.metric("1M Return", f"{r['ret_1m']:+.1f}%" if pd.notna(r.get("ret_1m")) else "—")
    m6.metric("vs Target", f"{r['vs_target']:+.1f}%" if pd.notna(r.get("vs_target")) else "—")

    st.divider()

    # Chart + Analysis
    chart_col, analysis_col = st.columns([2, 1])

    with chart_col:
        @st.cache_data(ttl=3600, show_spinner=False)
        def _load_dive_chart(ticker):
            t = yf.Ticker(ticker)
            return t.history(period="1y", interval="1d", auto_adjust=True)

        try:
            hist = _load_dive_chart(sel)
        except Exception:
            hist = pd.DataFrame()
            st.warning("⏳ Price chart temporarily unavailable (rate limited). Try again in a minute.")
        if not hist.empty:
            fig_chart = go.Figure()
            fig_chart.add_trace(go.Candlestick(
                x=hist.index,
                open=hist["Open"], high=hist["High"],
                low=hist["Low"], close=hist["Close"],
                name=sel,
                increasing_line_color="#a6e3a1",
                decreasing_line_color="#f38ba8",
            ))
            close = hist["Close"]
            for period, color, label in [(20, "#89b4fa", "SMA20"), (50, "#f9e2af", "SMA50"), (200, "#cba6f7", "SMA200")]:
                if len(close) >= period:
                    sma = close.rolling(period).mean()
                    fig_chart.add_trace(go.Scatter(
                        x=hist.index, y=sma, mode="lines",
                        name=label, line=dict(color=color, width=1.2, dash="dot"),
                    ))

            # Mark earnings dates on chart
            next_earn = detail.get("next_earnings_date")
            if next_earn:
                fig_chart.add_vline(
                    x=next_earn, line_width=2, line_dash="dash",
                    line_color="#f9e2af", annotation_text="📅 Earnings",
                    annotation_position="top left",
                    annotation=dict(font_color="#f9e2af", font_size=10),
                )

            fig_chart.update_layout(
                plot_bgcolor="#1e1e2e", paper_bgcolor="#1e1e2e",
                font=dict(color="#cdd6f4"), height=400,
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis_rangeslider_visible=False,
                legend=dict(bgcolor="#313244", borderwidth=0),
                xaxis=dict(gridcolor="#313244"), yaxis=dict(gridcolor="#313244"),
            )
            st.plotly_chart(fig_chart, use_container_width=True)

            # Volume chart
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
                font=dict(color="#cdd6f4"), height=120,
                margin=dict(l=10, r=10, t=4, b=10),
                showlegend=False,
                xaxis=dict(gridcolor="#313244"), yaxis=dict(gridcolor="#313244"),
            )
            st.plotly_chart(fig_vol, use_container_width=True)

    with analysis_col:
        # Score breakdown
        st.subheader("Score Breakdown")
        scores = {
            "Technical": (int(r.get("tech_score", 0)), 7),
            "Earnings": (int(r.get("earnings_score", 0)), 5),
            "Fundamental": (int(r.get("fund_score", 0)), 5),
        }
        for label, (score, max_s) in scores.items():
            pct = (score + max_s) / (2 * max_s) * 100
            pct = max(0, min(100, pct))
            color = "#a6e3a1" if score > 0 else "#f38ba8" if score < 0 else "#6c7086"
            st.markdown(
                f"<div style='margin:4px 0'>"
                f"<div style='display:flex;justify-content:space-between'>"
                f"<span style='color:#a6adc8;font-size:.8rem'>{label}</span>"
                f"<span style='color:{color};font-size:.8rem;font-weight:600'>{score:+d}</span></div>"
                f"<div style='background:#313244;border-radius:4px;height:10px'>"
                f"<div style='background:{color};border-radius:4px;height:10px;width:{pct:.0f}%'></div>"
                f"</div></div>",
                unsafe_allow_html=True,
            )

        st.divider()

        # EPS beat/miss
        st.subheader("Earnings History")
        surprises = detail.get("eps_surprises", [])
        actuals = detail.get("eps_actuals", [])
        estimates = detail.get("eps_estimates", [])

        if actuals:
            for i, (act, est) in enumerate(zip(actuals, estimates or [None]*len(actuals))):
                if act is None:
                    continue
                s = surprises[i] if i < len(surprises) else None
                beat = s is not None and s > 0
                icon = "🟢" if beat else "🔴" if s is not None else "⚫"
                est_str = f"${est:.2f}" if est is not None else "—"
                s_str = f" ({s:+.1f}%)" if s is not None else ""
                st.markdown(
                    f"<div style='font-size:.8rem;padding:2px 0;border-bottom:1px solid #313244'>"
                    f"{icon} Q{i+1}: ${act:.2f} vs {est_str}{s_str}</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption("No earnings history available")

        st.divider()

        # Key fundamentals
        st.subheader("Fundamentals")
        def _pct_str(v, mult=100):
            return f"{v*mult:+.1f}%" if pd.notna(v) else "—"

        fund_tbl = {
            "Forward P/E":   f"{r.get('forwardPE', float('nan')):.1f}" if pd.notna(r.get("forwardPE")) else "—",
            "Trailing P/E":  f"{r.get('trailingPE', float('nan')):.1f}" if pd.notna(r.get("trailingPE")) else "—",
            "P/Book":        f"{r.get('priceToBook', float('nan')):.1f}" if pd.notna(r.get("priceToBook")) else "—",
            "EPS Growth":    _pct_str(r.get("earningsGrowth")),
            "Rev Growth":    _pct_str(r.get("revenueGrowth")),
            "Profit Margin": _pct_str(r.get("profitMargins")),
            "ROE":           _pct_str(r.get("returnOnEquity")),
            "Debt/Equity":   f"{r.get('debtToEquity', float('nan')):.0f}%" if pd.notna(r.get("debtToEquity")) else "—",
            "Beta":          f"{r.get('beta', float('nan')):.2f}" if pd.notna(r.get("beta")) else "—",
            "Analyst":       f"{r.get('recommendationMean', float('nan')):.1f}" if pd.notna(r.get("recommendationMean")) else "—",
        }
        for k, v in fund_tbl.items():
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #313244'>"
                f"<span style='color:#a6adc8;font-size:.8rem'>{k}</span>"
                f"<span style='color:#cdd6f4;font-size:.8rem;font-weight:600'>{v}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

    # Quarterly EPS chart (full width)
    st.divider()
    eps_col, rev_col = st.columns(2)

    with eps_col:
        st.subheader(f"Quarterly EPS — {sel}")
        actuals = detail.get("eps_actuals", [])
        estimates = detail.get("eps_estimates", [])
        if actuals and any(a is not None for a in actuals):
            q_labels = [f"Q{i+1}" for i in range(len(actuals))]
            fig_eps = go.Figure()

            act_colors = []
            for i, (a, e) in enumerate(zip(actuals, estimates or [None]*len(actuals))):
                if a is None:
                    act_colors.append("#45475a")
                elif e is not None and a >= e:
                    act_colors.append("#a6e3a1")
                elif e is not None:
                    act_colors.append("#f38ba8")
                else:
                    act_colors.append("#89b4fa")

            fig_eps.add_trace(go.Bar(
                x=q_labels,
                y=[a if a is not None else 0 for a in actuals],
                name="Actual EPS",
                marker_color=act_colors,
            ))
            if estimates and any(e is not None for e in estimates):
                fig_eps.add_trace(go.Scatter(
                    x=q_labels,
                    y=[e if e is not None else 0 for e in estimates],
                    name="Estimate",
                    mode="lines+markers",
                    line=dict(color="#f9e2af", width=2, dash="dot"),
                    marker=dict(size=8),
                ))
            fig_eps.update_layout(
                plot_bgcolor="#1e1e2e", paper_bgcolor="#1e1e2e",
                font=dict(color="#cdd6f4"), height=250,
                margin=dict(l=10, r=10, t=10, b=10),
                legend=dict(bgcolor="#313244"),
                xaxis=dict(gridcolor="#313244"), yaxis=dict(gridcolor="#313244", title="EPS ($)"),
            )
            st.plotly_chart(fig_eps, use_container_width=True)
        else:
            st.caption("No quarterly EPS data available.")

    with rev_col:
        st.subheader(f"Quarterly Revenue — {sel}")
        rev_vals = detail.get("quarterly_revenue", [])
        if rev_vals:
            q_labels = [f"Q{i+1}" for i in range(len(rev_vals))]
            fig_rev = go.Figure()
            fig_rev.add_trace(go.Bar(
                x=q_labels,
                y=rev_vals,
                name="Revenue",
                marker_color="#89b4fa",
            ))
            fig_rev.update_layout(
                plot_bgcolor="#1e1e2e", paper_bgcolor="#1e1e2e",
                font=dict(color="#cdd6f4"), height=250,
                margin=dict(l=10, r=10, t=10, b=10),
                legend=dict(bgcolor="#313244"),
                xaxis=dict(gridcolor="#313244"),
                yaxis=dict(gridcolor="#313244", title="Revenue ($)"),
            )
            st.plotly_chart(fig_rev, use_container_width=True)
        else:
            st.caption("No quarterly revenue data available.")

    # AI Investment Thesis
    st.divider()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        try:
            anthropic_key = st.secrets.get("ANTHROPIC_API_KEY", "")
        except (KeyError, FileNotFoundError):
            pass

    if anthropic_key:
        st.subheader(f"🤖 AI Investment Thesis — {sel}")

        @st.cache_data(ttl=86400, show_spinner=False)
        def _generate_thesis(_ticker: str, _name: str, _data_summary: str) -> str:
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=anthropic_key)
                message = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=800,
                    messages=[{
                        "role": "user",
                        "content": (
                            f"You are a senior equity analyst. Write a concise investment thesis for {_ticker} ({_name}). "
                            f"Use this data:\n\n{_data_summary}\n\n"
                            "Format: 2-3 sentence Bull Case, 2-3 sentence Bear Case, and a one-line Verdict. "
                            "Be specific with numbers. Use markdown formatting."
                        ),
                    }],
                )
                return message.content[0].text
            except Exception as e:
                return f"Could not generate thesis: {e}"

        data_summary = (
            f"Price: ${r.get('price', 0):.2f}, Sector: {sect}\n"
            f"Technical score: {int(r.get('tech_score', 0)):+d}/7, "
            f"Earnings score: {int(r.get('earnings_score', 0)):+d}/5, "
            f"Fundamental score: {int(r.get('fund_score', 0)):+d}/5\n"
            f"Conviction: {conv}, Composite: {comp:+d}/17\n"
            f"Forward P/E: {r.get('forwardPE', 'N/A')}, Trailing P/E: {r.get('trailingPE', 'N/A')}\n"
            f"EPS Growth: {r.get('earningsGrowth', 'N/A')}, Rev Growth: {r.get('revenueGrowth', 'N/A')}\n"
            f"Profit Margin: {r.get('profitMargins', 'N/A')}, ROE: {r.get('returnOnEquity', 'N/A')}\n"
            f"Debt/Equity: {r.get('debtToEquity', 'N/A')}, Beta: {r.get('beta', 'N/A')}\n"
            f"1M Return: {r.get('ret_1m', 'N/A')}%, 3M Return: {r.get('ret_3m', 'N/A')}%\n"
            f"RSI-14: {r.get('rsi14', 'N/A')}, vs Analyst Target: {r.get('vs_target', 'N/A')}%\n"
            f"Beats last 4Q: {detail.get('beat_count', 'N/A')}, Tags: {r.get('tags', 'None')}"
        )

        with st.spinner("Generating AI thesis…"):
            thesis = _generate_thesis(sel, name, data_summary)
        st.markdown(thesis)
    else:
        st.info("Add an **Anthropic API key** to generate AI investment theses.")

    # Recent news
    st.divider()
    st.subheader(f"Recent News — {sel}")

    @st.cache_data(ttl=1800, show_spinner=False)
    def _load_news_tp(ticker):
        try:
            from massive import RESTClient
            key = os.getenv("MASSIVE_API_KEY")
            if not key:
                return []
            return list(RESTClient(api_key=key).list_ticker_news(
                ticker=ticker, limit=8,
                params={"order": "desc", "sort": "published_utc"}
            ))
        except Exception:
            return []

    news = _load_news_tp(sel)
    if news:
        for article in news[:6]:
            title = getattr(article, "title", "Untitled")
            url = getattr(article, "article_url", "#")
            pub = getattr(article, "published_utc", "")
            pub_str = pub[:10] if pub else ""
            publisher = ""
            pubs = getattr(article, "publisher", None)
            if pubs:
                publisher = getattr(pubs, "name", "")
            st.markdown(
                f"<div style='padding:4px 0;border-bottom:1px solid #1e2025;font-size:.85rem'>"
                f"<a href='{url}' style='color:#89b4fa;text-decoration:none'>{title}</a>"
                f"<span style='color:#555;margin-left:8px;font-size:.75rem'>"
                f"{publisher} · {pub_str}</span></div>",
                unsafe_allow_html=True,
            )
    else:
        massive_key = os.environ.get("MASSIVE_API_KEY", "")
        if not massive_key:
            st.caption("Add a Massive API key for ticker-specific news.")
        else:
            st.caption("No recent news available.")
