"""Vendored tick aggregation from backtesting-engine-2.0.

Provides Tick, TickAggregator, and Bar (the backtesting engine's Bar type,
not the dashboard's Pydantic Bar model).
"""

from tick_engine.types import Bar
from tick_engine.tick import Tick, TickAggregator

__all__ = ["Tick", "TickAggregator", "Bar"]
