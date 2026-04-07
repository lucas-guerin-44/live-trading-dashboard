import { useState } from "react";
import type { Metrics, Trade } from "../types";
import type { EquityPoint } from "../hooks/useWebSocket";
import EquityCurve from "./EquityCurve";
import MetricsPanel from "./MetricsPanel";
import TradeLog from "./TradeLog";

const SPEED_OPTIONS = [1, 2, 5, 10];

type Tab = "equity" | "performance" | "trades";

interface BottomPanelProps {
  metrics: Metrics;
  equityCurve: EquityPoint[];
  closedPositions: Trade[];
  speed: number;
  paused: boolean;
  onSpeedChange: (speed: number) => void;
  onTogglePause: () => void;
}

export default function BottomPanel({ metrics, equityCurve, closedPositions, speed, paused, onSpeedChange, onTogglePause }: BottomPanelProps) {
  const [tab, setTab] = useState<Tab>("equity");
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div
      className="shrink-0 flex flex-col overflow-hidden border-t border-neutral-700 transition-[height] duration-200"
      style={{ height: collapsed ? "38px" : "270px" }}
    >
      {/* Tab bar with collapse toggle, speed + equity/P&L */}
      <div className="flex items-center justify-between border-b border-neutral-700 shrink-0">
        <div className="flex items-center">
          <button
            onClick={() => setCollapsed((v) => !v)}
            className="px-2 py-2 text-neutral-500 hover:text-neutral-200 transition-colors"
            title={collapsed ? "Expand panel" : "Collapse panel"}
          >
            <svg
              className={`w-3.5 h-3.5 transition-transform duration-200 ${collapsed ? "rotate-180" : ""}`}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>

          {(["equity", "performance", "trades"] as const).map((t) => (
            <button
              key={t}
              onClick={() => { setTab(t); setCollapsed(false); }}
              className={`px-4 py-2 text-sm font-medium transition-colors capitalize ${
                tab === t
                  ? "text-white border-b-2 border-blue-500"
                  : "text-neutral-400 hover:text-neutral-200"
              }`}
            >
              {t === "equity" ? "Equity Curve" : t}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-5 pr-4 text-sm">
          {/* Pause + Speed control */}
          <div className="flex items-center gap-1">
            <button
              onClick={onTogglePause}
              className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                paused
                  ? "bg-amber-600 text-white"
                  : "bg-neutral-700 text-neutral-400 hover:text-white"
              }`}
              title={paused ? "Resume (click or pick a speed)" : "Pause replay"}
            >
              {paused ? (
                <svg className="w-3 h-3" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 010 1.972l-11.54 6.347a1.125 1.125 0 01-1.667-.986V5.653z" />
                </svg>
              ) : (
                <svg className="w-3 h-3" viewBox="0 0 24 24" fill="currentColor">
                  <path fillRule="evenodd" d="M6.75 5.25a.75.75 0 01.75-.75H9a.75.75 0 01.75.75v13.5a.75.75 0 01-.75.75H7.5a.75.75 0 01-.75-.75V5.25zm7.5 0A.75.75 0 0115 4.5h1.5a.75.75 0 01.75.75v13.5a.75.75 0 01-.75.75H15a.75.75 0 01-.75-.75V5.25z" clipRule="evenodd" />
                </svg>
              )}
            </button>
            {SPEED_OPTIONS.map((s) => (
              <button
                key={s}
                onClick={() => onSpeedChange(s)}
                className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${
                  !paused && s === speed
                    ? "bg-blue-600 text-white"
                    : "bg-neutral-700 text-neutral-400 hover:text-white"
                }`}
              >
                {s}x
              </button>
            ))}
          </div>

          <div className="w-px h-4 bg-neutral-600" />

          <div>
            <span className="text-neutral-400">Equity </span>
            <span className="text-white font-medium tabular-nums">
              ${metrics.current_capital.toFixed(2)}
            </span>
          </div>
          <div>
            <span className="text-neutral-400">P&L </span>
            <span
              className={`font-medium tabular-nums ${
                metrics.total_pnl >= 0 ? "text-green-400" : "text-red-400"
              }`}
            >
              ${metrics.total_pnl.toFixed(2)}
            </span>
          </div>
        </div>
      </div>

      {/* Tab content */}
      {!collapsed && (
        <div className={`flex-1 min-h-0 p-3 ${tab === "equity" ? "" : "overflow-y-auto"}`}>
          {tab === "equity" ? (
            <EquityCurve data={equityCurve} initialCapital={metrics.initial_capital} />
          ) : tab === "performance" ? (
            <MetricsPanel metrics={metrics} />
          ) : (
            <TradeLog trades={closedPositions} />
          )}
        </div>
      )}
    </div>
  );
}
