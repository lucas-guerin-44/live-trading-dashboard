"""RSI + ADX momentum strategy with regime detection.

Trades momentum signals (RSI crossovers) only when the market is trending
(ADX above threshold). In ranging regimes, no new positions are opened and
existing ones are closed on regime shift.

Usage:
    STRATEGY=momentum uvicorn main:app
"""

from __future__ import annotations

import math
from collections import deque

from models import Bar, Side
from base_strategy import AbstractStrategy, register_strategy


@register_strategy("momentum")
class MomentumStrategy(AbstractStrategy):
    """RSI momentum strategy filtered by ADX regime detection."""

    def __init__(
        self,
        rsi_period: int = 14,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
        rsi_overbought: float = 70.0,
        rsi_oversold: float = 30.0,
        stop_loss_pct: float = 1.5,
        initial_capital: float = 10_000,
        **_kwargs,
    ):
        super().__init__(initial_capital=initial_capital, **_kwargs)
        self.rsi_period = rsi_period
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.stop_loss_pct = stop_loss_pct

        # Need enough history for ADX (2 * adx_period + 1 for smoothing)
        max_lookback = max(rsi_period, adx_period) * 2 + 2
        self._closes: deque[float] = deque(maxlen=max_lookback)
        self._highs: deque[float] = deque(maxlen=max_lookback)
        self._lows: deque[float] = deque(maxlen=max_lookback)
        self._prev_rsi: float | None = None
        self._cached_rsi: float | None = None
        self._cached_adx: float | None = None

    # ── Indicator calculations ─────────────────────────────────────────────

    def _compute_rsi(self) -> float | None:
        """Wilder-smoothed RSI."""
        if len(self._closes) < self.rsi_period + 1:
            return None

        closes = list(self._closes)
        changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

        # Seed with SMA of first rsi_period changes
        gains = [max(c, 0) for c in changes[:self.rsi_period]]
        losses = [abs(min(c, 0)) for c in changes[:self.rsi_period]]
        avg_gain = sum(gains) / self.rsi_period
        avg_loss = sum(losses) / self.rsi_period

        # Wilder smoothing for remaining changes
        for c in changes[self.rsi_period:]:
            avg_gain = (avg_gain * (self.rsi_period - 1) + max(c, 0)) / self.rsi_period
            avg_loss = (avg_loss * (self.rsi_period - 1) + abs(min(c, 0))) / self.rsi_period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _compute_adx(self) -> float | None:
        """Wilder-smoothed ADX using +DI/-DI."""
        n = self.adx_period
        # Need at least 2*n +1 bars for full ADX smoothing
        if len(self._highs) < 2 * n + 1:
            return None

        highs = list(self._highs)
        lows = list(self._lows)
        closes = list(self._closes)

        # True Range, +DM, -DM series
        tr_list: list[float] = []
        plus_dm_list: list[float] = []
        minus_dm_list: list[float] = []

        for i in range(1, len(highs)):
            high_diff = highs[i] - highs[i - 1]
            low_diff = lows[i - 1] - lows[i]

            plus_dm = high_diff if high_diff > low_diff and high_diff > 0 else 0.0
            minus_dm = low_diff if low_diff > high_diff and low_diff > 0 else 0.0

            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            tr_list.append(tr)
            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)

        if len(tr_list) < 2 * n:
            return None

        # Wilder smoothed TR, +DM, -DM (seed with SMA of first n values)
        atr = sum(tr_list[:n]) / n
        smooth_plus = sum(plus_dm_list[:n]) / n
        smooth_minus = sum(minus_dm_list[:n]) / n

        dx_list: list[float] = []
        for i in range(n, len(tr_list)):
            atr = (atr * (n - 1) + tr_list[i]) / n
            smooth_plus = (smooth_plus * (n - 1) + plus_dm_list[i]) / n
            smooth_minus = (smooth_minus * (n - 1) + minus_dm_list[i]) / n

            if atr == 0:
                continue
            plus_di = 100 * smooth_plus / atr
            minus_di = 100 * smooth_minus / atr
            di_sum = plus_di + minus_di
            if di_sum == 0:
                continue
            dx = 100 * abs(plus_di - minus_di) / di_sum
            dx_list.append(dx)

        if len(dx_list) < n:
            return None

        # ADX = Wilder smoothed DX
        adx = sum(dx_list[:n]) / n
        for dx in dx_list[n:]:
            adx = (adx * (n - 1) + dx) / n

        return adx

    # ── Strategy interface ─────────────────────────────────────────────────

    @property
    def configurable_params(self) -> list[dict]:
        return [
            {"name": "rsi_period", "label": "RSI Period", "type": "int",
             "value": self.rsi_period, "min": 5, "max": 30, "step": 1},
            {"name": "adx_period", "label": "ADX Period", "type": "int",
             "value": self.adx_period, "min": 5, "max": 30, "step": 1},
            {"name": "adx_threshold", "label": "ADX Trend Threshold", "type": "float",
             "value": self.adx_threshold, "min": 15.0, "max": 40.0, "step": 1.0},
            {"name": "stop_loss_pct", "label": "Stop Loss %", "type": "float",
             "value": self.stop_loss_pct, "min": 0.1, "max": 10.0, "step": 0.1},
        ]

    @property
    def indicator_labels(self) -> tuple[str, str]:
        return ("RSI", "ADX")

    @property
    def indicator_overlay(self) -> bool:
        return False

    @property
    def current_fast_ma(self) -> float | None:
        """RSI value for chart overlay (cached from on_bar)."""
        return self._cached_rsi

    @property
    def current_slow_ma(self) -> float | None:
        """ADX value for chart overlay (cached from on_bar)."""
        return self._cached_adx

    def on_bar(self, bar: Bar) -> list[dict]:
        events: list[dict] = []
        self._closes.append(bar.close)
        self._highs.append(bar.high)
        self._lows.append(bar.low)

        rsi = self._compute_rsi()
        adx = self._compute_adx()
        self._cached_rsi = rsi
        self._cached_adx = adx

        if rsi is None:
            return events

        # Check exits first
        if self._open_trade is not None:
            close_reason = self._check_exit(bar, rsi, adx)
            if close_reason:
                events.extend(self._close_trade(bar, close_reason))

        # Entry signals — only in trending regime
        is_trending = adx is not None and adx >= self.adx_threshold

        if self._open_trade is None and is_trending and self._prev_rsi is not None:
            # RSI crosses above oversold → bullish momentum
            if self._prev_rsi <= self.rsi_oversold and rsi > self.rsi_oversold:
                signal = f"RSI crossed above {self.rsi_oversold} (ADX={adx:.1f})"
                sl = round(bar.close * (1 - self.stop_loss_pct / 100), 2)
                events.extend(self._open_new_trade(bar, Side.BUY, signal, stop_loss_price=sl))
            # RSI crosses below overbought → bearish momentum
            elif self._prev_rsi >= self.rsi_overbought and rsi < self.rsi_overbought:
                signal = f"RSI crossed below {self.rsi_overbought} (ADX={adx:.1f})"
                sl = round(bar.close * (1 + self.stop_loss_pct / 100), 2)
                events.extend(self._open_new_trade(bar, Side.SELL, signal, stop_loss_price=sl))

        self._prev_rsi = rsi
        return events

    def _check_exit(self, bar: Bar, rsi: float, adx: float | None) -> str | None:
        trade = self._open_trade
        if trade is None:
            return None

        # Check stop-loss against intra-bar extremes, not just close
        if trade.stop_loss_price is not None:
            if trade.side == Side.BUY and bar.low <= trade.stop_loss_price:
                return "STOP_LOSS"
            if trade.side == Side.SELL and bar.high >= trade.stop_loss_price:
                return "STOP_LOSS"

        # Regime shift: ADX dropped below threshold
        if adx is not None and adx < self.adx_threshold:
            return "REGIME_SHIFT"

        # RSI reversal exits
        if trade.side == Side.BUY and rsi >= self.rsi_overbought:
            return "RSI_OVERBOUGHT"
        if trade.side == Side.SELL and rsi <= self.rsi_oversold:
            return "RSI_OVERSOLD"

        return None
