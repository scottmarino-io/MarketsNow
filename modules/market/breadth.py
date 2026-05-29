"""
Market Breadth / $TICK Proxy
Calculates an Advance/Decline breadth proxy from get_snapshot_all("stocks").

Three measures:
    tick_all   — all ~12,500 stocks
    tick_nyse  — NYSE (XNYS) stocks only
    tick_nq    — Nasdaq (XNAS) stocks only
"""

import json
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_DIR        = Path(__file__).parent
CACHE_FILE  = _DIR / ".ticker_cache.json"
CACHE_TTL   = timedelta(hours=24)
HISTORY_LEN = 60


@dataclass
class BreadthSnapshot:
    up_all:   int = 0
    dn_all:   int = 0
    up_nyse:  int = 0
    dn_nyse:  int = 0
    up_nq:    int = 0
    dn_nq:    int = 0
    as_of:      Optional[datetime] = None
    fetched_at: Optional[datetime] = None

    @property
    def tick_all(self) -> int:
        return self.up_all - self.dn_all

    @property
    def tick_nyse(self) -> int:
        return self.up_nyse - self.dn_nyse

    @property
    def tick_nq(self) -> int:
        return self.up_nq - self.dn_nq

    @property
    def breadth_all_pct(self) -> Optional[float]:
        t = self.up_all + self.dn_all
        return (self.up_all / t * 100) if t > 0 else None

    @property
    def breadth_nyse_pct(self) -> Optional[float]:
        t = self.up_nyse + self.dn_nyse
        return (self.up_nyse / t * 100) if t > 0 else None

    @property
    def breadth_nq_pct(self) -> Optional[float]:
        t = self.up_nq + self.dn_nq
        return (self.up_nq / t * 100) if t > 0 else None

    @property
    def age_secs(self) -> Optional[int]:
        if not self.fetched_at:
            return None
        return int((datetime.now() - self.fetched_at).total_seconds())

    def __repr__(self) -> str:
        return (
            f"BreadthSnapshot("
            f"All: {self.tick_all:+,} ({self.breadth_all_pct:.1f}%◆)  "
            f"NYSE: {self.tick_nyse:+,} ({self.breadth_nyse_pct:.1f}%◆)  "
            f"NQ: {self.tick_nq:+,} ({self.breadth_nq_pct:.1f}%◆)  "
            f"age={self.age_secs}s)"
        )


class BreadthFetcher:
    """Fetches and computes market breadth from Massive API snapshots."""

    def __init__(self, client):
        self.client      = client
        self.nyse_set:   frozenset = frozenset()
        self.nasdaq_set: frozenset = frozenset()
        self.history:    deque[BreadthSnapshot] = deque(maxlen=HISTORY_LEN)
        self.latest:     Optional[BreadthSnapshot] = None
        self._lock       = threading.Lock()

    def load_exchange_tickers(self, force: bool = False) -> None:
        if not force and self._try_load_cache():
            return
        self._fetch_and_cache()

    def _try_load_cache(self) -> bool:
        try:
            if not CACHE_FILE.exists():
                return False
            data = json.loads(CACHE_FILE.read_text())
            cached_at = datetime.fromisoformat(data["cached_at"])
            if datetime.now() - cached_at > CACHE_TTL:
                return False
            self.nyse_set   = frozenset(data["nyse"])
            self.nasdaq_set = frozenset(data["nasdaq"])
            return True
        except Exception:
            return False

    def _fetch_and_cache(self) -> None:
        try:
            nyse   = [t.ticker for t in self.client.list_tickers(
                market="stocks", exchange="XNYS", active=True, limit=1000)]
            nasdaq = [t.ticker for t in self.client.list_tickers(
                market="stocks", exchange="XNAS", active=True, limit=1000)]
            self.nyse_set   = frozenset(nyse)
            self.nasdaq_set = frozenset(nasdaq)
            CACHE_FILE.write_text(json.dumps({
                "cached_at": datetime.now().isoformat(),
                "nyse":   nyse,
                "nasdaq": nasdaq,
            }))
        except Exception as e:
            print(f"[breadth] exchange ticker load failed: {e}")

    def fetch(self) -> BreadthSnapshot:
        """Fetch all stock snapshots and compute breadth counts. Thread-safe."""
        try:
            snaps = self.client.get_snapshot_all("stocks")
            snap  = self._compute(snaps)
        except Exception as e:
            print(f"[breadth] fetch error: {e}")
            snap = BreadthSnapshot(fetched_at=datetime.now())

        with self._lock:
            self.latest = snap
            self.history.append(snap)
        return snap

    def _compute(self, snaps: list) -> BreadthSnapshot:
        up_all = dn_all = 0
        up_nyse = dn_nyse = 0
        up_nq   = dn_nq   = 0
        latest_ts = 0

        for s in snaps:
            chg = getattr(s, "todays_change", None)
            if chg is None:
                continue

            ts = getattr(s, "updated", 0) or 0
            if ts > latest_ts:
                latest_ts = ts

            sym = getattr(s, "ticker", "")

            if chg > 0:
                up_all += 1
                if sym in self.nyse_set:   up_nyse += 1
                elif sym in self.nasdaq_set: up_nq  += 1
            elif chg < 0:
                dn_all += 1
                if sym in self.nyse_set:   dn_nyse += 1
                elif sym in self.nasdaq_set: dn_nq  += 1

        as_of = (
            datetime.fromtimestamp(latest_ts / 1e9)
            if latest_ts > 0 else None
        )

        return BreadthSnapshot(
            up_all=up_all, dn_all=dn_all,
            up_nyse=up_nyse, dn_nyse=dn_nyse,
            up_nq=up_nq,   dn_nq=dn_nq,
            as_of=as_of,
            fetched_at=datetime.now(),
        )

    def history_list(self) -> list[BreadthSnapshot]:
        with self._lock:
            return list(self.history)


if __name__ == "__main__":
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    from massive import RESTClient

    api_key = os.getenv("MASSIVE_API_KEY")
    if not api_key:
        print("ERROR: MASSIVE_API_KEY not set")
        raise SystemExit(1)

    print("Loading exchange tickers (cache or API)...", end=" ", flush=True)
    bf = BreadthFetcher(RESTClient(api_key=api_key))
    bf.load_exchange_tickers()
    print(f"done  (NYSE={len(bf.nyse_set):,}  Nasdaq={len(bf.nasdaq_set):,})")

    print("Fetching snapshot...", end=" ", flush=True)
    snap = bf.fetch()
    print("done\n")

    print(f"  All-Mkt TICK : {snap.tick_all:+,}  ({snap.breadth_all_pct:.1f}% advancing)")
    print(f"  NYSE    TICK : {snap.tick_nyse:+,}  ({snap.breadth_nyse_pct:.1f}% advancing)")
    print(f"  Nasdaq  TICK : {snap.tick_nq:+,}  ({snap.breadth_nq_pct:.1f}% advancing)")
    print(f"  Up/Dn counts : {snap.up_all:,} ▲ / {snap.dn_all:,} ▼  (all market)")
    print(f"  Data age     : {snap.age_secs}s")
    if snap.as_of:
        print(f"  As of        : {snap.as_of.strftime('%H:%M:%S')}")
