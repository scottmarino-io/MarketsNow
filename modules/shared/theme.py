"""Shared CSS styling and chart defaults for all pages."""

import streamlit as st


# ── Color palette (Catppuccin-inspired) ──────────────────────────────────────

GREEN = "#a6e3a1"
RED = "#f38ba8"
YELLOW = "#f9e2af"
BLUE = "#89b4fa"
PURPLE = "#cba6f7"
TEAL = "#94e2d5"
PEACH = "#fab387"
TEXT = "#cdd6f4"
SUBTEXT = "#a6adc8"
OVERLAY = "#6c7086"
SURFACE = "#313244"
BASE = "#1e1e2e"
MANTLE = "#181825"
CRUST = "#11111b"


def hex_to_rgba(hex_color: str, alpha: float = 0.15) -> str:
    """Convert a 6-digit hex color to an rgba() string Plotly accepts."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ── Plotly chart defaults ────────────────────────────────────────────────────

PLOTLY_LAYOUT = dict(
    plot_bgcolor="#1e1e2e",
    paper_bgcolor="#1e1e2e",
    font=dict(color="#cdd6f4"),
)

PLOTLY_LAYOUT_TRANSPARENT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font_color="#ccc",
)


# ── Shared CSS ───────────────────────────────────────────────────────────────

SHARED_CSS = """
<style>
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }

    .metric-card {
        background: #1e1e2e;
        border: 1px solid #313244;
        border-radius: 8px;
        padding: 12px 16px;
        text-align: center;
    }
    .metric-label { color: #a6adc8; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .metric-value { color: #cdd6f4; font-size: 1.4rem; font-weight: 600; }
    .metric-sub   { color: #6c7086; font-size: 0.8rem; }
    .up   { color: #a6e3a1 !important; }
    .down { color: #f38ba8 !important; }
    .neutral { color: #f9e2af !important; }

    .ind-card {
        background: #16181d;
        border-radius: 8px;
        padding: 14px 16px;
        margin-bottom: 10px;
        border-left: 4px solid #555;
    }
    .ind-label  { font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: .05em; }
    .ind-value  { font-size: 22px; font-weight: 700; color: #f0f0f0; line-height: 1.2; }
    .ind-delta  { font-size: 12px; margin-top: 2px; }
    .ind-score  { font-size: 11px; color: #777; margin-top: 6px; }

    .stress-badge {
        display: inline-block;
        padding: 4px 14px;
        border-radius: 20px;
        font-size: 15px;
        font-weight: 700;
        letter-spacing: .05em;
        margin-left: 12px;
    }

    .section-header {
        font-size: 13px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: .08em;
        color: #777;
        margin: 22px 0 8px 0;
        border-bottom: 1px solid #222;
        padding-bottom: 4px;
    }

    .wheel-zone { background-color: rgba(166,227,161,0.08) !important; }
    div[data-testid="stMetric"] label { font-size: 0.75rem !important; }
</style>
"""


def inject_css():
    """Inject shared CSS into the current Streamlit page."""
    st.markdown(SHARED_CSS, unsafe_allow_html=True)
