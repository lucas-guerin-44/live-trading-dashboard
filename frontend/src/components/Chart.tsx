import { useEffect, useRef, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  ColorType,
  type CandlestickData,
  type LineData,
  type UTCTimestamp,
  type ISeriesMarkersPluginApi,
} from "lightweight-charts";
import type { Bar, Trade } from "../types";

interface ChartProps {
  bars: Bar[];
  openPositions: Trade[];
  closedPositions: Trade[];
  instrument: string;
  timeframe: string;
  indicatorLabels: [string, string];
  indicatorOverlay: boolean;
}

function toChartTime(ts: string): UTCTimestamp {
  return (new Date(ts).getTime() / 1000) as UTCTimestamp;
}

const CHART_OPTS = {
  layout: {
    background: { type: ColorType.Solid as const, color: "#171717" },
    textColor: "#a3a3a3",
  },
  grid: {
    vertLines: { color: "rgba(64, 64, 64, 0.35)" },
    horzLines: { color: "rgba(64, 64, 64, 0.35)" },
  },
  crosshair: { mode: 0 as const },
  timeScale: {
    timeVisible: true,
    secondsVisible: false,
    rightOffset: 12,
  },
  autoSize: true,
};

export default function Chart({
  bars,
  openPositions,
  closedPositions,
  instrument,
  timeframe,
  indicatorLabels,
  indicatorOverlay,
}: ChartProps) {
  const [showLabels, setShowLabels] = useState(true);
  const oscillator = !indicatorOverlay;

  // Main chart refs
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const fastMaRef = useRef<ISeriesApi<"Line"> | null>(null);
  const slowMaRef = useRef<ISeriesApi<"Line"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<UTCTimestamp> | null>(null);
  const userScrolledRef = useRef(false);
  const prevBarCountRef = useRef(0);
  const updatesSinceResetRef = useRef(0);

  // Indicator sub-chart refs (oscillator mode)
  const indContainerRef = useRef<HTMLDivElement>(null);
  const indChartRef = useRef<IChartApi | null>(null);
  const indFastRef = useRef<ISeriesApi<"Line"> | null>(null);
  const indSlowRef = useRef<ISeriesApi<"Line"> | null>(null);

  // ── Create main chart once ──────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, { ...CHART_OPTS });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderDownColor: "#ef4444",
      borderUpColor: "#22c55e",
      wickDownColor: "#ef4444",
      wickUpColor: "#22c55e",
    });

    chartRef.current = chart;
    seriesRef.current = series;
    markersRef.current = createSeriesMarkers(series) as ISeriesMarkersPluginApi<UTCTimestamp>;

    const el = containerRef.current;
    el.addEventListener("mousedown", () => { userScrolledRef.current = true; });
    el.addEventListener("wheel", () => { userScrolledRef.current = true; });
    el.addEventListener("dblclick", () => {
      userScrolledRef.current = false;
      chart.timeScale().scrollToRealTime();
      indChartRef.current?.timeScale().scrollToRealTime();
    });

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      fastMaRef.current = null;
      slowMaRef.current = null;
    };
  }, []);

  // ── Toggle overlay vs oscillator indicator series ───────────────────────
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    try {
      // Remove old overlay series
      if (fastMaRef.current) { chart.removeSeries(fastMaRef.current); fastMaRef.current = null; }
      if (slowMaRef.current) { chart.removeSeries(slowMaRef.current); slowMaRef.current = null; }

      // Hide time axis on main chart when indicator pane shows it
      chart.applyOptions({ timeScale: { visible: !oscillator } });

      if (!oscillator) {
        fastMaRef.current = chart.addSeries(LineSeries, {
          color: "#f59e0b", lineWidth: 2, title: indicatorLabels[0],
          crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false,
        });
        slowMaRef.current = chart.addSeries(LineSeries, {
          color: "#8b5cf6", lineWidth: 2, title: indicatorLabels[1],
          crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false,
        });
      }
    } catch {
      // Chart may be disposed
    }

    prevBarCountRef.current = 0; // force full re-render
  }, [oscillator, indicatorLabels]);

  // ── Create/destroy indicator sub-chart ──────────────────────────────────
  useEffect(() => {
    if (!oscillator) {
      if (indChartRef.current) {
        indChartRef.current.remove();
        indChartRef.current = null;
        indFastRef.current = null;
        indSlowRef.current = null;
      }
      return;
    }
    if (!indContainerRef.current) return;

    const indChart = createChart(indContainerRef.current, {
      ...CHART_OPTS,
      grid: { ...CHART_OPTS.grid, horzLines: { color: "rgba(64, 64, 64, 0.2)" } },
      rightPriceScale: { scaleMargins: { top: 0.05, bottom: 0.05 } },
    });

    indFastRef.current = indChart.addSeries(LineSeries, {
      color: "#f59e0b", lineWidth: 2, title: indicatorLabels[0],
      crosshairMarkerVisible: false, lastValueVisible: true, priceLineVisible: false,
    });
    indSlowRef.current = indChart.addSeries(LineSeries, {
      color: "#8b5cf6", lineWidth: 2, title: indicatorLabels[1],
      crosshairMarkerVisible: false, lastValueVisible: true, priceLineVisible: false,
    });

    indChartRef.current = indChart;

    // Sync scrolling: main ↔ indicator via logical range with guard.
    // Use a disposed flag so callbacks become no-ops after cleanup.
    let disposed = false;
    let syncing = false;
    const mainTs = chartRef.current?.timeScale();
    if (mainTs) {
      mainTs.subscribeVisibleLogicalRangeChange((range) => {
        if (disposed || syncing || !range) return;
        syncing = true;
        try { indChart.timeScale().setVisibleLogicalRange(range); } catch { /* */ }
        syncing = false;
      });
      indChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (disposed || syncing || !range) return;
        syncing = true;
        try { mainTs.setVisibleLogicalRange(range); } catch { /* */ }
        syncing = false;
      });
    }

    // Scroll events on indicator pane
    const el = indContainerRef.current;
    el.addEventListener("mousedown", () => { userScrolledRef.current = true; });
    el.addEventListener("wheel", () => { userScrolledRef.current = true; });
    el.addEventListener("dblclick", () => {
      if (disposed) return;
      userScrolledRef.current = false;
      indChart.timeScale().scrollToRealTime();
      chartRef.current?.timeScale().scrollToRealTime();
    });

    prevBarCountRef.current = 0;

    return () => {
      disposed = true;
      indChart.remove();
      indChartRef.current = null;
      indFastRef.current = null;
      indSlowRef.current = null;
    };
  }, [oscillator, indicatorLabels]);

  // ── Stream bars ─────────────────────────────────────────────────────────
  useEffect(() => {
    if (!seriesRef.current || !chartRef.current || bars.length === 0) return;

    try {
      const prevCount = prevBarCountRef.current;
      const isInitialLoad = prevCount === 0;
      const needsFullReset = bars.length < prevCount;

      const fastSeries = oscillator ? indFastRef.current : fastMaRef.current;
      const slowSeries = oscillator ? indSlowRef.current : slowMaRef.current;

      // Periodic full reset: lightweight-charts accumulates all update() calls
      // internally even though React state is capped. Reset every 500 updates
      // to prevent unbounded chart memory growth.
      const needsPeriodicReset = updatesSinceResetRef.current >= 500;

      if (isInitialLoad || needsFullReset || needsPeriodicReset) {
        updatesSinceResetRef.current = 0;
        const seen = new Set<number>();
        const data: CandlestickData[] = [];
        const fastData: LineData[] = [];
        const slowData: LineData[] = [];

        for (const bar of bars) {
          const time = toChartTime(bar.timestamp);
          if (seen.has(time as number)) continue;
          seen.add(time as number);
          data.push({ time, open: bar.open, high: bar.high, low: bar.low, close: bar.close });
          if (oscillator) {
            fastData.push(bar.fast_ma != null ? { time, value: bar.fast_ma } : { time } as LineData);
            slowData.push(bar.slow_ma != null ? { time, value: bar.slow_ma } : { time } as LineData);
          } else {
            if (bar.fast_ma != null) fastData.push({ time, value: bar.fast_ma });
            if (bar.slow_ma != null) slowData.push({ time, value: bar.slow_ma });
          }
        }
        data.sort((a, b) => (a.time as number) - (b.time as number));
        fastData.sort((a, b) => (a.time as number) - (b.time as number));
        slowData.sort((a, b) => (a.time as number) - (b.time as number));

        seriesRef.current.setData(data);
        fastSeries?.setData(fastData);
        slowSeries?.setData(slowData);
      } else {
        updatesSinceResetRef.current++;
        const bar = bars[bars.length - 1];
        const time = toChartTime(bar.timestamp);
        seriesRef.current.update({ time, open: bar.open, high: bar.high, low: bar.low, close: bar.close });
        if (bar.fast_ma != null) fastSeries?.update({ time, value: bar.fast_ma });
        if (bar.slow_ma != null) slowSeries?.update({ time, value: bar.slow_ma });
      }

      prevBarCountRef.current = bars.length;

      if (!userScrolledRef.current) {
        chartRef.current.timeScale().scrollToRealTime();
        indChartRef.current?.timeScale().scrollToRealTime();
      }
    } catch {
      // Chart may be disposed during strategy switch or reconnect
    }
  }, [bars, oscillator]);

  // ── Markers ─────────────────────────────────────────────────────────────
  // Only rebuild when positions change or labels toggled — NOT on every bar.
  useEffect(() => {
    if (!markersRef.current) return;

    try {
      if (!showLabels) {
        markersRef.current.setMarkers([]);
        return;
      }

      // Dedup by trade ID — a trade can briefly appear in both arrays
      // during state transitions, or the same array can contain duplicates
      // if a broadcast + snapshot overlap between animation frames.
      const seenIds = new Set<number>();
      const allPositions = [...openPositions, ...closedPositions].filter((p) => {
        if (seenIds.has(p.id)) return false;
        seenIds.add(p.id);
        return true;
      });

      const entryMarkers = allPositions
        .map((pos) => ({
          time: toChartTime(pos.entry_time),
          position: pos.side === "BUY" ? ("belowBar" as const) : ("aboveBar" as const),
          color: pos.side === "BUY" ? "#22c55e" : "#ef4444",
          shape: pos.side === "BUY" ? ("arrowUp" as const) : ("arrowDown" as const),
          text: pos.side,
        }));

      const exitIds = new Set<number>();
      const exitMarkers = closedPositions
        .filter((t) => {
          if (!t.exit_time || !t.exit_price) return false;
          if (exitIds.has(t.id)) return false;
          exitIds.add(t.id);
          return true;
        })
        .map((t) => ({
          time: toChartTime(t.exit_time!),
          position: t.side === "BUY" ? ("aboveBar" as const) : ("belowBar" as const),
          color: t.pnl >= 0 ? "#22c55e" : "#ef4444",
          shape: "circle" as const,
          text: `${t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(0)}`,
        }));

      markersRef.current.setMarkers(
        [...entryMarkers, ...exitMarkers].sort((a, b) => (a.time as number) - (b.time as number))
      );
    } catch {
      // Chart may be disposed during strategy switch or reconnect
    }
  }, [openPositions, closedPositions, showLabels]);

  // ── Render ──────────────────────────────────────────────────────────────
  return (
    <div className="h-full flex flex-col p-3">
      <div className="flex items-center justify-between mb-1 shrink-0">
        <h2 className="text-sm font-semibold text-neutral-200">
          {instrument && timeframe ? `${instrument} ${timeframe}` : "Live Chart"}
          {!oscillator && (
            <span className="ml-3 text-xs font-normal">
              <span className="inline-block w-3 h-0.5 bg-amber-500 mr-1 align-middle"></span>
              <span className="text-neutral-400 mr-3">{indicatorLabels[0]}</span>
              <span className="inline-block w-3 h-0.5 bg-violet-500 mr-1 align-middle"></span>
              <span className="text-neutral-400">{indicatorLabels[1]}</span>
            </span>
          )}
        </h2>
        <button
          onClick={() => setShowLabels((v) => !v)}
          className={`text-xs px-2 py-0.5 rounded border transition-colors ${
            showLabels
              ? "border-blue-500 text-blue-400 bg-blue-500/10"
              : "border-neutral-600 text-neutral-500 bg-transparent"
          }`}
        >
          Executions
        </button>
      </div>

      {/* Main price chart */}
      <div className={`relative min-h-0 ${oscillator ? "flex-3" : "flex-1"}`}>
        <div ref={containerRef} className="h-full w-full" />
        {bars.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center bg-neutral-900/80">
            <div className="flex items-center gap-3 text-neutral-400">
              <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              <span className="text-sm">Waiting for market data...</span>
            </div>
          </div>
        )}
      </div>

      {/* Oscillator indicator sub-chart */}
      {oscillator && (
        <div className="flex-1 min-h-0 border-t border-neutral-700 pt-1 mt-1">
          <div className="flex items-center gap-3 mb-0.5 shrink-0">
            <span className="text-xs font-normal">
              <span className="inline-block w-3 h-0.5 bg-amber-500 mr-1 align-middle"></span>
              <span className="text-neutral-400 mr-3">{indicatorLabels[0]}</span>
              <span className="inline-block w-3 h-0.5 bg-violet-500 mr-1 align-middle"></span>
              <span className="text-neutral-400">{indicatorLabels[1]}</span>
            </span>
          </div>
          <div ref={indContainerRef} className="h-[calc(100%-1rem)] w-full" />
        </div>
      )}
    </div>
  );
}
