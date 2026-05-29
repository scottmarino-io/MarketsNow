"""Scrolling news ticker from RSS feeds."""

import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List

import streamlit as st
import streamlit.components.v1 as components

FEEDS = [
    ("https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en", "Google"),
    ("https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC"),
    ("https://feeds.content.dowjones.io/public/rss/mw_topstories", "MarketWatch"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

MAX_PER_FEED = 8
MAX_TOTAL    = 20


@st.cache_data(ttl=600, show_spinner=False)
def fetch_ticker_headlines() -> List[dict]:
    """Fetch and merge headlines from multiple RSS feeds, sorted by pubDate."""
    items = []

    for url, source in FEEDS:
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read()
            root = ET.fromstring(raw)
            count = 0
            for item in root.findall(".//item"):
                if count >= MAX_PER_FEED:
                    break

                title_el = item.find("title")
                if title_el is None or not title_el.text:
                    continue
                title = re.sub(r"<[^>]+>", "", title_el.text).strip()
                if not title:
                    continue

                link_el = item.find("link")
                link = link_el.text.strip() if link_el is not None and link_el.text else "#"

                pub_el = item.find("pubDate")
                pub_dt = None
                if pub_el is not None and pub_el.text:
                    try:
                        pub_dt = parsedate_to_datetime(pub_el.text)
                    except Exception:
                        pass

                items.append({
                    "title":  title,
                    "link":   link,
                    "source": source,
                    "pub_dt": pub_dt or datetime.now(tz=timezone.utc),
                })
                count += 1
        except Exception:
            continue

    items.sort(key=lambda x: x["pub_dt"], reverse=True)
    return items[:MAX_TOTAL]


def render_ticker():
    """Render a CSS-animated scrolling headline bar."""
    headlines = fetch_ticker_headlines()
    if not headlines:
        return

    spans = "".join(
        f'<a href="{h["link"]}" target="_blank" class="ticker-item">'
        f'{h["title"]} <span class="ticker-source">{h["source"]}</span></a>'
        f'<span class="ticker-sep">•</span>'
        for h in headlines
    )

    n = len(headlines)
    speed = max(25, n * 4)

    html = f"""
    <style>
    .ticker-wrap {{
        width: 100%;
        overflow: hidden;
        background: #0a0a0f;
        border-bottom: 1px solid #1a1a24;
        padding: 4px 0;
        font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    }}
    .ticker-move {{
        display: inline-block;
        white-space: nowrap;
        animation: ticker {speed}s linear infinite;
    }}
    .ticker-move:hover {{ animation-play-state: paused; }}
    @keyframes ticker {{
        0%   {{ transform: translateX(100%); }}
        100% {{ transform: translateX(-100%); }}
    }}
    .ticker-item {{
        color: #b0b8d1;
        text-decoration: none;
        font-size: 11px;
        letter-spacing: 0.02em;
        padding: 0 12px;
    }}
    .ticker-item:hover {{ color: #e0e4f0; text-decoration: underline; }}
    .ticker-source {{
        color: #4a90d9;
        font-size: 9px;
        text-transform: uppercase;
        margin-left: 4px;
    }}
    .ticker-sep {{ color: #2a2a3a; padding: 0 4px; font-size: 10px; }}
    </style>
    <div class="ticker-wrap">
        <div class="ticker-move">{spans}{spans}</div>
    </div>
    """
    components.html(html, height=28, scrolling=False)
