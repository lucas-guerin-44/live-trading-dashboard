"""Tick Scalper — high-frequency strategy for M1/tick data.

Uses fast EMAs on completed bars to determine trend bias, then uses on_tick()
to enter on pullbacks within the trend. Tight stops, quick take-profits.

Designed to trade frequently on M1 timeframes with tick data streaming.
"""

from __future__ import annotations

from collections import deque
from typing import Optional

from models import Bar, Side
from base_strategy import AbstractStrategy, register_strategy


def _ema(values: list[float], period: int) -> float | None:
    """Compute EMA with immediate warmup.

    Uses the first value as the seed instead of waiting for `period` bars.
    This means the EMA starts responding from bar 1 — it's less accurate
    initially but converges within a few bars, and lets the strategy trade
    the open instead of sitting idle for 13 minutes.
    """
    if not values:
        return None
    k = 2 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


@register_strategy("tick_scalper")
class TickScalperStrategy(AbstractStrategy):
    """Scalps small moves using fast EMAs + tick-level entries.

    Bar-level (on_bar):
    - Computes 5 and 13 EMA on close prices to determine trend bias
    - Tracks the current bar's range for breakout detection

    Tick-level (on_tick):
    - In uptrend (EMA5 > EMA13): buys when price pulls back to EMA5
      then ticks back up (pullback entry)
    - In downtrend: sells on pullback to EMA5 then tick back down
    - Manages stop-loss and take-profit intra-bar
    """

    def __init__(
        self,
        fast_ema: int = 5,
        slow_ema: int = 13,
        stop_loss_pct: float = 0.03,
        take_profit_pct: float = 0.05,
        pullback_ticks: int = 3,
        trend_strength: float = 0.01,
        initial_capital: float = 10_000,
        **_kwargs,
    ):
        super().__init__(initial_capital=initial_capital, **_kwargs)
        self.fast_ema = fast_ema
        self.slow_ema = slow_ema
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.pullback_ticks = pullback_ticks
        self.trend_strength = trend_strength

        self._closes: deque[float] = deque(maxlen=max(slow_ema + 5, 20))
        self._fast: float | None = None
        self._slow: float | None = None
        self._trend: str = "none"  # "up", "down", "none"

        # Tick-level state
        self._tick_prices: deque[float] = deque(maxlen=20)
        self._bar_high: float = 0.0
        self._bar_low: float = float("inf")
        self._last_bar_close: float = 0.0

    @property
    def configurable_params(self) -> list[dict]:
        return [
            {"name": "fast_ema", "label": "Fast EMA", "type": "int", "value": self.fast_ema, "min": 2, "max": 20, "step": 1},
            {"name": "slow_ema", "label": "Slow EMA", "type": "int", "value": self.slow_ema, "min": 5, "max": 50, "step": 1},
            {"name": "stop_loss_pct", "label": "Stop Loss %", "type": "float", "value": self.stop_loss_pct, "min": 0.01, "max": 0.20, "step": 0.01},
            {"name": "take_profit_pct", "label": "Take Profit %", "type": "float", "value": self.take_profit_pct, "min": 0.01, "max": 0.20, "step": 0.01},
            {"name": "trend_strength", "label": "Trend Strength %", "type": "float", "value": self.trend_strength, "min": 0.005, "max": 0.10, "step": 0.005},
        ]

    @property
    def indicator_labels(self) -> tuple[str, str]:
        return (f"EMA{self.fast_ema}", f"EMA{self.slow_ema}")

    @property
    def current_fast_ma(self) -> float | None:
        return self._fast

    @property
    def current_slow_ma(self) -> float | None:
        return self._slow

    def on_bar(self, bar: Bar) -> list[dict]:
        events: list[dict] = []
        self._closes.append(bar.close)
        self._last_bar_close = bar.close

        # Reset intra-bar state
        self._tick_prices.clear()
        self._bar_high = 0.0
        self._bar_low = float("inf")

        closes = list(self._closes)
        self._fast = _ema(closes, self.fast_ema)
        self._slow = _ema(closes, self.slow_ema)

        if self._fast is None or self._slow is None:
            self._trend = "none"
            return events

        # Determine trend — require minimum % gap between EMAs
        ema_gap_pct = abs(self._fast - self._slow) / self._slow * 100
        if ema_gap_pct < self.trend_strength:
            self._trend = "none"
        elif self._fast > self._slow:
            self._trend = "up"
        else:
            self._trend = "down"

        # Check stop-loss / take-profit on bar close for open positions
        if self._open_trade is not None:
            reason = self._check_bar_exit(bar)
            if reason:
                events.extend(self._close_trade(bar, reason))

        return events

    def on_tick(self, tick, current_bar: Optional[Bar], position, capital) -> Optional[dict]:
        price = tick.price
        self._tick_prices.append(price)
        self._bar_high = max(self._bar_high, price)
        self._bar_low = min(self._bar_low, price)

        if self._trend == "none" or self._fast is None:
            return None

        # --- Manage open position intra-bar ---
        if self._open_trade is not None:
            trade = self._open_trade
            # Intra-bar stop-loss
            if trade.stop_loss_price is not None:
                if trade.side == Side.BUY and price <= trade.stop_loss_price:
                    return self._close_on_tick(price, "STOP_LOSS", tick.ts)
                if trade.side == Side.SELL and price >= trade.stop_loss_price:
                    return self._close_on_tick(price, "STOP_LOSS", tick.ts)
            # Intra-bar take-profit
            if trade.take_profit_price is not None:
                if trade.side == Side.BUY and price >= trade.take_profit_price:
                    return self._close_on_tick(price, "TAKE_PROFIT", tick.ts)
                if trade.side == Side.SELL and price <= trade.take_profit_price:
                    return self._close_on_tick(price, "TAKE_PROFIT", tick.ts)
            return None

        # --- Entry logic: pullback in trend direction ---
        if len(self._tick_prices) < self.pullback_ticks + 1:
            return None

        recent = list(self._tick_prices)

        if self._trend == "up" and self._fast is not None:
            # Price pulled back near/below fast EMA, then ticked up
            pulled_back = any(p <= self._fast for p in recent[-self.pullback_ticks - 1:-1])
            ticked_up = recent[-1] > recent[-2]
            above_ema = recent[-1] > self._fast

            if pulled_back and ticked_up and above_ema:
                return self._enter_on_tick(price, Side.BUY, tick.ts)

        elif self._trend == "down" and self._fast is not None:
            pulled_back = any(p >= self._fast for p in recent[-self.pullback_ticks - 1:-1])
            ticked_down = recent[-1] < recent[-2]
            below_ema = recent[-1] < self._fast

            if pulled_back and ticked_down and below_ema:
                return self._enter_on_tick(price, Side.SELL, tick.ts)

        return None

    def _enter_on_tick(self, price: float, side: Side, ts) -> dict | None:
        """Open a position from a tick-level signal."""
        if side == Side.BUY:
            sl = round(price * (1 - self.stop_loss_pct / 100), 2)
            tp = round(price * (1 + self.take_profit_pct / 100), 2)
            signal = f"Pullback entry: trend UP, price bounced off EMA{self.fast_ema}"
        else:
            sl = round(price * (1 + self.stop_loss_pct / 100), 2)
            tp = round(price * (1 - self.take_profit_pct / 100), 2)
            signal = f"Pullback entry: trend DOWN, price rejected EMA{self.fast_ema}"

        tick_bar = Bar(
            timestamp=ts.to_pydatetime() if hasattr(ts, 'to_pydatetime') else ts,
            open=price, high=price, low=price, close=price,
        )
        events = self._open_new_trade(tick_bar, side, signal, stop_loss_price=sl, take_profit_price=tp)
        return events if events else None

    def _close_on_tick(self, price: float, reason: str, ts) -> list[dict] | None:
        """Close position from a tick-level signal."""
        tick_bar = Bar(
            timestamp=ts.to_pydatetime() if hasattr(ts, 'to_pydatetime') else ts,
            open=price, high=price, low=price, close=price,
        )
        events = self._close_trade(tick_bar, reason)
        return events if events else None

    def _check_bar_exit(self, bar: Bar) -> str | None:
        # No bar-level exits — SL/TP managed intra-bar via on_tick()
        return None
