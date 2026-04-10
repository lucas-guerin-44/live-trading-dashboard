"""Strategy modules - import all to trigger @register_strategy decorators."""

from strategies.ma_crossover import MACrossoverStrategy  # noqa: F401
from strategies.mean_reversion import MeanReversionStrategy  # noqa: F401
from strategies.momentum import MomentumStrategy  # noqa: F401
from strategies.tick_scalper import TickScalperStrategy  # noqa: F401
