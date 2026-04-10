"""Tick data types and bar aggregation.

Vendored from backtesting-engine-2.0/backtesting/tick.py.
Stripped aggregate_batch() (numpy dependency) — not needed for live streaming.

Provides:
- ``Tick``: a single price update (timestamp, price, volume, optional bid/ask).
- ``TickAggregator``: accumulates ticks into OHLC bars by timeframe boundary.
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from tick_engine.types import Bar


@dataclass(slots=True)
class Tick:
    """A single price update."""
    ts: pd.Timestamp
    price: float
    volume: float = 0.0
    bid: Optional[float] = None
    ask: Optional[float] = None


# Map common shorthand timeframes to pandas offset aliases.
_FREQ_MAP = {
    "M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
    "H1": "1h", "H4": "4h", "D1": "1D",
}

# Timeframe durations in nanoseconds for fast boundary computation.
_FREQ_NS = {
    "M1": 60_000_000_000,
    "M5": 300_000_000_000,
    "M15": 900_000_000_000,
    "M30": 1_800_000_000_000,
    "H1": 3_600_000_000_000,
    "H4": 14_400_000_000_000,
    "D1": 86_400_000_000_000,
    "1min": 60_000_000_000,
    "5min": 300_000_000_000,
    "15min": 900_000_000_000,
    "30min": 1_800_000_000_000,
    "1h": 3_600_000_000_000,
    "4h": 14_400_000_000_000,
    "1D": 86_400_000_000_000,
}


def _to_pd_freq(timeframe: str) -> str:
    """Convert a shorthand timeframe (e.g. 'M5') to a pandas freq string."""
    return _FREQ_MAP.get(timeframe, timeframe)


class TickAggregator:
    """Accumulates ticks into OHLC bars aligned to timeframe boundaries.

    Parameters
    ----------
    timeframe : str
        Bar timeframe, e.g. ``"M1"``, ``"M5"``, ``"H1"``.

    Uses integer nanosecond arithmetic for known timeframes (~50x faster
    than pd.Timestamp.floor()). Caches bar boundary to skip computation
    when consecutive ticks are in the same bar.
    """

    def __init__(self, timeframe: str):
        self._freq = _to_pd_freq(timeframe)
        self._bar_open_ts: Optional[pd.Timestamp] = None
        self._open: float = 0.0
        self._high: float = 0.0
        self._low: float = 0.0
        self._close: float = 0.0
        self._volume: float = 0.0
        self._tick_count: int = 0

        self._freq_ns: Optional[int] = _FREQ_NS.get(timeframe) or _FREQ_NS.get(self._freq)
        self._bar_start_ns: int = 0
        self._bar_end_ns: int = 0

    @property
    def current_bar(self) -> Optional[Bar]:
        """The in-progress (incomplete) bar, or None if no ticks received yet."""
        if self._bar_open_ts is None:
            return None
        return Bar(
            ts=self._bar_open_ts,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            volume=self._volume,
        )

    @property
    def tick_count(self) -> int:
        """Number of ticks in the current (incomplete) bar."""
        return self._tick_count

    def _floor_ns(self, ns: int) -> int:
        """Floor a nanosecond timestamp to bar boundary."""
        if self._freq_ns is not None:
            return (ns // self._freq_ns) * self._freq_ns
        return pd.Timestamp(ns, unit="ns").floor(self._freq).value

    def _emit_bar(self) -> Bar:
        """Package the current accumulator state as a completed Bar."""
        return Bar(
            ts=self._bar_open_ts,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            volume=self._volume,
        )

    def update(self, tick: Tick) -> Optional[Bar]:
        """Feed a tick. Returns a completed Bar if this tick crosses a boundary."""
        tick_ns = tick.ts.value

        if self._bar_open_ts is None:
            bar_ns = self._floor_ns(tick_ns)
            self._start_bar(bar_ns, tick.price, tick.volume)
            return None

        # Fast path: check if tick is within cached [start, end) boundary
        if self._bar_start_ns <= tick_ns < self._bar_end_ns:
            price = tick.price
            if price > self._high:
                self._high = price
            if price < self._low:
                self._low = price
            self._close = price
            self._volume += tick.volume
            self._tick_count += 1
            return None

        # Crossed boundary — emit old bar, start new one
        completed = self._emit_bar()
        bar_ns = self._floor_ns(tick_ns)
        self._start_bar(bar_ns, tick.price, tick.volume)
        return completed

    def flush(self) -> Optional[Bar]:
        """Force-emit the current in-progress bar (e.g. at end of data)."""
        if self._bar_open_ts is None:
            return None
        bar = self._emit_bar()
        self._bar_open_ts = None
        self._tick_count = 0
        return bar

    def _start_bar(self, bar_ns: int, price: float, volume: float) -> None:
        """Start a new bar from nanosecond timestamp and raw price/volume."""
        self._bar_open_ts = pd.Timestamp(bar_ns, unit="ns")
        self._open = price
        self._high = price
        self._low = price
        self._close = price
        self._volume = volume
        self._tick_count = 1
        self._bar_start_ns = bar_ns
        if self._freq_ns is not None:
            self._bar_end_ns = bar_ns + self._freq_ns
        else:
            self._bar_end_ns = bar_ns
