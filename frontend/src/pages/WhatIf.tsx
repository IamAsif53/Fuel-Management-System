import { useState } from "react";
import { executeWhatIf } from "@/api/client";
import type { WhatIfResponse, WhatIfScenario } from "@/api/types";
import { useLiveState } from "@/hooks/useLiveState";
import { LoadingSkeleton } from "@/components/LoadingSkeleton";
import { ErrorState } from "@/components/ErrorState";

export default function WhatIfPage() {
  const { state } = useLiveState();
  const [horizon, setHorizon] = useState<number>(48);
  const [eventType, setEventType] = useState<string>("demand_spike");
  const [eventOffset, setEventOffset] = useState<number>(4);
  const [eventDuration, setEventDuration] = useState<number>(24);
  const [eventMultiplier, setEventMultiplier] = useState<number>(1.8);

  const [customRoute, setCustomRoute] = useState<string>("r-1");
  const [customFuel, setCustomFuel] = useState<string>("DIESEL");
  const [customQty, setCustomQty] = useState<number>(3000);

  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<WhatIfResponse | null>(null);

  const handleSimulate = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const events = [
        {
          type: eventType,
          start_offset: eventOffset,
          duration_ticks: eventDuration,
          parameters: eventType === "demand_spike" ? { multiplier: eventMultiplier } : {},
        },
      ];
      const lines = [
        {
          route_id: customRoute,
          fuel_type: customFuel,
          quantity: customQty,
        },
      ];
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
          <h1 className="text-2xl font-bold tracking-tight text-foreground">What-If Scenario Simulation</h1>
          <p className="text-sm text-muted-foreground">
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
        <div className="rounded-lg border border-border bg-card p-4 space-y-3">
          <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
            <span>⚡ Hypothetical Disruption / Event</span>
          </h3>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <label className="text-muted-foreground block mb-1">Event Type</label>
              <select
                value={eventType}
                onChange={(e) => setEventType(e.target.value)}
                className="w-full rounded border border-border bg-background px-2.5 py-1.5 text-foreground"
              >
                <option value="demand_spike">Demand Spike (+Surge)</option>
                <option value="route_disruption">Route Disruption (Cut)</option>
                <option value="station_outage">Station Outage (Closed)</option>
                <option value="shipment_delay">Shipment Delay</option>
              </select>
            </div>
            <div>
              <label className="text-muted-foreground block mb-1">Horizon (Ticks)</label>
              <select
                value={horizon}
                onChange={(e) => setHorizon(Number(e.target.value))}
                className="w-full rounded border border-border bg-background px-2.5 py-1.5 text-foreground"
              >
                <option value={24}>24 Ticks (6 Hours)</option>
                <option value={48}>48 Ticks (12 Hours)</option>
                <option value={96}>96 Ticks (24 Hours)</option>
              </select>
            </div>
            <div>
              <label className="text-muted-foreground block mb-1">Start Offset (Ticks)</label>
              <input
                type="number"
                value={eventOffset}
                onChange={(e) => setEventOffset(Number(e.target.value))}
                className="w-full rounded border border-border bg-background px-2.5 py-1.5 text-foreground"
                min={0}
                max={96}
              />
            </div>
            <div>
              <label className="text-muted-foreground block mb-1">Duration (Ticks)</label>
              <input
                type="number"
                value={eventDuration}
                onChange={(e) => setEventDuration(Number(e.target.value))}
                className="w-full rounded border border-border bg-background px-2.5 py-1.5 text-foreground"
                min={1}
                max={96}
              />
            </div>
            {eventType === "demand_spike" && (
              <div className="col-span-2">
                <label className="text-muted-foreground block mb-1">Demand Multiplier (×)</label>
                <input
                  type="number"
                  step="0.1"
                  value={eventMultiplier}
                  onChange={(e) => setEventMultiplier(Number(e.target.value))}
                  className="w-full rounded border border-border bg-background px-2.5 py-1.5 text-foreground"
                  min={1.1}
                  max={3.0}
                />
              </div>
            )}
          </div>
        </div>

        {/* Candidate Dispatch Line Builder */}
        <div className="rounded-lg border border-border bg-card p-4 space-y-3">
          <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
            <span>📦 Candidate Custom Dispatch</span>
          </h3>
          <div className="grid grid-cols-3 gap-3 text-xs">
            <div>
              <label className="text-muted-foreground block mb-1">Route ID</label>
              <input
                type="text"
                value={customRoute}
                onChange={(e) => setCustomRoute(e.target.value)}
                className="w-full rounded border border-border bg-background px-2.5 py-1.5 text-foreground"
              />
            </div>
            <div>
              <label className="text-muted-foreground block mb-1">Fuel Type</label>
              <select
                value={customFuel}
                onChange={(e) => setCustomFuel(e.target.value)}
                className="w-full rounded border border-border bg-background px-2.5 py-1.5 text-foreground"
              >
                <option value="DIESEL">Diesel</option>
                <option value="PETROL">Petrol</option>
                <option value="OCTANE">Octane</option>
              </select>
            </div>
            <div>
              <label className="text-muted-foreground block mb-1">Quantity (L)</label>
              <input
                type="number"
                step="500"
                value={customQty}
                onChange={(e) => setCustomQty(Number(e.target.value))}
                className="w-full rounded border border-border bg-background px-2.5 py-1.5 text-foreground"
                min={300}
                max={10000}
              />
            </div>
          </div>
          <div className="pt-2 flex justify-end">
            <button
              onClick={handleSimulate}
              disabled={isLoading}
              className="rounded bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
            >
              {isLoading ? "Simulating Scenarios..." : "Run What-If Simulation ▸"}
            </button>
          </div>
        </div>
      </div>

      {/* Simulation Results Section */}
      {isLoading ? (
        <LoadingSkeleton count={3} />
      ) : error ? (
        <ErrorState title="Simulation Failed" message={error} onRetry={handleSimulate} />
      ) : result ? (
        <div className="space-y-4">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>Simulation origin tick: <strong>{result.tick}</strong></span>
            <span className="italic">{result.disclaimer}</span>
          </div>

          {/* Scenario Comparison Cards */}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {result.scenarios.map((sc: WhatIfScenario) => (
              <div
                key={sc.name}
                className={`rounded-lg border p-4 space-y-3 ${
                  sc.name === "recommended"
                    ? "border-emerald-600/40 bg-emerald-500/5"
                    : sc.name === "custom"
                    ? "border-blue-600/40 bg-blue-500/5"
                    : "border-border bg-card"
                }`}
              >
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-bold uppercase tracking-wider text-foreground">
                    {sc.name.replace("_", " ")}
                  </h4>
                  <span
                    className={`rounded px-2 py-0.5 text-[10px] font-bold ${
                      sc.min_station_sl >= 0.95
                        ? "bg-emerald-500/20 text-emerald-400"
                        : sc.min_station_sl >= 0.85
                        ? "bg-amber-500/20 text-amber-400"
                        : "bg-red-500/20 text-red-400"
                    }`}
                  >
                    SL {(sc.min_station_sl * 100).toFixed(1)}%
                  </span>
                </div>

                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded border border-border/40 bg-background/50 p-2">
                    <span className="text-muted-foreground block text-[10px]">Unmet Demand</span>
                    <span className="text-sm font-mono font-bold text-foreground">
                      {sc.unmet.toLocaleString()} L
                    </span>
                  </div>
                  <div className="rounded border border-border/40 bg-background/50 p-2">
                    <span className="text-muted-foreground block text-[10px]">Overflow Loss</span>
                    <span className="text-sm font-mono font-bold text-foreground">
                      {sc.overflow.toLocaleString()} L
                    </span>
                  </div>
                </div>

                {/* Station highlights */}
                <div className="space-y-1.5 pt-1">
                  <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider block">
                    Affected Stations
                  </span>
                  <div className="max-h-40 overflow-y-auto space-y-1 text-xs">
                    {sc.stations.slice(0, 6).map((st) => (
                      <div
                        key={`${st.station_id}-${st.fuel_type}`}
                        className="flex items-center justify-between border-b border-border/20 py-1"
                      >
                        <span className="font-medium text-foreground">
                          {st.station_id} <span className="text-[10px] text-muted-foreground">({st.fuel_type})</span>
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
