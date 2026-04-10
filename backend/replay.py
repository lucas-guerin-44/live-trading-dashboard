"""Replay engine: loads historical data and drip-feeds bars at configurable speed.

Supports three data modes:
- **bar**: Load OHLC bars from CSV or datalake REST API, drip-feed locally.
- **tick**: Load raw ticks from CSV or datalake REST API, aggregate + drip-feed.
- **stream**: Connect to datalake WebSocket endpoints (/ws/bars or /ws/ticks)
  which handle pacing via real-time timestamp deltas scaled by a speed multiplier.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

import httpx
import websockets

from models import Bar

# Add backtesting engine to path for Tick/TickAggregator imports.
# MUST append (not insert) to avoid shadowing the dashboard's own modules
# — the backtesting engine has its own strategies/ package.
_BACKTESTING_ROOT = Path(__file__).resolve().parent.parent.parent / "backtesting-engine-2.0"
if str(_BACKTESTING_ROOT) not in sys.path:
    sys.path.append(str(_BACKTESTING_ROOT))

try:
    from backtesting.tick import Tick, TickAggregator
    import pandas as pd
    TICK_SUPPORT = True
except ImportError:
    TICK_SUPPORT = False

logger = logging.getLogger(__name__)

# Path to OHLC data - configurable via CSV_DIR env var, falls back to sibling project
DEFAULT_CSV_DIR = Path(os.environ.get("CSV_DIR", "")) if os.environ.get("CSV_DIR") else (
    Path(__file__).resolve().parent.parent.parent / "backtesting-engine-2.0" / "ohlc_data"
)

# ── Timeframe helpers ──────────────────────────────────────────────────────

_TIMEFRAME_MINUTES: dict[str, int] = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240, "D1": 1440,
}


def _expected_interval(timeframe: str) -> timedelta:
    """Return the expected timedelta between consecutive bars."""
    minutes = _TIMEFRAME_MINUTES.get(timeframe, 15)
    return timedelta(minutes=minutes)


# ── Bar validation ─────────────────────────────────────────────────────────

def _validate_bar(bar: Bar, row_idx: int) -> bool:
    """Return True if the bar passes OHLC sanity checks."""
    if bar.open <= 0 or bar.high <= 0 or bar.low <= 0 or bar.close <= 0:
        logger.warning(
            "Row %d: non-positive price — skipping (O=%.2f H=%.2f L=%.2f C=%.2f)",
            row_idx, bar.open, bar.high, bar.low, bar.close,
        )
        return False
    if bar.high < bar.low:
        logger.warning("Row %d: high (%.2f) < low (%.2f) — skipping", row_idx, bar.high, bar.low)
        return False
    if bar.high < bar.open or bar.high < bar.close:
        logger.warning("Row %d: high (%.2f) < open/close — skipping", row_idx, bar.high)
        return False
    if bar.low > bar.open or bar.low > bar.close:
        logger.warning("Row %d: low (%.2f) > open/close — skipping", row_idx, bar.low)
        return False
    return True


def _validate_bar_sequence(bars: list[Bar], timeframe: str) -> list[Bar]:
    """Sort bars by timestamp, deduplicate, and log gaps."""
    if not bars:
        return bars

    # Sort by timestamp
    bars.sort(key=lambda b: b.timestamp)

    # Deduplicate by timestamp (keep last occurrence)
    seen: dict[datetime, Bar] = {}
    for bar in bars:
        seen[bar.timestamp] = bar
    deduped = sorted(seen.values(), key=lambda b: b.timestamp)

    if len(deduped) < len(bars):
        logger.warning("Removed %d duplicate timestamps", len(bars) - len(deduped))

    # Detect gaps (ignore normal market closures: overnight and weekends)
    expected = _expected_interval(timeframe)
    # Overnight gaps (~75 min for M15) and weekends (~3000+ min) are normal
    # Only flag gaps that are unusual within a trading session
    gap_threshold = timedelta(hours=4) if expected <= timedelta(hours=1) else expected * 3
    gap_count = 0
    for i in range(1, len(deduped)):
        prev_bar = deduped[i - 1]
        curr_bar = deduped[i]
        delta = curr_bar.timestamp - prev_bar.timestamp
        if delta <= gap_threshold:
            continue
        # Skip weekends (Fri→Mon) and daily closes (overnight gaps)
        prev_wd = prev_bar.timestamp.weekday()
        curr_wd = curr_bar.timestamp.weekday()
        is_weekend = prev_wd == 4 and curr_wd == 0  # Fri → Mon
        is_overnight = prev_bar.timestamp.date() != curr_bar.timestamp.date() and delta < timedelta(hours=18)
        if is_weekend or is_overnight:
            continue
        gap_count += 1
        logger.warning(
            "Data gap: %s → %s (%.0f min, expected %.0f min)",
            prev_bar.timestamp.isoformat(),
            curr_bar.timestamp.isoformat(),
            delta.total_seconds() / 60,
            expected.total_seconds() / 60,
        )
    if gap_count:
        logger.info("Found %d unexpected data gaps", gap_count)

    return deduped


# ── Data loaders ───────────────────────────────────────────────────────────

async def load_bars_from_csv(
    instrument: str = "XAUUSD",
    timeframe: str = "M15",
    csv_dir: Path = DEFAULT_CSV_DIR,
) -> list[Bar]:
    """Load bars from local CSV file."""
    filename = f"{instrument}_{timeframe}.csv"
    filepath = csv_dir / filename

    if not filepath.exists():
        raise FileNotFoundError(f"Data file not found: {filepath}")

    bars: list[Bar] = []
    with open(filepath, newline="") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            bar = Bar(
                timestamp=datetime.fromisoformat(row["timestamp"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                instrument=row.get("instrument", instrument),
                timeframe=row.get("timeframe", timeframe),
            )
            if _validate_bar(bar, idx):
                bars.append(bar)

    bars = _validate_bar_sequence(bars, timeframe)
    logger.info("Loaded %d bars from %s", len(bars), filepath)
    return bars


async def load_bars_from_datalake(
    instrument: str = "XAUUSD",
    timeframe: str = "M15",
    datalake_url: str = "http://localhost:8001",
    api_key: str | None = None,
) -> list[Bar]:
    """Load bars from the datalake API with cursor-based pagination."""
    bars: list[Bar] = []
    cursor: str | None = None
    headers = {"X-API-Key": api_key} if api_key else {}
    row_idx = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            params: dict = {
                "instrument": instrument,
                "timeframe": timeframe,
                "limit": 10000,
            }
            if cursor:
                params["cursor"] = cursor

            resp = await client.get(
                f"{datalake_url}/query",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
            body = resp.json()

            for row in body.get("data", []):
                bar = Bar(
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    instrument=instrument,
                    timeframe=timeframe,
                )
                if _validate_bar(bar, row_idx):
                    bars.append(bar)
                row_idx += 1

            if not body.get("has_more", False):
                break
            cursor = body.get("next_cursor")

    bars = _validate_bar_sequence(bars, timeframe)
    logger.info("Loaded %d bars from datalake API", len(bars))
    return bars


async def load_bars(
    instrument: str = "XAUUSD",
    timeframe: str = "M15",
    datalake_url: str | None = None,
    datalake_api_key: str | None = None,
    csv_dir: Path | None = None,
) -> list[Bar]:
    """Load bars from datalake if available, fall back to CSV."""
    if datalake_url:
        try:
            return await load_bars_from_datalake(
                instrument, timeframe, datalake_url, datalake_api_key
            )
        except Exception:
            logger.warning("Datalake unavailable, falling back to CSV", exc_info=True)

    return await load_bars_from_csv(instrument, timeframe, csv_dir=csv_dir or DEFAULT_CSV_DIR)


async def replay_bars(
    bars: list[Bar],
    speed: float | Callable[[], float] = 1.0,
    base_interval: float = 1.0,
) -> AsyncIterator[Bar]:
    """Yield bars one at a time with a delay simulating real-time feed.

    Args:
        bars: Pre-loaded list of bars.
        speed: Replay speed multiplier, or a callable returning the current speed.
        base_interval: Base seconds between bars (before speed adjustment).
    """
    for bar in bars:
        yield bar
        # Wait while paused (speed == 0)
        while True:
            current_speed = speed() if callable(speed) else speed
            if current_speed > 0:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(base_interval / current_speed)


# ── Tick data support ────────────────────────────────────────────────────────

def _validate_tick(price: float, ts_str: str, row_idx: int) -> bool:
    """Return True if tick data is sane."""
    if price <= 0 or math.isnan(price):
        logger.warning("Tick %d: invalid price %.4f — skipping", row_idx, price)
        return False
    if not ts_str:
        logger.warning("Tick %d: empty timestamp — skipping", row_idx)
        return False
    return True


def _validate_tick_sequence(ticks: list) -> list:
    """Ensure ticks are sorted by timestamp (monotonic)."""
    ticks.sort(key=lambda t: t.ts)
    return ticks


async def load_ticks_from_csv(
    instrument: str = "XAUUSD",
    csv_dir: Path = DEFAULT_CSV_DIR,
) -> list:
    """Load ticks from a CSV file.

    Expected format: timestamp,price,volume (or timestamp,bid,ask,volume).
    File naming: {INSTRUMENT}_TICK.csv
    """
    if not TICK_SUPPORT:
        raise ImportError("Tick support requires the backtesting engine (backtesting.tick)")

    filename = f"{instrument}_TICK.csv"
    filepath = csv_dir / filename

    if not filepath.exists():
        raise FileNotFoundError(f"Tick data file not found: {filepath}")

    ticks = []
    with open(filepath, newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        has_bid_ask = "bid" in headers and "ask" in headers

        for idx, row in enumerate(reader):
            ts_str = row.get("timestamp", "")
            if has_bid_ask:
                bid = float(row["bid"])
                ask = float(row["ask"])
                price = (bid + ask) / 2
            else:
                price = float(row.get("price", 0))
                bid = None
                ask = None

            if not _validate_tick(price, ts_str, idx):
                continue

            volume = float(row.get("volume", 0))
            tick = Tick(
                ts=pd.Timestamp(ts_str),
                price=price,
                volume=volume,
                bid=bid if has_bid_ask else None,
                ask=ask if has_bid_ask else None,
            )
            ticks.append(tick)

    ticks = _validate_tick_sequence(ticks)
    logger.info("Loaded %d ticks from %s", len(ticks), filepath)
    return ticks


async def load_ticks_from_datalake(
    instrument: str = "XAUUSD",
    datalake_url: str = "http://localhost:8001",
    api_key: str | None = None,
) -> list:
    """Load ticks from the datalake API."""
    if not TICK_SUPPORT:
        raise ImportError("Tick support requires the backtesting engine (backtesting.tick)")

    ticks = []
    cursor: str | None = None
    headers = {"X-API-Key": api_key} if api_key else {}
    row_idx = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            params: dict = {
                "instrument": instrument,
                "timeframe": "TICK",
                "limit": 10000,
            }
            if cursor:
                params["cursor"] = cursor

            resp = await client.get(
                f"{datalake_url}/query",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
            body = resp.json()

            for row in body.get("data", []):
                ts_str = row.get("timestamp", "")
                price = float(row.get("price", 0))
                if not _validate_tick(price, ts_str, row_idx):
                    row_idx += 1
                    continue
                tick = Tick(
                    ts=pd.Timestamp(ts_str),
                    price=price,
                    volume=float(row.get("volume", 0)),
                    bid=row.get("bid"),
                    ask=row.get("ask"),
                )
                ticks.append(tick)
                row_idx += 1

            if not body.get("has_more", False):
                break
            cursor = body.get("next_cursor")

    ticks = _validate_tick_sequence(ticks)
    logger.info("Loaded %d ticks from datalake API", len(ticks))
    return ticks


async def load_ticks(
    instrument: str = "XAUUSD",
    datalake_url: str | None = None,
    datalake_api_key: str | None = None,
    csv_dir: Path | None = None,
) -> list:
    """Load ticks from datalake if available, fall back to CSV."""
    if not TICK_SUPPORT:
        return []

    if datalake_url:
        try:
            return await load_ticks_from_datalake(instrument, datalake_url, datalake_api_key)
        except Exception:
            logger.warning("Datalake tick load failed, falling back to CSV", exc_info=True)

    try:
        return await load_ticks_from_csv(instrument, csv_dir=csv_dir or DEFAULT_CSV_DIR)
    except FileNotFoundError:
        return []


def tick_data_available(
    instrument: str = "XAUUSD",
    csv_dir: Path = DEFAULT_CSV_DIR,
) -> bool:
    """Check if tick data exists for the given instrument."""
    if not TICK_SUPPORT:
        return False
    filepath = csv_dir / f"{instrument}_TICK.csv"
    return filepath.exists()


def _backtesting_bar_to_dashboard(bar) -> Bar:
    """Convert a backtesting-engine Bar to a dashboard Bar."""
    return Bar(
        timestamp=bar.ts.to_pydatetime(),
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
    )


async def replay_ticks(
    ticks: list,
    aggregator: Any,
    speed: float | Callable[[], float] = 1.0,
    base_interval: float = 0.05,
) -> AsyncIterator[dict]:
    """Yield tick events with optional completed bars.

    Each yield is a dict: {"tick": Tick, "bar": Bar|None}
    where bar is not None when the tick crossed a bar boundary.

    At high speeds, processes multiple ticks per sleep cycle to avoid
    event-loop overhead dominating. asyncio.sleep() has ~1ms minimum
    resolution, so at 10x speed the naive 0.005s sleep per tick is
    clamped to ~1ms — meaning 900 ticks/bar takes ~0.9s of pure sleep
    overhead. Instead, we compute how many ticks to process in one
    batch so total elapsed time stays proportional to wall-clock speed.

    Args:
        ticks: Pre-loaded list of Tick objects.
        aggregator: TickAggregator instance (from backtesting engine).
        speed: Replay speed multiplier, or callable returning current speed.
        base_interval: Base seconds between ticks (before speed adjustment).
    """
    # Minimum sleep quantum — below this, asyncio.sleep resolution wastes time
    MIN_SLEEP = 0.01  # 10ms

    i = 0
    n = len(ticks)
    while i < n:
        # Wait while paused (speed == 0)
        while True:
            current_speed = speed() if callable(speed) else speed
            if current_speed > 0:
                break
            await asyncio.sleep(0.1)

        # Compute how many ticks to process before the next sleep.
        # target_sleep = base_interval / speed, but we clamp to MIN_SLEEP
        # and process multiple ticks to compensate.
        target_sleep = base_interval / current_speed
        if target_sleep >= MIN_SLEEP:
            batch_size = 1
            actual_sleep = target_sleep
        else:
            # How many ticks fit into one MIN_SLEEP window?
            batch_size = max(1, int(MIN_SLEEP / target_sleep))
            actual_sleep = MIN_SLEEP

        # Process batch_size ticks (or remaining, whichever is smaller)
        end = min(i + batch_size, n)
        while i < end:
            tick = ticks[i]
            completed_bar = aggregator.update(tick)
            dashboard_bar = _backtesting_bar_to_dashboard(completed_bar) if completed_bar else None
            yield {"tick": tick, "bar": dashboard_bar}
            i += 1

        await asyncio.sleep(actual_sleep)

    # Flush the last partial bar
    final_bar = aggregator.flush()
    if final_bar is not None:
        yield {"tick": ticks[-1], "bar": _backtesting_bar_to_dashboard(final_bar)}


# ── Datalake WebSocket streaming ─────────────────────────────────────────────

def _datalake_ws_url(base_url: str) -> str:
    """Convert a datalake HTTP URL to its WebSocket equivalent."""
    return base_url.replace("http://", "ws://").replace("https://", "wss://")


async def stream_bars_from_datalake(
    instrument: str,
    timeframe: str,
    datalake_url: str,
    speed: float = 1.0,
    max_delay: float = 0.5,
    start: str | None = None,
    end: str | None = None,
) -> AsyncIterator[Bar]:
    """Connect to datalake /ws/bars and yield Bar objects.

    The datalake controls pacing (real-time timestamp deltas / speed),
    capped by max_delay to avoid long waits on data gaps.
    Yields until the datalake sends {"done": true} or the connection closes.

    Args:
        instrument: e.g. "XAUUSD"
        timeframe: e.g. "M15"
        datalake_url: HTTP base URL (converted to ws:// internally)
        speed: Playback speed multiplier (1.0 = real-time)
        max_delay: Maximum seconds between messages (caps gap sleeps)
        start: Optional ISO-8601 start bound (for resuming after reconnect)
        end: Optional ISO-8601 end bound
    """
    ws_base = _datalake_ws_url(datalake_url)
    params = f"instrument={instrument}&timeframe={timeframe}&speed={speed}&max_delay={max_delay}"
    if start:
        params += f"&start={start}"
    if end:
        params += f"&end={end}"
    url = f"{ws_base}/ws/bars?{params}"

    logger.info("Connecting to datalake bar stream: %s", url)
    async with websockets.connect(url, open_timeout=120) as ws:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("done"):
                logger.info("Datalake bar stream complete")
                return
            if "error" in msg:
                logger.error("Datalake bar stream error: %s", msg["error"])
                return
            yield Bar(
                timestamp=datetime.fromisoformat(msg["timestamp"]),
                open=float(msg["open"]),
                high=float(msg["high"]),
                low=float(msg["low"]),
                close=float(msg["close"]),
                instrument=instrument,
                timeframe=timeframe,
            )


async def stream_ticks_from_datalake(
    instrument: str,
    datalake_url: str,
    speed: float = 1.0,
    max_delay: float = 0.5,
    start: str | None = None,
    end: str | None = None,
) -> AsyncIterator[dict]:
    """Connect to datalake /ws/ticks and yield raw tick dicts.

    Each yielded dict has: timestamp, price, volume, bid (optional), ask (optional).
    The datalake controls pacing, capped by max_delay. Yields until {"done": true}.

    Args:
        instrument: e.g. "XAUUSD"
        datalake_url: HTTP base URL (converted to ws:// internally)
        speed: Playback speed multiplier
        max_delay: Maximum seconds between messages
        start: Optional ISO-8601 start bound
        end: Optional ISO-8601 end bound
    """
    ws_base = _datalake_ws_url(datalake_url)
    params = f"instrument={instrument}&speed={speed}&max_delay={max_delay}"
    if start:
        params += f"&start={start}"
    if end:
        params += f"&end={end}"
    url = f"{ws_base}/ws/ticks?{params}"

    logger.info("Connecting to datalake tick stream: %s", url)
    async with websockets.connect(url, open_timeout=120) as ws:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("done"):
                logger.info("Datalake tick stream complete")
                return
            if "error" in msg:
                logger.error("Datalake tick stream error: %s", msg["error"])
                return
            yield msg
