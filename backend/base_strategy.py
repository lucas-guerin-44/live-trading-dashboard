"""Abstract base class for trading strategies.

Implement `on_bar()` to create a new strategy. The replay engine calls
`on_bar()` for every incoming bar and expects a list of event dicts back.

Example:
    class MyStrategy(AbstractStrategy):
        def on_bar(self, bar: Bar) -> list[dict]:
            if bar.close > some_threshold:
                return self._open_new_trade(bar, Side.BUY, "price breakout")
            return []

Register strategies via the STRATEGY_REGISTRY dict, then select at runtime
with the STRATEGY env var (e.g. STRATEGY=mean_reversion).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
import math

from models import Bar, Metrics, Side, Trade
from typing import Optional

logger = logging.getLogger(__name__)


class AbstractStrategy(ABC):
    """Base class all strategies must extend."""

    def __init__(
        self,
        initial_capital: float = 10_000,
        risk_per_trade_pct: float = 2.0,
        max_drawdown_pct: float = 20.0,
        spread: float = 0.0,
        commission_per_unit: float = 0.0,
        slippage_pct: float = 0.0,
        **_kwargs,
    ):
        self._next_trade_id = 1
        self._open_trade: Trade | None = None
        self._closed_trades: list[Trade] = []
        self._risk_per_trade_pct = risk_per_trade_pct
        self._max_drawdown_pct = max_drawdown_pct
        self._spread = spread
        self._commission_per_unit = commission_per_unit
        self._slippage_pct = slippage_pct
        self._metrics = Metrics(
            initial_capital=initial_capital,
            current_capital=initial_capital,
            peak_capital=initial_capital,
            risk_per_trade_pct=risk_per_trade_pct,
            max_drawdown_limit=max_drawdown_pct,
        )
        # Running accumulators for O(1) advanced metrics
        self._sum_wins = 0.0
        self._count_wins = 0
        self._sum_losses = 0.0
        self._count_losses = 0
        self._max_win = 0.0
        self._min_loss = 0.0
        self._sum_returns = 0.0
        self._sum_returns_sq = 0.0

    # ── Public interface ────────────────────────────────────────────────────

    @property
    def open_trade(self) -> Trade | None:
        return self._open_trade

    @property
    def closed_trades(self) -> list[Trade]:
        return self._closed_trades

    @property
    def metrics(self) -> Metrics:
        return self._metrics

    @abstractmethod
    def on_bar(self, bar: Bar) -> list[dict]:
        """Process a bar and return trade/metrics events.

        Must return a list of dicts with keys:
            - type: "TRADE_OPEN" | "TRADE_CLOSE" | "METRICS"
            - data: Trade or Metrics model instance
        """
        ...

    def on_tick(self, tick, current_bar: Optional[Bar], position, capital) -> Optional[dict]:
        """Called on every tick. current_bar is the in-progress (incomplete) bar.

        Default: no-op (strategy only reacts to completed bars).
        Override this for intra-bar logic (e.g., stop management, limit orders).
        """
        return None

    @property
    def indicator_labels(self) -> tuple[str, str]:
        """Labels for the two chart overlay lines. Override per strategy."""
        return ("Fast", "Slow")

    @property
    def indicator_overlay(self) -> bool:
        """True = draw indicators on the price chart (MA, BB).
        False = draw in a separate oscillator pane (RSI, ADX).
        Override per strategy."""
        return True

    @property
    def configurable_params(self) -> list[dict]:
        """Return list of params the UI can tune. Override per strategy.

        Each entry: {name, label, type: "int"|"float", value, min, max, step}
        """
        return []

    @property
    def current_fast_ma(self) -> float | None:
        """Override to expose indicator values for chart overlay."""
        return None

    @property
    def current_slow_ma(self) -> float | None:
        """Override to expose indicator values for chart overlay."""
        return None

    # ── Position sizing ────────────────────────────────────────────────────

    def _compute_quantity(self, entry_price: float, stop_loss_price: float | None) -> float:
        """Fixed-fraction position sizing: risk risk_per_trade_pct of current capital.

        Sizes so that hitting the stop-loss loses exactly the risk amount.
        Without a stop-loss, risk is undefined — log a warning and size
        conservatively using 1% of capital as notional.

        Caps position at 10x notional leverage to prevent blow-ups when the
        stop-loss distance is very small.
        """
        risk_amount = self._metrics.current_capital * (self._risk_per_trade_pct / 100)
        if stop_loss_price and abs(entry_price - stop_loss_price) > 1e-9:
            distance = abs(entry_price - stop_loss_price)
            qty = risk_amount / distance
        else:
            logger.warning("No stop-loss — sizing conservatively (1%% notional)")
            qty = (self._metrics.current_capital * 0.01) / entry_price

        # Cap at 10x leverage to prevent blow-ups from tiny stop distances
        max_qty = (self._metrics.current_capital * 10) / entry_price
        if qty > max_qty:
            logger.warning("Position size capped at 10x leverage (%.4f → %.4f)", qty, max_qty)
            qty = max_qty

        return round(qty, 4)

    # ── Trade lifecycle helpers ─────────────────────────────────────────────

    def _open_new_trade(
        self,
        bar: Bar,
        side: Side,
        signal: str = "",
        stop_loss_price: float | None = None,
        take_profit_price: float | None = None,
    ) -> list[dict]:
        # Drawdown gate: refuse new trades when account drawdown exceeds limit
        if self._metrics.max_drawdown >= self._max_drawdown_pct:
            logger.warning(
                "Drawdown limit reached (%.2f%% >= %.2f%%) — no new trades",
                self._metrics.max_drawdown, self._max_drawdown_pct,
            )
            return []

        qty = self._compute_quantity(bar.close, stop_loss_price)

        trade = Trade(
            id=self._next_trade_id,
            instrument=bar.instrument,
            side=side,
            entry_price=bar.close,
            entry_time=bar.timestamp,
            signal_reason=signal,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            quantity=qty,
        )
        self._next_trade_id += 1
        self._open_trade = trade
        self._metrics.open_positions = 1
        return [
            {"type": "TRADE_OPEN", "data": trade},
            {"type": "METRICS", "data": self._metrics},
        ]

    def _close_trade(self, bar: Bar, reason: str) -> list[dict]:
        trade = self._open_trade
        if trade is None:
            return []

        # Stop-loss fills at the stop price, not bar.close — in reality the
        # stop triggers intra-bar when price crosses the level.
        if reason == "STOP_LOSS" and trade.stop_loss_price is not None:
            exit_price = trade.stop_loss_price
        else:
            exit_price = bar.close

        # Slippage: adverse price movement on execution
        slippage = exit_price * (self._slippage_pct / 100)
        if trade.side == Side.BUY:
            exit_price -= slippage  # selling: worse fill = lower price
        else:
            exit_price += slippage  # covering: worse fill = higher price

        if trade.side == Side.BUY:
            raw_pnl = exit_price - trade.entry_price
        else:
            raw_pnl = trade.entry_price - exit_price

        # Costs per unit: spread (bid-ask crossing) + commission (entry + exit)
        spread_cost = self._spread * trade.quantity
        commission = self._commission_per_unit * trade.quantity * 2
        total_cost = spread_cost + commission

        trade.exit_price = round(exit_price, 2)
        trade.exit_time = bar.timestamp
        trade.pnl = round(raw_pnl * trade.quantity - total_cost, 2)
        trade.pnl_pct = round((trade.pnl / (trade.entry_price * trade.quantity)) * 100, 4)
        trade.exit_reason = reason

        self._open_trade = None
        self._closed_trades.append(trade)
        # Cap to prevent unbounded memory growth (metrics use accumulators)
        if len(self._closed_trades) > 200:
            self._closed_trades = self._closed_trades[-100:]

        # Update metrics
        m = self._metrics
        m.current_capital += trade.pnl
        m.total_pnl = round(m.current_capital - m.initial_capital, 2)
        m.total_pnl_pct = round(m.total_pnl / m.initial_capital * 100, 4)
        m.total_trades += 1
        m.open_positions = 0
        m.total_commission += round(commission, 2)
        m.total_spread_cost += round(spread_cost, 2)

        if trade.pnl > 0:
            m.winning_trades += 1
            self._sum_wins += trade.pnl
            self._count_wins += 1
            self._max_win = max(self._max_win, trade.pnl)
        else:
            m.losing_trades += 1
            self._sum_losses += trade.pnl
            self._count_losses += 1
            self._min_loss = min(self._min_loss, trade.pnl)
        self._sum_returns += trade.pnl_pct
        self._sum_returns_sq += trade.pnl_pct ** 2

        m.win_rate = (
            round(m.winning_trades / m.total_trades * 100, 2)
            if m.total_trades > 0
            else 0.0
        )

        if m.current_capital > m.peak_capital:
            m.peak_capital = m.current_capital
        drawdown = (
            (m.peak_capital - m.current_capital) / m.peak_capital * 100
            if m.peak_capital > 0
            else 0
        )
        if drawdown > m.max_drawdown:
            m.max_drawdown = round(drawdown, 4)

        # Advanced metrics (O(1) via accumulators)
        self._update_advanced_metrics()

        return [
            {"type": "TRADE_CLOSE", "data": trade},
            {"type": "METRICS", "data": self._metrics},
        ]

    def _update_advanced_metrics(self) -> None:
        """Update advanced metrics using running accumulators (O(1) per call)."""
        m = self._metrics

        m.avg_win = round(self._sum_wins / self._count_wins, 2) if self._count_wins else None
        m.avg_loss = round(self._sum_losses / self._count_losses, 2) if self._count_losses else None
        m.largest_win = round(self._max_win, 2) if self._count_wins else None
        m.largest_loss = round(self._min_loss, 2) if self._count_losses else None

        gross_loss = abs(self._sum_losses)
        m.profit_factor = round(self._sum_wins / gross_loss, 2) if gross_loss > 0 else None

        # Sharpe ratio (per-trade, not annualized) via sum-of-squares formula
        n = m.total_trades
        if n >= 2:
            mean_ret = self._sum_returns / n
            variance = (self._sum_returns_sq - n * mean_ret ** 2) / (n - 1)
            std_ret = math.sqrt(max(variance, 0))
            m.sharpe_ratio = round(mean_ret / std_ret, 2) if std_ret > 0 else None


# ── Strategy registry ──────────────────────────────────────────────────────
# Add new strategies here. Select at runtime via STRATEGY env var.

STRATEGY_REGISTRY: dict[str, type[AbstractStrategy]] = {}


def register_strategy(name: str):
    """Decorator to register a strategy class."""

    def decorator(cls: type[AbstractStrategy]):
        STRATEGY_REGISTRY[name] = cls
        return cls

    return decorator


def get_strategy(name: str, **kwargs) -> AbstractStrategy:
    """Instantiate a registered strategy by name."""
    if name not in STRATEGY_REGISTRY:
        available = ", ".join(STRATEGY_REGISTRY.keys()) or "(none)"
        raise ValueError(f"Unknown strategy '{name}'. Available: {available}")
    return STRATEGY_REGISTRY[name](**kwargs)
