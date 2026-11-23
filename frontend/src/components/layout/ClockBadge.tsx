import clsx from "clsx";
import type { WorldState } from "@/api/types";
import { simClock } from "@/lib/format";

interface ClockBadgeProps {
  state: WorldState | null;
}

export function ClockBadge({ state }: ClockBadgeProps) {
  const clockText =
    state?.clock_label ||
    (state?.tick !== undefined ? simClock(state.tick, state.tick_minutes || 15) : "—");

  const simStatus = state?.sim_status || "UNKNOWN";
  const isRunning = simStatus === "RUNNING";

  return (
    <div
      data-testid="sim-clock"
      className="inline-flex items-center gap-2 rounded-md border border-bg-border bg-bg-raised px-3 py-1 text-sm font-medium tabular-nums text-ink"
      title={`Simulator Status: ${simStatus}`}
    >
      <span
        className={clsx(
          "h-2 w-2 rounded-full",
          isRunning ? "bg-state-healthy" : simStatus === "PAUSED" ? "bg-state-degraded" : "bg-ink-faint"
        )}
      />
      <span>{clockText}</span>
      <span className="text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
        {simStatus}
      </span>
    </div>
  );
}
