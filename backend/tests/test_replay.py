"""Tests for the replay / data-loading module."""

from __future__ import annotations

import pytest

from replay import load_bars_from_csv, replay_bars


class TestCSVLoading:
    @pytest.mark.asyncio
    async def test_loads_bars_from_csv(self, sample_csv):
        bars = await load_bars_from_csv(csv_dir=sample_csv)
        assert len(bars) == 3
        assert bars[0].close == 2000.5
        assert bars[2].close == 2002

    @pytest.mark.asyncio
    async def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            await load_bars_from_csv(csv_dir=tmp_path, instrument="MISSING", timeframe="H1")

    @pytest.mark.asyncio
    async def test_bar_fields_populated(self, sample_csv):
        bars = await load_bars_from_csv(csv_dir=sample_csv)
        bar = bars[0]
        assert bar.instrument == "XAUUSD"
        assert bar.timeframe == "M15"
        assert bar.open == 2000
        assert bar.high == 2001
        assert bar.low == 1999


class TestReplayBars:
    @pytest.mark.asyncio
    async def test_yields_all_bars(self, sample_csv):
        bars = await load_bars_from_csv(csv_dir=sample_csv)
        replayed = []
        async for bar in replay_bars(bars, speed=1000):  # fast
            replayed.append(bar)
        assert len(replayed) == len(bars)

    @pytest.mark.asyncio
    async def test_respects_speed(self, sample_csv):
        """Faster speed = shorter total time."""
        import time

        bars = await load_bars_from_csv(csv_dir=sample_csv)

        start = time.monotonic()
        async for _ in replay_bars(bars, speed=100, base_interval=0.1):
            pass
        fast_elapsed = time.monotonic() - start

        start = time.monotonic()
        async for _ in replay_bars(bars, speed=1, base_interval=0.1):
            pass
        slow_elapsed = time.monotonic() - start

        assert fast_elapsed < slow_elapsed

    @pytest.mark.asyncio
    async def test_empty_bars_yields_nothing(self):
        replayed = []
        async for bar in replay_bars([], speed=100):
            replayed.append(bar)
        assert replayed == []
