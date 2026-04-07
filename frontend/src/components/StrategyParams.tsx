import { useState, useMemo, useRef } from "react";
import type { ParamDef } from "../hooks/useWebSocket";

interface StrategyParamsProps {
  params: ParamDef[];
  onUpdate: (params: Record<string, number>) => void;
  onClose: () => void;
}

// Stable key derived from param definitions - changes when strategy switches or restart updates values
function paramsKey(params: ParamDef[]): string {
  return params.map((p) => `${p.name}:${p.value}`).join(",");
}

function StrategyParamsInner({ params, onUpdate, onClose }: StrategyParamsProps) {
  const [values, setValues] = useState(() => {
    const v: Record<string, number> = {};
    for (const p of params) v[p.name] = p.value;
    return v;
  });
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const handleChange = (name: string, raw: number, param: ParamDef) => {
    const value = param.type === "int" ? Math.round(raw) : parseFloat(raw.toFixed(2));
    setValues((prev) => ({ ...prev, [name]: value }));

    // Debounce the API call so dragging a slider doesn't spam restarts
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      onUpdate({ [name]: value });
    }, 400);
  };

  if (params.length === 0) {
    return (
      <div className="p-3 text-neutral-500 text-sm">
        No configurable parameters for this strategy.
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <div className="px-3 py-2 border-b border-neutral-700 shrink-0 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-200">Parameters</h2>
        <button
          onClick={onClose}
          className="text-neutral-400 hover:text-white transition-colors"
          title="Close"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {params.map((p) => (
          <div key={p.name}>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs text-neutral-400">{p.label}</label>
              <span className="text-xs font-mono text-neutral-200 tabular-nums bg-neutral-800 px-1.5 py-0.5 rounded">
                {p.type === "int" ? values[p.name] : values[p.name]?.toFixed(1)}
              </span>
            </div>
            <input
              type="range"
              min={p.min}
              max={p.max}
              step={p.step}
              value={values[p.name] ?? p.value}
              onChange={(e) => handleChange(p.name, parseFloat(e.target.value), p)}
              className="w-full h-1.5 bg-neutral-700 rounded-full appearance-none cursor-pointer accent-blue-500
                [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3
                [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-blue-500
                [&::-webkit-slider-thumb]:hover:bg-blue-400 [&::-webkit-slider-thumb]:transition-colors"
            />
            <div className="flex justify-between text-[10px] text-neutral-600 mt-0.5">
              <span>{p.min}</span>
              <span>{p.max}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Wrapper that remounts the inner component when params change (via key),
// avoiding setState-in-effect or ref-during-render lint issues.
export default function StrategyParams(props: StrategyParamsProps) {
  const key = useMemo(() => paramsKey(props.params), [props.params]);
  return <StrategyParamsInner key={key} {...props} />;
}
