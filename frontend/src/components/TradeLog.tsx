import type { Trade } from "../types";

interface TradeLogProps {
  trades: Trade[];
}

function downloadCsv(trades: Trade[]) {
  const header = "id,instrument,side,entry_price,entry_time,exit_price,exit_time,pnl,pnl_pct,exit_reason\n";
  const rows = trades.map((t) =>
    [t.id, t.instrument, t.side, t.entry_price, t.entry_time, t.exit_price, t.exit_time, t.pnl, t.pnl_pct, t.exit_reason].join(",")
  ).join("\n");
  const blob = new Blob([header + rows], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "trades.csv";
  a.click();
  URL.revokeObjectURL(url);
}

export default function TradeLog({ trades }: TradeLogProps) {
  return (
    <div>
      {trades.length === 0 ? (
        <p className="text-neutral-500 text-sm">No trades yet</p>
      ) : (
        <div className="overflow-x-auto">
          <div className="flex justify-end mb-2">
            <button
              onClick={() => downloadCsv(trades)}
              className="text-xs px-2 py-1 rounded bg-neutral-700 text-neutral-300 hover:text-white transition-colors"
            >
              Export CSV
            </button>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-700 text-neutral-400">
                <th className="text-left py-1.5 px-3 font-medium">Time</th>
                <th className="text-left py-1.5 px-3 font-medium">Side</th>
                <th className="text-right py-1.5 px-3 font-medium">Entry</th>
                <th className="text-right py-1.5 px-3 font-medium">Exit</th>
                <th className="text-right py-1.5 px-3 font-medium">P&L</th>
                <th className="text-right py-1.5 px-3 font-medium">%</th>
                <th className="text-left py-1.5 px-3 font-medium">Signal</th>
                <th className="text-left py-1.5 px-3 font-medium">Exit</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((trade) => (
                <tr
                  key={trade.id}
                  className="border-b border-neutral-700/50 hover:bg-neutral-700/30"
                >
                  <td className="py-1.5 px-3 text-neutral-400">
                    {trade.exit_time
                      ? new Date(trade.exit_time).toLocaleTimeString()
                      : "-"}
                  </td>
                  <td className="py-1.5 px-3">
                    <span
                      className={
                        trade.side === "BUY"
                          ? "text-green-400"
                          : "text-red-400"
                      }
                    >
                      {trade.side}
                    </span>
                  </td>
                  <td className="py-1.5 px-3 text-right text-neutral-300">
                    {trade.entry_price.toFixed(2)}
                  </td>
                  <td className="py-1.5 px-3 text-right text-neutral-300">
                    {trade.exit_price?.toFixed(2) ?? "-"}
                  </td>
                  <td
                    className={`py-1.5 px-3 text-right font-medium ${
                      trade.pnl >= 0 ? "text-green-400" : "text-red-400"
                    }`}
                  >
                    ${trade.pnl.toFixed(2)}
                  </td>
                  <td
                    className={`py-1.5 px-3 text-right ${
                      trade.pnl_pct >= 0 ? "text-green-400" : "text-red-400"
                    }`}
                  >
                    {trade.pnl_pct.toFixed(2)}%
                  </td>
                  <td className="py-1.5 px-3 text-amber-400 text-xs">
                    {trade.signal_reason ?? "-"}
                  </td>
                  <td className="py-1.5 px-3 text-neutral-400">
                    {trade.exit_reason ?? "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
