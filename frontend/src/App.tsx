import { useWebSocket } from "./hooks/useWebSocket";
import Header from "./components/Header";
import Chart from "./components/Chart";
import BottomPanel from "./components/BottomPanel";
import Sidebar from "./components/Sidebar";

const WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8080/ws/stream";

function App() {
  const {
    bars, openPositions, closedPositions, metrics, status, replayComplete,
    instrument, timeframe, totalBars, barCount, speed, paused, setSpeed, togglePause,
    equityCurve, reconnectIn, strategy, strategies, switchStrategy, indicatorLabels, indicatorOverlay,
    configurableParams, updateParams, lastTickRef, currentBarRef, mode, tickCount,
    timeframes, switchTimeframe, dataTime,
  } = useWebSocket(WS_URL);

  return (
    <div className="h-screen flex flex-col bg-neutral-900 text-white overflow-hidden">
      <div className="flex-1 flex flex-col min-h-0 w-full">
        {/* Top: Chart + Sidebar */}
        <div className="flex-1 flex min-h-0">
          {/* Chart with floating header */}
          <div className="flex-1 min-w-0 relative">
            <Header
              status={status}
              replayComplete={replayComplete}
              barCount={barCount}
              totalBars={totalBars}
              reconnectIn={reconnectIn}
              mode={mode}
              dataTime={dataTime}
            />
            <Chart
              bars={bars}
              openPositions={openPositions}
              closedPositions={closedPositions}
              instrument={instrument}
              timeframe={timeframe}
              indicatorLabels={indicatorLabels}
              indicatorOverlay={indicatorOverlay}
              lastTickRef={lastTickRef}
              currentBarRef={currentBarRef}
              mode={mode}
              timeframes={timeframes}
              onTimeframeChange={switchTimeframe}
            />
          </div>

          {/* Sidebar */}
          <Sidebar
            positions={openPositions}
            lastBar={bars.length > 0 ? bars[bars.length - 1] : null}
            lastTickRef={lastTickRef}
            strategy={strategy}
            strategies={strategies}
            onStrategyChange={switchStrategy}
            configurableParams={configurableParams}
            onUpdateParams={updateParams}
          />
        </div>

        {/* Bottom panel */}
        <BottomPanel
          metrics={metrics}
          equityCurve={equityCurve}
          closedPositions={closedPositions}
          speed={speed}
          paused={paused}
          onSpeedChange={setSpeed}
          onTogglePause={togglePause}
        />
      </div>
    </div>
  );
}

export default App;
