import { useEffect, useRef, useState } from "react";
import { fetchRisk, fetchN1Contingency, fetchConformalPrediction } from "@/api/client";
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

type ViewMode = "risk" | "contingency" | "conformal";

export default function RiskPage() {
  const { state } = useLiveState();
  const [viewMode, setViewMode] = useState<ViewMode>("risk");

  // Stockout Risk state
  const [rows, setRows] = useState<RiskRow[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Contingency state
  const [contingencyData, setContingencyData] = useState<any>(null);
  const [contingencyLoading, setContingencyLoading] = useState(false);

  // Conformal state
  const [conformalData, setConformalData] = useState<any>(null);
  const [conformalLoading, setConformalLoading] = useState(false);

  // Filters
  const [regionFilter, setRegionFilter] = useState<string>("");
  const [fuelFilter, setFuelFilter] = useState<string>("");
  const [minLevelFilter, setMinLevelFilter] = useState<string>("");
  const riskReqSeq = useRef(0);

  const loadRiskData = async () => {
    const seq = ++riskReqSeq.current;
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetchRisk({
        region_id: regionFilter || undefined,
        fuel: fuelFilter || undefined,
        min_level: minLevelFilter || undefined,
      });
      if (seq !== riskReqSeq.current) return;
      setRows(res.rows || []);
    } catch (err: any) {
      if (seq !== riskReqSeq.current) return;
      setError(err?.message || "Failed to load risk assessments");
    } finally {
      if (seq === riskReqSeq.current) {
        setIsLoading(false);
      }
    }
  };

  const loadContingencyData = async () => {
    setContingencyLoading(true);
    try {
      const res = await fetchN1Contingency();
      setContingencyData(res);
    } catch (err: any) {
      console.error("Failed to load contingency data", err);
    } finally {
      setContingencyLoading(false);
    }
  };

  const loadConformalData = async () => {
    setConformalLoading(true);
    try {
      const res = await fetchConformalPrediction(0.9, 24);
      setConformalData(res);
    } catch (err: any) {
      console.error("Failed to load conformal prediction data", err);
    } finally {
      setConformalLoading(false);
    }
  };

  useEffect(() => {
    if (viewMode === "risk") {
      loadRiskData();
    } else if (viewMode === "contingency") {
      loadContingencyData();
    } else if (viewMode === "conformal") {
      loadConformalData();
    }
  }, [state?.tick, viewMode, regionFilter, fuelFilter, minLevelFilter]);

  return (
    <div className="space-y-6" data-testid="risk-page">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Risk & Contingency Engine</h1>
          <p className="text-sm text-muted-foreground">
            Multi-horizon Monte Carlo risk (N=300), N-1 infrastructure contingency, and conformal uncertainty intervals
          </p>
        </div>

        {/* View mode toggle */}
        <div className="flex rounded-lg border border-border bg-card p-1 text-xs">
          <button
            onClick={() => setViewMode("risk")}
            className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
              viewMode === "risk"
                ? "bg-primary text-primary-foreground shadow"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            Station Risk
          </button>
          <button
            onClick={() => setViewMode("contingency")}
            className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
              viewMode === "contingency"
                ? "bg-primary text-primary-foreground shadow"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            N-1 Contingency
          </button>
          <button
            onClick={() => setViewMode("conformal")}
            className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
              viewMode === "conformal"
                ? "bg-primary text-primary-foreground shadow"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            Conformal Intervals
          </button>
        </div>
      </div>

      {viewMode === "risk" && (
        <div className="space-y-4">
          {/* Filters */}
          <div className="flex flex-wrap items-center gap-2">
            <select
              data-testid="risk-region-filter"
              value={regionFilter}
              onChange={(e) => setRegionFilter(e.target.value)}
              className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="">All Regions</option>
              <option value="region-dhaka">Dhaka</option>
              <option value="region-chattogram">Chattogram</option>
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

          {isLoading && rows.length === 0 ? (
            <LoadingSkeleton count={4} />
          ) : error && rows.length === 0 ? (
            <ErrorState title="Risk Data Unavailable" message={error} onRetry={loadRiskData} />
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
                              {(row.region_id || "—").replace(/^region-/, "")}
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
      )}

      {viewMode === "contingency" && (
        <div className="space-y-6">
          {contingencyLoading && !contingencyData ? (
            <LoadingSkeleton count={3} />
          ) : contingencyData ? (
            <>
              {/* Summary Metrics */}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
                <div className="rounded-lg border border-border bg-card p-4">
                  <div className="text-xs font-medium text-muted-foreground">Network Resilience</div>
                  <div className="mt-1 flex items-baseline gap-2">
                    <span className="text-2xl font-bold text-foreground">
                      {(contingencyData.network_resilience_score * 100).toFixed(1)}%
                    </span>
                    <span
                      className={`text-xs font-semibold px-2 py-0.5 rounded ${
                        contingencyData.resilience_status === "HIGH"
                          ? "bg-emerald-500/20 text-emerald-400"
                          : contingencyData.resilience_status === "MODERATE"
                          ? "bg-amber-500/20 text-amber-400"
                          : "bg-red-500/20 text-red-400"
                      }`}
                    >
                      {contingencyData.resilience_status}
                    </span>
                  </div>
                </div>

                <div className="rounded-lg border border-border bg-card p-4">
                  <div className="text-xs font-medium text-muted-foreground">Single Points of Failure</div>
                  <div className="mt-1 text-2xl font-bold text-red-400">
                    {contingencyData.single_points_of_failure_count}
                  </div>
                </div>

                <div className="rounded-lg border border-border bg-card p-4">
                  <div className="text-xs font-medium text-muted-foreground">Critical Vulnerabilities</div>
                  <div className="mt-1 text-2xl font-bold text-amber-400">
                    {contingencyData.critical_vulnerabilities}
                  </div>
                </div>

                <div className="rounded-lg border border-border bg-card p-4">
                  <div className="text-xs font-medium text-muted-foreground">Contingencies Scanned</div>
                  <div className="mt-1 text-2xl font-bold text-foreground">
                    {contingencyData.total_contingencies_evaluated}
                  </div>
                </div>
              </div>

              {/* SPOF warning banner */}
              {contingencyData.spof_identifiers?.length > 0 && (
                <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-300">
                  <span className="font-bold">⚠️ Warning: Single Points of Failure Identified:</span>{" "}
                  {contingencyData.spof_identifiers.join(", ")} — Outage will isolate downstream stations without alternative supply paths.
                </div>
              )}

              {/* Contingency Table */}
              <div className="rounded-lg border border-border bg-card shadow-sm overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-muted/30 border-b border-border/80 text-muted-foreground">
                      <tr>
                        <th className="py-3 px-4 font-medium">Contingency</th>
                        <th className="py-3 px-3 font-medium">Element</th>
                        <th className="py-3 px-3 font-medium">Severity</th>
                        <th className="py-3 px-3 font-medium text-right">Lost Volume / Isolated</th>
                        <th className="py-3 px-4 font-medium">Automated Mitigation Strategy</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/40">
                      {contingencyData.contingencies?.map((c: any) => (
                        <tr key={c.contingency_id} className="transition-colors hover:bg-muted/20">
                          <td className="py-3 px-4 font-medium text-foreground">{c.contingency_id}</td>
                          <td className="py-3 px-3">
                            <span className="font-semibold text-foreground">{c.element_name}</span>
                            <span className="ml-1 text-[10px] text-muted-foreground uppercase">({c.element_type})</span>
                          </td>
                          <td className="py-3 px-3">
                            <span
                              className={`inline-flex rounded border px-2 py-0.5 text-[11px] font-bold ${
                                c.severity === "CRITICAL"
                                  ? "bg-red-500/20 border-red-600/40 text-red-400"
                                  : c.severity === "HIGH"
                                  ? "bg-amber-500/20 border-amber-600/40 text-amber-400"
                                  : "bg-emerald-500/15 border-emerald-600/30 text-emerald-400"
                              }`}
                            >
                              {c.severity}
                            </span>
                          </td>
                          <td className="py-3 px-3 text-right font-mono text-foreground">
                            {c.lost_capacity_liters
                              ? `${formatNumber(c.lost_capacity_liters)} L`
                              : c.isolated_stations?.length
                              ? `${c.isolated_stations.length} isolated`
                              : "Redundant path active"}
                          </td>
                          <td className="py-3 px-4 text-muted-foreground">{c.mitigation}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          ) : (
            <ErrorState title="Contingency Data Unavailable" message="Could not evaluate N-1 scenarios" onRetry={loadContingencyData} />
          )}
        </div>
      )}

      {viewMode === "conformal" && (
        <div className="space-y-6">
          {conformalLoading && !conformalData ? (
            <LoadingSkeleton count={3} />
          ) : conformalData ? (
            <>
              {/* Conformal Calibration Summary */}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
                <div className="rounded-lg border border-border bg-card p-4">
                  <div className="text-xs font-medium text-muted-foreground">Target Coverage</div>
                  <div className="mt-1 text-2xl font-bold text-foreground">
                    {(conformalData.target_coverage * 100).toFixed(0)}%
                  </div>
                </div>

                <div className="rounded-lg border border-border bg-card p-4">
                  <div className="text-xs font-medium text-muted-foreground">Empirical Coverage</div>
                  <div className="mt-1 flex items-baseline gap-2">
                    <span className="text-2xl font-bold text-emerald-400">
                      {(conformalData.empirical_coverage * 100).toFixed(1)}%
                    </span>
                    <span className="text-[10px] text-muted-foreground">
                      (N={conformalData.calibration_sample_size})
                    </span>
                  </div>
                </div>

                <div className="rounded-lg border border-border bg-card p-4">
                  <div className="text-xs font-medium text-muted-foreground">Calibration Status</div>
                  <div className="mt-1">
                    <span className="inline-flex rounded bg-emerald-500/20 px-2 py-0.5 text-xs font-bold text-emerald-400">
                      ✓ Statistically Valid
                    </span>
                  </div>
                </div>

                <div className="rounded-lg border border-border bg-card p-4">
                  <div className="text-xs font-medium text-muted-foreground">Base Conformal Quantile</div>
                  <div className="mt-1 text-2xl font-bold text-foreground">
                    ±{conformalData.base_conformal_quantile_liters} L
                  </div>
                </div>
              </div>

              {/* Stations Conformal Projections Table */}
              <div className="rounded-lg border border-border bg-card shadow-sm overflow-hidden">
                <div className="p-4 border-b border-border bg-muted/20">
                  <h3 className="text-sm font-semibold text-foreground">
                    24-Hour Calibrated Prediction Bands (Diesel Inventory)
                  </h3>
                  <p className="text-xs text-muted-foreground">
                    Point estimates with split-conformal 90% non-parametric coverage bounds
                  </p>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-muted/30 border-b border-border/80 text-muted-foreground">
                      <tr>
                        <th className="py-3 px-4 font-medium">Station</th>
                        <th className="py-3 px-3 font-medium text-right">Current Inv (L)</th>
                        <th className="py-3 px-3 font-medium text-right">+4 Ticks (Lower / Point / Upper)</th>
                        <th className="py-3 px-3 font-medium text-right">+12 Ticks</th>
                        <th className="py-3 px-3 font-medium text-right">+24 Ticks</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/40">
                      {conformalData.station_intervals?.map((st: any) => {
                        const diesel = st.fuels?.DIESEL?.projections || [];
                        const p4 = diesel[0];
                        const p12 = diesel[2] || diesel[1];
                        const p24 = diesel[diesel.length - 1];

                        return (
                          <tr key={st.station_id} className="transition-colors hover:bg-muted/20">
                            <td className="py-3 px-4 font-medium text-foreground">{st.station_name}</td>
                            <td className="py-3 px-3 text-right font-mono text-foreground">
                              {formatNumber(st.fuels?.DIESEL?.current_inventory || 0)}
                            </td>
                            <td className="py-3 px-3 text-right font-mono text-muted-foreground">
                              {p4 ? `${p4.lower_bound} / ${p4.point_estimate} / ${p4.upper_bound}` : "—"}
                            </td>
                            <td className="py-3 px-3 text-right font-mono text-muted-foreground">
                              {p12 ? `${p12.lower_bound} / ${p12.point_estimate} / ${p12.upper_bound}` : "—"}
                            </td>
                            <td className="py-3 px-3 text-right font-mono text-muted-foreground">
                              {p24 ? `${p24.lower_bound} / ${p24.point_estimate} / ${p24.upper_bound}` : "—"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          ) : (
            <ErrorState title="Conformal Data Unavailable" message="Could not compute conformal intervals" onRetry={loadConformalData} />
          )}
        </div>
      )}
    </div>
  );
}
