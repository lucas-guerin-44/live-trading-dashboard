"""Tests for the Bollinger Band mean reversion strategy."""

from __future__ import annotations

import pytest

from models import Side
from strategies.mean_reversion import MeanReversionStrategy

from .conftest import make_bar


class TestBollingerBands:
    def test_no_signal_before_period_filled(self):
        strategy = MeanReversionStrategy(period=5)
        events = []
        for i in range(4):
            events.extend(strategy.on_bar(make_bar(100.0 + i)))
        assert events == []
        assert strategy.open_trade is None

    def test_bands_computed_after_enough_data(self):
        strategy = MeanReversionStrategy(period=5, num_std=2.0)
        for i in range(5):
            strategy.on_bar(make_bar(100.0 + i))
        assert strategy.current_fast_ma is not None  # upper band
        assert strategy.current_slow_ma is not None  # lower band

    def test_upper_band_above_lower(self):
        strategy = MeanReversionStrategy(period=5, num_std=2.0)
        for i in range(5):
            strategy.on_bar(make_bar(100.0 + i))
        assert strategy.current_fast_ma > strategy.current_slow_ma


class TestMeanReversionEntry:
    def test_buy_on_lower_band_touch(self):
        """Price dropping to the lower band should trigger a BUY."""
        strategy = MeanReversionStrategy(period=5, num_std=1.0, stop_loss_pct=5.0)

        # Seed with stable prices to establish bands
        for _ in range(5):
            strategy.on_bar(make_bar(100.0))

        # Drop below lower band
        events = strategy.on_bar(make_bar(90.0))
        opens = [e for e in events if e["type"] == "TRADE_OPEN"]
        assert len(opens) == 1
        assert opens[0]["data"].side == Side.BUY

    def test_sell_on_upper_band_touch(self):
        """Price spiking to the upper band should trigger a SELL."""
        strategy = MeanReversionStrategy(period=5, num_std=1.0, stop_loss_pct=5.0)

        for _ in range(5):
            strategy.on_bar(make_bar(100.0))

        events = strategy.on_bar(make_bar(110.0))
        opens = [e for e in events if e["type"] == "TRADE_OPEN"]
        assert len(opens) == 1
        assert opens[0]["data"].side == Side.SELL


class TestMeanReversionExit:
    def test_buy_exits_at_mean(self):
        """A BUY should close when price reverts to the SMA."""
        strategy = MeanReversionStrategy(period=5, num_std=1.0, stop_loss_pct=10.0)

        for _ in range(5):
            strategy.on_bar(make_bar(100.0))
        # Trigger buy
        strategy.on_bar(make_bar(90.0))
        assert strategy.open_trade is not None

        # Revert to mean
        events = strategy.on_bar(make_bar(100.0))
        closes = [e for e in events if e["type"] == "TRADE_CLOSE"]
        assert len(closes) == 1
        assert closes[0]["data"].exit_reason == "MEAN_REVERSION"

    def test_stop_loss_overrides_mean_reversion(self):
        """Stop-loss should fire before mean reversion if price crashes."""
        strategy = MeanReversionStrategy(period=5, num_std=1.0, stop_loss_pct=2.0)

        for _ in range(5):
            strategy.on_bar(make_bar(100.0))
        strategy.on_bar(make_bar(90.0))
        assert strategy.open_trade is not None

        # Crash well below stop
        events = strategy.on_bar(make_bar(80.0, low=79.0))
        closes = [e for e in events if e["type"] == "TRADE_CLOSE"]
        assert len(closes) == 1
        assert closes[0]["data"].exit_reason == "STOP_LOSS"


class TestMeanReversionConfig:
    def test_configurable_params(self):
        strategy = MeanReversionStrategy()
        names = {p["name"] for p in strategy.configurable_params}
        assert "period" in names
        assert "num_std" in names
        assert "stop_loss_pct" in names

    def test_indicator_overlay_true(self):
        """BB bands should render on the price chart."""
        strategy = MeanReversionStrategy()
        assert strategy.indicator_overlay is True
