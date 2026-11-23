import { useQuery } from "@tanstack/react-query";
import type { StationView, AllocationView, Fuel, StationDetailResponse } from "@/api/types";
import { api } from "@/api/client";
import { liters, pct } from "@/lib/format";
import { StationInventoryChart } from "./StationInventoryChart";

interface StationCardProps {
  station: StationView;
  allocations?: AllocationView[];
}

const FUEL_TYPES: Fuel[] = ["DIESEL", "PETROL", "OCTANE"];

const FUEL_COLORS: Record<Fuel, { bar: string; text: string }> = {
  DIESEL: { bar: "bg-sky-400", text: "text-sky-400" },
  PETROL: { bar: "bg-purple-400", text: "text-purple-400" },
  OCTANE: { bar: "bg-pink-400", text: "text-pink-400" },
};

const RISK_BADGES: Record<string, { bg: string; text: string }> = {
  LOW: { bg: "bg-emerald-500/10 border-emerald-500/30", text: "text-emerald-400" },
  MEDIUM: { bg: "bg-amber-500/10 border-amber-500/30", text: "text-amber-400" },
  HIGH: { bg: "bg-orange-500/10 border-orange-500/30", text: "text-orange-400" },
  CRITICAL: { bg: "bg-red-500/10 border-red-500/30", text: "text-red-400" },
};

export function StationCard({ station, allocations = [] }: StationCardProps) {
  // Query station history
  const { data: stationDetail, isLoading } = useQuery<StationDetailResponse>({
    queryKey: ["station", station.id],
    queryFn: () => api<StationDetailResponse>(`/stations/${station.id}?history_ticks=48`),
    staleTime: 10000,
    retry: 1,
  });

  // Calculate in-flight inventory per fuel from active allocations or station.inflight
  const inFlightByFuel: Record<Fuel, number> = {
    DIESEL: station.inflight?.DIESEL ?? 0,
    PETROL: station.inflight?.PETROL ?? 0,
    OCTANE: station.inflight?.OCTANE ?? 0,
  };

  // If allocations provided, ensure active ones are reflected
  for (const alloc of allocations) {
    if (alloc.destination_station_id === station.id && alloc.status === "IN_TRANSIT") {
      inFlightByFuel[alloc.fuel_type] = Math.max(
        inFlightByFuel[alloc.fuel_type],
        alloc.quantity
      );
    }
  }

  const isOpen = station.status === "OPEN";

  return (
    <div
      data-testid={`station-card-${station.id}`}
      className="flex flex-col rounded-lg border border-panel-border bg-panel p-5 shadow-panel transition hover:border-panel-border/80"
    >
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-panel-border/60 pb-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-base font-semibold text-ink">{station.name}</h3>
            <span className="font-mono text-xs text-ink-muted">({station.id})</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span className="rounded bg-surface px-1.5 py-0.5 text-[10px] font-medium tracking-wide uppercase text-ink-muted">
              {station.region_id}
            </span>
            <span className="rounded bg-surface px-1.5 py-0.5 text-[10px] font-medium uppercase text-ink-muted">
              {station.demand_profile}
            </span>
            {station.demand_multiplier !== 1.0 && (
              <span className="rounded bg-accent/20 border border-accent/40 px-1.5 py-0.5 font-mono text-[10px] font-medium text-accent">
                {station.demand_multiplier}x demand
              </span>
            )}
          </div>
        </div>
        <span
          className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${
            isOpen
              ? "bg-state-healthy/15 text-state-healthy"
              : "bg-state-down/15 text-state-down"
          }`}
        >
          {station.status}
        </span>
      </div>

      {/* Mini Inventory Table */}
      <div className="my-4 overflow-x-auto">
        <table className="w-full text-left font-mono text-xs">
          <thead>
            <tr className="border-b border-panel-border/40 text-[10px] uppercase text-ink-muted">
              <th className="pb-1.5 font-sans font-medium">Fuel</th>
              <th className="pb-1.5 font-sans font-medium text-right">Inventory</th>
              <th className="pb-1.5 font-sans font-medium text-right">In-Flight</th>
              <th className="pb-1.5 font-sans font-medium text-right">Capacity</th>
              <th className="pb-1.5 font-sans font-medium text-right">Fill</th>
              <th className="pb-1.5 font-sans font-medium text-right">Risk</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-panel-border/20">
            {FUEL_TYPES.map((fuel) => {
              const inv = station.inventory[fuel] ?? 0;
              const cap = station.capacity[fuel] || 1;
              const inflight = inFlightByFuel[fuel] || 0;
              const ratio = Math.min(1, Math.max(0, inv / cap));
              const risk = station.risk?.[fuel]?.level ?? "LOW";
              const riskStyle = RISK_BADGES[risk] || RISK_BADGES.LOW;

              return (
                <tr key={fuel} className="hover:bg-surface/30">
                  <td className="py-2">
                    <span className={`font-sans font-semibold text-xs ${FUEL_COLORS[fuel].text}`}>
                      {fuel}
                    </span>
                  </td>
                  <td className="py-2 text-right text-ink font-semibold">{liters(inv)}</td>
                  <td className="py-2 text-right text-ink-muted">
                    {inflight > 0 ? `+${liters(inflight)}` : "—"}
                  </td>
                  <td className="py-2 text-right text-ink-muted">{liters(cap)}</td>
                  <td className="py-2 text-right">
                    <div className="flex items-center justify-end gap-1.5">
                      <div className="h-1.5 w-12 rounded-full bg-surface overflow-hidden">
                        <div
                          className={`h-full ${FUEL_COLORS[fuel].bar}`}
                          style={{ width: `${Math.round(ratio * 100)}%` }}
                        />
                      </div>
                      <span className="text-[11px] text-ink">{pct(ratio)}</span>
                    </div>
                  </td>
                  <td className="py-2 text-right">
                    <span
                      className={`inline-block rounded border px-1.5 py-0.5 text-[9px] font-sans font-semibold uppercase ${riskStyle.bg} ${riskStyle.text}`}
                    >
                      {risk}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Historical Inventory Chart */}
      <div className="mt-auto border-t border-panel-border/40 pt-3">
        <div className="mb-2 flex items-center justify-between text-xs text-ink-muted">
          <span className="font-sans font-medium text-ink">Inventory History (48 Ticks)</span>
          {isLoading && <span className="animate-pulse text-[10px]">Loading history...</span>}
        </div>
        <StationInventoryChart
          history={stationDetail?.history ?? []}
          capacity={station.capacity}
        />
      </div>
    </div>
  );
}
