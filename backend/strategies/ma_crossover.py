"""MA crossover strategy - the default demo strategy.

Deliberately minimal - the point is the dashboard, not the alpha.
See base_strategy.py for the interface to implement your own.
"""

from __future__ import annotations

from collections import deque

from models import Bar, Side
from base_strategy import AbstractStrategy, register_strategy


@register_strategy("ma_crossover")
class MACrossoverStrategy(AbstractStrategy):
    """Generates BUY/SELL signals from fast/slow moving average crossover."""

    def __init__(
        self,
        fast_period: int = 10,
        slow_period: int = 30,
        initial_capital: float = 10_000,
        stop_loss_pct: float = 1.0,
        **_kwargs,
    ):
        super().__init__(initial_capital=initial_capital, **_kwargs)
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.stop_loss_pct = stop_loss_pct

        self._closes: deque[float] = deque(maxlen=slow_period)
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    def _ma(self, period: int) -> float | None:
        if len(self._closes) < period:
            return None
        vals = list(self._closes)[-period:]
        return sum(vals) / period

    @property
    def configurable_params(self) -> list[dict]:
        return [
            {"name": "fast_period", "label": "Fast MA Period", "type": "int", "value": self.fast_period, "min": 2, "max": 50, "step": 1},
            {"name": "slow_period", "label": "Slow MA Period", "type": "int", "value": self.slow_period, "min": 5, "max": 200, "step": 1},
            {"name": "stop_loss_pct", "label": "Stop Loss %", "type": "float", "value": self.stop_loss_pct, "min": 0.1, "max": 10.0, "step": 0.1},
        ]

    @property
    def indicator_labels(self) -> tuple[str, str]:
        return (f"MA{self.fast_period}", f"MA{self.slow_period}")

    @property
    def current_fast_ma(self) -> float | None:
        return self._ma(self.fast_period)

    @property
    def current_slow_ma(self) -> float | None:
        return self._ma(self.slow_period)

    def on_bar(self, bar: Bar) -> list[dict]:
        events: list[dict] = []
        self._closes.append(bar.close)

        fast_ma = self._ma(self.fast_period)
        slow_ma = self._ma(self.slow_period)

        if fast_ma is None or slow_ma is None:
            return events

        if self._open_trade is not None:
            close_reason = self._check_exit(bar, fast_ma, slow_ma)
            if close_reason:
                events.extend(self._close_trade(bar, close_reason))

        if self._open_trade is None and self._prev_fast is not None:
            if self._prev_fast <= self._prev_slow and fast_ma > slow_ma:
                signal = f"MA{self.fast_period} crossed above MA{self.slow_period}"
                sl = round(bar.close * (1 - self.stop_loss_pct / 100), 2)
                events.extend(self._open_new_trade(bar, Side.BUY, signal, stop_loss_price=sl))
            elif self._prev_fast >= self._prev_slow and fast_ma < slow_ma:
                signal = f"MA{self.fast_period} crossed below MA{self.slow_period}"
                sl = round(bar.close * (1 + self.stop_loss_pct / 100), 2)
                events.extend(self._open_new_trade(bar, Side.SELL, signal, stop_loss_price=sl))

        self._prev_fast = fast_ma
        self._prev_slow = slow_ma
        return events

    def _check_exit(self, bar: Bar, fast_ma: float, slow_ma: float) -> str | None:
        trade = self._open_trade
        if trade is None:
            return None

        # Check stop-loss against intra-bar extremes, not just close
        if trade.stop_loss_price is not None:
            if trade.side == Side.BUY and bar.low <= trade.stop_loss_price:
                return "STOP_LOSS"
            if trade.side == Side.SELL and bar.high >= trade.stop_loss_price:
                return "STOP_LOSS"

        if self._prev_fast is not None and self._prev_slow is not None:
            if trade.side == Side.BUY and self._prev_fast >= self._prev_slow and fast_ma < slow_ma:
                return "MA_CROSSOVER"
            if trade.side == Side.SELL and self._prev_fast <= self._prev_slow and fast_ma > slow_ma:
                return "MA_CROSSOVER"

        return None
