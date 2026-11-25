import type { WorldState } from "@/api/types";
import { liters, pct } from "@/lib/format";
import { KpiCard } from "./KpiCard";

interface KpiGridProps {
  state: WorldState | null;
}

export function KpiGrid({ state }: KpiGridProps) {
  // 1. Service Level
  const sl = state?.metrics?.service_level;
  const slVal = sl != null ? pct(sl, 1) : "—";
  const slTone = sl != null ? (sl >= 0.98 ? "ok" : sl >= 0.90 ? "warn" : "bad") : "neutral";

  // 2. Unmet Liters
  const unmet = state?.metrics?.unmet_liters;
  const unmetVal = unmet != null ? liters(unmet) : "—";
  const unmetTone = unmet != null ? (unmet === 0 ? "ok" : "bad") : "neutral";

  // 3. Stations at Risk: count stations with at least one HIGH or CRITICAL fuel risk
  let stationsAtRiskCount = 0;
  if (state?.stations) {
    for (const station of state.stations) {
      if (station.risk) {
        const hasHighOrCrit = Object.values(station.risk).some(
          (r) => r && (r.level === "HIGH" || r.level === "CRITICAL")
        );
        if (hasHighOrCrit) {
          stationsAtRiskCount++;
        }
      }
    }
  }
  const stationsAtRiskVal = state ? String(stationsAtRiskCount) : "—";
  const stationsAtRiskTone = state
    ? stationsAtRiskCount === 0
      ? "ok"
      : stationsAtRiskCount === 1
      ? "warn"
      : "bad"
    : "neutral";

  // 4. Open Alerts
  let alertsCount = 0;
  if (state?.alerts_open_count) {
    alertsCount = Object.values(state.alerts_open_count).reduce((acc, c) => acc + (c || 0), 0);
  }
  const alertsVal = state ? String(alertsCount) : "—";
  const alertsTone = state ? (alertsCount === 0 ? "ok" : "warn") : "neutral";

  // 5. Pending Recommendations
  const pendingCount = state?.pending_recommendations?.length ?? 0;
  const pendingVal = state ? String(pendingCount) : "—";
  const pendingTone = state ? (pendingCount > 0 ? "warn" : "neutral") : "neutral";

  // 6. Allocation Failures
  const failures = state?.metrics?.allocation_failures;
  const failuresVal = failures != null ? String(failures) : "—";
  const failuresTone = failures != null ? (failures === 0 ? "ok" : "bad") : "neutral";

  // 7. Overflow Waste Est.
  const overflow = state?.metrics?.overflow_liters_est;
  const overflowVal = overflow != null ? liters(overflow) : "—";
  const overflowTone = overflow != null ? (overflow === 0 ? "ok" : "warn") : "neutral";

  // 8. Engine Mode
  const mode = state?.engine_mode ?? "—";
  const modeTone = state
    ? mode === "NORMAL"
      ? "ok"
      : mode === "DEGRADED"
      ? "warn"
      : "bad"
    : "neutral";

  return (
    <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4 xl:grid-cols-8 xl:gap-3">
      <KpiCard
        label="Service Level"
        value={slVal}
        sub="SLO 98%"
        tone={slTone}
        testId="kpi-service-level"
      />
      <KpiCard
        label="Unmet Liters"
        value={unmetVal}
        sub="Total unmet"
        tone={unmetTone}
        testId="kpi-unmet-liters"
      />
      <KpiCard
        label="Stations at Risk"
        value={stationsAtRiskVal}
        sub="High/Crit"
        tone={stationsAtRiskTone}
        testId="kpi-stations-at-risk"
      />
      <KpiCard
        label="Open Alerts"
        value={alertsVal}
        sub="Unresolved"
        tone={alertsTone}
        testId="kpi-open-alerts"
      />
      <KpiCard
        label="Pending Recs"
        value={pendingVal}
        sub="Awaiting review"
        tone={pendingTone}
        testId="kpi-pending-recs"
      />
      <KpiCard
        label="Allocation Fails"
        value={failuresVal}
        sub="Sim rejections"
        tone={failuresTone}
        testId="kpi-allocation-failures"
      />
      <KpiCard
        label="Overflow Waste"
        value={overflowVal}
        sub="Estimated"
        tone={overflowTone}
        testId="kpi-overflow-waste"
      />
      <KpiCard
        label="Engine Mode"
        value={mode}
        sub="Decision mode"
        tone={modeTone}
        testId="kpi-engine-mode"
      />
    </div>
  );
}
