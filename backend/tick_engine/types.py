"""Core data types — vendored from backtesting-engine-2.0/backtesting/types.py"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class Bar:
    """A single OHLC price bar."""
    ts: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
