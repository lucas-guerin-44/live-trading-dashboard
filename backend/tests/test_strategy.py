"""Unit tests for the MA crossover strategy."""

from __future__ import annotations

import pytest

from models import Side
from strategies.ma_crossover import MACrossoverStrategy

from .conftest import make_bar


class TestMACalculation:
    def test_no_signal_before_slow_period_filled(self):
        """Strategy should emit no events until we have enough bars for the slow MA."""
        strategy = MACrossoverStrategy(fast_period=3, slow_period=5)
        events = []
        for i in range(4):
            events.extend(strategy.on_bar(make_bar(2000.0 + i)))
        assert events == []
        assert strategy.open_trade is None

    def test_fast_ma_computed_correctly(self):
        strategy = MACrossoverStrategy(fast_period=3, slow_period=5)
        closes = [10.0, 20.0, 30.0, 40.0, 50.0]
        for c in closes:
            strategy.on_bar(make_bar(c))
        # fast MA(3) of last 3 closes: (30+40+50)/3
        assert strategy.current_fast_ma == pytest.approx(40.0)

    def test_slow_ma_computed_correctly(self):
        strategy = MACrossoverStrategy(fast_period=3, slow_period=5)
        closes = [10.0, 20.0, 30.0, 40.0, 50.0]
        for c in closes:
            strategy.on_bar(make_bar(c))
        # slow MA(5): (10+20+30+40+50)/5
        assert strategy.current_slow_ma == pytest.approx(30.0)


class TestBullishCrossover:
    def test_opens_buy_on_bullish_cross(self, rising_bars):
        strategy = MACrossoverStrategy()
        events = []
        for bar in rising_bars:
            events.extend(strategy.on_bar(bar))

        opens = [e for e in events if e["type"] == "TRADE_OPEN"]
        assert len(opens) >= 1
        assert opens[0]["data"].side == Side.BUY

    def test_metrics_update_on_open(self, rising_bars):
        strategy = MACrossoverStrategy()
        events = []
        for bar in rising_bars:
            events.extend(strategy.on_bar(bar))

        metrics_events = [e for e in events if e["type"] == "METRICS"]
        assert len(metrics_events) >= 1
        assert strategy.metrics.open_positions == 1


class TestBearishCrossover:
    def test_opens_sell_on_bearish_cross(self, falling_bars):
        strategy = MACrossoverStrategy()
        events = []
        for bar in falling_bars:
            events.extend(strategy.on_bar(bar))

        opens = [e for e in events if e["type"] == "TRADE_OPEN"]
        assert len(opens) >= 1
        assert opens[0]["data"].side == Side.SELL


class TestStopLoss:
    def test_stop_loss_triggers(self):
        """A BUY trade should close at stop-loss when price drops."""
        # Use tiny periods so crossover is deterministic
        strategy = MACrossoverStrategy(fast_period=2, slow_period=3, stop_loss_pct=1.0)

        # 3 flat bars to seed slow MA, then 3 rising to force bullish cross
        for _ in range(3):
            strategy.on_bar(make_bar(100.0))
        for i in range(3):
            strategy.on_bar(make_bar(100.0 + (i + 1) * 5))

        assert strategy.open_trade is not None, "Crossover must fire with these prices"
        entry = strategy.open_trade.entry_price

        # Drop below stop-loss (1% below entry)
        crash_bar = make_bar(entry * 0.95, low=entry * 0.94)
        events = strategy.on_bar(crash_bar)
        closes = [e for e in events if e["type"] == "TRADE_CLOSE"]
        assert len(closes) == 1
        assert closes[0]["data"].exit_reason == "STOP_LOSS"
        assert closes[0]["data"].pnl < 0

    def test_stop_loss_fills_at_stop_price_not_close(self):
        """Stop should fill at the stop level, not the bar close."""
        strategy = MACrossoverStrategy(fast_period=2, slow_period=3, stop_loss_pct=2.0)

        for _ in range(3):
            strategy.on_bar(make_bar(100.0))
        for i in range(3):
            strategy.on_bar(make_bar(100.0 + (i + 1) * 5))

        assert strategy.open_trade is not None
        stop_price = strategy.open_trade.stop_loss_price

        # Bar close is way below stop, but fill should be at stop price
        crash_bar = make_bar(80.0, low=79.0)
        strategy.on_bar(crash_bar)

        trade = strategy.closed_trades[-1]
        assert trade.exit_price == stop_price

    def test_sell_stop_loss_triggers_on_high(self):
        """A SELL trade stop-loss triggers when bar high >= stop price."""
        strategy = MACrossoverStrategy(fast_period=2, slow_period=3, stop_loss_pct=1.0)

        # Seed flat, then falling to get bearish cross
        for _ in range(3):
            strategy.on_bar(make_bar(100.0))
        for i in range(3):
            strategy.on_bar(make_bar(100.0 - (i + 1) * 5))

        assert strategy.open_trade is not None
        assert strategy.open_trade.side == Side.SELL

        entry = strategy.open_trade.entry_price
        # Spike above stop
        spike_bar = make_bar(entry * 0.99, high=entry * 1.05)
        events = strategy.on_bar(spike_bar)
        closes = [e for e in events if e["type"] == "TRADE_CLOSE"]
        assert len(closes) == 1
        assert closes[0]["data"].exit_reason == "STOP_LOSS"


class TestTradeLifecycle:
    def test_full_cycle_buy_then_sell(self, rising_bars, falling_bars):
        """Feed rising then falling bars - should open BUY, then close on bearish cross."""
        strategy = MACrossoverStrategy()
        all_events = []
        for bar in rising_bars + falling_bars:
            all_events.extend(strategy.on_bar(bar))

        opens = [e for e in all_events if e["type"] == "TRADE_OPEN"]
        closes = [e for e in all_events if e["type"] == "TRADE_CLOSE"]

        assert len(opens) >= 1
        assert len(closes) >= 1

    def test_closed_trade_has_pnl(self):
        """Deterministic open+close: verify trade has exit fields populated."""
        strategy = MACrossoverStrategy(fast_period=2, slow_period=3, stop_loss_pct=1.0)

        # Open a trade
        for _ in range(3):
            strategy.on_bar(make_bar(100.0))
        for i in range(3):
            strategy.on_bar(make_bar(100.0 + (i + 1) * 5))
        assert strategy.open_trade is not None

        # Close via stop-loss
        entry = strategy.open_trade.entry_price
        strategy.on_bar(make_bar(entry * 0.95, low=entry * 0.94))

        assert len(strategy.closed_trades) == 1
        trade = strategy.closed_trades[0]
        assert trade.exit_price is not None
        assert trade.exit_time is not None
        assert trade.pnl != 0.0

    def test_metrics_track_wins_losses(self):
        """After a losing trade, metrics reflect total_trades and win_rate."""
        strategy = MACrossoverStrategy(fast_period=2, slow_period=3, stop_loss_pct=1.0)

        for _ in range(3):
            strategy.on_bar(make_bar(100.0))
        for i in range(3):
            strategy.on_bar(make_bar(100.0 + (i + 1) * 5))
        assert strategy.open_trade is not None

        entry = strategy.open_trade.entry_price
        strategy.on_bar(make_bar(entry * 0.95, low=entry * 0.94))

        m = strategy.metrics
        assert m.total_trades == 1
        assert m.total_trades == m.winning_trades + m.losing_trades
        assert 0 <= m.win_rate <= 100


class TestMetricsAccuracy:
    def test_capital_changes_by_pnl(self):
        strategy = MACrossoverStrategy(fast_period=2, slow_period=3, initial_capital=10000, stop_loss_pct=1.0)

        for _ in range(3):
            strategy.on_bar(make_bar(100.0))
        for i in range(3):
            strategy.on_bar(make_bar(100.0 + (i + 1) * 5))

        if strategy.open_trade is not None:
            entry = strategy.open_trade.entry_price
            strategy.on_bar(make_bar(entry * 0.95, low=entry * 0.94))

        m = strategy.metrics
        expected_capital = m.initial_capital + m.total_pnl
        assert m.current_capital == pytest.approx(expected_capital, abs=0.01)

    def test_drawdown_non_negative(self, rising_bars, falling_bars):
        strategy = MACrossoverStrategy()
        for bar in rising_bars + falling_bars:
            strategy.on_bar(bar)
        assert strategy.metrics.max_drawdown >= 0


class TestCostModel:
    def test_spread_deducted_from_pnl(self):
        """Trades with spread should have lower PnL than without."""
        def run_strategy(spread):
            s = MACrossoverStrategy(fast_period=2, slow_period=3, stop_loss_pct=1.0, spread=spread)
            for _ in range(3):
                s.on_bar(make_bar(100.0))
            for i in range(3):
                s.on_bar(make_bar(100.0 + (i + 1) * 5))
            assert s.open_trade is not None
            entry = s.open_trade.entry_price
            s.on_bar(make_bar(entry * 0.95, low=entry * 0.94))
            return s

        no_cost = run_strategy(spread=0.0)
        with_cost = run_strategy(spread=1.0)

        assert with_cost.closed_trades[0].pnl < no_cost.closed_trades[0].pnl
        assert with_cost.metrics.total_spread_cost > 0

    def test_commission_deducted_from_pnl(self):
        def run_strategy(commission):
            s = MACrossoverStrategy(fast_period=2, slow_period=3, stop_loss_pct=1.0, commission_per_unit=commission)
            for _ in range(3):
                s.on_bar(make_bar(100.0))
            for i in range(3):
                s.on_bar(make_bar(100.0 + (i + 1) * 5))
            assert s.open_trade is not None
            entry = s.open_trade.entry_price
            s.on_bar(make_bar(entry * 0.95, low=entry * 0.94))
            return s

        no_cost = run_strategy(commission=0.0)
        with_cost = run_strategy(commission=0.5)

        assert with_cost.closed_trades[0].pnl < no_cost.closed_trades[0].pnl
        assert with_cost.metrics.total_commission > 0

    def test_slippage_worsens_exit(self):
        """Slippage should make exit price worse for BUY trades."""
        def run_strategy(slippage):
            s = MACrossoverStrategy(fast_period=2, slow_period=3, stop_loss_pct=1.0, slippage_pct=slippage)
            for _ in range(3):
                s.on_bar(make_bar(100.0))
            for i in range(3):
                s.on_bar(make_bar(100.0 + (i + 1) * 5))
            assert s.open_trade is not None
            entry = s.open_trade.entry_price
            s.on_bar(make_bar(entry * 0.95, low=entry * 0.94))
            return s

        no_slip = run_strategy(slippage=0.0)
        with_slip = run_strategy(slippage=1.0)

        # BUY exit with slippage → lower exit price
        assert with_slip.closed_trades[0].exit_price < no_slip.closed_trades[0].exit_price


class TestDrawdownGate:
    def test_refuses_trade_at_max_drawdown(self):
        """After drawdown exceeds limit, new trades should be blocked."""
        strategy = MACrossoverStrategy(
            fast_period=2, slow_period=3, stop_loss_pct=1.0,
            initial_capital=1000, max_drawdown_pct=5.0,
        )
        # Artificially set drawdown past limit
        strategy._metrics.max_drawdown = 6.0
        strategy._metrics.peak_capital = 1000
        strategy._metrics.current_capital = 940

        # Try to open a trade via crossover
        for _ in range(3):
            strategy.on_bar(make_bar(100.0))
        for i in range(3):
            strategy.on_bar(make_bar(100.0 + (i + 1) * 5))

        # Should be blocked by drawdown gate
        assert strategy.open_trade is None


class TestPositionSizing:
    def test_tiny_stop_capped_by_leverage_limit(self):
        """A very small stop distance should not produce an enormous position."""
        strategy = MACrossoverStrategy(fast_period=2, slow_period=3, initial_capital=10000)

        # Tiny stop distance: 0.01% of a $100 entry = $0.01
        qty = strategy._compute_quantity(entry_price=100.0, stop_loss_price=99.99)
        # 10x leverage cap: max notional = 100k, max qty at $100 = 1000
        max_qty = (10000 * 10) / 100.0
        assert qty <= max_qty

    def test_no_stop_uses_conservative_sizing(self):
        strategy = MACrossoverStrategy(initial_capital=10000)
        qty = strategy._compute_quantity(entry_price=100.0, stop_loss_price=None)
        # 1% notional / price = 100 / 100 = 1.0
        assert qty == pytest.approx(1.0, abs=0.01)

    def test_normal_stop_sizes_by_risk(self):
        """With 2% risk and $2 stop distance on $100, qty = 200/2 = 100."""
        strategy = MACrossoverStrategy(initial_capital=10000, risk_per_trade_pct=2.0)
        qty = strategy._compute_quantity(entry_price=100.0, stop_loss_price=98.0)
        # risk = 200, distance = 2, qty = 100
        assert qty == pytest.approx(100.0, abs=0.01)
