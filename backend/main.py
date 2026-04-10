"""FastAPI backend - streams replayed market data + strategy signals over WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Load .env file if present (so env vars work without manual export)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # python-dotenv not installed — use system env vars

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware

from models import Bar, Metrics, MessageType, WSMessage, Trade

from replay import (
    load_bars, replay_bars,
    load_ticks, replay_ticks, tick_data_available,
    stream_bars_from_datalake, stream_ticks_from_datalake,
    _backtesting_bar_to_dashboard,
    TICK_SUPPORT,
)

# Conditional imports for tick support (used in stream tick replay)
if TICK_SUPPORT:
    from backtesting.tick import Tick
    import pandas as pd
from base_strategy import get_strategy, STRATEGY_REGISTRY
import strategies as _strategies  # noqa: F401 - triggers @register_strategy decorators

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────

INSTRUMENT = os.getenv("INSTRUMENT", "XAUUSD")
TIMEFRAME = os.getenv("TIMEFRAME", "M15")
DATALAKE_URL = os.getenv("DATALAKE_URL")  # e.g. http://localhost:8001
DATALAKE_API_KEY = os.getenv("DATALAKE_API_KEY")
REPLAY_SPEED = float(os.getenv("REPLAY_SPEED", "2.0"))
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "10000"))
MAX_BAR_BUFFER = int(os.getenv("MAX_BAR_BUFFER", "1000"))
REPLAY_LOOP = os.getenv("REPLAY_LOOP", "true").lower() in ("true", "1", "yes")
REPLAY_PAUSE = int(os.getenv("REPLAY_PAUSE", "10"))  # seconds between loops
CSV_DIR = os.getenv("CSV_DIR")  # Override default CSV data directory
STRATEGY = os.getenv("STRATEGY", "ma_crossover")
# Realistic cost defaults for XAUUSD — override via env for other instruments
SPREAD = float(os.getenv("SPREAD", "0.30"))
COMMISSION_PER_UNIT = float(os.getenv("COMMISSION_PER_UNIT", "0.01"))
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.02"))
# Optional time bounds for stream mode (ISO-8601, e.g. "2024-07-09")
STREAM_START = os.getenv("STREAM_START")
STREAM_END = os.getenv("STREAM_END")
# Data mode:
#   "bar"    = load OHLC bars from CSV/REST, drip-feed locally
#   "tick"   = load raw ticks from CSV/REST, aggregate + drip-feed locally
#   "stream" = connect to datalake WebSocket, datalake controls pacing
#   "auto"   = stream if DATALAKE_URL set, else tick if available, else bar
DATA_MODE = os.getenv("DATA_MODE", "auto")
# In stream mode: use ticks (True) or bars (False) from the datalake WS
STREAM_TICKS = os.getenv("STREAM_TICKS", "false").lower() in ("true", "1", "yes")

# ── Shared replay state ─────────────────────────────────────────────────────

bars_cache: list[Bar] = []
ticks_cache: list = []  # Tick objects when in tick mode

# Per-client outbound queues. broadcast() pushes to each queue (zero-alloc),
# a dedicated drain task per client pulls and sends. Slow clients overflow
# their queue and get dropped — same as the old timeout approach but without
# creating N coroutines + futures per broadcast call.
MAX_CLIENT_QUEUE = 256  # messages; at 20 batches/sec this is ~12s of buffer

class ClientConnection:
    """WebSocket + outbound queue pair."""
    __slots__ = ("ws", "queue")
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=MAX_CLIENT_QUEUE)

connected_clients: dict[WebSocket, ClientConnection] = {}


class ReplayState:
    """Shared state for the single replay loop that all clients observe."""

    def __init__(self) -> None:
        self.bar_buffer: list[dict[str, Any]] = []  # bars with indicator data
        self.open_positions: list[dict[str, Any]] = []
        self.closed_positions: list[dict[str, Any]] = []
        self.metrics: dict[str, Any] = {}
        self.complete: bool = False
        self.started: bool = False
        self.active_strategy: str = STRATEGY
        self.active_timeframe: str = TIMEFRAME
        self.strategy_params: dict[str, Any] = {}  # custom params for current strategy
        self.configurable_params: list[dict] = []  # param definitions for UI
        self.speed: float = REPLAY_SPEED
        self.indicator_labels: tuple[str, str] = ("Fast", "Slow")
        self.indicator_overlay: bool = True
        self.mode: str = "bar"  # "bar" or "tick"
        self.tick_count: int = 0
        self._strategy_dirty = False  # params changed, needs hot-swap
        self.lock = asyncio.Lock()
        self._restart_event = asyncio.Event()

    def request_restart(self, strategy_name: str | None = None) -> None:
        """Signal the replay loop to restart, optionally with a new strategy."""
        if strategy_name is not None:
            self.active_strategy = strategy_name
        self._restart_event.set()

    def request_timeframe_change(self, timeframe: str) -> None:
        """Change the active timeframe.

        In tick streaming mode, only the aggregator needs resetting — the tick
        stream itself continues uninterrupted. In bar streaming mode, a full
        restart is needed since the datalake sends pre-built bars.
        """
        self.active_timeframe = timeframe
        if self.mode != "stream" or not STREAM_TICKS:
            # Bar mode or bar streaming: need full restart
            self._restart_event.set()

    async def snapshot_data(self) -> dict[str, Any]:
        """Build a snapshot payload of the current state."""
        async with self.lock:
            if self.mode == "stream":
                total = 0  # unknown upfront in stream mode
            elif self.mode == "tick":
                total = len(ticks_cache)
            else:
                total = len(bars_cache)
            return {
                "instrument": INSTRUMENT,
                "timeframe": self.active_timeframe,
                "timeframes": ["M1", "M5", "M15", "H1"],
                "total_bars": total,
                "speed": self.speed,
                "mode": self.mode,
                "tick_count": self.tick_count,
                "metrics": dict(self.metrics),
                "bars": list(self.bar_buffer),
                "open_positions": list(self.open_positions),
                "closed_positions": list(self.closed_positions[-50:]),
                "complete": self.complete,
                "strategy": self.active_strategy,
                "strategies": list(STRATEGY_REGISTRY.keys()),
                "indicator_labels": list(self.indicator_labels),
                "indicator_overlay": self.indicator_overlay,
                "configurable_params": self.configurable_params,
            }


replay_state = ReplayState()


def _resolve_data_mode() -> str:
    """Determine whether to use stream, tick, or bar mode."""
    if DATA_MODE == "stream":
        return "stream"
    if DATA_MODE == "tick":
        return "tick"
    if DATA_MODE == "bar":
        return "bar"
    # auto: stream if datalake URL set, else tick if available, else bar
    if DATALAKE_URL:
        return "stream"
    csv_dir = Path(CSV_DIR) if CSV_DIR else None
    if tick_data_available(INSTRUMENT, csv_dir or Path(__file__).resolve().parent.parent.parent / "backtesting-engine-2.0" / "ohlc_data"):
        return "tick"
    return "bar"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load data on startup and start the shared replay loop."""
    global bars_cache, ticks_cache

    mode = _resolve_data_mode()
    replay_state.mode = mode

    if mode == "stream":
        # Stream mode: no pre-loading — data arrives via datalake WebSocket
        sub_mode = "tick" if STREAM_TICKS else "bar"
        logger.info(
            "Ready — stream mode (%s) from %s for %s %s",
            sub_mode, DATALAKE_URL, INSTRUMENT, TIMEFRAME,
        )
    elif mode == "tick":
        ticks_cache = await load_ticks(
            instrument=INSTRUMENT,
            datalake_url=DATALAKE_URL,
            datalake_api_key=DATALAKE_API_KEY,
            csv_dir=Path(CSV_DIR) if CSV_DIR else None,
        )
        logger.info("Ready - %d ticks loaded for %s (tick mode)", len(ticks_cache), INSTRUMENT)
        if not ticks_cache:
            logger.warning("No tick data found, falling back to bar mode")
            replay_state.mode = "bar"

    if replay_state.mode == "bar":
        bars_cache = await load_bars(
            instrument=INSTRUMENT,
            timeframe=TIMEFRAME,
            datalake_url=DATALAKE_URL,
            datalake_api_key=DATALAKE_API_KEY,
            csv_dir=Path(CSV_DIR) if CSV_DIR else None,
        )
        logger.info("Ready - %d bars loaded for %s %s", len(bars_cache), INSTRUMENT, TIMEFRAME)

    # Start the shared replay loop
    async def _replay_wrapper():
        try:
            await run_shared_replay()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Replay loop crashed")

    task = asyncio.create_task(_replay_wrapper())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Live Trading Dashboard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def serialize(msg: WSMessage) -> str:
    return msg.model_dump_json()


def broadcast(payload: str):
    """Enqueue a pre-serialized message for all connected clients.

    Non-blocking: each client has a bounded asyncio.Queue. If a client's
    queue is full (slow consumer), the message is dropped for that client
    and the client is marked for disconnect. No coroutine allocation, no
    gather, no futures — just a dict iteration + put_nowait per call.
    """
    if not connected_clients:
        return
    dead: list[WebSocket] = []
    for ws, client in connected_clients.items():
        try:
            client.queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning("Client queue full — dropping client")
            dead.append(ws)
    for ws in dead:
        client = connected_clients.pop(ws, None)
        if client:
            # Signal the drain task to exit
            try:
                client.queue.put_nowait(None)
            except asyncio.QueueFull:
                pass  # drain task will see the removal


# ── Shared replay loop ──────────────────────────────────────────────────────

async def _run_strategy_on_bar(strategy, bar: Bar) -> list[dict]:
    """Run strategy.on_bar() in a thread pool so it can't block tick ingestion.

    The strategy is CPU-bound (indicator math, position sizing) — even a few ms
    of blocking per bar adds up at high tick rates. Offloading to a thread keeps
    the async event loop free to receive the next tick immediately.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, strategy.on_bar, bar)


async def _broadcast_events(strategy, events: list[dict]) -> None:
    """Broadcast trade/metrics events and update shared state."""
    for event in events:
        event_data = event["data"].model_dump(mode="json") if hasattr(event["data"], "model_dump") else event["data"]

        async with replay_state.lock:
            if event["type"] == "TRADE_OPEN":
                if not any(p["id"] == event_data["id"] for p in replay_state.open_positions):
                    replay_state.open_positions.append(event_data)
            elif event["type"] == "TRADE_CLOSE":
                replay_state.open_positions = [
                    p for p in replay_state.open_positions if p["id"] != event_data["id"]
                ]
                if not any(p["id"] == event_data["id"] for p in replay_state.closed_positions):
                    replay_state.closed_positions.append(event_data)
                    if len(replay_state.closed_positions) > 500:
                        replay_state.closed_positions = replay_state.closed_positions[-200:]

        event_payload = serialize(WSMessage(
            type=MessageType(event["type"]),
            data=event_data,
            timestamp=datetime.now(timezone.utc),
        ))
        broadcast(event_payload)


async def _process_bar(strategy, bar: Bar, bar_data: dict) -> None:
    """Common bar processing: update state, broadcast bar + events."""
    async with replay_state.lock:
        replay_state.bar_buffer.append(bar_data)
        if len(replay_state.bar_buffer) > MAX_BAR_BUFFER:
            replay_state.bar_buffer = replay_state.bar_buffer[-MAX_BAR_BUFFER:]
        replay_state.metrics = strategy.metrics.model_dump()

    bar_payload = serialize(WSMessage(
        type=MessageType.BAR,
        data=bar_data,
        timestamp=datetime.now(timezone.utc),
    ))
    broadcast(bar_payload)


async def _run_bar_replay(strategy) -> bool:
    """Run bar-by-bar replay. Returns True if restarted (strategy switch)."""
    async for bar in replay_bars(bars_cache, speed=lambda: replay_state.speed):
        if replay_state._restart_event.is_set():
            logger.info("Strategy switch requested → %s", replay_state.active_strategy)
            return True

        try:
            events = await _run_strategy_on_bar(strategy, bar)
        except Exception as exc:
            logger.exception("Strategy error on bar %s — skipping", bar.timestamp)
            error_payload = serialize(WSMessage(
                type=MessageType.METRICS,
                data={**strategy.metrics.model_dump(), "strategy_error": str(exc)},
                timestamp=datetime.now(timezone.utc),
            ))
            broadcast(error_payload)
            continue

        bar_data = bar.model_dump(mode="json")
        if strategy.current_fast_ma is not None:
            bar_data["fast_ma"] = round(strategy.current_fast_ma, 2)
        if strategy.current_slow_ma is not None:
            bar_data["slow_ma"] = round(strategy.current_slow_ma, 2)

        await _process_bar(strategy, bar, bar_data)
        await _broadcast_events(strategy, events)

        if not events and len(replay_state.bar_buffer) % 10 == 0:
            metrics_payload = serialize(WSMessage(
                type=MessageType.METRICS,
                data=strategy.metrics.model_dump(),
                timestamp=datetime.now(timezone.utc),
            ))
            broadcast(metrics_payload)

    return False


async def _run_tick_replay(strategy) -> bool:
    """Run tick-by-tick replay with bar aggregation. Returns True if restarted.

    Performance optimizations vs naive per-tick broadcast:
    1. Tick batching: accumulates ticks and flushes as a single TICK_BATCH
       message every TICK_FLUSH_MS, cutting WS frame overhead ~20x.
    2. Raw JSON serialization: tick payloads bypass Pydantic WSMessage and
       use json.dumps directly on plain dicts — no model validation overhead.
    3. Lock-free tick counter: tick_count is a single-writer int, no lock needed.
    4. No redundant envelope timestamps: tick data carries its own timestamp,
       the WSMessage envelope timestamp is omitted for TICK_BATCH.
    """
    from backtesting.tick import TickAggregator

    TICK_FLUSH_MS = 50  # flush tick buffer every 50ms
    aggregator = TickAggregator(timeframe=replay_state.active_timeframe)
    tick_buffer: list[dict] = []
    last_flush = asyncio.get_event_loop().time()

    def _flush_ticks() -> None:
        """Serialize and broadcast the accumulated tick buffer."""
        nonlocal tick_buffer, last_flush
        if not tick_buffer:
            return
        # Bypass Pydantic: build JSON directly from plain dicts
        payload = json.dumps({
            "type": "TICK_BATCH",
            "data": tick_buffer,
            "timestamp": None,
        })
        broadcast(payload)
        tick_buffer = []
        last_flush = asyncio.get_event_loop().time()

    async for event in replay_ticks(ticks_cache, aggregator, speed=lambda: replay_state.speed):
        if replay_state._restart_event.is_set():
            logger.info("Strategy switch requested → %s", replay_state.active_strategy)
            _flush_ticks()  # drain remaining ticks
            return True

        tick = event["tick"]
        completed_bar = event["bar"]

        # Accumulate tick into buffer (no lock, no Pydantic, no broadcast yet)
        tick_entry: dict = {
            "timestamp": str(tick.ts),
            "price": tick.price,
            "volume": tick.volume,
        }
        if tick.bid is not None:
            tick_entry["bid"] = tick.bid
        if tick.ask is not None:
            tick_entry["ask"] = tick.ask
        tick_buffer.append(tick_entry)

        # Lock-free: single-writer counter, safe on CPython (GIL)
        replay_state.tick_count += 1

        # Flush tick buffer on timer or when a bar completes
        now = asyncio.get_event_loop().time()
        should_flush = (
            completed_bar is not None
            or (now - last_flush) >= TICK_FLUSH_MS / 1000
        )
        if should_flush:
            _flush_ticks()

        # When a bar completes, run strategy.on_bar() and broadcast
        if completed_bar is not None:
            completed_bar.instrument = INSTRUMENT
            completed_bar.timeframe = replay_state.active_timeframe

            try:
                events = await _run_strategy_on_bar(strategy, completed_bar)
            except Exception as exc:
                logger.exception("Strategy error on bar %s — skipping", completed_bar.timestamp)
                events = []

            bar_data = completed_bar.model_dump(mode="json")
            if strategy.current_fast_ma is not None:
                bar_data["fast_ma"] = round(strategy.current_fast_ma, 2)
            if strategy.current_slow_ma is not None:
                bar_data["slow_ma"] = round(strategy.current_slow_ma, 2)

            await _process_bar(strategy, completed_bar, bar_data)
            await _broadcast_events(strategy, events)

        # Call on_tick for intra-bar updates
        current_bar = aggregator.current_bar
        try:
            tick_signal = strategy.on_tick(
                tick, current_bar, strategy.open_trade, strategy.metrics.current_capital
            )
        except Exception:
            logger.exception("Strategy on_tick error — skipping")
            tick_signal = None

        if tick_signal is not None:
            await _broadcast_events(strategy, [tick_signal] if isinstance(tick_signal, dict) else tick_signal)

    # Flush any remaining ticks after the loop ends
    _flush_ticks()
    return False


# ── Datalake WebSocket stream replay ─────────────────────────────────────────

_TIMEFRAME_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400,
}


def _compute_datalake_params(ui_speed: float, timeframe: str) -> tuple[float, float]:
    """Convert dashboard UI speed to datalake speed + max_delay.

    The UI speed means "bars per second at 1x". We want:
      1x → 1 bar/sec, 2x → 2 bars/sec, 10x → 10 bars/sec

    The datalake paces by: sleep = min(timestamp_delta / speed, max_delay).
    For M15 bars (900s apart), to get 1 bar/sec we need speed = 900.
    max_delay caps overnight/weekend gaps to a few seconds.

    Returns (datalake_speed, max_delay).
    """
    bar_seconds = _TIMEFRAME_SECONDS.get(timeframe, 900)
    # datalake_speed = bar_interval * ui_speed → delivers ui_speed bars/sec
    datalake_speed = bar_seconds * ui_speed
    # Cap gaps at 5s so weekends/overnights don't stall
    max_delay = 5.0
    return datalake_speed, max_delay


async def _run_stream_bar_replay(strategy) -> bool:
    """Consume bars from datalake /ws/bars stream. Returns True if restarted.

    The datalake handles pacing via real-time timestamp deltas / speed,
    capped by max_delay to keep delivery rate matched to the UI speed.
    Speed changes require reconnecting with the new params + start=last_ts.
    """
    last_ts: str | None = None

    while True:
        ui_speed = replay_state.speed if replay_state.speed > 0 else REPLAY_SPEED
        datalake_speed, max_delay = _compute_datalake_params(ui_speed, replay_state.active_timeframe)

        try:
            logger.info(
                "Connecting to datalake (ui_speed=%.1f, datalake_speed=%.0f, max_delay=%.1f, start=%s)",
                ui_speed, datalake_speed, max_delay, last_ts,
            )
            async for bar in stream_bars_from_datalake(
                instrument=INSTRUMENT,
                timeframe=replay_state.active_timeframe,
                datalake_url=DATALAKE_URL,
                speed=datalake_speed,
                max_delay=max_delay,
                start=last_ts or STREAM_START,
                end=STREAM_END,
            ):
                if replay_state._restart_event.is_set():
                    logger.info("Strategy switch requested → %s", replay_state.active_strategy)
                    return True

                # Pause: spin until unpaused, then reconnect with new speed
                if replay_state.speed == 0:
                    while replay_state.speed == 0:
                        await asyncio.sleep(0.1)
                    # Speed changed while paused — break to reconnect
                    break

                # Detect speed change — break to reconnect with new speed
                if replay_state.speed != ui_speed and replay_state.speed > 0:
                    break

                last_ts = bar.timestamp.isoformat()

                try:
                    events = await _run_strategy_on_bar(strategy, bar)
                except Exception as exc:
                    logger.exception("Strategy error on bar %s — skipping", bar.timestamp)
                    events = []

                bar_data = bar.model_dump(mode="json")
                if strategy.current_fast_ma is not None:
                    bar_data["fast_ma"] = round(strategy.current_fast_ma, 2)
                if strategy.current_slow_ma is not None:
                    bar_data["slow_ma"] = round(strategy.current_slow_ma, 2)

                await _process_bar(strategy, bar, bar_data)
                await _broadcast_events(strategy, events)

                if not events and len(replay_state.bar_buffer) % 10 == 0:
                    metrics_payload = serialize(WSMessage(
                        type=MessageType.METRICS,
                        data=strategy.metrics.model_dump(),
                        timestamp=datetime.now(timezone.utc),
                    ))
                    broadcast(metrics_payload)
            else:
                # Stream completed (datalake sent {"done": true})
                return False

        except Exception:
            logger.exception("Datalake bar stream error — reconnecting in 2s")
            await asyncio.sleep(2)

        # Check for restart before reconnecting
        if replay_state._restart_event.is_set():
            return True

        logger.info("Reconnecting to datalake bar stream (speed=%.1f, start=%s)", replay_state.speed, last_ts)


async def _run_stream_tick_replay(strategy) -> bool:
    """Consume ticks from datalake /ws/ticks stream with bar aggregation.

    Returns True if restarted (strategy switch).
    """
    from backtesting.tick import TickAggregator

    TICK_FLUSH_MS = 50
    TICK_HISTORY_MAX = 200_000  # raw ticks to keep for timeframe re-aggregation (~60min of active trading)
    active_tf = replay_state.active_timeframe
    aggregator = TickAggregator(timeframe=active_tf)
    tick_buffer: list[dict] = []  # pending broadcast buffer
    tick_history: list[dict] = []  # rolling raw tick buffer for re-aggregation
    last_flush = asyncio.get_event_loop().time()
    last_ts: str | None = None

    def _flush_ticks() -> None:
        nonlocal tick_buffer, last_flush
        if not tick_buffer:
            return
        # Include the current partial bar so the frontend can update
        # the in-progress candle as ticks arrive.
        partial = aggregator.current_bar
        current_bar = None
        if partial is not None:
            current_bar = {
                "timestamp": partial.ts.isoformat(),
                "open": partial.open,
                "high": partial.high,
                "low": partial.low,
                "close": partial.close,
            }
        payload = json.dumps({
            "type": "TICK_BATCH",
            "data": tick_buffer,
            "current_bar": current_bar,
            "timestamp": None,
        })
        broadcast(payload)
        tick_buffer = []
        last_flush = asyncio.get_event_loop().time()

    while True:
        ui_speed = replay_state.speed if replay_state.speed > 0 else REPLAY_SPEED
        # Tick mode: datalake speed = UI speed directly.
        # At 1x, ticks arrive at real-time pace. At 10x, 10x faster.
        # max_delay only caps truly large gaps (overnight/weekends) — normal
        # inter-tick gaps (seconds to minutes) must play out at real speed.
        # At 1x: cap at 120s (2 min). At 10x: cap at 12s. Minimum 5s.
        datalake_speed = ui_speed
        max_delay = max(5.0, 120.0 / ui_speed)

        try:
            async for tick_msg in stream_ticks_from_datalake(
                instrument=INSTRUMENT,
                datalake_url=DATALAKE_URL,
                speed=datalake_speed,
                max_delay=max_delay,
                start=last_ts or STREAM_START,
                end=STREAM_END,
            ):
                if replay_state._restart_event.is_set():
                    _flush_ticks()
                    return True

                if replay_state.speed == 0:
                    _flush_ticks()
                    while replay_state.speed == 0:
                        await asyncio.sleep(0.1)
                    break  # reconnect with new speed

                if replay_state.speed != ui_speed and replay_state.speed > 0:
                    _flush_ticks()
                    break  # reconnect with new speed

                # Timeframe change: reset aggregator without reconnecting.
                # Re-aggregate buffered ticks to backfill the chart.
                if replay_state.active_timeframe != active_tf:
                    _flush_ticks()
                    active_tf = replay_state.active_timeframe
                    aggregator = TickAggregator(timeframe=active_tf)

                    # Re-aggregate tick history into bars for the new timeframe
                    backfill_bars: list[dict] = []
                    for raw in tick_history:
                        t = Tick(
                            ts=pd.Timestamp(raw["timestamp"]),
                            price=float(raw["price"]),
                            volume=float(raw.get("volume", 0)),
                            bid=raw.get("bid"),
                            ask=raw.get("ask"),
                        )
                        completed = aggregator.update(t)
                        if completed is not None:
                            bar = _backtesting_bar_to_dashboard(completed)
                            bar.instrument = INSTRUMENT
                            bar.timeframe = active_tf
                            backfill_bars.append(bar.model_dump(mode="json"))

                    async with replay_state.lock:
                        replay_state.bar_buffer = backfill_bars

                    snapshot = await replay_state.snapshot_data()
                    broadcast(serialize(WSMessage(
                        type=MessageType.SNAPSHOT,
                        data=snapshot,
                        timestamp=datetime.now(timezone.utc),
                    )))
                    logger.info(
                        "Aggregator reset for %s — backfilled %d bars from %d ticks",
                        active_tf, len(backfill_bars), len(tick_history),
                    )

                # Strategy params changed: hot-swap strategy without reconnecting.
                # Reinstantiate with new params, reset aggregator + chart.
                if replay_state._strategy_dirty:
                    replay_state._strategy_dirty = False
                    _flush_ticks()
                    strategy = get_strategy(
                        replay_state.active_strategy,
                        initial_capital=INITIAL_CAPITAL,
                        spread=SPREAD,
                        commission_per_unit=COMMISSION_PER_UNIT,
                        slippage_pct=SLIPPAGE_PCT,
                        **replay_state.strategy_params,
                    )
                    replay_state.indicator_labels = strategy.indicator_labels
                    replay_state.indicator_overlay = strategy.indicator_overlay
                    replay_state.configurable_params = strategy.configurable_params
                    aggregator = TickAggregator(timeframe=active_tf)
                    async with replay_state.lock:
                        replay_state.bar_buffer = []
                        replay_state.metrics = strategy.metrics.model_dump()
                    snapshot = await replay_state.snapshot_data()
                    broadcast(serialize(WSMessage(
                        type=MessageType.SNAPSHOT,
                        data=snapshot,
                        timestamp=datetime.now(timezone.utc),
                    )))
                    logger.info("Strategy hot-swapped with new params")

                last_ts = tick_msg["timestamp"]

                # Accumulate tick for batched broadcast + rolling history
                tick_buffer.append(tick_msg)
                tick_history.append(tick_msg)
                if len(tick_history) > TICK_HISTORY_MAX:
                    tick_history[:] = tick_history[-TICK_HISTORY_MAX:]
                replay_state.tick_count += 1

                # Aggregate into bars via backtesting engine
                tick_obj = Tick(
                    ts=pd.Timestamp(tick_msg["timestamp"]),
                    price=float(tick_msg["price"]),
                    volume=float(tick_msg.get("volume", 0)),
                    bid=tick_msg.get("bid"),
                    ask=tick_msg.get("ask"),
                )
                completed_bt_bar = aggregator.update(tick_obj)

                # Flush tick buffer on timer or bar completion
                now = asyncio.get_event_loop().time()
                if completed_bt_bar is not None or (now - last_flush) >= TICK_FLUSH_MS / 1000:
                    _flush_ticks()

                # When bar completes, run strategy
                if completed_bt_bar is not None:
                    completed_bar = _backtesting_bar_to_dashboard(completed_bt_bar)
                    completed_bar.instrument = INSTRUMENT
                    completed_bar.timeframe = active_tf

                    try:
                        events = await _run_strategy_on_bar(strategy, completed_bar)
                    except Exception:
                        logger.exception("Strategy error — skipping")
                        events = []

                    bar_data = completed_bar.model_dump(mode="json")
                    if strategy.current_fast_ma is not None:
                        bar_data["fast_ma"] = round(strategy.current_fast_ma, 2)
                    if strategy.current_slow_ma is not None:
                        bar_data["slow_ma"] = round(strategy.current_slow_ma, 2)

                    await _process_bar(strategy, completed_bar, bar_data)
                    await _broadcast_events(strategy, events)

                # on_tick for intra-bar updates
                current_bar = aggregator.current_bar
                try:
                    tick_signal = strategy.on_tick(
                        tick_obj, current_bar, strategy.open_trade, strategy.metrics.current_capital
                    )
                except Exception:
                    tick_signal = None

                if tick_signal is not None:
                    await _broadcast_events(strategy, [tick_signal] if isinstance(tick_signal, dict) else tick_signal)
            else:
                # Stream completed
                _flush_ticks()
                # Flush last partial bar
                final_bt_bar = aggregator.flush()
                if final_bt_bar is not None:
                    final_bar = _backtesting_bar_to_dashboard(final_bt_bar)
                    final_bar.instrument = INSTRUMENT
                    final_bar.timeframe = active_tf
                    bar_data = final_bar.model_dump(mode="json")
                    await _process_bar(strategy, final_bar, bar_data)
                return False

        except Exception:
            logger.exception("Datalake tick stream error — reconnecting in 2s")
            await asyncio.sleep(2)

        if replay_state._restart_event.is_set():
            _flush_ticks()
            return True

        logger.info("Reconnecting to datalake tick stream (speed=%.1f, start=%s)", replay_state.speed, last_ts)


async def run_shared_replay():
    """Single replay loop that broadcasts to all connected clients.

    When REPLAY_LOOP is enabled (default), the replay restarts automatically
    after a pause - keeps the demo alive indefinitely for deployed instances.
    """
    while True:
        replay_state._restart_event.clear()
        strategy = get_strategy(
            replay_state.active_strategy,
            initial_capital=INITIAL_CAPITAL,
            spread=SPREAD,
            commission_per_unit=COMMISSION_PER_UNIT,
            slippage_pct=SLIPPAGE_PCT,
            **replay_state.strategy_params,
        )
        replay_state.indicator_labels = strategy.indicator_labels
        replay_state.indicator_overlay = strategy.indicator_overlay
        replay_state.configurable_params = strategy.configurable_params

        # Reset shared state for this run
        async with replay_state.lock:
            replay_state.bar_buffer = []
            replay_state.open_positions = []
            replay_state.closed_positions = []
            replay_state.metrics = strategy.metrics.model_dump()
            replay_state.complete = False
            replay_state.started = True
            replay_state.tick_count = 0

        # Notify clients of fresh start
        snapshot = await replay_state.snapshot_data()
        restart_payload = serialize(WSMessage(
            type=MessageType.SNAPSHOT,
            data=snapshot,
            timestamp=datetime.now(timezone.utc),
        ))
        broadcast(restart_payload)

        if replay_state.mode == "stream":
            if STREAM_TICKS:
                restarted = await _run_stream_tick_replay(strategy)
            else:
                restarted = await _run_stream_bar_replay(strategy)
        elif replay_state.mode == "tick" and ticks_cache:
            restarted = await _run_tick_replay(strategy)
        else:
            restarted = await _run_bar_replay(strategy)

        if restarted:
            continue  # immediately restart with new strategy

        # Replay complete
        async with replay_state.lock:
            replay_state.complete = True

        complete_payload = serialize(WSMessage(
            type=MessageType.SNAPSHOT,
            data={"status": "complete", "total_bars_sent": len(replay_state.bar_buffer)},
            timestamp=datetime.now(timezone.utc),
        ))
        broadcast(complete_payload)
        logger.info("Shared replay complete - %d bars processed", len(replay_state.bar_buffer))

        if not REPLAY_LOOP:
            break

        logger.info("Restarting replay in %ds", REPLAY_PAUSE)
        await asyncio.sleep(REPLAY_PAUSE)


# ── REST endpoints ───────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "instrument": INSTRUMENT,
        "timeframe": replay_state.active_timeframe,
        "mode": replay_state.mode,
        "bars_loaded": len(bars_cache),
        "ticks_loaded": len(ticks_cache),
        "connected_clients": len(connected_clients),
        "replay_complete": replay_state.complete,
        "bars_replayed": len(replay_state.bar_buffer),
        "ticks_replayed": replay_state.tick_count,
    }


@app.get("/api/strategies")
async def list_strategies():
    return {
        "active": replay_state.active_strategy,
        "available": list(STRATEGY_REGISTRY.keys()),
    }


@app.post("/api/strategy")
async def switch_strategy(body: dict):
    name = body.get("name", "")
    if name not in STRATEGY_REGISTRY:
        available = list(STRATEGY_REGISTRY.keys())
        return {"error": f"Unknown strategy '{name}'. Available: {available}"}, 400
    if name == replay_state.active_strategy:
        return {"status": "already_active", "strategy": name}
    replay_state.strategy_params = {}  # reset params on strategy switch
    replay_state.request_restart(name)
    logger.info("Strategy switch: %s → %s", replay_state.active_strategy, name)
    return {"status": "switching", "strategy": name}


@app.post("/api/timeframe")
async def switch_timeframe(body: dict):
    tf = body.get("timeframe", "")
    valid = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
    if tf not in valid:
        return {"error": f"Invalid timeframe '{tf}'. Valid: {valid}"}, 400
    if tf == replay_state.active_timeframe:
        return {"status": "already_active", "timeframe": tf}
    old = replay_state.active_timeframe
    replay_state.request_timeframe_change(tf)
    logger.info("Timeframe switch: %s → %s", old, tf)
    return {"status": "switching", "timeframe": tf}


@app.post("/api/strategy/params")
async def update_strategy_params(body: dict):
    params = body.get("params", {})
    if not isinstance(params, dict):
        return {"error": "params must be a dict"}
    # Validate against configurable param names
    valid_names = {p["name"] for p in replay_state.configurable_params}
    filtered = {k: v for k, v in params.items() if k in valid_names}
    replay_state.strategy_params.update(filtered)
    if replay_state.mode == "stream" and STREAM_TICKS:
        # Hot-swap: tick stream continues, strategy gets reinstantiated in-loop
        replay_state._strategy_dirty = True
        logger.info("Strategy params updated (hot-swap): %s", filtered)
        return {"status": "hot_swap", "params": replay_state.strategy_params}
    else:
        replay_state.request_restart()
        logger.info("Strategy params updated (restart): %s", filtered)
        return {"status": "restarting", "params": replay_state.strategy_params}


@app.post("/api/pause")
async def toggle_pause():
    """Toggle pause/resume. Stores previous speed so resume restores it."""
    if replay_state.speed > 0:
        replay_state._prev_speed = replay_state.speed
        replay_state.speed = 0.0
        paused = True
    else:
        replay_state.speed = getattr(replay_state, "_prev_speed", REPLAY_SPEED)
        paused = False
    broadcast(serialize(WSMessage(
        type=MessageType.SNAPSHOT,
        data={"speed": replay_state.speed, "paused": paused},
        timestamp=datetime.now(timezone.utc),
    )))
    logger.info("Replay %s", "paused" if paused else "resumed")
    return {"status": "ok", "paused": paused, "speed": replay_state.speed}


@app.post("/api/speed")
async def set_speed(body: dict):
    speed = body.get("speed")
    if not isinstance(speed, (int, float)) or speed <= 0:
        return {"error": "speed must be a positive number"}
    replay_state.speed = float(speed)
    # Broadcast updated speed to all clients
    broadcast(serialize(WSMessage(
        type=MessageType.SNAPSHOT,
        data={"speed": replay_state.speed},
        timestamp=datetime.now(timezone.utc),
    )))
    logger.info("Speed changed to %.1fx", replay_state.speed)
    return {"status": "ok", "speed": replay_state.speed}


@app.get("/api/trades/export")
async def export_trades():
    """Export closed trades as CSV."""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "instrument", "side", "entry_price", "entry_time",
        "exit_price", "exit_time", "pnl", "pnl_pct", "exit_reason",
    ])

    async with replay_state.lock:
        for t in replay_state.closed_positions:
            writer.writerow([
                t.get("id"), t.get("instrument"), t.get("side"),
                t.get("entry_price"), t.get("entry_time"),
                t.get("exit_price"), t.get("exit_time"),
                t.get("pnl"), t.get("pnl_pct"), t.get("exit_reason"),
            ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trades.csv"},
    )


# ── WebSocket endpoint ──────────────────────────────────────────────────────

async def _drain_client(client: ClientConnection) -> None:
    """Drain the outbound queue for a single client.

    Runs as a background task per client. Exits on None sentinel or any
    send error (client disconnect, timeout, etc.).
    """
    ws = client.ws
    try:
        while True:
            payload = await client.queue.get()
            if payload is None:
                break  # sentinel — client removed
            try:
                await asyncio.wait_for(ws.send_text(payload), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                break  # send failed — exit drain loop
    except Exception:
        pass  # queue closed or other error
    finally:
        # Ensure client is removed (may already be gone)
        connected_clients.pop(ws, None)
        try:
            await ws.close()
        except Exception:
            pass


@app.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket):
    await websocket.accept()

    client = ClientConnection(websocket)
    drain_task: asyncio.Task | None = None

    try:
        # Join broadcast FIRST, then send snapshot. This ensures no events
        # are lost between snapshot and join. The frontend's snapshot handler
        # replaces all state, so any broadcast events that arrive before the
        # snapshot are harmless — the snapshot resets everything.
        connected_clients[websocket] = client
        drain_task = asyncio.create_task(_drain_client(client))
        logger.info("Client connected (%d total)", len(connected_clients))

        snapshot = await replay_state.snapshot_data()
        await websocket.send_text(serialize(WSMessage(
            type=MessageType.SNAPSHOT,
            data=snapshot,
            timestamp=datetime.now(timezone.utc),
        )))

        # Keep connection alive - bars arrive via drain task
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send heartbeat to keep connection alive
                await websocket.send_text(serialize(WSMessage(
                    type=MessageType.HEARTBEAT,
                    data={},
                    timestamp=datetime.now(timezone.utc),
                )))

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception:
        logger.exception("WebSocket error")
    finally:
        connected_clients.pop(websocket, None)
        # Signal drain task to exit
        try:
            client.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        if drain_task:
            drain_task.cancel()
        logger.info("Client removed (%d remaining)", len(connected_clients))
