"""
Unusual Whales data fetching — Market Tide + Gamma Exposure (SPY proxy).

Unusual Whales has no first-party Python SDK, so this module talks to their
REST API directly over HTTP. Mirrors the house fetcher convention used
elsewhere in this app (see modules/stress/data_fetchers.py):
    - every cached function takes only primitive args (never a client object),
      so Streamlit's cache-key hashing stays correct
    - every function catches broadly at its own boundary, emits st.warning(),
      and returns an empty-but-correctly-shaped value rather than raising
    - TTLs are chosen per-source based on how fast the underlying data
      actually changes, not a single blanket value

Caveats baked into the TTL choices below (observed on live data):
    - Market Tide revises intraday even for 5-minute bars that have already
      "closed" — polling it frequently just captures noise, not signal.
    - GEX levels (source="vol") are intraday-reactive — real multi-point
      moves in gamma_flip were observed within 30 minutes on live SPY data.
      source="oi" is structurally more stable but shares the same endpoint/TTL
      here since Streamlit's cache is keyed per-`source` value automatically.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd
import requests
import streamlit as st

UW_BASE_URL = "https://api.unusualwhales.com"


def _uw_get(api_key: str, path: str, params: Optional[dict] = None, timeout: int = 10) -> dict:
    """Raw authenticated GET against the Unusual Whales REST API. Raises on failure —
    callers (the @st.cache_data-wrapped public functions below) are responsible for
    catching and degrading gracefully, matching the house convention."""
    url = f"{UW_BASE_URL}{path}"
    resp = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        params=params or {},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


# ── Market Tide ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_market_tide(api_key: str, date_str: Optional[str] = None) -> pd.DataFrame:
    """
    Fetch today's (or `date_str`'s) market-wide options Market Tide series —
    net call/put premium and net volume per 5-minute tick.

    TTL is 30 minutes, deliberately looser than a "live" cadence: Tide ticks
    revise even after they've nominally closed, so polling every 30s (the raw
    breadth-tick cadence) would just capture API noise/revisions with no
    informational gain.
    """
    try:
        params = {"interval_5m": "true", "otm_only": "false"}
        if date_str:
            params["date"] = date_str
        payload = _uw_get(api_key, "/api/market/market-tide", params)
        ticks = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not ticks:
            return pd.DataFrame(columns=["net_call_premium", "net_put_premium", "net_volume"])

        rows = []
        for t in ticks:
            ts = t.get("timestamp") or t.get("time") or t.get("tick_time")
            rows.append({
                "timestamp": ts,
                "net_call_premium": float(t.get("net_call_premium") or 0),
                "net_put_premium": float(t.get("net_put_premium") or 0),
                "net_volume": int(float(t.get("net_volume") or 0)),
            })
        df = pd.DataFrame(rows)
        if not df.empty and df["timestamp"].notna().any():
            df = df.set_index("timestamp")
        return df
    except Exception as exc:
        st.warning(f"Unusual Whales [market-tide]: {exc}")
        return pd.DataFrame(columns=["net_call_premium", "net_put_premium", "net_volume"])


def fetch_market_tide_eod(api_key: str, date_str: Optional[str] = None) -> dict:
    """
    Single settled end-of-day Tide reading (last tick of the day), as a flat dict.
    Used only by the Direction-forecast path, which wants one representative,
    non-revising value rather than an intraday series — see module docstring.
    """
    df = fetch_market_tide(api_key, date_str)
    if df.empty:
        return {
            "net_call_premium": None,
            "net_put_premium": None,
            "net_volume": None,
            "as_of": None,
        }
    last = df.iloc[-1]
    return {
        "net_call_premium": float(last["net_call_premium"]),
        "net_put_premium": float(last["net_put_premium"]),
        "net_volume": int(last["net_volume"]),
        "as_of": str(df.index[-1]) if df.index.name or not isinstance(df.index, pd.RangeIndex) else None,
    }


# ── Gamma Exposure (GEX levels) ──────────────────────────────────────────────

@st.cache_data(ttl=900, show_spinner=False)
def fetch_gex_levels(
    api_key: str,
    ticker: str = "SPY",
    source: str = "vol",
    date_str: Optional[str] = None,
) -> dict:
    """
    Fetch a ticker's key gamma-exposure price levels: call_wall, put_wall,
    gamma_magnet, gamma_flip, nearby_flips.

    `source="vol"` (directionalized volume) is intraday-reactive — 15 min TTL
    balances responsiveness against not hammering the endpoint on every rerun.
    `source="oi"` (open interest) is structurally stable; it shares this same
    function/TTL since Streamlit caches per-parameter-value automatically, so
    an `oi` call gets its own cache slot and 15 min staleness is a non-issue
    for data that only moves meaningfully day-to-day anyway.
    """
    empty = {
        "ticker": ticker,
        "source": source,
        "call_wall": None,
        "put_wall": None,
        "gamma_magnet": None,
        "gamma_flip": None,
        "nearby_flips": [],
        "as_of_date": None,
        "as_of_time": None,
    }
    try:
        params = {"source": source}
        if date_str:
            params["date"] = date_str
        payload = _uw_get(api_key, f"/api/stock/{ticker}/gex-levels", params)
        data = payload.get("data", payload) if isinstance(payload, dict) else payload

        def _f(key):
            v = data.get(key)
            return float(v) if v is not None else None

        return {
            "ticker": ticker,
            "source": data.get("source", source),
            "call_wall": _f("call_wall"),
            "put_wall": _f("put_wall"),
            "gamma_magnet": _f("gamma_magnet"),
            "gamma_flip": _f("gamma_flip"),
            "nearby_flips": [float(x) for x in (data.get("nearby_flips") or [])],
            "as_of_date": data.get("date"),
            "as_of_time": data.get("time"),
        }
    except Exception as exc:
        st.warning(f"Unusual Whales [gex-levels/{ticker}/{source}]: {exc}")
        return empty


# ── Display-layer convenience dataclasses ────────────────────────────────────

@dataclass
class GammaLevels:
    ticker: str = "SPY"
    source: str = "vol"
    call_wall: Optional[float] = None
    put_wall: Optional[float] = None
    gamma_magnet: Optional[float] = None
    gamma_flip: Optional[float] = None
    nearby_flips: list = field(default_factory=list)
    as_of_date: Optional[str] = None
    as_of_time: Optional[str] = None

    @property
    def has_data(self) -> bool:
        return self.gamma_flip is not None or self.call_wall is not None


@dataclass
class MarketTideSnapshot:
    net_call_premium: Optional[float] = None
    net_put_premium: Optional[float] = None
    net_volume: Optional[int] = None
    as_of: Optional[str] = None
    fetched_at: Optional[datetime] = None

    @property
    def net_premium(self) -> Optional[float]:
        """Net call minus put premium — positive = call-skewed / bullish flow."""
        if self.net_call_premium is None or self.net_put_premium is None:
            return None
        return self.net_call_premium + self.net_put_premium

    @property
    def has_data(self) -> bool:
        return self.net_call_premium is not None
