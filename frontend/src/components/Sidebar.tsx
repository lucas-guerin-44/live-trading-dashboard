import { useState } from "react";
import type { Bar, Tick, Trade } from "../types";
import type { ParamDef } from "../hooks/useWebSocket";
import Positions from "./Positions";

interface SidebarProps {
  positions: Trade[];
  lastBar: Bar | null;
  lastTickRef: React.RefObject<Tick | null>;
  strategy: string;
  strategies: string[];
  onStrategyChange: (name: string) => void;
  configurableParams: ParamDef[];
  onUpdateParams: (params: Record<string, number>) => void;
}

export default function Sidebar(props: SidebarProps) {
  const [open, setOpen] = useState(true);

  return (
    <div className={`relative shrink-0 hidden lg:flex ${open ? "w-80 xl:w-96" : "w-0"}`}>
      {/* Toggle tab on the left edge */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="absolute top-1/2 -translate-y-1/2 -left-5 z-20 flex items-center justify-center w-5 h-10 bg-neutral-800 border border-neutral-600 border-r-0 rounded-l text-neutral-400 hover:text-white hover:bg-neutral-700 transition-colors"
        title={open ? "Hide sidebar" : "Show sidebar"}
      >
        <svg
          className={`w-3 h-3 transition-transform duration-200 ${open ? "" : "rotate-180"}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="9 6 15 12 9 18" />
        </svg>
      </button>

      {open && (
        <div className="w-full border-l border-neutral-700 overflow-hidden">
          <Positions {...props} />
        </div>
      )}
    </div>
  );
}
