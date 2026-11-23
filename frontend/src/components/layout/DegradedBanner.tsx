import clsx from "clsx";
import { AlertTriangle, AlertOctagon } from "lucide-react";
import type { WorldState } from "@/api/types";
import type { ConnectionState } from "@/hooks/useLiveState";

interface DegradedBannerProps {
  state: WorldState | null;
  connection?: ConnectionState;
}

export function DegradedBanner({ state, connection }: DegradedBannerProps) {
  const isOffline = connection === "offline";
  const isDegraded =
    isOffline ||
    Boolean(state?.degraded) ||
    Boolean(state?.engine_mode && state.engine_mode !== "NORMAL");

  if (!isDegraded) return null;

  const isCritical =
    isOffline ||
    state?.engine_mode === "SAFE_HOLD" ||
    state?.engine_mode === "NO_FEASIBLE_ROUTES";

  let title = "SYSTEM DEGRADED";
  if (isOffline) {
    title = "DATA OFFLINE / STALE";
  } else if (isCritical) {
    title = "CRITICAL SYSTEM STATE";
  }

  const reasons = isOffline
    ? [
        state?.tick !== undefined
          ? `Backend connection lost — showing cached state from Tick ${state.tick}`
          : "Backend connection lost — unable to synchronize live telemetry",
      ]
    : state?.degraded_reasons && state.degraded_reasons.length > 0
    ? state.degraded_reasons
    : ["System operating in degraded mode"];

  return (
    <div
      data-testid="degraded-banner"
      className={clsx(
        "flex flex-wrap items-center gap-3 border-b px-6 py-2 text-xs font-medium transition-all",
        isCritical
          ? "border-state-down/40 bg-state-down/15 text-state-down"
          : "border-state-degraded/40 bg-state-degraded/15 text-state-degraded"
      )}
    >
      <div className="flex items-center gap-1.5 font-semibold">
        {isCritical ? <AlertOctagon size={16} /> : <AlertTriangle size={16} />}
        <span>{title}</span>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {reasons.map((reason, idx) => (
          <span
            key={idx}
            className={clsx(
              "rounded px-2 py-0.5 text-[11px] font-mono",
              isCritical ? "bg-state-down/20" : "bg-state-degraded/20"
            )}
          >
            {reason}
          </span>
        ))}
      </div>

      {state && (
        <div className="ml-auto flex items-center gap-3 text-ink-muted">
          <span>t={state.tick}</span>
          {state.age_seconds !== undefined && (
            <span>{state.age_seconds}s old</span>
          )}
        </div>
      )}
    </div>
  );
}
