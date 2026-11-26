import { useState, useEffect } from "react";
import { executeWhatIf } from "@/api/client";
import type { WhatIfResponse, WhatIfScenario, RouteView, StationView, DepotView } from "@/api/types";
import { useLiveState } from "@/hooks/useLiveState";
import { LoadingSkeleton } from "@/components/LoadingSkeleton";

export default function WhatIfPage() {
  const { state } = useLiveState();
  const routes: RouteView[] = state?.routes || [];
  const stations: StationView[] = state?.stations || [];
  const depots: DepotView[] = state?.depots || [];

  const [horizon, setHorizon] = useState<number>(48);
  const [eventType, setEventType] = useState<string>("demand_spike");
  const [eventOffset, setEventOffset] = useState<number>(4);
  const [eventDuration, setEventDuration] = useState<number>(24);
  const [eventMultiplier, setEventMultiplier] = useState<number>(1.8);
  const [delayTicks, setDelayTicks] = useState<number>(4);
  const [shortfallFactor, setShortfallFactor] = useState<number>(0.5);
  const [targetEntity, setTargetEntity] = useState<string>("");

  const [includeCustomDispatch, setIncludeCustomDispatch] = useState<boolean>(true);
  const [customRoute, setCustomRoute] = useState<string>("route-gazipur-mirpur");
  const [customFuel, setCustomFuel] = useState<string>("DIESEL");
  const [customQty, setCustomQty] = useState<number>(2400);

  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<WhatIfResponse | null>(null);

  // Sync route default when live routes load
  useEffect(() => {
    if (routes.length > 0 && !routes.some((r) => r.id === customRoute)) {
      setCustomRoute(routes[0].id);
    }
  }, [routes, customRoute]);

  const selectedRoute = routes.find((r) => r.id === customRoute);
  const sourceDepot = depots.find((d) => d.id === selectedRoute?.source_depot_id);
  const depotFuelAvail = sourceDepot?.inventory?.[customFuel as keyof typeof sourceDepot.inventory] ?? 0;

  const handleSimulate = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const params: Record<string, unknown> = {};
      if (eventType === "demand_spike") {
        params.multiplier = eventMultiplier;
        if (targetEntity) params.station_id = targetEntity;
      } else if (eventType === "route_disruption") {
        if (targetEntity) params.route_id = targetEntity;
      } else if (eventType === "station_outage") {
        if (targetEntity) params.station_id = targetEntity;
      } else if (eventType === "depot_constraint") {
        if (targetEntity) params.depot_id = targetEntity;
      } else if (eventType === "shipment_delay") {
        params.delay_ticks = delayTicks;
        if (targetEntity) params.depot_id = targetEntity;
      } else if (eventType === "supply_shortfall") {
        params.factor = shortfallFactor;
        if (targetEntity) params.depot_id = targetEntity;
      }

      const events = [
        {
          type: eventType,
          start_offset: eventOffset,
          duration_ticks: eventDuration,
          parameters: params,
        },
      ];

      const lines = includeCustomDispatch
        ? [
            {
              route_id: customRoute,
              fuel_type: customFuel,
              quantity: customQty,
            },
          ]
        : [];

      const res = await executeWhatIf({
        horizon_ticks: horizon,
        events,
        lines,
        compare: ["do_nothing", "recommended", "custom"],
      });
      setResult(res);
    } catch (err: any) {
      setError(err?.message || "Failed to execute what-if simulation");
    } finally {
      setIsLoading(false);
    }
  };

  const isScarcity = Object.values(state?.scarcity || {}).some(Boolean);

  return (
    <div className="space-y-6" data-testid="whatif-page">
      {/* Header */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-ink">What-If Scenario Simulation</h1>
          <p className="text-xs text-ink-muted mt-1">
            Counterfactual digital twin simulation: evaluate disruptions, candidate dispatches, and multi-scenario impacts.
          </p>
        </div>
      </div>

      {/* Scarcity Banner */}
      {isScarcity && (
        <div className="rounded-lg border border-red-600/40 bg-red-500/10 p-3 text-xs text-red-400 font-semibold flex items-center justify-between">
          <span>⚠️ SCARCITY MODE ACTIVE: Strategic fuel rationing and max-min fairness active across network.</span>
          <span className="uppercase tracking-wider text-[10px] rounded bg-red-500/20 px-2 py-0.5">Critical Supply</span>
        </div>
      )}

      {/* Control Configuration Grid */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Hypothetical Event Builder */}
        <div className="rounded-xl border border-bg-border bg-bg-panel p-4 space-y-4 shadow-sm">
          <h3 className="text-sm font-semibold text-ink flex items-center gap-2">
            <span>⚡ Hypothetical Disruption / Event</span>
          </h3>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <label className="text-ink-muted block mb-1 text-xs font-medium">Event Type</label>
              <select
                value={eventType}
                onChange={(e) => {
                  setEventType(e.target.value);
                  setTargetEntity("");
                }}
                className="w-full rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand transition-colors"
              >
                <option value="demand_spike" className="bg-bg-raised text-ink">Demand Spike (+Surge)</option>
                <option value="route_disruption" className="bg-bg-raised text-ink">Route Disruption (Cut)</option>
                <option value="station_outage" className="bg-bg-raised text-ink">Station Outage (Closed)</option>
                <option value="depot_constraint" className="bg-bg-raised text-ink">Depot Constraint (Constrained)</option>
                <option value="shipment_delay" className="bg-bg-raised text-ink">Shipment Delay</option>
                <option value="supply_shortfall" className="bg-bg-raised text-ink">Supply Shortfall</option>
              </select>
            </div>
            <div>
              <label className="text-ink-muted block mb-1 text-xs font-medium">Horizon (Ticks)</label>
              <select
                value={horizon}
                onChange={(e) => setHorizon(Number(e.target.value))}
                className="w-full rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand transition-colors"
              >
                <option value={24} className="bg-bg-raised text-ink">24 Ticks (6 Hours)</option>
                <option value={48} className="bg-bg-raised text-ink">48 Ticks (12 Hours)</option>
                <option value={96} className="bg-bg-raised text-ink">96 Ticks (24 Hours)</option>
              </select>
            </div>

            <div>
              <label className="text-ink-muted block mb-1 text-xs font-medium">Start Offset (Ticks)</label>
              <input
                type="number"
                value={eventOffset}
                onChange={(e) => setEventOffset(Number(e.target.value))}
                className="w-full rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs text-ink placeholder:text-ink-faint focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand transition-colors"
                min={0}
                max={96}
              />
            </div>
            <div>
              <label className="text-ink-muted block mb-1 text-xs font-medium">Duration (Ticks)</label>
              <input
                type="number"
                value={eventDuration}
                onChange={(e) => setEventDuration(Number(e.target.value))}
                className="w-full rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs text-ink placeholder:text-ink-faint focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand transition-colors"
                min={1}
                max={96}
              />
            </div>

            {/* Target Selectors depending on Event Type */}
            {eventType === "demand_spike" && (
              <>
                <div>
                  <label className="text-ink-muted block mb-1 text-xs font-medium">Target Station</label>
                  <select
                    value={targetEntity}
                    onChange={(e) => setTargetEntity(e.target.value)}
                    className="w-full rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand transition-colors"
                  >
                    <option value="" className="bg-bg-raised text-ink">All Stations (Network Wide)</option>
                    {stations.map((s) => (
                      <option key={s.id} value={s.id} className="bg-bg-raised text-ink">
                        {s.name || s.id} ({s.region_id})
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-ink-muted block mb-1 text-xs font-medium">Demand Multiplier (×)</label>
                  <input
                    type="number"
                    step="0.1"
                    value={eventMultiplier}
                    onChange={(e) => setEventMultiplier(Number(e.target.value))}
                    className="w-full rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs text-ink placeholder:text-ink-faint focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand transition-colors"
                    min={1.1}
                    max={3.0}
                  />
                </div>
              </>
            )}

            {eventType === "route_disruption" && (
              <div className="col-span-2">
                <label className="text-ink-muted block mb-1 text-xs font-medium">Target Route</label>
                <select
                  value={targetEntity}
                  onChange={(e) => setTargetEntity(e.target.value)}
                  className="w-full rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand transition-colors"
                >
                  <option value="" className="bg-bg-raised text-ink">All Routes (Full Network Cut)</option>
                  {routes.map((r) => (
                    <option key={r.id} value={r.id} className="bg-bg-raised text-ink">
                      {r.id} ({r.source_depot_id} → {r.destination_station_id})
                    </option>
                  ))}
                </select>
              </div>
            )}

            {eventType === "station_outage" && (
              <div className="col-span-2">
                <label className="text-ink-muted block mb-1 text-xs font-medium">Target Station Outage</label>
                <select
                  value={targetEntity}
                  onChange={(e) => setTargetEntity(e.target.value)}
                  className="w-full rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand transition-colors"
                >
                  <option value="" className="bg-bg-raised text-ink">All Stations (Total Blackout)</option>
                  {stations.map((s) => (
                    <option key={s.id} value={s.id} className="bg-bg-raised text-ink">
                      {s.name || s.id} ({s.region_id})
                    </option>
                  ))}
                </select>
              </div>
            )}

            {(eventType === "depot_constraint" ||
              eventType === "shipment_delay" ||
              eventType === "supply_shortfall") && (
              <div className="col-span-2">
                <label className="text-ink-muted block mb-1 text-xs font-medium">Target Depot</label>
                <select
                  value={targetEntity}
                  onChange={(e) => setTargetEntity(e.target.value)}
                  className="w-full rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand transition-colors"
                >
                  <option value="" className="bg-bg-raised text-ink">All Depots</option>
                  {depots.map((d) => (
                    <option key={d.id} value={d.id} className="bg-bg-raised text-ink">
                      {d.name || d.id}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {eventType === "shipment_delay" && (
              <div>
                <label className="text-ink-muted block mb-1 text-xs font-medium">Delay Ticks</label>
                <input
                  type="number"
                  value={delayTicks}
                  onChange={(e) => setDelayTicks(Number(e.target.value))}
                  className="w-full rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand transition-colors"
                  min={1}
                  max={48}
                />
              </div>
            )}

            {eventType === "supply_shortfall" && (
              <div>
                <label className="text-ink-muted block mb-1 text-xs font-medium">Shortfall Factor (×)</label>
                <input
                  type="number"
                  step="0.05"
                  value={shortfallFactor}
                  onChange={(e) => setShortfallFactor(Number(e.target.value))}
                  className="w-full rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand transition-colors"
                  min={0.1}
                  max={0.95}
                />
              </div>
            )}
          </div>
          <p className="text-[11px] text-ink-faint border-t border-bg-border/40 pt-3">
            Projection only — nothing is executed against the live simulator until you approve a real recommendation.
          </p>
        </div>

        {/* Candidate Dispatch Line Builder */}
        <div className="rounded-xl border border-bg-border bg-bg-panel p-4 space-y-4 shadow-sm flex flex-col justify-between">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-ink flex items-center gap-2">
                <span>📦 Candidate Custom Dispatch</span>
              </h3>
              <label className="flex items-center gap-1.5 text-xs text-ink-muted cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={includeCustomDispatch}
                  onChange={(e) => setIncludeCustomDispatch(e.target.checked)}
                  className="rounded border-bg-border bg-bg-raised text-brand focus:ring-brand"
                />
                <span>Include in simulation</span>
              </label>
            </div>

            {includeCustomDispatch ? (
              <div className="space-y-3">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3 text-xs">
                  <div className="sm:col-span-1">
                    <label className="text-ink-muted block mb-1 text-xs font-medium">Route</label>
                    <select
                      value={customRoute}
                      onChange={(e) => setCustomRoute(e.target.value)}
                      className="w-full rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand transition-colors"
                    >
                      {routes.length > 0 ? (
                        routes.map((r) => (
                          <option key={r.id} value={r.id} className="bg-bg-raised text-ink">
                            {r.id}
                          </option>
                        ))
                      ) : (
                        <option value="route-gazipur-mirpur" className="bg-bg-raised text-ink">
                          route-gazipur-mirpur
                        </option>
                      )}
                    </select>
                  </div>
                  <div>
                    <label className="text-ink-muted block mb-1 text-xs font-medium">Fuel Type</label>
                    <select
                      value={customFuel}
                      onChange={(e) => setCustomFuel(e.target.value)}
                      className="w-full rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs text-ink focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand transition-colors"
                    >
                      <option value="DIESEL" className="bg-bg-raised text-ink">Diesel</option>
                      <option value="PETROL" className="bg-bg-raised text-ink">Petrol</option>
                      <option value="OCTANE" className="bg-bg-raised text-ink">Octane</option>
                    </select>
                  </div>
                  <div>
                    <label className="text-ink-muted block mb-1 text-xs font-medium">Quantity (L)</label>
                    <input
                      type="number"
                      step="100"
                      value={customQty}
                      onChange={(e) => setCustomQty(Number(e.target.value))}
                      className="w-full rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs text-ink placeholder:text-ink-faint focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand transition-colors"
                      min={100}
                      max={12000}
                    />
                  </div>
                </div>

                {selectedRoute && (
                  <div className="rounded-lg bg-bg-raised/60 border border-bg-border/60 p-2 text-[11px] text-ink-muted flex items-center justify-between">
                    <span>
                      Corridor: <strong className="text-ink">{selectedRoute.source_depot_id}</strong> → <strong className="text-ink">{selectedRoute.destination_station_id}</strong> ({selectedRoute.transit_ticks} ticks)
                    </span>
                    <span>
                      Max Route Limit: <strong className="text-ink">{selectedRoute.max_shipment.toLocaleString()} L</strong>
                    </span>
                  </div>
                )}
                {sourceDepot && (
                  <div className="text-[10px] text-ink-faint">
                    Depot Inventory ({sourceDepot.id}): {depotFuelAvail.toLocaleString()} L {customFuel} available
                  </div>
                )}
              </div>
            ) : (
              <div className="rounded-lg bg-bg-raised/40 border border-bg-border/40 p-4 text-xs text-ink-faint italic text-center">
                Custom dispatch disabled. Simulation will compare Do Nothing vs Recommended plan under hypothetical event.
              </div>
            )}
          </div>

          <div className="pt-2 flex justify-end">
            <button
              onClick={handleSimulate}
              disabled={isLoading}
              className="rounded-lg bg-brand hover:bg-brand/90 text-white font-semibold px-4 py-2 text-xs transition-colors shadow-sm focus:outline-none focus:ring-2 focus:ring-brand/40 disabled:opacity-50 flex items-center gap-2"
            >
              {isLoading ? (
                <>
                  <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent" />
                  <span>Simulating Scenarios...</span>
                </>
              ) : (
                <span>Run What-If Simulation ▸</span>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Inline Validation / API Error Banner */}
      {error && (
        <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-4 text-xs text-red-300 shadow-sm flex items-start justify-between gap-3">
          <div className="space-y-1">
            <span className="font-bold text-red-400 block text-sm">❌ Dispatch Validation / Simulation Error</span>
            <p className="font-mono text-xs">{error}</p>
          </div>
          <button
            onClick={() => setError(null)}
            className="text-red-400 hover:text-red-200 text-xs font-semibold px-2 py-1 rounded bg-red-500/20"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Simulation Results Section */}
      {isLoading ? (
        <LoadingSkeleton count={3} />
      ) : result ? (
        <div className="space-y-4">
          <div className="flex items-center justify-between text-xs text-ink-muted">
            <span>Simulation origin tick: <strong className="text-ink">{result.tick}</strong> (Horizon: <strong className="text-ink">{horizon} ticks</strong>)</span>
            <span className="italic text-ink-faint">{result.disclaimer}</span>
          </div>

          {/* Scenario Comparison Cards */}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {result.scenarios.map((sc: WhatIfScenario) => (
              <div
                key={sc.name}
                className={`rounded-xl border p-4 space-y-3 shadow-sm ${
                  sc.name === "recommended"
                    ? "border-emerald-600/40 bg-emerald-500/10"
                    : sc.name === "custom"
                    ? "border-brand/40 bg-brand/10"
                    : "border-bg-border bg-bg-panel"
                }`}
              >
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-bold uppercase tracking-wider text-ink flex items-center gap-1.5">
                    {sc.name === "recommended" && <span>✨</span>}
                    {sc.name === "custom" && <span>📦</span>}
                    {sc.name === "do_nothing" && <span>⏸️</span>}
                    {sc.name.replace("_", " ")}
                  </h4>
                  <span
                    className={`rounded px-2 py-0.5 text-[10px] font-bold ${
                      sc.min_station_sl >= 0.95
                        ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                        : sc.min_station_sl >= 0.85
                        ? "bg-amber-500/20 text-amber-400 border border-amber-500/30"
                        : "bg-red-500/20 text-red-400 border border-red-500/30"
                    }`}
                  >
                    SL {(sc.min_station_sl * 100).toFixed(1)}%
                  </span>
                </div>

                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded-lg border border-bg-border bg-bg-raised/70 p-2.5">
                    <span className="text-ink-muted block text-[10px] font-medium uppercase tracking-wider">Unmet Demand</span>
                    <span className="text-sm font-mono font-bold text-ink">
                      {sc.unmet.toLocaleString()} L
                    </span>
                  </div>
                  <div className="rounded-lg border border-bg-border bg-bg-raised/70 p-2.5">
                    <span className="text-ink-muted block text-[10px] font-medium uppercase tracking-wider">Overflow Loss</span>
                    <span className="text-sm font-mono font-bold text-ink">
                      {sc.overflow.toLocaleString()} L
                    </span>
                  </div>
                </div>

                {/* Station highlights & inventory delta */}
                <div className="space-y-1.5 pt-1">
                  <span className="text-[10px] font-semibold text-ink-muted uppercase tracking-wider block">
                    Station Projections & Inventory Delta
                  </span>
                  <div className="max-h-56 overflow-y-auto space-y-1.5 text-xs pr-1">
                    {sc.stations.slice(0, 8).map((st) => (
                      <div
                        key={`${st.station_id}-${st.fuel_type}`}
                        className="rounded border border-bg-border/40 bg-bg-raised/40 p-2 space-y-1"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-medium text-ink">
                            {st.station_id} <span className="text-[10px] text-ink-muted">({st.fuel_type})</span>
                          </span>
                          <span
                            className={`font-mono text-[11px] font-semibold ${
                              st.stockout_prob > 0.4
                                ? "text-red-400"
                                : st.stockout_prob > 0.1
                                ? "text-amber-400"
                                : "text-emerald-400"
                            }`}
                          >
                            {(st.stockout_prob * 100).toFixed(0)}% risk
                          </span>
                        </div>

                        {st.ending_inv !== undefined && st.delta_liters !== undefined && (
                          <div className="flex items-center justify-between text-[10px] text-ink-muted font-mono">
                            <span>Ending: {st.ending_inv.toLocaleString()} L</span>
                            <span className={st.delta_liters >= 0 ? "text-emerald-400 font-semibold" : "text-amber-400"}>
                              {st.delta_liters >= 0 ? "+" : ""}{st.delta_liters.toLocaleString()} L
                            </span>
                          </div>
                        )}

                        {st.unmet_liters && st.unmet_liters > 0 ? (
                          <div className="text-[10px] text-red-400 font-mono">
                            Unmet: {st.unmet_liters.toLocaleString()} L
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
