import { useEffect, useState } from "react";
import { fetchRisk } from "@/api/client";
import type { RiskLevel, RiskRow } from "@/api/types";
import { FUELS } from "@/api/types";
import { useLiveState } from "@/hooks/useLiveState";
import { LoadingSkeleton } from "@/components/LoadingSkeleton";
import { ErrorState } from "@/components/ErrorState";
import { formatNumber } from "@/lib/format";

const LEVEL_COLORS: Record<RiskLevel, { badge: string; text: string }> = {
  CRITICAL: { badge: "bg-red-500/20 border-red-600/40 text-red-400", text: "text-red-400 font-bold" },
  HIGH: { badge: "bg-amber-500/20 border-amber-600/40 text-amber-400", text: "text-amber-400 font-semibold" },
  MEDIUM: { badge: "bg-yellow-500/20 border-yellow-600/40 text-yellow-400", text: "text-yellow-400" },
  LOW: { badge: "bg-emerald-500/15 border-emerald-600/30 text-emerald-400", text: "text-emerald-400" },
};

export default function RiskPage() {
  const { state } = useLiveState();
  const [rows, setRows] = useState<RiskRow[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [regionFilter, setRegionFilter] = useState<string>("");
  const [fuelFilter, setFuelFilter] = useState<string>("");
  const [minLevelFilter, setMinLevelFilter] = useState<string>("");

  const loadData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetchRisk({
        region_id: regionFilter || undefined,
        fuel: fuelFilter || undefined,
        min_level: minLevelFilter || undefined,
      });
      setRows(res.rows || []);
    } catch (err: any) {
      setError(err?.message || "Failed to load risk assessments");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [state?.tick, regionFilter, fuelFilter, minLevelFilter]);

  return (
    <div className="space-y-6" data-testid="risk-page">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Risk Assessment Engine</h1>
          <p className="text-sm text-muted-foreground">
            Multi-horizon Monte Carlo stockout probability (N=300), survival times, and causal drivers
          </p>
        </div>

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={regionFilter}
            onChange={(e) => setRegionFilter(e.target.value)}
            className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="">All Regions</option>
            <option value="dhaka">Dhaka</option>
            <option value="chattogram">Chattogram</option>
          </select>

          <select
            value={fuelFilter}
            onChange={(e) => setFuelFilter(e.target.value)}
            className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="">All Fuels</option>
            {FUELS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>

          <select
            value={minLevelFilter}
            onChange={(e) => setMinLevelFilter(e.target.value)}
            className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="">Min Level: Any</option>
            <option value="CRITICAL">Critical Only</option>
            <option value="HIGH">High & Above</option>
            <option value="MEDIUM">Medium & Above</option>
          </select>
        </div>
      </div>

      {isLoading && rows.length === 0 ? (
        <LoadingSkeleton count={4} />
      ) : error && rows.length === 0 ? (
        <ErrorState title="Risk Data Unavailable" message={error} onRetry={loadData} />
      ) : (
        <div className="rounded-lg border border-border bg-card shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-muted/30 border-b border-border/80 text-muted-foreground">
                <tr>
                  <th className="py-3 px-4 font-medium">Station</th>
                  <th className="py-3 px-3 font-medium">Region</th>
                  <th className="py-3 px-3 font-medium">Fuel</th>
                  <th className="py-3 px-3 font-medium">Risk Level</th>
                  <th className="py-3 px-3 font-medium text-right">Hours to Stockout</th>
                  <th className="py-3 px-3 font-medium text-right">Stockout Prob</th>
                  <th className="py-3 px-3 font-medium text-right">Inventory (L)</th>
                  <th className="py-3 px-4 font-medium">Causal Drivers</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="py-8 text-center text-muted-foreground">
                      No station fuel series match the selected risk criteria.
                    </td>
                  </tr>
                ) : (
                  rows.map((row) => {
                    const styling = LEVEL_COLORS[row.risk_level] || LEVEL_COLORS.LOW;
                    return (
                      <tr
                        key={`${row.station_id}-${row.fuel_type}`}
                        className="transition-colors hover:bg-muted/20"
                      >
                        <td className="py-3 px-4 font-medium text-foreground">
                          {row.station_name || row.station_id}
                        </td>
                        <td className="py-3 px-3 capitalize text-muted-foreground">
                          {row.region_id || "—"}
                        </td>
                        <td className="py-3 px-3 font-semibold text-foreground">
                          {row.fuel_type}
                        </td>
                        <td className="py-3 px-3">
                          <span
                            className={`inline-flex rounded border px-2 py-0.5 text-[11px] font-bold ${styling.badge}`}
                          >
                            {row.risk_level}
                          </span>
                        </td>
                        <td className="py-3 px-3 text-right font-medium text-foreground">
                          {row.hours_to_stockout < 900
                            ? `${row.hours_to_stockout.toFixed(1)} h`
                            : "Safe (>96h)"}
                        </td>
                        <td className="py-3 px-3 text-right">
                          <span
                            className={`font-semibold ${
                              row.stockout_prob > 0.5 ? "text-red-400" : "text-foreground"
                            }`}
                          >
                            {(row.stockout_prob * 100).toFixed(0)}%
                          </span>
                        </td>
                        <td className="py-3 px-3 text-right font-mono text-foreground">
                          {formatNumber(row.inventory)}
                        </td>
                        <td className="py-3 px-4">
                          <div className="flex flex-wrap gap-1">
                            {row.drivers && row.drivers.length > 0 ? (
                              row.drivers.map((d, i) => (
                                <span
                                  key={i}
                                  className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                                >
                                  {d}
                                </span>
                              ))
                            ) : (
                              <span className="text-muted-foreground/60">—</span>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
