"""Centralized API key resolution for all pages."""

import os

import streamlit as st


def _resolve_key(
    secret_name: str,
    env_name: str,
    label: str,
    placeholder: str,
    help_text: str,
    required: bool = True,
) -> str:
    """Try st.secrets → env → sidebar input."""
    key = ""
    try:
        key = st.secrets[secret_name]
    except (KeyError, FileNotFoundError):
        key = os.environ.get(env_name, "")

    if key:
        return key

    if required:
        key = st.text_input(
            label,
            type="password",
            placeholder=placeholder,
            help=help_text,
        )
    else:
        key = st.text_input(
            label,
            type="password",
            placeholder=placeholder,
            help=help_text,
        )
    return key or ""


def render_api_key_sidebar() -> dict:
    """Render API key inputs in the sidebar. Returns dict of resolved keys."""
    with st.sidebar:
        st.markdown("## 🔑 API Keys")

        fred_key = _resolve_key(
            "FRED_API_KEY",
            "FRED_API_KEY",
            "FRED API Key",
            "Free key — required for Market Stress",
            "Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html",
            required=False,
        )
        if fred_key:
            st.success("FRED ✓", icon="🔑")

        massive_key = _resolve_key(
            "MASSIVE_API_KEY",
            "MASSIVE_API_KEY",
            "Massive API Key",
            "Required for Screener / Monitor / Options",
            "Get a key at https://massive.com — Stocks Starter plan or higher",
            required=False,
        )
        if massive_key:
            st.success("Massive ✓", icon="🔑")

        anthropic_key = _resolve_key(
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_API_KEY",
            "Anthropic API Key",
            "Optional — enables AI features",
            "Get a key at https://console.anthropic.com",
            required=False,
        )
        if anthropic_key:
            st.success("Anthropic ✓", icon="🤖")

        st.divider()

    return {
        "fred": fred_key,
        "massive": massive_key,
        "anthropic": anthropic_key,
    }


def get_fred_key() -> str:
    """Get FRED API key from secrets/env."""
    try:
        return st.secrets["FRED_API_KEY"]
    except (KeyError, FileNotFoundError):
        return os.environ.get("FRED_API_KEY", "")


def get_massive_key() -> str:
    """Get Massive API key from secrets/env."""
    try:
        return st.secrets["MASSIVE_API_KEY"]
    except (KeyError, FileNotFoundError):
        return os.environ.get("MASSIVE_API_KEY", "")


def get_anthropic_key() -> str:
    """Get Anthropic API key from secrets/env."""
    try:
        return st.secrets["ANTHROPIC_API_KEY"]
    except (KeyError, FileNotFoundError):
        return os.environ.get("ANTHROPIC_API_KEY", "")
