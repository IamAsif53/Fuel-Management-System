import type { DepotView, WorldState, Fuel } from "@/api/types";
import { liters, pct } from "@/lib/format";
import { AlertCircle, Clock, CheckCircle2 } from "lucide-react";

interface DepotCardProps {
  depot: DepotView;
  supplyUpcoming?: WorldState["supply_upcoming"];
}

const FUEL_TYPES: Fuel[] = ["DIESEL", "PETROL", "OCTANE"];

const FUEL_COLORS: Record<Fuel, { bar: string; text: string }> = {
  DIESEL: { bar: "bg-sky-400", text: "text-sky-400" },
  PETROL: { bar: "bg-purple-400", text: "text-purple-400" },
  OCTANE: { bar: "bg-pink-400", text: "text-pink-400" },
};

export function DepotCard({ depot, supplyUpcoming = [] }: DepotCardProps) {
  // Filter upcoming supply for this depot
  const depotSupplies = supplyUpcoming.filter((s) => s.depot_id === depot.id);

  // Dispatch capacity details
  const dispatchCap = depot.dispatch_capacity_per_tick;
  const dispatchUsed = depot.dispatch_used_this_tick ?? 0;
  const isConstrained = depot.constrained_self_cap || depot.status === "CONSTRAINED";

  return (
    <div
      data-testid={`depot-card-${depot.id}`}
      className="flex flex-col rounded-lg border border-panel-border bg-panel p-5 shadow-panel transition hover:border-panel-border/80"
    >
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-panel-border/60 pb-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-base font-semibold text-ink">{depot.name}</h3>
            <span className="font-mono text-xs text-ink-muted">({depot.id})</span>
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span className="rounded bg-surface px-1.5 py-0.5 text-[10px] font-medium tracking-wide uppercase text-ink-muted">
              {depot.region_id}
            </span>
            <span className="font-mono text-xs text-ink-muted">
              Cap: {liters(dispatchCap)} / tick
            </span>
          </div>
        </div>
        <span
          className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${
            depot.status === "OPEN"
              ? "bg-state-healthy/15 text-state-healthy"
              : "bg-state-degraded/15 text-state-degraded"
          }`}
        >
          {depot.status}
        </span>
      </div>

      {/* Dispatch Capacity Bar */}
      <div className="my-3 rounded border border-panel-border/40 bg-surface/30 p-3">
        <div className="flex items-center justify-between text-xs text-ink-muted">
          <span className="font-sans font-medium text-ink">Dispatch Throughput Limit</span>
          <span className="font-mono text-ink font-semibold">
            {liters(dispatchUsed)} / {liters(dispatchCap)} per tick
          </span>
        </div>
        {isConstrained && (
          <div className="mt-2 flex items-center gap-1.5 text-xs text-amber-400">
            <AlertCircle size={14} />
            <span>High dispatch volume: constrained capacity</span>
          </div>
        )}
      </div>

      {/* Inventory Breakdown */}
      <div className="my-2 space-y-3">
        <h4 className="text-xs font-medium uppercase tracking-wider text-ink-muted">
          Current Bulk Inventory
        </h4>
        <div className="space-y-2 font-mono text-xs">
          {FUEL_TYPES.map((fuel) => {
            const inv = depot.inventory[fuel] ?? 0;
            const cap = depot.capacity[fuel] || 1;
            const ratio = Math.min(1, Math.max(0, inv / cap));

            return (
              <div key={fuel} className="rounded bg-surface/40 p-2.5">
                <div className="flex items-center justify-between text-xs">
                  <span className={`font-sans font-semibold ${FUEL_COLORS[fuel].text}`}>
                    {fuel}
                  </span>
                  <div className="flex items-center gap-2">
                    <span className="text-ink font-semibold">{liters(inv)}</span>
                    <span className="text-ink-muted">/ {liters(cap)}</span>
                    <span className="text-[11px] text-ink font-medium">({pct(ratio)})</span>
                  </div>
                </div>
                <div className="mt-2 h-1.5 w-full rounded-full bg-surface overflow-hidden">
                  <div
                    className={`h-full ${FUEL_COLORS[fuel].bar}`}
                    style={{ width: `${Math.round(ratio * 100)}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Supply Arrivals Timeline */}
      <div className="mt-4 border-t border-panel-border/40 pt-3">
        <h4 className="mb-2.5 text-xs font-medium uppercase tracking-wider text-ink-muted">
          Supply Arrivals Timeline
        </h4>
        {depotSupplies.length === 0 ? (
          <div className="rounded border border-panel-border/30 bg-surface/20 p-3 text-center text-xs text-ink-muted">
            No upcoming scheduled supply deliveries
          </div>
        ) : (
          <div className="space-y-2">
            {depotSupplies.map((item) => {
              const isScheduled = item.status === "SCHEDULED";
              const isDelayed = item.status === "DELAYED";
              const isArrived = item.status === "ARRIVED";

              return (
                <div
                  key={item.id}
                  className="flex items-center justify-between rounded border border-panel-border/40 bg-surface/30 px-3 py-2 text-xs"
                >
                  <div className="flex items-center gap-2.5">
                    {/* Status Marker */}
                    {isScheduled && (
                      <div
                        title="SCHEDULED"
                        className="h-3.5 w-3.5 rounded-full border-2 border-sky-400 bg-transparent"
                      />
                    )}
                    {isDelayed && (
                      <div
                        title="DELAYED"
                        className="flex h-3.5 w-3.5 items-center justify-center rounded-full bg-amber-500/20 text-amber-400"
                      >
                        <Clock size={12} />
                      </div>
                    )}
                    {isArrived && (
                      <div
                        title="ARRIVED"
                        className="flex h-3.5 w-3.5 items-center justify-center rounded-full text-emerald-400"
                      >
                        <CheckCircle2 size={14} />
                      </div>
                    )}
                    <span className="font-semibold text-ink">{item.fuel_type}</span>
                    <span className="font-mono text-ink-muted">{liters(item.quantity)}</span>
                  </div>
                  <div className="flex items-center gap-2 font-mono">
                    <span className="text-ink-muted">Planned t={item.planned_tick}</span>
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-sans font-semibold uppercase ${
                        isArrived
                          ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/30"
                          : isDelayed
                          ? "bg-amber-500/10 text-amber-400 border border-amber-500/30"
                          : "bg-sky-500/10 text-sky-400 border border-sky-500/30"
                      }`}
                    >
                      {item.status}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
