"""
Shared render helpers for Unusual Whales cards (Market Tide + Gamma Exposure).

Written once, correctly, rather than duplicated per-page — unlike the
pre-existing Market Breadth TICK cards (duplicated 3x across app.py,
Wheel Screener, and Market Monitor), which this change deliberately does
NOT refactor, to keep this addition's diff scoped to what's new.

Reuses the existing `.metric-card` CSS class and `theme.PLOTLY_LAYOUT` for
chart styling (the pre-existing breadth charts redeclare bgcolor/font inline
instead of using PLOTLY_LAYOUT — new charts here use it properly).
"""

from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules.shared import theme


def render_tide_card(tide: dict, col=None) -> None:
    """Render a single compact Market Tide card. `tide` is the dict returned by
    fetch_market_tide_eod() — net_call_premium / net_put_premium / net_volume / as_of."""
    target = col if col is not None else st

    call_prem = tide.get("net_call_premium")
    put_prem = tide.get("net_put_premium")

    if call_prem is None or put_prem is None:
        target.markdown(
            "<div class='metric-card'>"
            "<div class='metric-label'>Market Tide</div>"
            "<div class='metric-sub'>No data</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        return

    net = call_prem + put_prem
    color = theme.GREEN if net >= 0 else theme.RED
    sign = "+" if net >= 0 else ""
    net_m = net / 1_000_000
    as_of = tide.get("as_of")
    sub = f"as of last close · {as_of}" if as_of else "as of last close"

    target.markdown(
        f"<div class='metric-card'>"
        f"<div class='metric-label'>Market Tide (Net Premium)</div>"
        f"<div class='metric-value' style='color:{color}'>{sign}${net_m:,.1f}M</div>"
        f"<div class='metric-sub'>{sub} · calls − puts</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_gamma_card(
    levels_vol: dict,
    levels_oi: Optional[dict] = None,
    spot_price: Optional[float] = None,
    col=None,
) -> None:
    """Render a compact Gamma Exposure card for the `vol` basis (intraday-reactive
    dealer positioning), optionally with `oi`-basis (structural) walls alongside.
    Discloses which basis each number comes from — these are NOT the same
    measurement and shouldn't be read as interchangeable."""
    target = col if col is not None else st

    if not levels_vol or levels_vol.get("gamma_flip") is None:
        target.markdown(
            "<div class='metric-card'>"
            "<div class='metric-label'>Gamma Exposure (SPY)</div>"
            "<div class='metric-sub'>No data</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        return

    flip = levels_vol.get("gamma_flip")
    call_wall = levels_vol.get("call_wall")
    put_wall = levels_vol.get("put_wall")

    bias_html = ""
    if spot_price is not None and flip is not None:
        above = spot_price >= flip
        bias_color = theme.GREEN if above else theme.RED
        bias_label = "above flip (dampening)" if above else "below flip (accelerating)"
        bias_html = f"<div class='metric-sub' style='color:{bias_color}'>Spot {bias_label}</div>"

    walls_line = ""
    if call_wall is not None and put_wall is not None:
        walls_line = (
            f"<div class='metric-sub'>Walls (vol): "
            f"<span style='color:{theme.RED}'>{put_wall:g}</span> / "
            f"<span style='color:{theme.GREEN}'>{call_wall:g}</span></div>"
        )

    oi_line = ""
    if levels_oi and levels_oi.get("gamma_flip") is not None:
        oi_flip = levels_oi.get("gamma_flip")
        oi_call = levels_oi.get("call_wall")
        oi_put = levels_oi.get("put_wall")
        oi_walls = (
            f"{oi_put:g} / {oi_call:g}" if oi_call is not None and oi_put is not None else "—"
        )
        oi_line = (
            f"<div class='metric-sub' style='margin-top:4px;border-top:1px solid #313244;padding-top:4px'>"
            f"Structural (OI): flip {oi_flip:g} · walls {oi_walls}</div>"
        )

    target.markdown(
        f"<div class='metric-card'>"
        f"<div class='metric-label'>Gamma Flip (SPY, vol basis)</div>"
        f"<div class='metric-value'>{flip:g}</div>"
        f"{bias_html}"
        f"{walls_line}"
        f"{oi_line}"
        f"<div class='metric-sub' style='margin-top:4px;color:#585b70'>"
        f"intraday-reactive — directional-volume basis, not a fixed level</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_tide_history_chart(tide_df: pd.DataFrame) -> go.Figure:
    """Line chart of net_call_premium vs. net_put_premium across today's ticks."""
    fig = go.Figure()
    if tide_df.empty:
        fig.update_layout(**theme.PLOTLY_LAYOUT, height=220)
        return fig

    # Ticks come with an ET UTC-offset (e.g. "2026-08-25T09:30:00-04:00"). Parse
    # then strip the tz rather than converting to UTC/browser-local, so the axis
    # shows the actual ET wall-clock time the data was captured at.
    x = pd.to_datetime(tide_df.index)
    if getattr(x, "tz", None) is not None:
        x = x.tz_localize(None)

    fig.add_trace(go.Scatter(
        x=x, y=tide_df["net_call_premium"], mode="lines", name="Net Call Premium",
        line=dict(color=theme.GREEN, width=2),
        hovertemplate="<b>%{x|%H:%M}</b><br>Net Call Premium: $%{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=x, y=tide_df["net_put_premium"], mode="lines", name="Net Put Premium",
        line=dict(color=theme.RED, width=2),
        hovertemplate="<b>%{x|%H:%M}</b><br>Net Put Premium: $%{y:,.0f}<extra></extra>",
    ))
    fig.add_hline(y=0, line_dash="dot", line_color=theme.OVERLAY, line_width=1)
    fig.update_layout(
        **theme.PLOTLY_LAYOUT,
        height=240,
        margin=dict(l=40, r=20, t=10, b=30),
        legend=dict(bgcolor=theme.SURFACE, borderwidth=0),
        xaxis=dict(
            gridcolor=theme.SURFACE, tickformat="%H:%M",
            tickfont=dict(size=10, color=theme.SUBTEXT), title="Time (ET)",
        ),
        yaxis=dict(gridcolor=theme.SURFACE, title="Premium ($)"),
        hovermode="x unified",
    )
    return fig
