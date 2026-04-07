"""FastAPI backend - streams replayed market data + strategy signals over WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware

from models import Bar, Metrics, MessageType, WSMessage, Trade
from pathlib import Path

from replay import load_bars, replay_bars
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

# ── Shared replay state ─────────────────────────────────────────────────────

bars_cache: list[Bar] = []
connected_clients: set[WebSocket] = set()


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
        self.strategy_params: dict[str, Any] = {}  # custom params for current strategy
        self.configurable_params: list[dict] = []  # param definitions for UI
        self.speed: float = REPLAY_SPEED
        self.indicator_labels: tuple[str, str] = ("Fast", "Slow")
        self.indicator_overlay: bool = True
        self.lock = asyncio.Lock()
        self._restart_event = asyncio.Event()

    def request_restart(self, strategy_name: str | None = None) -> None:
        """Signal the replay loop to restart, optionally with a new strategy."""
        if strategy_name is not None:
            self.active_strategy = strategy_name
        self._restart_event.set()

    async def snapshot_data(self) -> dict[str, Any]:
        """Build a snapshot payload of the current state."""
        async with self.lock:
            return {
                "instrument": INSTRUMENT,
                "timeframe": TIMEFRAME,
                "total_bars": len(bars_cache),
                "speed": self.speed,
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load data on startup and start the shared replay loop."""
    global bars_cache
    bars_cache = await load_bars(
        instrument=INSTRUMENT,
        timeframe=TIMEFRAME,
        datalake_url=DATALAKE_URL,
        datalake_api_key=DATALAKE_API_KEY,
        csv_dir=Path(CSV_DIR) if CSV_DIR else None,
    )
    logger.info("Ready - %d bars loaded for %s %s", len(bars_cache), INSTRUMENT, TIMEFRAME)

    # Start the shared replay loop
    task = asyncio.create_task(run_shared_replay())
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


async def broadcast(payload: str):
    """Send a pre-serialized message to all connected clients concurrently.

    Each send gets a short timeout — slow/stalled clients are dropped rather
    than blocking the replay loop for everyone. At 10x speed the loop ticks
    every 0.1s, so a long timeout causes visible UI freezes.
    """
    if not connected_clients:
        return
    results = await asyncio.gather(
        *(asyncio.wait_for(ws.send_text(payload), timeout=0.5) for ws in connected_clients),
        return_exceptions=True,
    )
    dead = [ws for ws, r in zip(connected_clients, results) if isinstance(r, Exception)]
    for ws in dead:
        connected_clients.discard(ws)


# ── Shared replay loop ──────────────────────────────────────────────────────

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

        # Notify clients of fresh start
        snapshot = await replay_state.snapshot_data()
        restart_payload = serialize(WSMessage(
            type=MessageType.SNAPSHOT,
            data=snapshot,
            timestamp=datetime.now(timezone.utc),
        ))
        await broadcast(restart_payload)

        restarted = False
        async for bar in replay_bars(bars_cache, speed=lambda: replay_state.speed):
            # Check if strategy switch was requested
            if replay_state._restart_event.is_set():
                logger.info("Strategy switch requested → %s", replay_state.active_strategy)
                restarted = True
                break

            try:
                events = strategy.on_bar(bar)
            except Exception as exc:
                logger.exception("Strategy error on bar %s — skipping", bar.timestamp)
                events = []
                error_payload = serialize(WSMessage(
                    type=MessageType.METRICS,
                    data={**strategy.metrics.model_dump(), "strategy_error": str(exc)},
                    timestamp=datetime.now(timezone.utc),
                ))
                await broadcast(error_payload)
                continue

            # Build bar data with indicators
            bar_data = bar.model_dump(mode="json")
            if strategy.current_fast_ma is not None:
                bar_data["fast_ma"] = round(strategy.current_fast_ma, 2)
            if strategy.current_slow_ma is not None:
                bar_data["slow_ma"] = round(strategy.current_slow_ma, 2)

            # Update shared state
            async with replay_state.lock:
                replay_state.bar_buffer.append(bar_data)
                # Trim buffer to keep memory bounded
                if len(replay_state.bar_buffer) > MAX_BAR_BUFFER:
                    replay_state.bar_buffer = replay_state.bar_buffer[-MAX_BAR_BUFFER:]
                replay_state.metrics = strategy.metrics.model_dump()

            # Broadcast bar
            bar_payload = serialize(WSMessage(
                type=MessageType.BAR,
                data=bar_data,
                timestamp=datetime.now(timezone.utc),
            ))
            await broadcast(bar_payload)

            # Broadcast trade events
            for event in events:
                event_data = event["data"].model_dump(mode="json") if hasattr(event["data"], "model_dump") else event["data"]

                # Update shared positions
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
                await broadcast(event_payload)

            # Periodic metrics broadcast (every 10 bars if no trade event)
            if not events and len(replay_state.bar_buffer) % 10 == 0:
                metrics_payload = serialize(WSMessage(
                    type=MessageType.METRICS,
                    data=strategy.metrics.model_dump(),
                    timestamp=datetime.now(timezone.utc),
                ))
                await broadcast(metrics_payload)

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
        await broadcast(complete_payload)
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
        "timeframe": TIMEFRAME,
        "bars_loaded": len(bars_cache),
        "connected_clients": len(connected_clients),
        "replay_complete": replay_state.complete,
        "bars_replayed": len(replay_state.bar_buffer),
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


@app.post("/api/strategy/params")
async def update_strategy_params(body: dict):
    params = body.get("params", {})
    if not isinstance(params, dict):
        return {"error": "params must be a dict"}
    # Validate against configurable param names
    valid_names = {p["name"] for p in replay_state.configurable_params}
    filtered = {k: v for k, v in params.items() if k in valid_names}
    replay_state.strategy_params.update(filtered)
    replay_state.request_restart()
    logger.info("Strategy params updated: %s", filtered)
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
    await broadcast(serialize(WSMessage(
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
    await broadcast(serialize(WSMessage(
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

@app.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket):
    await websocket.accept()

    try:
        # Join broadcast FIRST, then send snapshot. This ensures no events
        # are lost between snapshot and join. The frontend's snapshot handler
        # replaces all state, so any broadcast events that arrive before the
        # snapshot are harmless — the snapshot resets everything.
        connected_clients.add(websocket)
        logger.info("Client connected (%d total)", len(connected_clients))

        snapshot = await replay_state.snapshot_data()
        await websocket.send_text(serialize(WSMessage(
            type=MessageType.SNAPSHOT,
            data=snapshot,
            timestamp=datetime.now(timezone.utc),
        )))

        # Keep connection alive - bars arrive via broadcast
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
        connected_clients.discard(websocket)
        logger.info("Client removed (%d remaining)", len(connected_clients))
