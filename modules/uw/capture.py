"""Capture UW data into the local store.

Usage:
    python -m modules.uw.capture init
    python -m modules.uw.capture backfill --days 500
    python -m modules.uw.capture backfill --days 500 --only vol_state
    python -m modules.uw.capture daily
    python -m modules.uw.capture status

Backfill is resumable and idempotent -- upserts on (ticker, date), so
re-running after a failure costs API calls but never duplicates or corrupts.
Run it once early in the subscription window, then `daily` on a schedule to
keep the tail fresh until the subscription lapses.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from .client import UWClient

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "uw.sqlite"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


# --------------------------------------------------------------------------- #
# universe
# --------------------------------------------------------------------------- #

def load_universe() -> list[str]:
    """Reuse the screener's existing universe rather than defining a second one.

    Falls back to a small index set if the import shape differs -- adjust the
    import to match whatever modules/screener/universe.py actually exports.
    """
    try:
        from ..screener import universe as u  # type: ignore

        for attr in ("get_universe", "all_tickers", "UNIVERSE", "TICKERS"):
            if hasattr(u, attr):
                val = getattr(u, attr)
                tickers = val() if callable(val) else val
                return sorted({str(t).upper() for t in tickers})
    except Exception as exc:  # pragma: no cover
        log.warning("could not load screener universe (%s), using fallback", exc)

    return ["SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "SLV", "XLE", "XLF", "SMH"]


BENCHMARKS = ["SPY", "QQQ", "IWM", "VIX"]


# --------------------------------------------------------------------------- #
# db helpers
# --------------------------------------------------------------------------- #

def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    log.info("schema applied to %s", DB_PATH)


def _upsert(
    conn: sqlite3.Connection,
    table: str,
    keys: Sequence[str],
    rows: list[dict[str, Any]],
) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in keys)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({', '.join(keys)}) DO UPDATE SET {updates}"
    )
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    conn.commit()
    return len(rows)


def _log_capture(conn: sqlite3.Connection, **kw: Any) -> None:
    cols = list(kw.keys())
    conn.execute(
        f"INSERT INTO capture_log ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)})",
        tuple(kw.values()),
    )
    conn.commit()


def _f(d: dict, *names: str) -> float | None:
    """First present, coercible value among `names`. UW field naming varies
    slightly between endpoints, so accept aliases rather than guessing one."""
    for n in names:
        v = d.get(n)
        if v is not None and v != "":
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _i(d: dict, *names: str) -> int | None:
    v = _f(d, *names)
    return int(v) if v is not None else None


# --------------------------------------------------------------------------- #
# capture: vol_state
# --------------------------------------------------------------------------- #

def capture_vol_state(
    client: UWClient, conn: sqlite3.Connection, ticker: str, days: int
) -> int:
    raw = client.get("ticker_ohlc", ticker=ticker, params={"limit": min(days, 500)})
    if not isinstance(raw, list):
        raise RuntimeError(f"unexpected shape for {ticker} vol_state: {type(raw)}")

    rows = []
    for r in raw:
        d = r.get("date")
        if not d:
            continue
        rows.append(
            {
                "ticker": ticker,
                "date": d,
                "open": _f(r, "open"),
                "high": _f(r, "high"),
                "low": _f(r, "low"),
                "close": _f(r, "close"),
                "iv_rank": _f(r, "iv_rank"),
                "volatility_30": _f(r, "volatility_30", "volatility"),
                "volatility_60": _f(r, "volatility_60"),
                "implied_move_30": _f(r, "implied_move_30", "implied_move"),
                "implied_move_perc_30": _f(r, "implied_move_perc_30", "implied_move_perc"),
                "implied_move_60": _f(r, "implied_move_60"),
                "implied_move_perc_60": _f(r, "implied_move_perc_60"),
                "call_volume": _i(r, "call_volume"),
                "put_volume": _i(r, "put_volume"),
                "call_premium": _f(r, "call_premium"),
                "put_premium": _f(r, "put_premium"),
                "net_premium": _f(r, "net_premium"),
                "bullish_premium": _f(r, "bullish_premium"),
                "bearish_premium": _f(r, "bearish_premium"),
                "call_open_interest": _i(r, "call_open_interest"),
                "put_open_interest": _i(r, "put_open_interest"),
                "total_open_interest": _i(r, "total_open_interest"),
            }
        )

    n = _upsert(conn, "vol_state", ("ticker", "date"), rows)

    dates = sorted(r["date"] for r in rows)
    iv_dates = sorted(r["date"] for r in rows if r["volatility_30"] is not None)
    _log_capture(
        conn,
        endpoint="vol_state",
        ticker=ticker,
        status="ok" if n else "partial",
        rows_written=n,
        earliest_date=dates[0] if dates else None,
        latest_date=dates[-1] if dates else None,
        iv_earliest=iv_dates[0] if iv_dates else None,
        note=(
            None
            if len(iv_dates) == len(dates)
            else f"interpolated IV present on {len(iv_dates)}/{len(dates)} rows"
        ),
    )
    return n


# --------------------------------------------------------------------------- #
# capture: gex
# --------------------------------------------------------------------------- #

def capture_gex_daily(
    client: UWClient, conn: sqlite3.Connection, ticker: str, timeframe: str = "1Y"
) -> int:
    raw = client.get("greek_exposure", ticker=ticker, params={"timeframe": timeframe})
    if not isinstance(raw, list):
        raise RuntimeError(f"unexpected shape for {ticker} gex_daily")

    rows = []
    for r in raw:
        d = r.get("date")
        if not d:
            continue
        cg = _f(r, "call_gamma", "call_gex")
        pg = _f(r, "put_gamma", "put_gex")
        rows.append(
            {
                "ticker": ticker,
                "date": d,
                "call_gex": cg,
                "put_gex": pg,
                "net_gex": (cg + pg) if (cg is not None and pg is not None) else None,
                "call_delta": _f(r, "call_delta"),
                "put_delta": _f(r, "put_delta"),
                "call_charm": _f(r, "call_charm"),
                "put_charm": _f(r, "put_charm"),
                "call_vanna": _f(r, "call_vanna"),
                "put_vanna": _f(r, "put_vanna"),
            }
        )

    n = _upsert(conn, "gex_daily", ("ticker", "date"), rows)
    dates = sorted(r["date"] for r in rows)
    _log_capture(
        conn,
        endpoint="gex_daily",
        ticker=ticker,
        status="ok" if n else "partial",
        rows_written=n,
        earliest_date=dates[0] if dates else None,
        latest_date=dates[-1] if dates else None,
    )
    return n


def capture_gex_levels(
    client: UWClient,
    conn: sqlite3.Connection,
    ticker: str,
    on_date: str,
    sources: Iterable[str] = ("vol", "oi"),
) -> int:
    total = 0
    for src in sources:
        raw = client.get(
            "gex_levels", ticker=ticker, params={"date": on_date, "source": src}
        )
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        if not raw:
            continue
        total += _upsert(
            conn,
            "gex_levels",
            ("ticker", "date", "source"),
            [
                {
                    "ticker": ticker,
                    "date": raw.get("date", on_date),
                    "source": src,
                    "call_wall": _f(raw, "call_wall"),
                    "put_wall": _f(raw, "put_wall"),
                    "gamma_flip": _f(raw, "gamma_flip"),
                    "gamma_magnet": _f(raw, "gamma_magnet"),
                    "spot": _f(raw, "spot", "price", "close"),
                }
            ],
        )
    return total


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #

TASKS = ("vol_state", "gex_daily", "gex_levels")


def _already_captured(conn: sqlite3.Connection, endpoint: str, ticker: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM capture_log WHERE endpoint=? AND ticker=? AND status='ok' "
        "AND ran_at > datetime('now','-20 hours') LIMIT 1",
        (endpoint, ticker),
    ).fetchone()
    return row is not None


def backfill(days: int = 500, only: str | None = None, resume: bool = True) -> None:
    conn = connect()
    init_db(conn)
    client = UWClient()
    tickers = sorted(set(load_universe()) | set(BENCHMARKS))
    tasks = (only,) if only else TASKS
    today = date.today().isoformat()

    log.info("backfill: %d tickers x %s", len(tickers), ", ".join(t for t in tasks if t))

    for i, tkr in enumerate(tickers, 1):
        for task in tasks:
            if not task:
                continue
            if resume and _already_captured(conn, task, tkr):
                log.debug("[%d/%d] %s %s already fresh, skipping", i, len(tickers), tkr, task)
                continue
            try:
                if task == "vol_state":
                    n = capture_vol_state(client, conn, tkr, days)
                elif task == "gex_daily":
                    n = capture_gex_daily(client, conn, tkr)
                else:
                    n = capture_gex_levels(client, conn, tkr, today)
                log.info("[%d/%d] %-6s %-10s %d rows", i, len(tickers), tkr, task, n)
            except Exception as exc:
                log.error("[%d/%d] %-6s %-10s FAILED: %s", i, len(tickers), tkr, task, exc)
                _log_capture(
                    conn, endpoint=task, ticker=tkr, status="error", note=str(exc)[:500]
                )

    conn.close()


def daily() -> None:
    """Keep the tail fresh. Cheap -- pulls a short window per ticker."""
    backfill(days=10, resume=False)


def status() -> None:
    conn = connect()
    init_db(conn)
    print(f"store: {DB_PATH}\n")
    for table in ("vol_state", "gex_daily", "gex_levels", "earnings_vol"):
        row = conn.execute(
            f"SELECT COUNT(*) n, COUNT(DISTINCT ticker) t, "
            f"MIN(date) lo, MAX(date) hi FROM {table}"
        ).fetchone()
        print(f"{table:<14} {row['n']:>8,} rows  {row['t']:>4} tickers  "
              f"{row['lo'] or '-'} .. {row['hi'] or '-'}")

    gap = conn.execute(
        "SELECT COUNT(*) n FROM vol_state WHERE volatility_30 IS NULL"
    ).fetchone()["n"]
    tot = conn.execute("SELECT COUNT(*) n FROM vol_state").fetchone()["n"]
    if tot:
        print(f"\ninterpolated IV coverage: {tot - gap:,}/{tot:,} "
              f"({100 * (tot - gap) / tot:.1f}%)")
        print("  low coverage means the IV depth is plan-gated -- percentile")
        print("  baselines will be shallower than the price history suggests.")

    errs = conn.execute(
        "SELECT endpoint, ticker, note FROM capture_log WHERE status='error' "
        "ORDER BY ran_at DESC LIMIT 10"
    ).fetchall()
    if errs:
        print("\nrecent errors:")
        for e in errs:
            print(f"  {e['endpoint']:<12} {e['ticker']:<6} {(e['note'] or '')[:70]}")
    conn.close()


def main() -> None:
    p = argparse.ArgumentParser(prog="modules.uw.capture")
    p.add_argument("command", choices=("init", "backfill", "daily", "status"))
    p.add_argument("--days", type=int, default=500, help="trading days back (max 500)")
    p.add_argument("--only", choices=TASKS, help="run a single task")
    p.add_argument("--no-resume", action="store_true", help="ignore capture_log freshness")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "init":
        conn = connect()
        init_db(conn)
        conn.close()
    elif args.command == "backfill":
        backfill(days=args.days, only=args.only, resume=not args.no_resume)
    elif args.command == "daily":
        daily()
    else:
        status()


if __name__ == "__main__":
    main()
