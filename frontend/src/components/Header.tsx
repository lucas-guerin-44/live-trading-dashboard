import { useEffect, useState } from "react";
import type { ConnectionStatus } from "../types";

interface HeaderProps {
  status: ConnectionStatus;
  replayComplete: boolean;
  barCount: number;
  totalBars: number;
  reconnectIn: number;
}

const GLASS = "glass rounded-md px-2.5 py-1 whitespace-nowrap";

export default function Header({
  status,
  replayComplete,
  barCount,
  totalBars,
  reconnectIn,
}: HeaderProps) {
  const [pulse, setPulse] = useState(false);
  const [showInfo, setShowInfo] = useState(false);

  useEffect(() => {
    if (status !== "connected") return;
    const id = setInterval(() => {
      setPulse(true);
      setTimeout(() => setPulse(false), 1000);
    }, 5000);
    return () => clearInterval(id);
  }, [status]);

  const alwaysPulse = status === "connecting" || status === "waking_up";
  const dotColor = {
    connected: "bg-green-500",
    connecting: "bg-yellow-500",
    disconnected: "bg-red-500",
    waking_up: "bg-orange-500",
  }[status];

  const statusLabel = replayComplete
    ? "Replay Complete"
    : status === "waking_up"
      ? `Waking up server${reconnectIn > 0 ? ` (${reconnectIn}s)` : "..."}`
      : status === "disconnected" && reconnectIn > 0
        ? `Reconnecting in ${reconnectIn}s`
        : status;
  const progressPct = totalBars > 0 ? (barCount / totalBars) * 100 : 0;

  const alwaysShowLabel = status !== "connected";

  return (
    <>
      <div className="absolute top-3 left-0 right-0 z-10 flex justify-center pointer-events-none">
        <div className={`pointer-events-auto flex items-center gap-3 glass rounded-lg px-4 py-2`}>
          <h1 className="text-sm font-bold text-white tracking-tight">
            Replay
          </h1>
          <div className="w-px h-4 bg-neutral-600" />

          {/* Status dot + tooltip */}
          <div className="group relative flex items-center">
            <div className={`w-2 h-2 rounded-full ${dotColor} ${alwaysPulse || pulse ? "animate-pulse" : ""}`} />
            {alwaysShowLabel ? (
              <span className="ml-2 text-xs text-neutral-400 capitalize">{statusLabel}</span>
            ) : (
              <div className="absolute left-1/2 -translate-x-1/2 top-full mt-2 opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none">
                <div className={GLASS}>
                  <span className="text-xs text-neutral-300 capitalize">{statusLabel}</span>
                </div>
              </div>
            )}
          </div>

          {/* Progress bar + tooltip for count */}
          {totalBars > 0 && (
            <div className="group relative flex items-center">
              <div className="w-20 h-1.5 bg-neutral-700 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-500 rounded-full transition-all duration-300"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
              <div className="absolute left-1/2 -translate-x-1/2 top-full mt-2 opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none">
                <div className={GLASS}>
                  <span className="text-xs text-neutral-300 tabular-nums">
                    {barCount.toLocaleString()} / {totalBars.toLocaleString()}
                  </span>
                </div>
              </div>
            </div>
          )}

          <div className="w-px h-4 bg-neutral-600" />

          {/* Info button */}
          <button
            onClick={() => setShowInfo(true)}
            className="text-neutral-400 hover:text-white transition-colors"
            title="About this dashboard"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9 5.25h.008v.008H12v-.008z" />
            </svg>
          </button>
        </div>
      </div>

      {/* Info modal */}
      {showInfo && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={() => setShowInfo(false)}
        >
          <div
            className="glass rounded-xl p-6 max-w-md mx-4 border border-neutral-700/50"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-white">About this Dashboard</h2>
              <button
                onClick={() => setShowInfo(false)}
                className="text-neutral-400 hover:text-white transition-colors"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="space-y-3 text-sm text-neutral-300">
              <p>
                This is a <span className="text-white font-medium">historical replay</span>, not a live trading system. It replays
                pre-recorded XAUUSD M15 data through configurable trading strategies.
              </p>
              <p>
                The strategies included are deliberately simple, the point is the
                dashboard and infrastructure, not the alpha.
              </p>
              <p className="text-neutral-400 text-xs border-t border-neutral-700 pt-3">
                All viewers share the same replay feed. Changing strategy or
                parameters restarts the replay for everyone. Built
                with FastAPI, React 19, and lightweight-charts.
              </p>
              <a
                href="https://github.com/lucas-guerin-44/live-trading-dashboard"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-xs text-blue-400 hover:text-blue-300 transition-colors"
              >
                <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="currentColor">
                  <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
                </svg>
                View source on GitHub
              </a>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
