# Live Trading Dashboard

**Demo / educational project** — not financial advice, not intended for real-money trading.

Streaming trading dashboard that runs pluggable strategies against market data, rendering candlestick charts, trade signals, and performance metrics over WebSocket. Supports three data modes: CSV replay, datalake bar streaming, and real-time tick streaming with live candle aggregation.

![Stack](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)
![Stack](https://img.shields.io/badge/React_19-61DAFB?style=flat&logo=react&logoColor=black)
![Stack](https://img.shields.io/badge/TypeScript-3178C6?style=flat&logo=typescript&logoColor=white)
![Stack](https://img.shields.io/badge/TailwindCSS_4-06B6D4?style=flat&logo=tailwindcss&logoColor=white)

![Dashboard Demo](docs/demo.gif)

## Quick Start

### Docker (recommended)

```bash
docker compose up --build
```

Frontend on `http://localhost:3000`, backend on `http://localhost:8080`.

### Manual

**Prerequisites:** Python 3.11+, Node.js 20+

```bash
# Backend
cd backend && pip install -r requirements.txt
python -m uvicorn main:app --port 8080  # → http://localhost:8080

# Frontend (separate terminal)
cd frontend && npm install
npm run dev  # → http://localhost:5173
```

### Tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest -v
```

## Features

- **Real-time candlestick chart** with auto-scrolling, indicator overlays, and separate oscillator pane
- **Tick data streaming** — connects to datalake WebSocket, aggregates ticks into candles in real-time, shows candles building live
- **Live timeframe switching** — M1, M5, M15, H1 buttons on the chart; in tick mode, switches instantly without reconnecting (just re-groups ticks)
- **Four strategies** — MA Crossover, Bollinger Mean Reversion, RSI+ADX Momentum, Tick Scalper (with intra-bar entries/exits via `on_tick()`)
- **Live parameter tuning** via sidebar sliders (debounced, restarts replay)
- **Live unrealized P&L** — sidebar position card updates from tick prices every 200ms
- **Position sizing** — fixed-fraction risk model (2% of capital per trade), sized by stop-loss distance, capped at 10x leverage
- **Cost model** — configurable spread, commission, and slippage
- **Risk management** — drawdown gate halts new trades at configurable threshold
- **Realistic stop-loss execution** — stops trigger on intra-bar highs/lows and fill at the stop price; tick scalper manages stops/TP intra-bar via `on_tick()`
- **Data validation** — OHLC sanity checks, timestamp ordering, deduplication, market-hours-aware gap detection
- **Live metrics** — P&L, return %, win rate, max drawdown, Sharpe ratio, profit factor, equity curve
- **Data clock** — smooth UTC clock in the header, interpolates between ticks, scales with replay speed
- **Replay loop** with speed control (1x–10x), pause/resume

### Performance

- **Tick batching** — ticks are buffered server-side and flushed as a single `TICK_BATCH` message every 50ms, cutting WS frame overhead ~20x
- **Per-client outbound queues** — `broadcast()` is non-blocking; each client has a bounded `asyncio.Queue` drained by a dedicated task. Slow clients overflow and get dropped
- **Ref-based tick rendering** — tick data bypasses React state entirely; chart updates imperatively via polling intervals, zero re-renders per tick
- **Threaded strategy execution** — `strategy.on_bar()` runs in a thread pool via `run_in_executor`, keeping the async event loop free for tick ingestion

## Data Modes

| Mode | Set via | How it works |
|---|---|---|
| **Bar** (default) | `DATA_MODE=bar` | Loads OHLC bars from CSV or datalake REST API, drip-feeds locally |
| **Tick** | `DATA_MODE=tick` | Loads raw ticks from CSV, aggregates into bars via `TickAggregator` |
| **Stream** | `DATA_MODE=stream` or set `DATALAKE_URL` | Connects to datalake WebSocket (`/ws/bars` or `/ws/ticks`), datalake controls pacing |
| **Auto** | `DATA_MODE=auto` (default) | Stream if `DATALAKE_URL` set, else tick if available, else bar |

In stream tick mode (`STREAM_TICKS=true`), raw ticks arrive from the datalake at real-time speed, are aggregated into candles by `TickAggregator`, and broadcast to the frontend as `TICK_BATCH` messages with the current partial bar so the chart shows candles building live.

## Strategies

Strategies extend `AbstractStrategy` and register via `@register_strategy`. All inherit position sizing, cost model, and drawdown gating.

| Strategy | Entry | Exit | Overlay |
|---|---|---|---|
| **MA Crossover** | Fast MA crosses slow MA | Opposite cross or stop-loss | Price chart |
| **Bollinger Mean Reversion** | Price touches upper/lower band | Reverts to SMA or stop-loss | Price chart |
| **RSI+ADX Momentum** | RSI crosses 30/70 when ADX > 25 | RSI reversal, regime shift, or stop-loss | Separate pane |
| **Tick Scalper** | EMA pullback entry via `on_tick()` | Intra-bar stop-loss/take-profit or trend reversal | Price chart |

### Writing a new strategy

Implement `on_bar()` (required) and optionally `on_tick()` for intra-bar logic. Decorate with `@register_strategy`, import in `strategies/__init__.py`.

```python
@register_strategy("my_strategy")
class MyStrategy(AbstractStrategy):
    def on_bar(self, bar: Bar) -> list[dict]:
        # Called on each completed bar — return trade/metrics events
        ...

    def on_tick(self, tick, current_bar, position, capital):
        # Optional — called on every tick for intra-bar logic
        # current_bar is the in-progress (partial) bar
        return None  # or return events to broadcast
```

## Configuration

All backend config is via environment variables. Create a `.env` file in the project root (auto-loaded via `python-dotenv`).

### Backend

| Variable | Default | Description |
|---|---|---|
| `INSTRUMENT` | `XAUUSD` | Trading instrument |
| `TIMEFRAME` | `M15` | Bar timeframe (M1, M5, M15, M30, H1, H4, D1) |
| `STRATEGY` | `ma_crossover` | Active strategy on startup |
| `REPLAY_SPEED` | `2.0` | Replay speed multiplier |
| `INITIAL_CAPITAL` | `10000` | Starting capital |
| `SPREAD` | `0.30` | Bid-ask spread per unit |
| `COMMISSION_PER_UNIT` | `0.01` | Commission per unit per leg |
| `SLIPPAGE_PCT` | `0.02` | Slippage as % of exit price |
| `DATALAKE_URL` | - | Datalake API URL (enables stream mode) |
| `DATA_MODE` | `auto` | `bar`, `tick`, `stream`, or `auto` |
| `STREAM_TICKS` | `false` | Use `/ws/ticks` instead of `/ws/bars` in stream mode |
| `STREAM_START` | - | ISO-8601 start bound for stream queries (e.g. `2024-07-09T13:30:00`) |
| `STREAM_END` | - | ISO-8601 end bound |
| `CSV_DIR` | - | Override CSV data directory |

### Frontend

| Variable | Default | Description |
|---|---|---|
| `VITE_WS_URL` | `ws://localhost:8080/ws/stream` | WebSocket endpoint |

### Example `.env` for tick streaming

```env
INSTRUMENT=XAUUSD
TIMEFRAME=M1
DATALAKE_URL=http://localhost:8000
STREAM_TICKS=true
STREAM_START=2024-07-09T13:30:00
STRATEGY=tick_scalper
```

## REST API

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Server health + stats |
| `GET` | `/api/strategies` | List strategies |
| `POST` | `/api/strategy` | Switch strategy (restarts replay) |
| `POST` | `/api/strategy/params` | Update strategy params (restarts replay) |
| `POST` | `/api/timeframe` | Switch timeframe (instant in tick stream mode) |
| `POST` | `/api/speed` | Change replay speed |
| `POST` | `/api/pause` | Toggle pause/resume |
| `GET` | `/api/trades/export` | Download trades CSV |

## WebSocket Protocol

All messages: `{ type, data, timestamp }`

| Message | Description |
|---|---|
| `SNAPSHOT` | Full state on connect (bars, positions, metrics, config, mode, timeframes) |
| `BAR` | Completed OHLC bar with indicator values |
| `TICK_BATCH` | Batched ticks + current partial bar (`{ data: Tick[], current_bar: Bar }`) |
| `TRADE_OPEN` / `TRADE_CLOSE` | Position opened/closed |
| `METRICS` | Updated performance metrics |
| `HEARTBEAT` | Keep-alive (30s) |

## Architecture

```
Datalake ──WS──> Dashboard Backend ──WS──> Frontend
(/ws/ticks)      (aggregate + strategy)    (chart + UI)
                       │
                  per-client queues
                  (non-blocking broadcast)
```

- **Backend** connects to datalake as a WebSocket client, receives ticks/bars
- **TickAggregator** (from backtesting engine) groups ticks into OHLC bars by timeframe boundary
- **Strategy** runs `on_bar()` in a thread pool on each completed bar; `on_tick()` runs inline for intra-bar logic
- **Broadcast** enqueues serialized messages to per-client bounded queues; dedicated drain tasks send to each frontend client
- **Frontend** receives `TICK_BATCH` messages, updates the current candle imperatively via refs (no React re-renders), and appends completed `BAR` messages to state

## Related Projects

- **[Datalake API](https://github.com/lucas-guerin-44/datalake-api)** — OHLC + tick data storage, WebSocket streaming endpoints (`/ws/bars`, `/ws/ticks`)
- **[Backtesting Engine](https://github.com/lucas-guerin-44/backtesting-engine)** — Offline backtesting with the same strategy interface; provides `Tick`, `TickAggregator`
