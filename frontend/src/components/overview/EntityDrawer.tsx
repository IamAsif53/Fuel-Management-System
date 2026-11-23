import { X, ShieldAlert, Truck, Fuel as FuelIcon } from "lucide-react";
import type { DepotView, StationView, Fuel } from "@/api/types";
import { liters, pct } from "@/lib/format";

interface EntityDrawerProps {
  entity: DepotView | StationView | null;
  onClose: () => void;
}

export function EntityDrawer({ entity, onClose }: EntityDrawerProps) {
  if (!entity) return null;

  const isStation = "demand_profile" in entity;
  const fuels: Fuel[] = ["DIESEL", "PETROL", "OCTANE"];

  const fuelColors: Record<Fuel, string> = {
    DIESEL: "text-fuel-diesel border-fuel-diesel/30 bg-fuel-diesel/10",
    PETROL: "text-fuel-petrol border-fuel-petrol/30 bg-fuel-petrol/10",
    OCTANE: "text-fuel-octane border-fuel-octane/30 bg-fuel-octane/10",
  };

  return (
    <div
      data-testid="entity-drawer"
      className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-bg-border bg-bg-panel shadow-2xl transition-transform duration-300 ease-in-out"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-bg-border p-5">
        <div>
          <span className="text-[11px] font-semibold uppercase tracking-wider text-brand">
            {isStation ? "Station Entity" : "Depot Entity"}
          </span>
          <h2 className="text-lg font-bold text-ink">{entity.name}</h2>
          <span className="text-xs font-mono text-ink-muted">{entity.id}</span>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg p-2 text-ink-muted hover:bg-bg-raised hover:text-ink transition"
          aria-label="Close detail drawer"
        >
          <X size={20} />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-5 space-y-6">
        {/* Core Attributes */}
        <div className="grid grid-cols-2 gap-3 rounded-lg border border-bg-border bg-bg-raised p-3 text-xs">
          <div>
            <span className="text-ink-faint">Region:</span>
            <div className="font-medium text-ink mt-0.5">{entity.region_id}</div>
          </div>
          <div>
            <span className="text-ink-faint">Status:</span>
            <div className="font-semibold text-state-healthy mt-0.5">
              {entity.status}
            </div>
          </div>
          {isStation ? (
            <>
              <div>
                <span className="text-ink-faint">Demand Profile:</span>
                <div className="font-medium text-ink mt-0.5">
                  {(entity as StationView).demand_profile}
                </div>
              </div>
              <div>
                <span className="text-ink-faint">Demand Multiplier:</span>
                <div className="font-mono text-ink mt-0.5">
                  {(entity as StationView).demand_multiplier}x
                </div>
              </div>
            </>
          ) : (
            <>
              <div>
                <span className="text-ink-faint">Dispatch Cap / Tick:</span>
                <div className="font-mono text-ink mt-0.5">
                  {liters((entity as DepotView).dispatch_capacity_per_tick)}
                </div>
              </div>
              <div>
                <span className="text-ink-faint">Constrained Self-Cap:</span>
                <div className="font-medium text-ink mt-0.5">
                  {(entity as DepotView).constrained_self_cap ? "Yes" : "No"}
                </div>
              </div>
            </>
          )}
        </div>

        {/* Inventory & Capacity Breakdown */}
        <div>
          <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-ink-muted flex items-center gap-1.5">
            <FuelIcon size={14} /> Fuel Inventory & Capacity
          </h3>
          <div className="space-y-3">
            {fuels.map((fuel) => {
              const inv = entity.inventory?.[fuel] ?? 0;
              const cap = entity.capacity?.[fuel] ?? 1;
              const fillPct = isStation
                ? (entity as StationView).fill_pct?.[fuel] ?? (cap > 0 ? inv / cap : 0)
                : cap > 0
                ? inv / cap
                : 0;

              return (
                <div
                  key={fuel}
                  className="rounded-lg border border-bg-border bg-bg-raised/50 p-3"
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs font-bold border ${fuelColors[fuel]}`}
                    >
                      {fuel}
                    </span>
                    <span className="text-xs font-mono font-semibold text-ink">
                      {pct(fillPct, 1)}
                    </span>
                  </div>
                  <div className="flex justify-between text-xs text-ink-muted mb-2">
                    <span>
                      Inventory: <strong className="text-ink">{liters(inv)}</strong>
                    </span>
                    <span>
                      Capacity: <strong className="text-ink">{liters(cap)}</strong>
                    </span>
                  </div>
                  <div className="h-1.5 w-full rounded-full bg-bg overflow-hidden">
                    <div
                      className="h-full bg-brand transition-all duration-300"
                      style={{ width: `${Math.min(100, Math.max(0, fillPct * 100))}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* In-Flight Inventory (for Stations) */}
        {isStation && (
          <div>
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-ink-muted flex items-center gap-1.5">
              <Truck size={14} /> In-Flight Allocations
            </h3>
            <div className="grid grid-cols-3 gap-2">
              {fuels.map((fuel) => {
                const inflight = (entity as StationView).inflight?.[fuel] ?? 0;
                return (
                  <div
                    key={fuel}
                    className="rounded border border-bg-border bg-bg-raised p-2 text-center"
                  >
                    <span className="text-[10px] font-mono text-ink-faint block uppercase">
                      {fuel}
                    </span>
                    <span className="text-xs font-mono font-medium text-ink">
                      {liters(inflight)}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Risk Assessment (for Stations) */}
        {isStation && (entity as StationView).risk && (
          <div>
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-ink-muted flex items-center gap-1.5">
              <ShieldAlert size={14} /> Risk Assessment
            </h3>
            <div className="space-y-2">
              {fuels.map((fuel) => {
                const r = (entity as StationView).risk[fuel];
                if (!r) return null;

                const levelColors: Record<string, string> = {
                  LOW: "text-state-healthy border-state-healthy/30 bg-state-healthy/10",
                  MEDIUM: "text-state-degraded border-state-degraded/30 bg-state-degraded/10",
                  HIGH: "text-risk-high border-risk-high/30 bg-risk-high/10",
                  CRITICAL: "text-risk-critical border-risk-critical/30 bg-risk-critical/10",
                };

                return (
                  <div
                    key={fuel}
                    className="flex items-center justify-between rounded-lg border border-bg-border bg-bg-raised p-2.5 text-xs"
                  >
                    <span className="font-semibold text-ink">{fuel}</span>
                    <div className="flex items-center gap-3">
                      <span className="text-ink-muted">
                        Stockout:{" "}
                        <strong className="text-ink">
                          {r.hours_to_stockout != null ? `${r.hours_to_stockout.toFixed(1)}h` : "> horiz"}
                        </strong>
                      </span>
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-bold border ${
                          levelColors[r.level] || levelColors.LOW
                        }`}
                      >
                        {r.level}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
