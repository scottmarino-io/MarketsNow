"""Thin Unusual Whales REST client.

Deliberately minimal: auth, rate limiting, retry, and envelope unwrapping.
No business logic -- that lives in capture.py.

IMPORTANT -- verify PATHS before first run.
The path map below is the one thing here I could not verify. Get the exact
routes from either:

    curl -H "Accept: text/plain" https://api.unusualwhales.com/docs
    curl https://api.unusualwhales.com/api/openapi

or ask the MCP server directly via its `get_public_api_docs` tool. Fix any
mismatches in PATHS only -- nothing else in this module or capture.py hardcodes
a route.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE_URL = os.getenv("UW_BASE_URL", "https://api.unusualwhales.com")

# --- VERIFY THESE (see module docstring) -----------------------------------
PATHS: dict[str, str] = {
    # daily per-ticker day-state series; supports `limit` (<=500) and `date`
    "ticker_ohlc":   "/api/stock/{ticker}/ohlc/1d",
    # aggregate greek exposure history; supports a lookback timeframe
    "greek_exposure": "/api/stock/{ticker}/greek-exposure",
    # named GEX levels; supports `date` and `source` (vol|oi)
    "gex_levels":    "/api/stock/{ticker}/spot-exposures",
    # earnings history / upcoming with expected-move context
    "earnings":      "/api/earnings/{ticker}",
}
# ---------------------------------------------------------------------------


class RateLimitError(RuntimeError):
    pass


class _TokenBucket:
    """Simple thread-safe token bucket.

    UW does not publish a hard public rate limit that I could confirm, so the
    default here is conservative. Raise it once you have measured what your
    tier actually tolerates -- a backfill of 170 tickers is only a few hundred
    calls and there is no reason to be aggressive about it.
    """

    def __init__(self, rate_per_sec: float, capacity: int) -> None:
        self.rate = rate_per_sec
        self.capacity = capacity
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def take(self, n: int = 1) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._last) * self.rate
                )
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                deficit = (n - self._tokens) / self.rate
            time.sleep(deficit)


class UWClient:
    def __init__(
        self,
        api_key: str | None = None,
        rate_per_sec: float = 2.0,
        burst: int = 4,
        timeout: float = 30.0,
        max_retries: int = 4,
    ) -> None:
        self.api_key = api_key or os.getenv("UW_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "UW_API_KEY not set. Add it to .env alongside MASSIVE_API_KEY "
                "and load it through modules.shared.api_keys."
            )
        self.timeout = timeout
        self.max_retries = max_retries
        self._bucket = _TokenBucket(rate_per_sec, burst)
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": "MarketsNow/uw-capture",
            }
        )

    def get(self, path_key: str, *, params: dict | None = None, **fmt: Any) -> Any:
        """GET a mapped endpoint and return the unwrapped payload.

        UW responses are usually {"data": [...]}, sometimes a bare list, and
        occasionally {"data": {...}}. All three are handled; callers get the
        inner object.
        """
        path = PATHS[path_key].format(**fmt)
        url = f"{BASE_URL}{path}"

        backoff = 1.0
        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            self._bucket.take()
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_exc = exc
                log.warning("%s attempt %d transport error: %s", path, attempt, exc)
            else:
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", backoff))
                    log.warning("%s rate limited, sleeping %.1fs", path, retry_after)
                    time.sleep(retry_after)
                    backoff = min(backoff * 2, 60)
                    continue
                if resp.status_code in (401, 403):
                    raise RateLimitError(
                        f"{resp.status_code} on {path} -- key rejected or endpoint "
                        f"not included in your plan tier."
                    )
                if resp.status_code >= 500:
                    last_exc = RuntimeError(f"{resp.status_code} on {path}")
                    log.warning("%s attempt %d server error", path, attempt)
                else:
                    resp.raise_for_status()
                    body = resp.json()
                    if isinstance(body, dict) and "data" in body:
                        return body["data"]
                    return body

            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

        raise RuntimeError(f"{path} failed after {self.max_retries} attempts") from last_exc
