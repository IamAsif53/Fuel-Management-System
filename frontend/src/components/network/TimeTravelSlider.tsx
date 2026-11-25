import React from "react";
import { History, RotateCcw, Clock, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, AlertTriangle } from "lucide-react";

interface TimeTravelSliderProps {
  isReplaying: boolean;
  selectedTick: number | null;
  availableTicks: number[];
  onSelectTick: (tick: number) => void;
  onToggleReplay: (enable: boolean) => void;
  isLoading: boolean;
}

export const TimeTravelSlider: React.FC<TimeTravelSliderProps> = ({
  isReplaying,
  selectedTick,
  availableTicks,
  onSelectTick,
  onToggleReplay,
  isLoading,
}) => {
  if (availableTicks.length === 0) {
    return null;
  }

  const minTick = availableTicks[0];
  const maxTick = availableTicks[availableTicks.length - 1];
  const currentTick = selectedTick ?? maxTick;

  const handleStep = (delta: number) => {
    const currentIndex = availableTicks.indexOf(currentTick);
    if (currentIndex === -1) {
      onSelectTick(availableTicks[0]);
      return;
    }
    const nextIndex = Math.max(0, Math.min(availableTicks.length - 1, currentIndex + delta));
    onSelectTick(availableTicks[nextIndex]);
  };

  return (
    <div className="space-y-3">
      {/* Control Strip */}
      <div className="flex flex-col gap-3 rounded-lg border border-panel-border bg-panel p-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <History className={`h-4 w-4 ${isReplaying ? "text-amber-400 animate-spin" : "text-ink-muted"}`} />
          <span className="text-xs font-semibold uppercase tracking-wider text-ink">
            Simulation Time-Travel
          </span>
          <span className="rounded bg-panel-border/40 px-1.5 py-0.5 text-[10px] font-mono text-ink-muted">
            {availableTicks.length} Ticks Recorded
          </span>
        </div>

        <div className="flex items-center gap-2">
          {!isReplaying ? (
            <button
              type="button"
              onClick={() => onToggleReplay(true)}
              className="flex items-center gap-1.5 rounded-md border border-accent/40 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/20 transition"
            >
              <Clock size={14} />
              <span>Enter Replay Mode</span>
            </button>
          ) : (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => handleStep(-10)}
                title="Jump back 10 ticks"
                className="rounded p-1 text-ink-muted hover:bg-surface hover:text-ink transition"
              >
                <ChevronsLeft size={16} />
              </button>
              <button
                type="button"
                onClick={() => handleStep(-1)}
                title="Previous tick"
                className="rounded p-1 text-ink-muted hover:bg-surface hover:text-ink transition"
              >
                <ChevronLeft size={16} />
              </button>
              <span className="min-w-16 text-center font-mono text-xs font-bold text-amber-300">
                Tick {currentTick}
              </span>
              <button
                type="button"
                onClick={() => handleStep(1)}
                title="Next tick"
                className="rounded p-1 text-ink-muted hover:bg-surface hover:text-ink transition"
              >
                <ChevronRight size={16} />
              </button>
              <button
                type="button"
                onClick={() => handleStep(10)}
                title="Jump forward 10 ticks"
                className="rounded p-1 text-ink-muted hover:bg-surface hover:text-ink transition"
              >
                <ChevronsRight size={16} />
              </button>

              <button
                type="button"
                onClick={() => onToggleReplay(false)}
                className="flex items-center gap-1 rounded-md border border-panel-border bg-surface px-2.5 py-1 text-xs font-medium text-ink hover:bg-panel transition ml-2"
              >
                <RotateCcw size={12} />
                <span>Return to Live</span>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Replay Slider & Prominent Status Banner */}
      {isReplaying && (
        <div className="space-y-3 animate-fadeIn">
          {/* Historical Banner */}
          <div className="flex items-center justify-between rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-amber-200">
            <div className="flex items-center gap-2.5">
              <AlertTriangle className="h-4 w-4 text-amber-400 shrink-0" />
              <div className="text-xs">
                <span className="font-bold tracking-wide uppercase text-amber-300 mr-2">
                  Historical Replay (Tick {currentTick})
                </span>
                <span className="text-amber-200/80 hidden sm:inline">
                  Viewing historical asset telemetry and pipelines. System mutations and approvals are disabled.
                </span>
              </div>
            </div>
            <span className="rounded bg-amber-500/20 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-amber-300 shrink-0">
              Read Only
            </span>
          </div>

          {/* Slider Controls */}
          <div className="rounded-lg border border-panel-border bg-panel/70 p-3">
            <div className="flex items-center justify-between text-[11px] font-mono text-ink-muted mb-2">
              <span>Tick {minTick} (Earliest)</span>
              <span className="text-xs font-bold text-ink">
                Scrubber: Tick {currentTick} {isLoading ? "(Loading...)" : ""}
              </span>
              <span>Tick {maxTick} (Latest)</span>
            </div>

            <input
              type="range"
              min={minTick}
              max={maxTick}
              value={currentTick}
              onChange={(e) => onSelectTick(Number(e.target.value))}
              className="w-full accent-amber-500 cursor-pointer"
            />
          </div>
        </div>
      )}
    </div>
  );
};
