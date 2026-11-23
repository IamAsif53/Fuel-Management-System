import { Activity, Bell } from "lucide-react";
import type { WorldState } from "@/api/types";
import type { ConnectionState } from "@/hooks/useLiveState";
import { ClockBadge } from "./ClockBadge";
import { ConnectionDot } from "./ConnectionDot";
import { EngineModeBadge } from "./EngineModeBadge";

interface HeaderProps {
  state: WorldState | null;
  connection: ConnectionState;
}

export function Header({ state, connection }: HeaderProps) {
  const openAlerts = state?.alerts_open_count
    ? Object.values(state.alerts_open_count).reduce((acc, c) => acc + c, 0)
    : 0;

  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-bg-border bg-bg-panel px-6">
      {/* Brand logo */}
      <div className="flex items-center gap-2">
        <Activity className="text-brand" size={20} aria-hidden />
        <span className="font-semibold tracking-wide text-ink">Fuel Ops Console</span>
      </div>

      {/* Clock Badge */}
      <div className="ml-4">
        <ClockBadge state={state} />
      </div>

      {/* Engine Mode */}
      <div>
        <EngineModeBadge mode={state?.engine_mode} />
      </div>

      {/* Policy Chip */}
      {state?.policy && (
        <div className="hidden items-center gap-1 rounded bg-bg-raised px-2.5 py-1 text-xs text-ink-muted sm:flex">
          <span className="text-ink-faint">Policy:</span>
          <span className="font-mono text-ink">{state.policy}</span>
        </div>
      )}

      {/* Spacer */}
      <div className="ml-auto flex items-center gap-4">
        {/* Alerts count */}
        {openAlerts > 0 && (
          <div
            className="flex items-center gap-1.5 rounded-md border border-risk-high/30 bg-risk-high/15 px-2 py-0.5 text-xs font-semibold text-risk-high"
            title={`${openAlerts} open alerts`}
          >
            <Bell size={13} />
            <span>{openAlerts}</span>
          </div>
        )}

        {/* Connection status */}
        <ConnectionDot connection={connection} />
      </div>
    </header>
  );
}
