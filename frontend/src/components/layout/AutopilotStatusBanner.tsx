import { Bot, ShieldAlert } from "lucide-react";
import clsx from "clsx";
import type { WorldState } from "@/api/types";
import { domainSloBudget } from "@/lib/sloBudget";

interface AutopilotStatusBannerProps {
  state: WorldState | null;
}

function collectFrozenReasons(state: WorldState): string[] {
  const reasons: string[] = [];
  const budget = domainSloBudget(state.metrics?.service_level);
  if (budget.exhausted) {
    reasons.push(
      `Domain SLO budget ${ (budget.remaining * 100).toFixed(1) }% remaining (need ≥${(
        budget.required * 100
      ).toFixed(0)}%)`
    );
  }
  for (const r of state.degraded_reasons || []) {
    if (r === "error_budget_exhausted") continue; // already covered by meter
    if (!reasons.includes(r)) reasons.push(r);
  }
  const largePending = (state.pending_recommendations || []).filter(
    (p) => (p.total_liters ?? 0) > 7000
  );
  if (largePending.length > 0) {
    const liters = largePending[0].total_liters;
    reasons.push(
      `Pending plan ${liters.toLocaleString()} L exceeds 7,000 L four-eyes ceiling (autopilot may dispatch a ≤7k L subset)`
    );
  }
  return reasons;
}

export function AutopilotStatusBanner({ state }: AutopilotStatusBannerProps) {
  if (!state || state.operating_mode !== "autopilot") return null;

  const budget = domainSloBudget(state.metrics?.service_level);
  const reasons = collectFrozenReasons(state);
  const frozen = budget.exhausted || reasons.length > 0;
  const resumePct = Math.min(
    100,
    Math.round((budget.remainingClamped / Math.max(budget.required, 0.01)) * 100)
  );

  return (
    <div
      data-testid="autopilot-status-banner"
      className={clsx(
        "flex flex-wrap items-center gap-3 border-b px-6 py-2 text-xs transition-all",
        frozen
          ? "border-amber-500/40 bg-amber-500/10 text-amber-100"
          : "border-brand/30 bg-brand/10 text-ink"
      )}
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-1.5 font-semibold">
        {frozen ? <ShieldAlert size={16} className="text-amber-400" /> : <Bot size={16} className="text-brand" />}
        <span>{frozen ? "AUTOPILOT FROZEN / GATED" : "AUTOPILOT ACTIVE"}</span>
      </div>

      <div className="flex min-w-[12rem] flex-1 flex-col gap-1 sm:max-w-xs">
        <div className="flex items-center justify-between text-[10px] text-ink-muted">
          <span>Domain SLO budget → resume</span>
          <span className="font-mono">
            {(budget.remaining * 100).toFixed(0)}% / need {(budget.required * 100).toFixed(0)}%
          </span>
        </div>
        <div
          className="h-1.5 overflow-hidden rounded-full bg-bg-raised"
          role="meter"
          aria-label="Domain SLO error budget remaining toward autopilot resume"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(budget.remainingClamped * 100)}
        >
          <div
            className={clsx(
              "h-full rounded-full transition-all",
              budget.exhausted ? "bg-amber-400" : "bg-brand"
            )}
            style={{ width: `${Math.round(budget.remainingClamped * 100)}%` }}
          />
        </div>
        {budget.exhausted && (
          <span className="text-[10px] text-ink-muted">
            Recovery progress to autopilot resume gate: {resumePct}%
          </span>
        )}
      </div>

      {frozen && reasons.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {reasons.slice(0, 4).map((reason) => (
            <span
              key={reason}
              className="rounded bg-amber-500/20 px-2 py-0.5 font-mono text-[11px] text-amber-100"
            >
              {reason}
            </span>
          ))}
        </div>
      )}

      {!frozen && (
        <span className="text-ink-muted">
          Low-risk ≤7k L allocations auto-dispatch; larger plans stay for review.
        </span>
      )}

      <div className="ml-auto font-mono text-ink-muted">
        SL={(budget.serviceLevel * 100).toFixed(2)}% · t={state.tick}
      </div>
    </div>
  );
}
