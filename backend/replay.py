"""Replay engine: loads historical data and drip-feeds bars at configurable speed."""

from __future__ import annotations

import asyncio
import csv
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator, Callable

import httpx

from models import Bar

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
