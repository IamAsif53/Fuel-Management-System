import clsx from "clsx";
import type { WorldState } from "@/api/types";
import type { ConnectionState } from "@/hooks/useLiveState";
import { simClock } from "@/lib/format";

interface ClockBadgeProps {
  state: WorldState | null;
  connection?: ConnectionState;
}

export function ClockBadge({ state, connection }: ClockBadgeProps) {
  const isOffline = connection === "offline";
  const clockText =
    state?.clock_label ||
    (state?.tick !== undefined ? simClock(state.tick, state.tick_minutes || 15) : "—");

  const simStatus = isOffline ? "STALE" : state?.sim_status || "UNKNOWN";
  const isRunning = !isOffline && simStatus === "RUNNING";

  return (
    <div
      data-testid="sim-clock"
      className={clsx(
        "inline-flex items-center gap-2 rounded-md border px-3 py-1 text-sm font-medium tabular-nums transition-colors",
        isOffline
          ? "border-state-down/40 bg-state-down/10 text-state-down"
          : "border-bg-border bg-bg-raised text-ink"
      )}
      title={`Simulator Status: ${simStatus}`}
    >
      <span
        className={clsx(
          "h-2 w-2 rounded-full",
          isOffline
            ? "bg-state-down animate-pulse"
            : isRunning
            ? "bg-state-healthy"
            : simStatus === "PAUSED"
            ? "bg-state-degraded"
            : "bg-ink-faint"
        )}
      />
      <span>{clockText}</span>
      <span
        className={clsx(
          "text-[11px] font-semibold uppercase tracking-wider",
          isOffline ? "text-state-down" : "text-ink-muted"
        )}
      >
        {simStatus}
      </span>
    </div>
  );
}
