"""Tests for data validation: OHLC sanity, timestamp ordering, dedup, gaps."""

from __future__ import annotations

import pytest

from replay import load_bars_from_csv


class TestOHLCValidation:
    @pytest.mark.asyncio
    async def test_negative_prices_skipped(self, tmp_path):
        csv = tmp_path / "XAUUSD_M15.csv"
        csv.write_text(
            "timestamp,open,high,low,close\n"
            "2024-01-01T00:00:00+00:00,2000,2001,1999,2000.5\n"
            "2024-01-01T00:15:00+00:00,-1,2002,2000,2001\n"  # negative open
            "2024-01-01T00:30:00+00:00,2001,2003,2000.5,2002\n"
        )
        bars = await load_bars_from_csv(csv_dir=tmp_path)
        assert len(bars) == 2  # bad row skipped

    @pytest.mark.asyncio
    async def test_high_less_than_low_skipped(self, tmp_path):
        csv = tmp_path / "XAUUSD_M15.csv"
        csv.write_text(
            "timestamp,open,high,low,close\n"
            "2024-01-01T00:00:00+00:00,2000,2001,1999,2000.5\n"
            "2024-01-01T00:15:00+00:00,2000,1998,2002,2001\n"  # high < low
            "2024-01-01T00:30:00+00:00,2001,2003,2000.5,2002\n"
        )
        bars = await load_bars_from_csv(csv_dir=tmp_path)
        assert len(bars) == 2

    @pytest.mark.asyncio
    async def test_high_less_than_close_skipped(self, tmp_path):
        csv = tmp_path / "XAUUSD_M15.csv"
        csv.write_text(
            "timestamp,open,high,low,close\n"
            "2024-01-01T00:00:00+00:00,2000,2001,1999,2000.5\n"
            "2024-01-01T00:15:00+00:00,2000,2000.5,1999,2001\n"  # high < close
            "2024-01-01T00:30:00+00:00,2001,2003,2000.5,2002\n"
        )
        bars = await load_bars_from_csv(csv_dir=tmp_path)
        assert len(bars) == 2


class TestTimestampValidation:
    @pytest.mark.asyncio
    async def test_out_of_order_sorted(self, tmp_path):
        csv = tmp_path / "XAUUSD_M15.csv"
        csv.write_text(
            "timestamp,open,high,low,close\n"
            "2024-01-01T00:30:00+00:00,2001,2003,2000.5,2002\n"  # 3rd
            "2024-01-01T00:00:00+00:00,2000,2001,1999,2000.5\n"  # 1st
            "2024-01-01T00:15:00+00:00,2000.5,2002,2000,2001\n"  # 2nd
        )
        bars = await load_bars_from_csv(csv_dir=tmp_path)
        assert len(bars) == 3
        # Verify sorted order
        assert bars[0].close == 2000.5
        assert bars[1].close == 2001
        assert bars[2].close == 2002

    @pytest.mark.asyncio
    async def test_duplicate_timestamps_deduplicated(self, tmp_path):
        csv = tmp_path / "XAUUSD_M15.csv"
        csv.write_text(
            "timestamp,open,high,low,close\n"
            "2024-01-01T00:00:00+00:00,2000,2001,1999,2000.5\n"
            "2024-01-01T00:00:00+00:00,2000,2001,1999,2000.8\n"  # duplicate
            "2024-01-01T00:15:00+00:00,2000.5,2002,2000,2001\n"
        )
        bars = await load_bars_from_csv(csv_dir=tmp_path)
        assert len(bars) == 2
        # Last occurrence kept
        assert bars[0].close == 2000.8
