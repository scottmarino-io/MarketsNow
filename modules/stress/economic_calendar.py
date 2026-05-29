"""Economic calendar from Forex Factory RSS, enriched with FRED actuals."""

import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional

import streamlit as st


FRED_MAP = {
    "Nonfarm Payrolls":           "PAYEMS",
    "Unemployment Rate":          "UNRATE",
    "CPI m/m":                    "CPIAUCSL",
    "Core CPI m/m":               "CPILFESL",
    "PPI m/m":                    "PPIACO",
    "Retail Sales m/m":           "RSAFS",
    "Advance GDP q/q":            "GDP",
    "Prelim GDP q/q":             "GDP",
    "Final GDP q/q":              "GDP",
    "ISM Manufacturing PMI":      "MANEMP",
    "Consumer Confidence":        "UMCSENT",
    "Prelim UoM Consumer Sentiment": "UMCSENT",
    "Initial Jobless Claims":     "ICSA",
    "Existing Home Sales":        "EXHOSLUSM495S",
    "New Home Sales":             "HSN1F",
    "Durable Goods Orders m/m":   "DGORDER",
    "Industrial Production m/m":  "INDPRO",
    "Trade Balance":              "BOPGSTB",
    "JOLTS Job Openings":         "JTSJOL",
    "PCE Price Index m/m":        "PCEPI",
    "Core PCE Price Index m/m":   "PCEPILFE",
}


@st.cache_data(ttl=3600, show_spinner=False)
def _lookup_actual(api_key: str, event_title: str) -> Optional[str]:
    """Try to find the latest FRED value for a known economic event."""
    fred_id = FRED_MAP.get(event_title)
    if not fred_id or not api_key:
        return None
    try:
        from fredapi import Fred
        fred = Fred(api_key=api_key)
        series = fred.get_series(fred_id, observation_start="2024-01-01")
        if series.empty:
            return None
        val = float(series.dropna().iloc[-1])
        if abs(val) > 1_000_000:
            return f"{val / 1e9:.1f}B" if abs(val) >= 1e9 else f"{val / 1e6:.1f}M"
        return f"{val:.1f}"
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_ff_calendar() -> List[Dict]:
    """Fetch this week's USD events from Forex Factory RSS."""
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/xml, text/xml, */*",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
    except Exception:
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    now = datetime.now()
    events = []

    for e in root.findall("event"):
        country = (e.findtext("country") or "").strip()
        if country != "USD":
            continue

        title  = (e.findtext("title") or "").strip()
        date_s = (e.findtext("date") or "").strip()
        time_s = (e.findtext("time") or "").strip()
        impact = (e.findtext("impact") or "").strip()
        fore   = (e.findtext("forecast") or "").strip() or "—"
        prev   = (e.findtext("previous") or "").strip() or "—"
        link   = (e.findtext("url") or "").strip()

        dt = None
        for fmt in ("%m-%d-%Y %I:%M%p", "%m-%d-%Y %I%p", "%m-%d-%Y"):
            try:
                dt = datetime.strptime(f"{date_s} {time_s}".strip(), fmt)
                break
            except ValueError:
                continue
        if dt is None:
            continue

        events.append({
            "datetime": dt,
            "date_str": dt.strftime("%a %b %-d"),
            "time_str": dt.strftime("%-I:%M %p") if time_s else "All Day",
            "title": title,
            "impact": impact,
            "forecast": fore,
            "previous": prev,
            "is_past": dt < now,
            "url": f"https://www.forexfactory.com{link}" if link.startswith("/") else link,
        })

    return sorted(events, key=lambda x: x["datetime"])


def build_calendar(api_key: str, impact_filter: List[str] = None) -> List[Dict]:
    """Return calendar events enriched with FRED actual values where available."""
    events = fetch_ff_calendar()
    if not events:
        return []

    if impact_filter:
        events = [e for e in events if e["impact"] in impact_filter]

    now = datetime.now()
    for ev in events:
        if ev["is_past"]:
            actual = _lookup_actual(api_key, ev["title"])
            ev["actual"] = actual if actual else "Released"
        else:
            ev["actual"] = "—"

    return events
