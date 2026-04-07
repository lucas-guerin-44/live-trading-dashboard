export interface Bar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  instrument: string;
  timeframe: string;
  fast_ma?: number;
  slow_ma?: number;
}

export interface Trade {
  id: number;
  instrument: string;
  side: "BUY" | "SELL";
  entry_price: number;
  entry_time: string;
  exit_price: number | null;
  exit_time: string | null;
  pnl: number;
  pnl_pct: number;
  exit_reason: string | null;
  quantity: number;
  signal_reason: string | null;
  stop_loss_price: number | null;
  take_profit_price: number | null;
}

export interface Metrics {
  total_pnl: number;
  total_pnl_pct: number;
  win_rate: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  open_positions: number;
  current_capital: number;
  initial_capital: number;
  max_drawdown: number;
  peak_capital: number;
  sharpe_ratio: number | null;
  profit_factor: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  largest_win: number | null;
  largest_loss: number | null;
  avg_trade_duration_bars: number | null;
}

export type MessageType =
  | "SNAPSHOT"
  | "BAR"
  | "TRADE_OPEN"
  | "TRADE_CLOSE"
  | "METRICS"
  | "HEARTBEAT";

export interface WSMessage {
  type: MessageType;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- runtime WS boundary, not validated
  data: any;
  timestamp: string | null;
}

export type ConnectionStatus = "connecting" | "connected" | "disconnected" | "waking_up";
