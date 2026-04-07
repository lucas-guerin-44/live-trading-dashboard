# Live Trading Dashboard

**Demo / educational project** — not financial advice, not intended for real-money trading.

Streaming trading dashboard that runs pluggable strategies against historical market data, rendering candlestick charts, trade signals, and performance metrics over WebSocket. Ships with sample CSV data for demo — swap in a live feed via `DATALAKE_URL` with zero code changes.

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

Frontend on `http://localhost:3000`, backend on `http://localhost:8000`.

### Manual

**Prerequisites:** Python 3.11+, Node.js 20+

```bash
# Backend
cd backend && pip install -r requirements.txt
uvicorn main:app --reload  # → http://localhost:8000

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
- **Three strategies** - MA Crossover, Bollinger Band Mean Reversion, RSI+ADX Momentum (with regime detection)
- **Live parameter tuning** via sidebar sliders (debounced, restarts replay)
- **Position sizing** - fixed-fraction risk model (2% of capital per trade), sized by stop-loss distance, capped at 10x leverage
- **Cost model** - configurable spread, commission, and slippage
- **Risk management** - drawdown gate halts new trades at configurable threshold
- **Realistic stop-loss execution** - stops trigger on intra-bar highs/lows and fill at the stop price
- **Data validation** - OHLC sanity checks, timestamp ordering, deduplication, market-hours-aware gap detection
- **Live metrics** - P&L, return %, win rate, max drawdown, Sharpe ratio, profit factor, equity curve
- **WebSocket streaming** with exponential backoff reconnection
- **Replay loop** with speed control (1x–10x), pause/resume

## Strategies

Strategies extend `AbstractStrategy` and register via `@register_strategy`. All inherit position sizing, cost model, and drawdown gating.

| Strategy | Entry | Exit | Overlay |
|---|---|---|---|
| **MA Crossover** | Fast MA crosses slow MA | Opposite cross or stop-loss | Price chart |
| **Bollinger Mean Reversion** | Price touches upper/lower band | Reverts to SMA or stop-loss | Price chart |
| **RSI+ADX Momentum** | RSI crosses 30/70 when ADX > 25 | RSI reversal, regime shift, or stop-loss | Separate pane |

New strategies: implement `on_bar()`, decorate with `@register_strategy`, import in `strategies/__init__.py`.

## Configuration

### Backend (env vars)

| Variable | Default | Description |
|---|---|---|
| `INSTRUMENT` | `XAUUSD` | Trading instrument |
| `TIMEFRAME` | `M15` | Bar timeframe |
| `STRATEGY` | `ma_crossover` | Active strategy |
| `REPLAY_SPEED` | `2.0` | Replay speed multiplier |
| `INITIAL_CAPITAL` | `10000` | Starting capital |
| `SPREAD` | `0.30` | Bid-ask spread per unit |
| `COMMISSION_PER_UNIT` | `0.01` | Commission per unit per leg |
| `SLIPPAGE_PCT` | `0.02` | Slippage as % of exit price |
| `DATALAKE_URL` | - | Optional datalake API URL |
| `CSV_DIR` | - | Override CSV data directory |

### Frontend

| Variable | Default | Description |
|---|---|---|
| `VITE_WS_URL` | `ws://localhost:8000/ws/stream` | WebSocket endpoint |

## Data

Loads OHLC data from **Datalake API** (if `DATALAKE_URL` set) or **CSV fallback** (`{INSTRUMENT}_{TIMEFRAME}.csv`). CSV format: `timestamp,open,high,low,close` with ISO 8601 timestamps.

The bundled CSVs are sample data for demo. Point `DATALAKE_URL` at a live feed for production - zero code changes needed.

## REST API

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Server health + stats |
| `GET` | `/api/strategies` | List strategies |
| `POST` | `/api/strategy` | Switch strategy (restarts replay) |
| `POST` | `/api/strategy/params` | Update strategy params (restarts replay) |
| `POST` | `/api/speed` | Change replay speed (no restart) |
| `POST` | `/api/pause` | Toggle pause/resume |
| `GET` | `/api/trades/export` | Download trades CSV |

## WebSocket Protocol

All messages: `{ type, data, timestamp }`

| Message | Description |
|---|---|
| `SNAPSHOT` | Full state on connect (bars, positions, metrics, config) |
| `BAR` | OHLC bar with indicator values |
| `TRADE_OPEN` / `TRADE_CLOSE` | Position opened/closed |
| `METRICS` | Updated performance metrics |
| `HEARTBEAT` | Keep-alive (30s) |

## Related Projects

- **[Datalake API](https://github.com/lucas-guerin-44/datalake-api)** - OHLC data ingestion/serving, consumed via `DATALAKE_URL`
- **[Backtesting Engine](https://github.com/lucas-guerin-44/backtesting-engine)** - Offline backtesting with the same strategy interface
