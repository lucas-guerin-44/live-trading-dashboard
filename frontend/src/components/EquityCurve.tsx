import { useEffect, useRef } from "react";
import {
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  ColorType,
  type UTCTimestamp,
} from "lightweight-charts";
import type { EquityPoint } from "../hooks/useWebSocket";

interface EquityCurveProps {
  data: EquityPoint[];
  initialCapital: number;
}

export default function EquityCurve({ data, initialCapital }: EquityCurveProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#171717" },
        textColor: "#a3a3a3",
      },
      grid: {
        vertLines: { color: "#404040" },
        horzLines: { color: "#404040" },
      },
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      crosshair: { mode: 0 },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: "#404040",
      },
      autoSize: true,
    });

    const series = chart.addSeries(LineSeries, {
      color: "#3b82f6",
      lineWidth: 2,
      crosshairMarkerVisible: true,
      lastValueVisible: true,
      priceLineVisible: false,
    });

    // Add baseline at initial capital
    series.createPriceLine({
      price: initialCapital,
      color: "#737373",
      lineWidth: 1,
      lineStyle: 2, // dashed
      axisLabelVisible: false,
      title: "Initial",
    });

    chartRef.current = chart;
    seriesRef.current = series;

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [initialCapital]);

  useEffect(() => {
    if (!seriesRef.current || data.length === 0) return;

    const lineData = data.map((p) => ({
      time: p.time as UTCTimestamp,
      value: p.value,
    }));

    seriesRef.current.setData(lineData);

    // Color the line based on profit/loss
    const lastValue = data[data.length - 1].value;
    const color = lastValue >= initialCapital ? "#22c55e" : "#ef4444";
    seriesRef.current.applyOptions({ color });
  }, [data, initialCapital]);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />
      {data.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center text-neutral-500 text-sm">
          Equity curve will appear as trades execute...
        </div>
      )}
    </div>
  );
}
