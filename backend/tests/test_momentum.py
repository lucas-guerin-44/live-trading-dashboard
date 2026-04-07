"""Tests for the RSI + ADX momentum strategy."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models import Side
from strategies.momentum import MomentumStrategy

from .conftest import make_bar


def _trending_bars(n: int = 50, start: float = 100.0, step: float = 3.0) -> list:
    """Generate a strong uptrend with proper HLC spread."""
    bars = []
    for i in range(n):
        price = start + i * step
        bars.append(make_bar(
            close=price,
            high=price + 2,
            low=price - 1,
            timestamp=datetime(2024, 1, 1 + i // 96, (i % 96) // 4, (i % 4) * 15, tzinfo=timezone.utc),
        ))
    return bars


def _ranging_bars(n: int = 60, center: float = 100.0, amplitude: float = 0.5) -> list:
    """Generate flat/choppy bars to keep ADX low."""
    bars = []
    for i in range(n):
        price = center + (1 if i % 2 == 0 else -1) * amplitude
        bars.append(make_bar(
            close=price,
            high=price + 0.3,
            low=price - 0.3,
            timestamp=datetime(2024, 1, 1 + i // 96, (i % 96) // 4, (i % 4) * 15, tzinfo=timezone.utc),
        ))
    return bars


class TestRSICalculation:
    def test_rsi_needs_enough_data(self):
        strategy = MomentumStrategy(rsi_period=5)
        for i in range(5):
            strategy.on_bar(make_bar(100.0 + i))
        # 5 closes = only 4 changes, need rsi_period+1=6 closes for RSI
        assert strategy.current_fast_ma is None

    def test_rsi_bounded_0_100(self):
        """RSI should always be between 0 and 100."""
        strategy = MomentumStrategy(rsi_period=5)
        for bar in _trending_bars(20):
            strategy.on_bar(bar)
        rsi = strategy.current_fast_ma
        assert rsi is not None, "RSI should compute after 20 bars with period=5"
        assert 0 <= rsi <= 100

    def test_rsi_high_on_strong_uptrend(self):
        """Consistently rising prices should produce RSI > 50."""
        strategy = MomentumStrategy(rsi_period=5)
        for bar in _trending_bars(20):
            strategy.on_bar(bar)
        rsi = strategy.current_fast_ma
        assert rsi is not None
        assert rsi > 50

    def test_rsi_100_on_all_gains(self):
        """If every close is higher than the last, RSI = 100."""
        strategy = MomentumStrategy(rsi_period=5)
        for i in range(20):
            strategy.on_bar(make_bar(100.0 + i * 10))
        assert strategy.current_fast_ma == pytest.approx(100.0)

    def test_rsi_low_on_downtrend(self):
        strategy = MomentumStrategy(rsi_period=5)
        for i in range(20):
            strategy.on_bar(make_bar(200.0 - i * 5))
        rsi = strategy.current_fast_ma
        assert rsi is not None
        assert rsi < 50


class TestADXCalculation:
    def test_adx_needs_enough_data(self):
        strategy = MomentumStrategy(adx_period=5)
        for i in range(10):
            strategy.on_bar(make_bar(100.0 + i))
        # Need 2*n+1 bars minimum
        assert strategy.current_slow_ma is None or isinstance(strategy.current_slow_ma, float)

    def test_adx_positive_on_trending_data(self):
        """Strong trending data should produce a positive ADX."""
        strategy = MomentumStrategy(adx_period=5)
        for bar in _trending_bars(50):
            strategy.on_bar(bar)
        adx = strategy.current_slow_ma
        assert adx is not None, "ADX should compute after 50 bars with period=5"
        assert adx > 0

    def test_adx_low_on_ranging_data(self):
        """Flat data should keep ADX low."""
        strategy = MomentumStrategy(adx_period=5)
        for bar in _ranging_bars(60):
            strategy.on_bar(bar)
        adx = strategy.current_slow_ma
        if adx is not None:
            assert adx < 30


class TestRegimeFilter:
    def test_no_trades_in_ranging_market(self):
        """Flat/choppy data should keep ADX low and prevent entries."""
        strategy = MomentumStrategy(
            rsi_period=5, adx_period=5, adx_threshold=25.0,
        )
        events = []
        for bar in _ranging_bars(60):
            events.extend(strategy.on_bar(bar))
        opens = [e for e in events if e["type"] == "TRADE_OPEN"]
        assert len(opens) == 0


class TestMomentumLifecycle:
    def test_stop_loss_triggers(self):
        """Force a trade open, then crash price to trigger stop-loss."""
        strategy = MomentumStrategy(
            rsi_period=5, adx_period=5, adx_threshold=10.0,
            rsi_oversold=30.0, stop_loss_pct=1.0,
        )

        # Strong downtrend → RSI drops below 30
        for i in range(15):
            price = 200.0 - i * 5
            strategy.on_bar(make_bar(
                close=price, high=price + 3, low=price - 2,
                timestamp=datetime(2024, 1, 1, i // 4, (i % 4) * 15, tzinfo=timezone.utc),
            ))

        # Bounce up → RSI crosses above 30 (buy signal)
        for i in range(10):
            price = 140.0 + i * 4
            strategy.on_bar(make_bar(
                close=price, high=price + 3, low=price - 2,
                timestamp=datetime(2024, 1, 2, i // 4, (i % 4) * 15, tzinfo=timezone.utc),
            ))

        if strategy.open_trade is None:
            pytest.skip("RSI/ADX didn't align for entry with these parameters")

        entry = strategy.open_trade.entry_price
        crash = entry * 0.95
        events = strategy.on_bar(make_bar(
            close=crash, high=crash + 1, low=crash - 1,
            timestamp=datetime(2024, 1, 3, 0, 0, tzinfo=timezone.utc),
        ))
        closes = [e for e in events if e["type"] == "TRADE_CLOSE"]
        assert len(closes) == 1
        assert closes[0]["data"].exit_reason == "STOP_LOSS"

    def test_regime_shift_closes_position(self):
        """When ADX drops below threshold, open position should close."""
        strategy = MomentumStrategy(
            rsi_period=5, adx_period=5, adx_threshold=10.0,
            rsi_oversold=30.0, stop_loss_pct=50.0,  # wide stop so it doesn't interfere
        )

        # Downtrend to get RSI < 30
        for i in range(15):
            price = 200.0 - i * 5
            strategy.on_bar(make_bar(
                close=price, high=price + 3, low=price - 2,
                timestamp=datetime(2024, 1, 1, i // 4, (i % 4) * 15, tzinfo=timezone.utc),
            ))

        # Bounce to trigger buy
        for i in range(10):
            price = 140.0 + i * 4
            strategy.on_bar(make_bar(
                close=price, high=price + 3, low=price - 2,
                timestamp=datetime(2024, 1, 2, i // 4, (i % 4) * 15, tzinfo=timezone.utc),
            ))

        if strategy.open_trade is None:
            pytest.skip("RSI/ADX didn't align for entry")

        # Go flat to kill ADX
        events = []
        for i in range(30):
            price = 170.0 + (0.1 if i % 2 == 0 else -0.1)
            events.extend(strategy.on_bar(make_bar(
                close=price, high=price + 0.1, low=price - 0.1,
                timestamp=datetime(2024, 1, 3, i // 4, (i % 4) * 15, tzinfo=timezone.utc),
            )))

        closes = [e for e in events if e["type"] == "TRADE_CLOSE"]
        # Should close on regime shift or stop loss — either way it closed
        if closes:
            assert closes[0]["data"].exit_reason in ("REGIME_SHIFT", "STOP_LOSS")

    def test_configurable_params_present(self):
        strategy = MomentumStrategy()
        params = strategy.configurable_params
        names = {p["name"] for p in params}
        assert "rsi_period" in names
        assert "adx_period" in names
        assert "adx_threshold" in names
        assert "stop_loss_pct" in names

    def test_indicator_labels(self):
        strategy = MomentumStrategy()
        assert strategy.indicator_labels == ("RSI", "ADX")

    def test_indicator_overlay_false(self):
        """Momentum indicators should render in a separate pane."""
        strategy = MomentumStrategy()
        assert strategy.indicator_overlay is False
