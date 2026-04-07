"""Mean reversion strategy using Bollinger Bands.

Buys when price touches the lower band (oversold), sells when price
touches the upper band (overbought). Exits at the middle band (SMA)
or on stop-loss.

Usage:
    STRATEGY=mean_reversion uvicorn main:app
"""

from __future__ import annotations

from collections import deque
import math

from models import Bar, Side
from base_strategy import AbstractStrategy, register_strategy


@register_strategy("mean_reversion")
class MeanReversionStrategy(AbstractStrategy):
    """Bollinger Band mean-reversion strategy."""

    def __init__(
        self,
        period: int = 20,
        num_std: float = 2.0,
        initial_capital: float = 10_000,
        stop_loss_pct: float = 1.5,
        **_kwargs,
    ):
        super().__init__(initial_capital=initial_capital, **_kwargs)
        self.period = period
        self.num_std = num_std
        self.stop_loss_pct = stop_loss_pct

        self._closes: deque[float] = deque(maxlen=period)

    def _sma(self) -> float | None:
        if len(self._closes) < self.period:
            return None
        return sum(self._closes) / self.period

    def _std(self) -> float | None:
        if len(self._closes) < self.period:
            return None
        mean = sum(self._closes) / self.period
        variance = sum((x - mean) ** 2 for x in self._closes) / self.period
        return math.sqrt(variance)

    @property
    def configurable_params(self) -> list[dict]:
        return [
            {"name": "period", "label": "BB Period", "type": "int", "value": self.period, "min": 5, "max": 100, "step": 1},
            {"name": "num_std", "label": "Std Deviations", "type": "float", "value": self.num_std, "min": 0.5, "max": 4.0, "step": 0.1},
            {"name": "stop_loss_pct", "label": "Stop Loss %", "type": "float", "value": self.stop_loss_pct, "min": 0.1, "max": 10.0, "step": 0.1},
        ]

    @property
    def indicator_labels(self) -> tuple[str, str]:
        return (f"BB Upper ({self.num_std}σ)", f"BB Lower ({self.num_std}σ)")

    @property
    def current_fast_ma(self) -> float | None:
        """Upper band - displayed as the 'fast' indicator on chart."""
        sma = self._sma()
        std = self._std()
        if sma is None or std is None:
            return None
        return sma + self.num_std * std

    @property
    def current_slow_ma(self) -> float | None:
        """Lower band - displayed as the 'slow' indicator on chart."""
        sma = self._sma()
        std = self._std()
        if sma is None or std is None:
            return None
        return sma - self.num_std * std

    def on_bar(self, bar: Bar) -> list[dict]:
        events: list[dict] = []
        self._closes.append(bar.close)

        sma = self._sma()
        std = self._std()

        if sma is None or std is None:
            return events

        upper = sma + self.num_std * std
        lower = sma - self.num_std * std

        # Check exit: price reverted to mean (SMA) or stop-loss
        if self._open_trade is not None:
            close_reason = self._check_exit(bar, sma)
            if close_reason:
                events.extend(self._close_trade(bar, close_reason))

        # Entry signals (only if no open position)
        if self._open_trade is None:
            if bar.close <= lower:
                signal = f"Price ({bar.close:.2f}) touched lower band ({lower:.2f})"
                sl = round(bar.close * (1 - self.stop_loss_pct / 100), 2)
                events.extend(self._open_new_trade(bar, Side.BUY, signal, stop_loss_price=sl))
            elif bar.close >= upper:
                signal = f"Price ({bar.close:.2f}) touched upper band ({upper:.2f})"
                sl = round(bar.close * (1 + self.stop_loss_pct / 100), 2)
                events.extend(self._open_new_trade(bar, Side.SELL, signal, stop_loss_price=sl))

        return events

    def _check_exit(self, bar: Bar, sma: float) -> str | None:
        trade = self._open_trade
        if trade is None:
            return None

        # Check stop-loss against intra-bar extremes, not just close
        if trade.stop_loss_price is not None:
            if trade.side == Side.BUY and bar.low <= trade.stop_loss_price:
                return "STOP_LOSS"
            if trade.side == Side.SELL and bar.high >= trade.stop_loss_price:
                return "STOP_LOSS"

        # Mean reversion: exit when price crosses back to SMA
        if trade.side == Side.BUY and bar.close >= sma:
            return "MEAN_REVERSION"
        if trade.side == Side.SELL and bar.close <= sma:
            return "MEAN_REVERSION"

        return None
