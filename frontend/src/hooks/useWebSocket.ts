import { useEffect, useRef, useState, useCallback } from "react";
import type { Bar, Trade, Metrics, Tick, WSMessage, ConnectionStatus } from "../types";

const INITIAL_METRICS: Metrics = {
  total_pnl: 0,
  total_pnl_pct: 0,
  win_rate: 0,
  total_trades: 0,
  winning_trades: 0,
  losing_trades: 0,
  open_positions: 0,
  current_capital: 10000,
  initial_capital: 10000,
  max_drawdown: 0,
  peak_capital: 10000,
  sharpe_ratio: null,
  profit_factor: null,
  avg_win: null,
  avg_loss: null,
  largest_win: null,
  largest_loss: null,
  avg_trade_duration_bars: null,
};

const MAX_BARS = 500;
const MAX_CLOSED_TRADES = 50;
const MAX_EQUITY_POINTS = 1000;
const MAX_RECONNECT_DELAY = 30000;

export interface EquityPoint {
  time: number; // unix seconds
  value: number;
}

export interface ParamDef {
  name: string;
  label: string;
  type: "int" | "float";
  value: number;
  min: number;
  max: number;
  step: number;
}

export interface DashboardState {
  bars: Bar[];
  openPositions: Trade[];
  closedPositions: Trade[];
  metrics: Metrics;
  status: ConnectionStatus;
  replayComplete: boolean;
  instrument: string;
  timeframe: string;
  totalBars: number;
  barCount: number;
  speed: number;
  paused: boolean;
  setSpeed: (speed: number) => void;
  togglePause: () => void;
  equityCurve: EquityPoint[];
  reconnectIn: number; // seconds until next reconnect, 0 if connected
  strategy: string;
  strategies: string[];
  switchStrategy: (name: string) => void;
  indicatorLabels: [string, string];
  indicatorOverlay: boolean;
  configurableParams: ParamDef[];
  updateParams: (params: Record<string, number>) => void;
  timeframes: string[];
  switchTimeframe: (tf: string) => void;
  /** Ref to latest tick — read imperatively, does NOT trigger re-renders. */
  lastTickRef: React.RefObject<Tick | null>;
  /** Ref to current (partial) bar being built from ticks — updated on each TICK_BATCH. */
  currentBarRef: React.RefObject<Bar | null>;
  mode: "bar" | "tick" | "stream";
  tickCount: number;
  /** Current data time (from latest tick/bar timestamp), synced every 500ms. */
  dataTime: string;
}

export function useWebSocket(baseUrl: string): DashboardState {
  const [bars, setBars] = useState<Bar[]>([]);
  const [openPositions, setOpenPositions] = useState<Trade[]>([]);
  const [closedPositions, setClosedPositions] = useState<Trade[]>([]);
  const [metrics, setMetrics] = useState<Metrics>(INITIAL_METRICS);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [replayComplete, setReplayComplete] = useState(false);
  const [instrument, setInstrument] = useState("");
  const [timeframe, setTimeframe] = useState("");
  const [totalBars, setTotalBars] = useState(0);
  const [barCount, setBarCount] = useState(0);
  const [speed, setSpeedState] = useState(2);
  const [paused, setPaused] = useState(false);
  const [equityCurve, setEquityCurve] = useState<EquityPoint[]>([]);
  const [reconnectIn, setReconnectIn] = useState(0);
  const [strategy, setStrategy] = useState(() => localStorage.getItem("strategy") || "");
  const [strategies, setStrategies] = useState<string[]>([]);
  const [indicatorLabels, setIndicatorLabels] = useState<[string, string]>(["MA10", "MA30"]);
  const [indicatorOverlay, setIndicatorOverlay] = useState(true);
  const [configurableParams, setConfigurableParams] = useState<ParamDef[]>([]);
  const [timeframes, setTimeframes] = useState<string[]>([]);
  const [mode, setMode] = useState<"bar" | "tick" | "stream">("bar");
  const [tickCount, setTickCount] = useState(0);

  // Tick data uses refs instead of state to avoid re-renders on every tick.
  // The chart reads lastTickRef imperatively; tickCount syncs to state on
  // a throttled interval (every 500ms) so the UI doesn't churn.
  const speedRef = useRef(2);
  const lastTickRef = useRef<Tick | null>(null);
  const currentBarRef = useRef<Bar | null>(null);
  const tickCountRef = useRef(0);
  const [dataTime, setDataTime] = useState("");
  const tickCountSyncTimer = useRef<ReturnType<typeof setInterval>>(undefined);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttempt = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const countdownTimer = useRef<ReturnType<typeof setInterval>>(undefined);
  const connectRef = useRef<() => void>(undefined);

  // Message batching: queue all WS messages and flush to React once per
  // animation frame. At 10x speed the backend sends ~10 msgs/sec — without
  // batching each one triggers a separate React render cycle.
  const msgQueueRef = useRef<WSMessage[]>([]);
  const rafRef = useRef(0);

  const scheduleFlush = useCallback(() => {
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0;
      const msgs = msgQueueRef.current;
      if (msgs.length === 0) return;
      msgQueueRef.current = [];

      // Accumulate changes, apply once
      const newBars: Bar[] = [];
      let latestMetrics: Metrics | null = null;
      let latestEquityPoint: { time: number; value: number } | null = null;
      const opened: Trade[] = [];
      const closed: Trade[] = [];
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- runtime WS boundary
      let snapshot: Record<string, any> | null = null;

      for (const msg of msgs) {
        switch (msg.type) {
          case "BAR":
            newBars.push(msg.data as Bar);
            break;
          case "TICK":
            // Legacy single-tick message (backwards compat)
            lastTickRef.current = msg.data as Tick;
            tickCountRef.current++;
            break;
          case "TICK_BATCH": {
            // Batched ticks from server — only keep the last one for the price line
            const ticks = msg.data as Tick[];
            if (ticks.length > 0) {
              lastTickRef.current = ticks[ticks.length - 1];
              tickCountRef.current += ticks.length;
            }
            // Partial bar being built from ticks — update the current candle
            if (msg.current_bar) {
              currentBarRef.current = msg.current_bar as Bar;
            }
            break;
          }
          case "TRADE_OPEN":
            opened.push(msg.data as Trade);
            break;
          case "TRADE_CLOSE":
            closed.push(msg.data as Trade);
            break;
          case "METRICS": {
            latestMetrics = msg.data as Metrics;
            if (msg.timestamp) {
              const time = Math.floor(new Date(msg.timestamp).getTime() / 1000);
              latestEquityPoint = { time, value: latestMetrics.current_capital };
            }
            break;
          }
          case "SNAPSHOT":
            snapshot = msg.data;
            break;
        }
      }

      // Apply snapshot first (resets state)
      if (snapshot) {
        if (snapshot.status === "complete") {
          setReplayComplete(true);
        } else {
          if (snapshot.instrument) setInstrument(snapshot.instrument);
          if (snapshot.timeframe) setTimeframe(snapshot.timeframe);
          if (snapshot.total_bars) setTotalBars(snapshot.total_bars);
          if (snapshot.speed != null) {
            setSpeedState(snapshot.speed);
            speedRef.current = snapshot.speed;
            setPaused(snapshot.speed === 0);
          }
          if (snapshot.paused != null) setPaused(snapshot.paused);
          if (snapshot.metrics) setMetrics(snapshot.metrics as Metrics);
          if (snapshot.strategy) setStrategy(snapshot.strategy);
          if (snapshot.strategies) setStrategies(snapshot.strategies);
          if (snapshot.indicator_labels) setIndicatorLabels(snapshot.indicator_labels);
          if (snapshot.indicator_overlay != null) setIndicatorOverlay(snapshot.indicator_overlay as boolean);
          if (snapshot.configurable_params) setConfigurableParams(snapshot.configurable_params);
          if (snapshot.timeframes) setTimeframes(snapshot.timeframes);
          if (snapshot.mode) setMode(snapshot.mode as "bar" | "tick" | "stream");
          if (snapshot.tick_count != null) {
            tickCountRef.current = snapshot.tick_count;
            setTickCount(snapshot.tick_count);
          }
          if (snapshot.bars) {
            setBars(snapshot.bars as Bar[]);
            setBarCount(snapshot.bars.length);
          }
          if (snapshot.open_positions) {
            // Dedup by ID in case snapshot overlaps with recent TRADE_OPEN events
            const positions = snapshot.open_positions as Trade[];
            const seen = new Set<number>();
            setOpenPositions(positions.filter((p) => { if (seen.has(p.id)) return false; seen.add(p.id); return true; }));
          }
          if (snapshot.closed_positions) setClosedPositions(snapshot.closed_positions as Trade[]);
          if (snapshot.bars?.length === 0) {
            currentBarRef.current = null;
            lastTickRef.current = null;
            setEquityCurve([]);
            setReplayComplete(false);
          }
          if (snapshot.complete) setReplayComplete(true);
        }
      }

      // If a snapshot arrived, it already contains current positions —
      // skip individual trade events to avoid duplicates.
      if (snapshot) {
        opened.length = 0;
        closed.length = 0;
      }

      // Batch bar updates
      if (newBars.length > 0) {
        setBars((prev) => {
          const combined = prev.concat(newBars);
          return combined.length > MAX_BARS ? combined.slice(-MAX_BARS) : combined;
        });
        setBarCount((prev) => prev + newBars.length);
      }

      // Dedup within batch (same trade ID can arrive twice if broadcast
      // overlaps with snapshot across animation frames)
      const dedupById = (arr: Trade[]): Trade[] => {
        const seen = new Set<number>();
        return arr.filter((t) => { if (seen.has(t.id)) return false; seen.add(t.id); return true; });
      };
      const uniqueOpened = dedupById(opened);
      const uniqueClosed = dedupById(closed);

      // Batch position updates (dedup within batch + against existing state)
      const closedIds = new Set(uniqueClosed.map((t) => t.id));
      if (uniqueOpened.length > 0 || uniqueClosed.length > 0) {
        setOpenPositions((prev) => {
          const ids = new Set(prev.map((p) => p.id));
          const fresh = uniqueOpened.filter((t) => !ids.has(t.id) && !closedIds.has(t.id));
          const afterClose = [...prev, ...fresh].filter((p) => !closedIds.has(p.id));
          return afterClose;
        });
      }
      if (uniqueClosed.length > 0) {
        setClosedPositions((prev) => {
          const ids = new Set(prev.map((p) => p.id));
          const fresh = uniqueClosed.filter((t) => !ids.has(t.id));
          return [...fresh, ...prev].slice(0, MAX_CLOSED_TRADES);
        });
      }

      // Apply latest metrics (only need the most recent one per frame)
      if (latestMetrics) setMetrics(latestMetrics);
      if (latestEquityPoint) {
        setEquityCurve((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.time === latestEquityPoint!.time) return prev;
          const next = [...prev, latestEquityPoint!];
          return next.length > MAX_EQUITY_POINTS ? next.slice(-MAX_EQUITY_POINTS) : next;
        });
      }
    });
  }, []);

  // Derive HTTP health URL from WebSocket URL (ws://host/ws/stream → http://host/api/health)
  const healthUrl = baseUrl.replace(/^ws(s?):\/\//, "http$1://").replace(/\/ws\/.*$/, "/api/health");

  const checkServerHealth = useCallback(async (): Promise<boolean> => {
    try {
      const resp = await fetch(healthUrl, { signal: AbortSignal.timeout(5000) });
      return resp.ok;
    } catch {
      return false;
    }
  }, [healthUrl]);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(baseUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("connected");
      setReconnectIn(0);
      clearInterval(countdownTimer.current);
      reconnectAttempt.current = 0;
    };

    ws.onmessage = (event) => {
      const msg: WSMessage = JSON.parse(event.data);
      if (msg.type === "HEARTBEAT") return;
      msgQueueRef.current.push(msg);
      scheduleFlush();
    };

    ws.onclose = () => {
      wsRef.current = null;

      // Check if server is cold-starting (Render free tier sleeps after 15min)
      checkServerHealth().then((alive) => {
        setStatus(alive ? "disconnected" : "waking_up");
      });

      // Exponential backoff reconnect with countdown
      const delay = Math.min(
        1000 * 2 ** reconnectAttempt.current,
        MAX_RECONNECT_DELAY
      );
      reconnectAttempt.current++;

      let remaining = Math.ceil(delay / 1000);
      setReconnectIn(remaining);
      clearInterval(countdownTimer.current);
      countdownTimer.current = setInterval(() => {
        remaining--;
        setReconnectIn(Math.max(0, remaining));
        if (remaining <= 0) clearInterval(countdownTimer.current);
      }, 1000);

      reconnectTimer.current = setTimeout(() => connectRef.current?.(), delay);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [baseUrl, checkServerHealth, scheduleFlush]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  useEffect(() => {
    connect();
    // Sync tick count every 500ms (avoids per-tick re-renders)
    tickCountSyncTimer.current = setInterval(() => {
      setTickCount((prev) => (prev === tickCountRef.current ? prev : tickCountRef.current));
    }, 500);

    // Smooth data clock: syncs once on first tick, then advances at speed rate.
    // Only hard-resyncs if clock drifts >30s from the latest tick timestamp
    // (e.g. after a pause/resume or speed change).
    let clockMs = 0;
    let synced = false;
    let lastTickTs: string | null = null;
    const DRIFT_THRESHOLD = 30_000; // 30s
    const clockTimer = setInterval(() => {
      const tick = lastTickRef.current;
      if (tick && tick.timestamp !== lastTickTs) {
        // Ensure UTC parsing — datalake timestamps are UTC but may lack Z suffix
        const tsStr = tick.timestamp.endsWith("Z") ? tick.timestamp : tick.timestamp + "Z";
        const tickMs = new Date(tsStr).getTime();
        lastTickTs = tick.timestamp;
        if (!synced) {
          // First tick — initialize clock
          clockMs = tickMs;
          synced = true;
        } else if (Math.abs(tickMs - clockMs) > DRIFT_THRESHOLD) {
          // Drifted too far — hard resync
          clockMs = tickMs;
        }
        // Otherwise ignore — let the clock free-run smoothly
      }
      if (synced) {
        const spd = speedRef.current;
        clockMs += 1000 * (spd > 0 ? spd : 1);
        const d = new Date(clockMs);
        const hh = String(d.getUTCHours()).padStart(2, "0");
        const mm = String(d.getUTCMinutes()).padStart(2, "0");
        const ss = String(d.getUTCSeconds()).padStart(2, "0");
        const t = `${hh}:${mm}:${ss}`;
        setDataTime((prev) => (prev === t ? prev : t));
      }
    }, 1000);
    return () => {
      clearTimeout(reconnectTimer.current);
      clearInterval(countdownTimer.current);
      clearInterval(tickCountSyncTimer.current);
      clearInterval(clockTimer);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      msgQueueRef.current = [];
      wsRef.current?.close();
    };
  }, [connect]);

  // Derive API base from WS URL (ws://host/ws/stream → http://host)
  const apiBase = baseUrl.replace(/^ws(s?):\/\//, "http$1://").replace(/\/ws\/.*$/, "");

  const togglePause = useCallback(() => {
    fetch(`${apiBase}/api/pause`, { method: "POST" }).catch(() => {});
  }, [apiBase]);

  const setSpeed = useCallback(
    (newSpeed: number) => {
      setSpeedState(newSpeed);
      speedRef.current = newSpeed;
      setPaused(false);
      fetch(`${apiBase}/api/speed`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ speed: newSpeed }),
      }).catch(() => {});
    },
    [apiBase]
  );

  const switchStrategy = useCallback(
    (name: string) => {
      localStorage.setItem("strategy", name);
      fetch(`${apiBase}/api/strategy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      }).catch(() => {});
    },
    [apiBase]
  );

  // On first connect, if localStorage has a saved strategy that differs from server, switch
  useEffect(() => {
    const saved = localStorage.getItem("strategy");
    if (saved && strategy && saved !== strategy && strategies.includes(saved)) {
      switchStrategy(saved);
    }
  }, [strategy, strategies, switchStrategy]);

  const switchTimeframe = useCallback(
    (tf: string) => {
      fetch(`${apiBase}/api/timeframe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ timeframe: tf }),
      }).catch(() => {});
    },
    [apiBase]
  );

  const updateParams = useCallback(
    (params: Record<string, number>) => {
      fetch(`${apiBase}/api/strategy/params`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ params }),
      }).catch(() => {});
    },
    [apiBase]
  );

  return {
    bars, openPositions, closedPositions, metrics, status, replayComplete,
    instrument, timeframe, totalBars, barCount, speed, paused, setSpeed, togglePause, equityCurve,
    reconnectIn, strategy, strategies, switchStrategy, indicatorLabels, indicatorOverlay,
    configurableParams, updateParams, lastTickRef, currentBarRef, mode, tickCount,
    timeframes, switchTimeframe, dataTime,
  };
}
