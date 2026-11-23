import type { RouteView, AllocationView } from "@/api/types";
import { liters } from "@/lib/format";
import { AlertTriangle, ArrowRight } from "lucide-react";

interface RoutesTableProps {
  routes: RouteView[];
  allocations?: AllocationView[];
}

export function RoutesTable({ routes, allocations = [] }: RoutesTableProps) {
  // Compute in-transit allocations count per route
  const inTransitByRoute: Record<string, number> = {};
  for (const alloc of allocations) {
    if (alloc.status === "IN_TRANSIT") {
      inTransitByRoute[alloc.route_id] = (inTransitByRoute[alloc.route_id] || 0) + 1;
    }
  }

  return (
    <div className="overflow-hidden rounded-lg border border-panel-border bg-panel shadow-panel">
      <div className="border-b border-panel-border/60 px-5 py-4">
        <h3 className="text-base font-semibold text-ink">Supply Distribution Routes</h3>
        <p className="mt-1 text-xs text-ink-muted">
          Active pipeline routes connecting bulk storage depots to retail filling stations.
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left font-mono text-xs">
          <thead>
            <tr className="border-b border-panel-border/40 bg-surface/40 text-[11px] uppercase tracking-wider text-ink-muted">
              <th className="px-5 py-3 font-sans font-medium">Route ID</th>
              <th className="px-5 py-3 font-sans font-medium">Origin → Destination</th>
              <th className="px-5 py-3 font-sans font-medium text-right">Transit</th>
              <th className="px-5 py-3 font-sans font-medium text-right">Max Shipment</th>
              <th className="px-5 py-3 font-sans font-medium text-center">In-Transit</th>
              <th className="px-5 py-3 font-sans font-medium text-center">Status</th>
              <th className="px-5 py-3 font-sans font-medium">Scheduled Disruption</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-panel-border/20">
            {routes.map((route) => {
              const inTransitCount = inTransitByRoute[route.id] || 0;
              const isDisrupted = route.status === "DISRUPTED";

              return (
                <tr
                  key={route.id}
                  data-testid={`route-row-${route.id}`}
                  className={`transition hover:bg-surface/30 ${
                    isDisrupted ? "bg-red-500/5" : ""
                  }`}
                >
                  <td className="px-5 py-3.5 font-semibold text-ink">
                    <div className="flex items-center gap-1.5">
                      {isDisrupted && <AlertTriangle size={14} className="text-red-400" />}
                      <span>{route.id}</span>
                    </div>
                  </td>
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-2 font-sans text-xs">
                      <span className="font-medium text-ink">{route.source_depot_id}</span>
                      <ArrowRight size={13} className="text-ink-muted" />
                      <span className="font-medium text-ink">{route.destination_station_id}</span>
                    </div>
                  </td>
                  <td className="px-5 py-3.5 text-right text-ink-muted">
                    {route.transit_ticks} ticks
                  </td>
                  <td className="px-5 py-3.5 text-right font-medium text-ink">
                    {liters(route.max_shipment)}
                  </td>
                  <td className="px-5 py-3.5 text-center">
                    {inTransitCount > 0 ? (
                      <span className="inline-flex items-center justify-center rounded-full bg-accent/20 px-2 py-0.5 font-mono text-[11px] font-bold text-accent">
                        {inTransitCount} active
                      </span>
                    ) : (
                      <span className="text-ink-muted">0</span>
                    )}
                  </td>
                  <td className="px-5 py-3.5 text-center">
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
                        route.status === "AVAILABLE"
                          ? "bg-state-healthy/15 text-state-healthy"
                          : "bg-state-down/15 text-state-down"
                      }`}
                    >
                      {route.status}
                    </span>
                  </td>
                  <td className="px-5 py-3.5 font-sans">
                    {route.scheduled_disruption ? (
                      <span className="inline-flex items-center gap-1 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-400">
                        Ticks {route.scheduled_disruption.start_tick} –{" "}
                        {route.scheduled_disruption.end_tick}
                      </span>
                    ) : (
                      <span className="text-ink-muted">None</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
