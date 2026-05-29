"""
Market Direction Monitor — Massive API  (v2)
=============================================
Terminal Bloomberg-style dashboard. Polls REST after hours;
switches to WebSocket streaming when market is open.

Usage:
    python3 -m cli.terminal_monitor
    python3 -m cli.terminal_monitor --tickers SPY QQQ IWM DIA QQQI --interval 60

Keyboard:
    Q  — quit
    R  — force REST refresh now
    P  — pause / resume auto-refresh
"""
# ruff: noqa: E402

import argparse
import os
import select
import signal
import sys
import termios
import threading
import time
import tty
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from massive import RESTClient, WebSocketClient
from massive.websocket.models import WebSocketMessage
from modules.market.breadth import BreadthFetcher, BreadthSnapshot
from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ── constants ─────────────────────────────────────────────────────────────────

DEFAULT_TICKERS  = ["SPY", "QQQ", "IWM", "DIA", "QQQI"]
SPARK_CHARS      = "▁▂▃▄▅▆▇█"
SPARK_BARS       = 20   # days of history for sparkline
RSI_OVERBOUGHT   = 70
RSI_OVERSOLD     = 30

SESSION_STYLES = {
    "open":           ("OPEN",      "bold bright_green"),
    "extended-hours": ("AFTER-HRS", "bold yellow"),
    "pre-market":     ("PRE-MKT",   "bold cyan"),
    "closed":         ("CLOSED",    "dim white"),
    "unknown":        ("UNKNOWN",   "dim"),
}

console = Console()


# ── helpers ───────────────────────────────────────────────────────────────────

def sparkline(values: List[float]) -> str:
    if not values:
        return "─" * 10
    mn, mx = min(values), max(values)
    if mx == mn:
        return "─" * len(values)
    n = len(SPARK_CHARS) - 1
    return "".join(SPARK_CHARS[round((v - mn) / (mx - mn) * n)] for v in values)

def fmt_price(v: Optional[float]) -> str:
    return f"${v:>7.2f}" if v is not None else "    --  "

def fmt_float(v: Optional[float], decimals: int = 2, width: int = 7) -> str:
    return f"{v:>{width}.{decimals}f}" if v is not None else " " * width + "--"

def pct_text(v: Optional[float], decimals: int = 2) -> Text:
    if v is None:
        return Text("   --  ", style="dim")
    arrow = "▲" if v > 0 else ("▼" if v < 0 else "─")
    style = "bright_green" if v > 0 else ("bright_red" if v < 0 else "white")
    return Text(f"{arrow}{abs(v):>{5}.{decimals}f}%", style=style)

def signed_text(v: Optional[float], decimals: int = 2) -> Text:
    if v is None:
        return Text("   --  ", style="dim")
    sign  = "+" if v > 0 else ""
    style = "bright_green" if v > 0 else ("bright_red" if v < 0 else "white")
    return Text(f"{sign}{v:.{decimals}f}", style=style)

def rsi_text(rsi: Optional[float]) -> Text:
    if rsi is None:
        return Text("  -- ", style="dim")
    if rsi >= RSI_OVERBOUGHT: style, tag = "bright_red",   "OB"
    elif rsi <= RSI_OVERSOLD: style, tag = "bright_green", "OS"
    elif rsi >= 55:           style, tag = "green",        "  "
    elif rsi <= 45:           style, tag = "red",          "  "
    else:                     style, tag = "yellow",       "  "
    return Text(f"{rsi:>5.1f}{tag}", style=style)

def hist_bar(v: Optional[float]) -> Text:
    """Render MACD histogram as a small visual bar."""
    if v is None:
        return Text("  --  ", style="dim")
    width = min(int(abs(v) * 20), 5)
    bar   = "█" * width or "▏"
    style = "bright_green" if v >= 0 else "bright_red"
    sign  = "+" if v >= 0 else "-"
    return Text(f"{sign}{bar:<5}", style=style)

def vol_vs_avg(volume: Optional[float], avg: Optional[float]) -> Text:
    if volume is None or avg is None or avg == 0:
        return Text("   -- ", style="dim")
    pct = (volume / avg) * 100
    style = "bright_green" if pct >= 150 else ("green" if pct >= 100 else ("yellow" if pct >= 60 else "dim"))
    return Text(f"{pct:>5.0f}%", style=style)


# ── data model ────────────────────────────────────────────────────────────────

@dataclass
class TickerState:
    symbol: str
    # price
    price:      Optional[float] = None
    prev_close: Optional[float] = None
    day_open:   Optional[float] = None
    day_high:   Optional[float] = None
    day_low:    Optional[float] = None
    volume:     Optional[float] = None
    vwap:       Optional[float] = None
    # indicators
    sma20:      Optional[float] = None
    sma50:      Optional[float] = None
    sma200:     Optional[float] = None
    rsi14:      Optional[float] = None
    macd_val:   Optional[float] = None
    macd_sig:   Optional[float] = None
    macd_hist:  Optional[float] = None
    # extended
    spark:      str             = ""
    avg_volume: Optional[float] = None   # 30-day avg
    # meta
    last_updated: Optional[datetime] = None
    source:     str = "REST"

    # ── derived ──

    @property
    def day_change(self) -> Optional[float]:
        if self.price and self.prev_close:
            return self.price - self.prev_close
        return None

    @property
    def day_change_pct(self) -> Optional[float]:
        c = self.day_change
        return (c / self.prev_close * 100) if c and self.prev_close else None

    def _vs_sma(self, sma: Optional[float]) -> Optional[float]:
        if self.price and sma:
            return (self.price - sma) / sma * 100
        return None

    @property
    def vs_sma20(self):  return self._vs_sma(self.sma20)
    @property
    def vs_sma50(self):  return self._vs_sma(self.sma50)
    @property
    def vs_sma200(self): return self._vs_sma(self.sma200)

    @property
    def trend_score(self) -> int:
        """Bullish signals count +1, bearish -1. Range -5..+5."""
        score = 0
        for v in (self.vs_sma20, self.vs_sma50, self.vs_sma200):
            if v is not None: score += 1 if v > 0 else -1
        if self.rsi14   is not None: score += 1 if self.rsi14 > 50 else -1
        if self.macd_val is not None and self.macd_sig is not None:
            score += 1 if self.macd_val > self.macd_sig else -1
        return score

    @property
    def direction_label(self) -> str:
        s = self.trend_score
        return {5:"STRONG UP", 4:"STRONG UP", 3:"UP", 2:"UP",
                1:"SLIGHT UP", 0:"NEUTRAL",
               -1:"SLIGHT DN",-2:"DOWN",-3:"DOWN",
               -4:"STRONG DN",-5:"STRONG DN"}.get(s, "NEUTRAL")

    @property
    def direction_color(self) -> str:
        s = self.trend_score
        if s >= 3:  return "bright_green"
        if s >= 1:  return "green"
        if s == 0:  return "yellow"
        if s >= -2: return "red"
        return "bright_red"

    @property
    def data_age(self) -> str:
        if not self.last_updated:
            return "  --"
        secs = int((datetime.now() - self.last_updated).total_seconds())
        if secs < 60:   return f"{secs:>3}s"
        if secs < 3600: return f"{secs//60:>3}m"
        return f"{secs//3600:>3}h"


# ── data fetcher ──────────────────────────────────────────────────────────────

class DataFetcher:
    def __init__(self, client: RESTClient):
        self.client = client

    def _safe(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None

    def fetch_snapshot(self, s: TickerState) -> None:
        snap = self._safe(self.client.get_snapshot_ticker, "stocks", s.symbol)
        if not snap: return
        d, p = snap.day, snap.prev_day
        s.price      = d.close or d.vwap
        s.day_open   = d.open
        s.day_high   = d.high
        s.day_low    = d.low
        s.volume     = d.volume
        s.vwap       = d.vwap
        s.prev_close = p.close if p else None

    def fetch_sparkline(self, s: TickerState) -> None:
        today      = date.today().isoformat()
        from_date  = (date.today() - timedelta(days=SPARK_BARS * 2)).isoformat()
        bars = self._safe(
            lambda: list(self.client.list_aggs(
                ticker=s.symbol, multiplier=1, timespan="day",
                from_=from_date, to=today, adjusted=True,
                sort="asc", limit=SPARK_BARS,
            ))
        )
        if not bars: return
        closes = [b.close for b in bars if b.close]
        s.spark      = sparkline(closes[-SPARK_BARS:])
        s.avg_volume = sum(b.volume for b in bars if b.volume) / len(bars)

    def fetch_indicators(self, s: TickerState) -> None:
        for attr, window in (("sma20", 20), ("sma50", 50), ("sma200", 200)):
            r = self._safe(self.client.get_sma, s.symbol,
                           timespan="day", window=window,
                           series_type="close", order="desc", limit=1)
            if r and r.values:
                setattr(s, attr, r.values[0].value)

        r = self._safe(self.client.get_rsi, s.symbol,
                       timespan="day", window=14,
                       series_type="close", order="desc", limit=1)
        if r and r.values:
            s.rsi14 = r.values[0].value

        r = self._safe(self.client.get_macd, s.symbol,
                       timespan="day", short_window=12, long_window=26,
                       signal_window=9, series_type="close",
                       order="desc", limit=1)
        if r and r.values:
            v = r.values[0]
            s.macd_val  = v.value
            s.macd_sig  = v.signal
            s.macd_hist = v.histogram

        s.last_updated = datetime.now()
        s.source       = "REST"

    def full_refresh(self, states: Dict[str, "TickerState"]) -> None:
        for s in states.values():
            self.fetch_snapshot(s)
            self.fetch_sparkline(s)
            self.fetch_indicators(s)

    def snapshot_only(self, states: Dict[str, "TickerState"]) -> None:
        """Lightweight refresh — price + volume only (no indicator API calls)."""
        for s in states.values():
            self.fetch_snapshot(s)
            s.last_updated = datetime.now()


# ── display ───────────────────────────────────────────────────────────────────

def build_price_table(states: Dict[str, TickerState]) -> Table:
    t = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan",
              title="[bold white]Price  ·  Volume  ·  Trend[/bold white]",
              title_justify="left", min_width=96)
    t.add_column("",        width=6,  style="bold white")   # ticker
    t.add_column("Price",   width=9,  justify="right")
    t.add_column("Chg",     width=8,  justify="right")
    t.add_column("Chg %",   width=8,  justify="right")
    t.add_column("VWAP",    width=9,  justify="right")
    t.add_column("Vol",     width=10, justify="right")
    t.add_column("vs Avg",  width=7,  justify="right")
    t.add_column("Hi / Lo", width=17, justify="center")
    t.add_column("20d spark",width=22,justify="left")
    t.add_column("Age",     width=5,  justify="right", style="dim")

    for s in states.values():
        hi_lo = (
            Text(f"{fmt_price(s.day_high)}", style="green") +
            Text(" / ") +
            Text(f"{fmt_price(s.day_low)}", style="red")
        ) if s.day_high else Text("   --  /  --  ", style="dim")

        t.add_row(
            s.symbol,
            Text(fmt_price(s.price)),
            signed_text(s.day_change),
            pct_text(s.day_change_pct),
            Text(fmt_price(s.vwap), style="dim"),
            Text(f"{s.volume:>9,.0f}" if s.volume else "        --"),
            vol_vs_avg(s.volume, s.avg_volume),
            hi_lo,
            Text(s.spark or "─" * 20, style="cyan"),
            Text(s.data_age),
        )
    return t

def build_trend_table(states: Dict[str, TickerState]) -> Table:
    t = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan",
              title="[bold white]Indicators  ·  Signal Scores[/bold white]",
              title_justify="left", min_width=96)
    t.add_column("",          width=6,  style="bold white")
    t.add_column("Direction", width=11, justify="center")
    t.add_column("Score",     width=6,  justify="center")
    t.add_column("SMA20",     width=8,  justify="right")
    t.add_column("SMA50",     width=8,  justify="right")
    t.add_column("SMA200",    width=8,  justify="right")
    t.add_column("RSI-14",    width=8,  justify="right")
    t.add_column("MACD",      width=8,  justify="right")
    t.add_column("Signal",    width=8,  justify="right")
    t.add_column("Hist",      width=8,  justify="left")

    for s in states.values():
        score = s.trend_score
        score_style = s.direction_color
        t.add_row(
            s.symbol,
            Text(s.direction_label, style=s.direction_color),
            Text(f"{'+' if score > 0 else ''}{score}", style=score_style),
            pct_text(s.vs_sma20),
            pct_text(s.vs_sma50),
            pct_text(s.vs_sma200),
            rsi_text(s.rsi14),
            signed_text(s.macd_val, 3),
            signed_text(s.macd_sig, 3),
            hist_bar(s.macd_hist),
        )
    return t

def build_breadth_bar(states: Dict[str, TickerState]) -> Text:
    scores = [s.trend_score for s in states.values() if s.price is not None]
    if not scores:
        return Text("No data yet", style="dim")
    bull    = sum(1 for sc in scores if sc > 0)
    bear    = sum(1 for sc in scores if sc < 0)
    neut    = len(scores) - bull - bear
    avg_sc  = sum(scores) / len(scores)
    total   = len(scores)
    width   = 30
    b_fill  = round(bull / total * width)
    r_fill  = round(bear / total * width)
    n_fill  = width - b_fill - r_fill

    bar = (
        f"[bright_green]{'█' * b_fill}[/bright_green]"
        f"[yellow]{'█' * n_fill}[/yellow]"
        f"[bright_red]{'█' * r_fill}[/bright_red]"
    )
    avg_style = "bright_green" if avg_sc > 0.5 else ("bright_red" if avg_sc < -0.5 else "yellow")
    return Text.from_markup(
        f" {bar}  "
        f"[bright_green]▲ {bull}[/bright_green]  "
        f"[yellow]─ {neut}[/yellow]  "
        f"[bright_red]▼ {bear}[/bright_red]    "
        f"avg score [{avg_style}]{avg_sc:+.1f}[/{avg_style}] / 5"
    )

def session_badge(market: str) -> Text:
    label, style = SESSION_STYLES.get(market, ("UNKNOWN", "dim"))
    return Text(f" {label} ", style=style)

def tick_color(val: int) -> str:
    if val >= 1000:  return "bright_green"
    if val >= 500:   return "green"
    if val >= -499:  return "yellow"
    if val >= -999:  return "red"
    return "bright_red"

def tick_segment(label: str, tick: int, breadth_pct: Optional[float],
                 bar_width: int = 20) -> Text:
    """Render one TICK segment:  label  +1880  ████████████░░░░░░░░  83.8%"""
    color = tick_color(tick)
    if breadth_pct is not None:
        filled = round(breadth_pct / 100 * bar_width)
        bar    = "█" * filled + "░" * (bar_width - filled)
        pct    = f"{breadth_pct:>5.1f}%"
    else:
        bar  = "─" * bar_width
        pct  = "   N/A"
    return Text.from_markup(
        f"  [bold white]{label:<8}[/bold white]"
        f"[{color}]{tick:>+6,}[/{color}]"
        f"  [{color}]{bar}[/{color}]"
        f"  [{color}]{pct}[/{color}]"
    )

def build_tick_panel(
    snap:    Optional[BreadthSnapshot],
    history: list,
) -> Panel:
    if snap is None:
        return Panel(Text("Loading breadth data...", style="dim"),
                     title="[dim]Breadth TICK Proxy[/dim]", box=box.ROUNDED)

    row1 = (
        tick_segment("All-Mkt", snap.tick_all,  snap.breadth_all_pct)
        + tick_segment("NYSE",   snap.tick_nyse, snap.breadth_nyse_pct)
        + tick_segment("Nasdaq", snap.tick_nq,   snap.breadth_nq_pct)
        + Text(f"   age: {snap.age_secs}s", style="dim")
    )

    spark_vals = [s.tick_all for s in history if s.tick_all is not None]
    spark_str  = sparkline(spark_vals) if len(spark_vals) >= 2 else "─" * 20
    # color the sparkline based on latest value
    sp_color = tick_color(snap.tick_all)
    row2 = Text.from_markup(
        f"  [dim]hist [/dim][{sp_color}]{spark_str}[/{sp_color}]"
        f"  [dim]({len(spark_vals)} readings)[/dim]"
    )

    content = Text()
    content.append_text(row1)
    content.append("\n")
    content.append_text(row2)

    return Panel(
        content,
        title="[dim]Breadth TICK Proxy  ·  advance/decline count  ·  not true $TICK  ·  15-min delayed[/dim]",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def build_layout(
    states: Dict[str, TickerState],
    market:   str,
    interval: int,
    paused:   bool,
    source:   str,
    breadth_snap:    Optional[BreadthSnapshot] = None,
    breadth_history: Optional[list] = None,
) -> Layout:
    now   = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    pause = "  [bold yellow]⏸ PAUSED[/bold yellow]" if paused else ""
    note  = "[dim]  ⚠ data delayed 15 min[/dim]" if source != "WS (live)" else ""

    header_markup = (
        f"[bold white]Market Monitor[/bold white]  [dim]{now}[/dim]  "
        f"session: {session_badge(market).markup}  "
        f"[dim]source: {source}  refresh: {interval}s[/dim]"
        f"{note}{pause}"
    )

    keys = (
        "[dim]  Q[/dim][white] quit[/white]   "
        "[dim]R[/dim][white] refresh[/white]   "
        "[dim]P[/dim][white] pause/resume[/white]   "
        "[dim]data delayed 15 min per Stocks Starter plan[/dim]"
    )

    layout = Layout()
    layout.split_column(
        Layout(name="header",  size=3),
        Layout(name="prices",  size=len(states) + 6),
        Layout(name="trends",  size=len(states) + 6),
        Layout(name="breadth", size=3),
        Layout(name="tick",    size=5),
        Layout(name="keys",    size=1),
    )
    layout["header"].update(Panel(Text.from_markup(header_markup), box=box.HORIZONTALS, padding=(0, 1)))
    layout["prices"].update(Panel(build_price_table(states),  box=box.ROUNDED, padding=(0, 1)))
    layout["trends"].update(Panel(build_trend_table(states),  box=box.ROUNDED, padding=(0, 1)))
    layout["breadth"].update(Panel(build_breadth_bar(states), title="[dim]Signal Breadth[/dim]", box=box.ROUNDED, padding=(0, 1)))
    layout["tick"].update(build_tick_panel(breadth_snap, breadth_history or []))
    layout["keys"].update(Text.from_markup(keys))
    return layout


# ── keyboard input ────────────────────────────────────────────────────────────

class KeyReader:
    """Reads single keypresses from stdin without blocking the main thread."""

    def __init__(self):
        self._key: Optional[str] = None
        self._lock = threading.Lock()
        self._fd   = sys.stdin.fileno()
        try:
            self._old = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self._active = True
        except Exception:
            self._active = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while self._active:
            try:
                r, _, _ = select.select([sys.stdin], [], [], 0.1)
                if r:
                    ch = sys.stdin.read(1).lower()
                    with self._lock:
                        self._key = ch
            except Exception:
                break

    def get(self) -> Optional[str]:
        with self._lock:
            k, self._key = self._key, None
            return k

    def restore(self):
        self._active = False
        if hasattr(self, "_old"):
            try:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
            except Exception:
                pass


# ── websocket live feed ───────────────────────────────────────────────────────

class LivePriceFeed:
    def __init__(self, api_key: str, states: Dict[str, TickerState]):
        self.states = states
        subs = [f"A.{t}" for t in states]  # per-second aggs
        self.ws = WebSocketClient(api_key=api_key, subscriptions=subs)

    def handle_msg(self, msgs: List[WebSocketMessage]) -> None:
        for m in msgs:
            ev  = getattr(m, "ev",  None)
            sym = getattr(m, "sym", None)
            if ev in ("A", "AM") and sym in self.states:
                s = self.states[sym]
                s.price    = getattr(m, "c",  s.price)
                s.day_high = max(s.day_high or 0, getattr(m, "h", 0)) or None
                s.day_low  = min(s.day_low  or 9e9, getattr(m, "l", 9e9)) or None
                s.volume   = getattr(m, "av", s.volume)
                s.source   = "WS (live)"
                s.last_updated = datetime.now()

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.ws.run, args=(self.handle_msg,), daemon=True)
        t.start()
        return t


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Market Direction Monitor")
    parser.add_argument("--tickers",  nargs="+", default=DEFAULT_TICKERS)
    parser.add_argument("--interval", type=int,  default=60,
                        help="REST refresh interval in seconds")
    args = parser.parse_args()

    api_key = os.getenv("MASSIVE_API_KEY")
    if not api_key:
        console.print("[red]ERROR:[/red] MASSIVE_API_KEY not set.")
        sys.exit(1)

    tickers  = [t.upper() for t in args.tickers]
    rest     = RESTClient(api_key=api_key)
    fetcher  = DataFetcher(rest)
    states: Dict[str, TickerState] = {t: TickerState(symbol=t) for t in tickers}

    breadth_fetcher = BreadthFetcher(rest)
    breadth_snap: Optional[BreadthSnapshot] = None

    market_status = "unknown"
    ws_running    = False
    paused        = False

    def get_market_status() -> str:
        try:
            return rest.get_market_status().market or "unknown"
        except Exception:
            return "unknown"

    def maybe_start_ws():
        nonlocal ws_running
        if market_status == "open" and not ws_running:
            try:
                LivePriceFeed(api_key, states).start()
                ws_running = True
            except Exception as e:
                console.log(f"[yellow]WS error: {e}[/yellow]")

    # ── initial load ──
    console.print("[cyan]Loading data...[/cyan]", end=" ")
    market_status = get_market_status()
    fetcher.full_refresh(states)
    breadth_fetcher.load_exchange_tickers()
    breadth_snap = breadth_fetcher.fetch()
    maybe_start_ws()
    console.print("[green]done.[/green]")

    running = True
    def on_signal(sig, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, on_signal)

    keys = KeyReader()

    next_full_refresh   = time.time() + args.interval
    next_status_check   = time.time() + 60
    next_breadth_refresh = time.time() + 30

    try:
        with Live(console=console, refresh_per_second=2, screen=True) as live:
            while running:
                now = time.time()

                # keyboard
                key = keys.get()
                if key == "q":
                    running = False
                elif key == "r":
                    fetcher.full_refresh(states)
                    next_full_refresh = time.time() + args.interval
                elif key == "p":
                    paused = not paused

                # scheduled refreshes
                if not paused:
                    if now >= next_full_refresh:
                        fetcher.full_refresh(states)
                        next_full_refresh = time.time() + args.interval
                    if now >= next_status_check:
                        market_status = get_market_status()
                        maybe_start_ws()
                        next_status_check = time.time() + 60
                    if now >= next_breadth_refresh:
                        breadth_snap = breadth_fetcher.fetch()
                        next_breadth_refresh = time.time() + 30

                data_source = "WS (live)" if ws_running else "REST (delayed)"
                live.update(build_layout(
                    states, market_status, args.interval, paused, data_source,
                    breadth_snap=breadth_snap,
                    breadth_history=breadth_fetcher.history_list(),
                ))
                time.sleep(0.4)
    finally:
        keys.restore()
        console.print("\n[cyan]Monitor stopped.[/cyan]")


if __name__ == "__main__":
    main()
