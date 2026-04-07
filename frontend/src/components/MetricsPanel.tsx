import type { Metrics } from "../types";

interface MetricsPanelProps {
  metrics: Metrics;
}

function Row({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: "green" | "red";
}) {
  const textColor =
    color === "green"
      ? "text-green-400"
      : color === "red"
        ? "text-red-400"
        : "text-white";

  return (
    <div className="flex justify-between items-center py-1.5">
      <span className="text-neutral-400 text-sm">{label}</span>
      <span className={`font-medium text-sm ${textColor}`}>{value}</span>
    </div>
  );
}

export default function MetricsPanel({ metrics }: MetricsPanelProps) {
  const pnlColor = metrics.total_pnl >= 0 ? "green" : "red";
  const returnColor = metrics.total_pnl_pct >= 0 ? "green" : "red";

  const fmt = (v: number | null, prefix = "", suffix = "") =>
    v != null ? `${prefix}${v.toFixed(2)}${suffix}` : "-";

  return (
    <div className="grid grid-cols-2 gap-x-8 gap-y-0">
      {/* Column 1: Core */}
      <div className="divide-y divide-neutral-700">
        <Row label="Total P&L" value={`$${metrics.total_pnl.toFixed(2)}`} color={pnlColor} />
        <Row label="Return" value={`${metrics.total_pnl_pct.toFixed(2)}%`} color={returnColor} />
        <Row label="Win Rate" value={`${metrics.win_rate.toFixed(1)}%`} />
        <Row label="Trades" value={`${metrics.total_trades}`} />
        <Row label="W / L" value={`${metrics.winning_trades} / ${metrics.losing_trades}`} />
        <Row
          label="Max Drawdown"
          value={`${metrics.max_drawdown.toFixed(2)}%`}
          color={metrics.max_drawdown > 0 ? "red" : undefined}
        />
      </div>

      {/* Column 2: Advanced */}
      <div className="divide-y divide-neutral-700">
        <Row label="Sharpe Ratio" value={fmt(metrics.sharpe_ratio)} />
        <Row
          label="Profit Factor"
          value={fmt(metrics.profit_factor)}
          color={metrics.profit_factor != null ? (metrics.profit_factor >= 1 ? "green" : "red") : undefined}
        />
        <Row label="Avg Win" value={fmt(metrics.avg_win, "$")} color="green" />
        <Row label="Avg Loss" value={fmt(metrics.avg_loss, "$")} color="red" />
        <Row label="Largest Win" value={fmt(metrics.largest_win, "$")} color="green" />
        <Row label="Largest Loss" value={fmt(metrics.largest_loss, "$")} color="red" />
      </div>
    </div>
  );
}
