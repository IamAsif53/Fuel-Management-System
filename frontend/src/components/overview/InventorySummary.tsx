import type { WorldState, DepotView, StationView, Fuel } from "@/api/types";
import { liters, pct } from "@/lib/format";

interface InventorySummaryProps {
  state: WorldState | null;
  onSelectEntity?: (entity: DepotView | StationView) => void;
}

export function InventorySummary({ state, onSelectEntity }: InventorySummaryProps) {
  const depots = state?.depots || [];
  const stations = state?.stations || [];
  const fuels: Fuel[] = ["DIESEL", "PETROL", "OCTANE"];

  if (!state || (depots.length === 0 && stations.length === 0)) {
    return null;
  }

  return (
    <div
      data-testid="inventory-summary"
      className="rounded-lg border border-bg-border bg-bg-panel p-4"
    >
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-medium uppercase tracking-wider text-ink-muted">
          Network Inventory Summary
        </h2>
        <span className="text-xs text-ink-faint">
          {depots.length} Depots · {stations.length} Stations
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs text-ink">
          <thead className="border-b border-bg-border bg-bg-raised text-[11px] uppercase tracking-wider text-ink-muted font-semibold">
            <tr>
              <th className="py-2.5 px-3">Entity</th>
              <th className="py-2.5 px-2">Type</th>
              <th className="py-2.5 px-2">Status</th>
              {fuels.map((f) => (
                <th key={f} className="py-2.5 px-3 text-right">
                  {f}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-bg-border/60 font-mono">
            {/* Depots */}
            {depots.map((depot) => (
              <tr
                key={depot.id}
                onClick={() => onSelectEntity?.(depot)}
                className="cursor-pointer transition hover:bg-bg-raised/60 group"
              >
                <td className="py-2 px-3 font-sans font-medium text-ink group-hover:text-brand">
                  {depot.name}
                </td>
                <td className="py-2 px-2 text-ink-muted">Depot</td>
                <td className="py-2 px-2">
                  <span className="inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold text-state-healthy bg-state-healthy/10">
                    {depot.status}
                  </span>
                </td>
                {fuels.map((fuel) => {
                  const inv = depot.inventory?.[fuel] ?? 0;
                  const cap = depot.capacity?.[fuel] ?? 1;
                  const fill = cap > 0 ? inv / cap : 0;
                  return (
                    <td key={fuel} className="py-2 px-3 text-right">
                      <span className="text-ink font-semibold">{liters(inv)}</span>
                      <span className="ml-1 text-[10px] text-ink-muted">
                        ({pct(fill, 0)})
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}

            {/* Stations */}
            {stations.map((station) => (
              <tr
                key={station.id}
                onClick={() => onSelectEntity?.(station)}
                className="cursor-pointer transition hover:bg-bg-raised/60 group"
              >
                <td className="py-2 px-3 font-sans font-medium text-ink group-hover:text-brand">
                  {station.name}
                </td>
                <td className="py-2 px-2 text-ink-muted">Station</td>
                <td className="py-2 px-2">
                  <span className="inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold text-state-healthy bg-state-healthy/10">
                    {station.status}
                  </span>
                </td>
                {fuels.map((fuel) => {
                  const inv = station.inventory?.[fuel] ?? 0;
                  const fill = station.fill_pct?.[fuel] ?? 0;
                  return (
                    <td key={fuel} className="py-2 px-3 text-right">
                      <span className="text-ink font-semibold">{liters(inv)}</span>
                      <span className="ml-1 text-[10px] text-ink-muted">
                        ({pct(fill, 0)})
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
