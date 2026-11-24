import React from "react";
import type { CoverResponse, Fuel } from "@/api/types";
import { FUELS } from "@/api/types";
import { formatNumber } from "@/lib/format";

interface FuelBudgetCardProps {
  cover: CoverResponse | null;
  currentTick?: number;
}

export const FuelBudgetCard: React.FC<FuelBudgetCardProps> = ({ cover, currentTick = 0 }) => {
  if (!cover) {
    return (
      <div className="rounded-lg border border-border bg-card p-4 shadow-sm" data-testid="fuel-budget">
        <h3 className="text-base font-semibold text-foreground">Network Fuel Budget (F-COVER)</h3>
        <p className="text-xs text-muted-foreground mt-2">Loading fuel budget data...</p>
      </div>
    );
  }

  const supplyEnds = cover.supply_ends_tick || 212;
  const ticksRemaining = Math.max(0, supplyEnds - currentTick);
  const hoursRemaining = (ticksRemaining * 15) / 60;

  return (
    <div className="rounded-lg border border-border bg-card p-4 shadow-sm" data-testid="fuel-budget">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-foreground">Network Fuel Budget (F-COVER)</h3>
          <p className="text-xs text-muted-foreground">
            Total supply headroom vs forecast demand across depots & stations
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
            Supply Ends: t={supplyEnds} ({hoursRemaining.toFixed(1)}h left)
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {FUELS.map((fuel) => {
          const metric = cover.network[fuel as Fuel] || { available: 0, demand_96: 0, cover_days: 0 };
          const isScarce = cover.scarcity[fuel as Fuel] || metric.cover_days < 3.0;

          return (
            <div
              key={fuel}
              className={`rounded-md border p-3 transition-colors ${
                isScarce
                  ? "border-red-600/40 bg-red-500/10"
                  : "border-border/60 bg-muted/20"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-foreground">{fuel}</span>
                {isScarce && (
                  <span className="rounded bg-red-500/20 px-1.5 py-0.5 text-[10px] font-bold text-red-400">
                    SCARCITY
                  </span>
                )}
              </div>
              <div className="mt-2 flex items-baseline gap-1">
                <span className="text-2xl font-bold tracking-tight text-foreground">
                  {metric.cover_days.toFixed(1)}
                </span>
                <span className="text-xs text-muted-foreground">days cover</span>
              </div>
              <div className="mt-2 text-[11px] text-muted-foreground space-y-0.5">
                <div className="flex justify-between">
                  <span>Available:</span>
                  <span className="font-medium text-foreground">{formatNumber(metric.available)} L</span>
                </div>
                <div className="flex justify-between">
                  <span>24h Demand:</span>
                  <span className="font-medium text-foreground">{formatNumber(metric.demand_96)} L</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
