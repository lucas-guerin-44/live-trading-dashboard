from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class Bar(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    instrument: str = "XAUUSD"
    timeframe: str = "M15"


class TickData(BaseModel):
    timestamp: str
    price: float
    volume: float = 0.0
    bid: Optional[float] = None
    ask: Optional[float] = None


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Trade(BaseModel):
    id: int
    instrument: str
    side: Side
    entry_price: float
    entry_time: datetime
    exit_price: float | None = None
    exit_time: datetime | None = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str | None = None
    quantity: float = 1.0
    # Strategy context
    signal_reason: str | None = None  # e.g. "MA10 crossed below MA30"
    stop_loss_price: float | None = None
    take_profit_price: float | None = None


class Metrics(BaseModel):
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    open_positions: int = 0
    current_capital: float = 10000.0
    initial_capital: float = 10000.0
    max_drawdown: float = 0.0
    peak_capital: float = 10000.0
    # Risk / sizing
    risk_per_trade_pct: float = 2.0
    max_drawdown_limit: float = 20.0
    # Cost tracking
    total_commission: float = 0.0
    total_spread_cost: float = 0.0
    # Advanced metrics
    sharpe_ratio: float | None = None
    profit_factor: float | None = None
    avg_win: float | None = None
    avg_loss: float | None = None
    largest_win: float | None = None
    largest_loss: float | None = None
    avg_trade_duration_bars: float | None = None


class MessageType(str, Enum):
    SNAPSHOT = "SNAPSHOT"
    BAR = "BAR"
    TICK = "TICK"
    TICK_BATCH = "TICK_BATCH"
    TRADE_OPEN = "TRADE_OPEN"
    TRADE_CLOSE = "TRADE_CLOSE"
    METRICS = "METRICS"
    HEARTBEAT = "HEARTBEAT"


class WSMessage(BaseModel):
    type: MessageType
    data: Any
    timestamp: datetime | None = None
