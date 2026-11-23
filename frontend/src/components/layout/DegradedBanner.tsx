import clsx from "clsx";
import { AlertTriangle, AlertOctagon } from "lucide-react";
import type { WorldState } from "@/api/types";

interface DegradedBannerProps {
  state: WorldState | null;
}

export function DegradedBanner({ state }: DegradedBannerProps) {
  if (!state) return null;

  const isDegraded = state.degraded || (state.engine_mode && state.engine_mode !== "NORMAL");
  if (!isDegraded) return null;

  const isCritical =
    state.engine_mode === "SAFE_HOLD" || state.engine_mode === "NO_FEASIBLE_ROUTES";

  const reasons = state.degraded_reasons && state.degraded_reasons.length > 0
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
        <span>{isCritical ? "CRITICAL SYSTEM STATE" : "SYSTEM DEGRADED"}</span>
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

      <div className="ml-auto flex items-center gap-3 text-ink-muted">
        <span>t={state.tick}</span>
        {state.age_seconds !== undefined && (
          <span>{state.age_seconds}s old</span>
        )}
      </div>
    </div>
  );
}
