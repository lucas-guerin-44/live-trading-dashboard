"""Shared test fixtures."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models import Bar


def make_bar(
    close: float,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    timestamp: datetime | None = None,
) -> Bar:
    """Create a Bar with sensible defaults - only `close` is required."""
    c = close
    o = open_ if open_ is not None else c
    h = high if high is not None else max(o, c) + 0.5
    l_ = low if low is not None else min(o, c) - 0.5
    return Bar(
        timestamp=timestamp or datetime.now(timezone.utc),
        open=o,
        high=h,
        low=l_,
        close=c,
    )


@pytest.fixture()
def rising_bars() -> list[Bar]:
    """40 bars with a clear uptrend - enough to trigger a bullish MA crossover."""
    base = 2000.0
    bars: list[Bar] = []
    # 30 flat bars (seed the slow MA)
    for i in range(30):
        bars.append(make_bar(base, timestamp=datetime(2024, 1, 1, i // 4, (i % 4) * 15, tzinfo=timezone.utc)))
    # 10 rising bars (pull fast MA above slow)
    for i in range(10):
        price = base + (i + 1) * 3
        bars.append(make_bar(price, timestamp=datetime(2024, 1, 2, i // 4, (i % 4) * 15, tzinfo=timezone.utc)))
    return bars


@pytest.fixture()
def falling_bars() -> list[Bar]:
    """40 bars: 30 flat then 10 dropping - triggers bearish crossover."""
    base = 2000.0
    bars: list[Bar] = []
    for i in range(30):
        bars.append(make_bar(base, timestamp=datetime(2024, 1, 1, i // 4, (i % 4) * 15, tzinfo=timezone.utc)))
    for i in range(10):
        price = base - (i + 1) * 3
        bars.append(make_bar(price, timestamp=datetime(2024, 1, 2, i // 4, (i % 4) * 15, tzinfo=timezone.utc)))
    return bars


@pytest.fixture()
def sample_csv(tmp_path):
    """Write a tiny CSV and return its path."""
    csv_path = tmp_path / "XAUUSD_M15.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close\n"
        "2024-01-01T00:00:00+00:00,2000,2001,1999,2000.5\n"
        "2024-01-01T00:15:00+00:00,2000.5,2002,2000,2001\n"
        "2024-01-01T00:30:00+00:00,2001,2003,2000.5,2002\n"
    )
    return tmp_path
