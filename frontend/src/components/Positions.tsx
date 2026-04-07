import { useState } from "react";
import type { Bar, Trade } from "../types";
import type { ParamDef } from "../hooks/useWebSocket";
import StrategyParams from "./StrategyParams";

const STRATEGY_LABELS: Record<string, string> = {
  ma_crossover: "MA Crossover",
  mean_reversion: "Mean Reversion",
  momentum: "RSI + ADX Momentum",
};

interface PositionsProps {
  positions: Trade[];
  lastBar: Bar | null;
  strategy: string;
  strategies: string[];
  onStrategyChange: (name: string) => void;
  configurableParams: ParamDef[];
  onUpdateParams: (params: Record<string, number>) => void;
}

export default function Positions({ positions, lastBar, strategy, strategies, onStrategyChange, configurableParams, onUpdateParams }: PositionsProps) {
  const [editingParams, setEditingParams] = useState(false);

  return (
    <div className="h-full flex flex-col">
      {/* Strategy selector header */}
      <div className="px-3 py-2 border-b border-neutral-700 shrink-0">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-neutral-200">Strategy</h2>
          <div className="flex items-center gap-2">
            {strategies.length > 1 ? (
              <select
                value={strategy}
                onChange={(e) => { onStrategyChange(e.target.value); setEditingParams(false); }}
                className="bg-neutral-800 text-neutral-200 text-xs rounded px-2 py-1 border border-neutral-600 focus:outline-none focus:border-blue-500"
              >
                {strategies.map((s) => (
                  <option key={s} value={s}>
                    {STRATEGY_LABELS[s] || s}
                  </option>
                ))}
              </select>
            ) : (
              <span className="text-xs text-neutral-400">
                {STRATEGY_LABELS[strategy] || strategy || "-"}
              </span>
            )}
            {configurableParams.length > 0 && (
              <button
                onClick={() => setEditingParams((v) => !v)}
                className={`p-1 rounded transition-colors ${
                  editingParams
                    ? "text-blue-400 bg-blue-500/10"
                    : "text-neutral-400 hover:text-white"
                }`}
                title="Tune parameters"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0115.75 21H5.25A2.25 2.25 0 013 18.75V8.25A2.25 2.25 0 015.25 6H10" />
                </svg>
              </button>
            )}
          </div>
        </div>
      </div>

      {editingParams ? (
        <StrategyParams
          params={configurableParams}
          onUpdate={onUpdateParams}
          onClose={() => setEditingParams(false)}
        />
      ) : (
      /* Open Positions */
      <div className="flex-1 flex flex-col p-3 min-h-0">
        <h2 className="text-sm font-semibold mb-2 text-neutral-200 shrink-0">
          Open Positions
        </h2>

        {positions.length === 0 ? (
          <p className="text-neutral-500 text-sm">No open positions</p>
        ) : (
          <div className="space-y-2 overflow-y-auto min-h-0 flex-1">
            {positions.map((pos) => {
              const currentPrice = lastBar?.close ?? pos.entry_price;
              const unrealizedPnl =
                pos.side === "BUY"
                  ? currentPrice - pos.entry_price
                  : pos.entry_price - currentPrice;
              const unrealizedPct = (unrealizedPnl / pos.entry_price) * 100;

              return (
                <div
                  key={pos.id}
                  className="bg-neutral-750 rounded px-3 py-2 border border-neutral-700"
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-3">
                      <span
                        className={`text-xs font-bold px-2 py-0.5 rounded ${
                          pos.side === "BUY"
                            ? "bg-green-900 text-green-400"
                            : "bg-red-900 text-red-400"
                        }`}
                      >
                        {pos.side}
                      </span>
                      <span className="text-sm text-neutral-300">
                        @ {pos.entry_price.toFixed(2)}
                      </span>
                    </div>
                    <span className="text-xs text-neutral-400">
                      {new Date(pos.entry_time).toLocaleTimeString()}
                    </span>
                  </div>

                  {pos.signal_reason && (
                    <div className="text-xs text-amber-400 mb-1.5">
                      Signal: {pos.signal_reason}
                    </div>
                  )}

                  <div className="flex items-center gap-4 text-xs mb-1.5">
                    {pos.stop_loss_price != null && (
                      <span className="text-red-400">
                        SL: {pos.stop_loss_price.toFixed(2)}
                      </span>
                    )}
                    {pos.take_profit_price != null ? (
                      <span className="text-green-400">
                        TP: {pos.take_profit_price.toFixed(2)}
                      </span>
                    ) : (
                      <span className="text-violet-400">
                        Exit: MA crossover
                      </span>
                    )}
                  </div>

                  <div className="flex items-center justify-between text-xs">
                    <span className="text-neutral-500">Unrealized P&L</span>
                    <span
                      className={
                        unrealizedPnl >= 0 ? "text-green-400" : "text-red-400"
                      }
                    >
                      {unrealizedPnl >= 0 ? "+" : ""}
                      {unrealizedPnl.toFixed(2)} ({unrealizedPct >= 0 ? "+" : ""}
                      {unrealizedPct.toFixed(2)}%)
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
      )}
    </div>
  );
}
