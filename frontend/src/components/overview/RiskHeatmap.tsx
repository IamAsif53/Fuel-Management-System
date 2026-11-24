import React from "react";
import type { Fuel, RiskLevel, StationView } from "@/api/types";
import { FUELS } from "@/api/types";

interface RiskHeatmapProps {
  stations: StationView[];
  onSelectStation?: (stationId: string) => void;
}

const LEVEL_COLORS: Record<RiskLevel, { bg: string; text: string; border: string }> = {
  CRITICAL: {
    bg: "bg-red-500/15 hover:bg-red-500/25",
    text: "text-red-400",
    border: "border-red-600/50",
  },
  HIGH: {
    bg: "bg-amber-500/15 hover:bg-amber-500/25",
    text: "text-amber-400",
    border: "border-amber-600/50",
  },
  MEDIUM: {
    bg: "bg-yellow-500/15 hover:bg-yellow-500/25",
    text: "text-yellow-400",
    border: "border-yellow-600/50",
  },
  LOW: {
    bg: "bg-emerald-500/10 hover:bg-emerald-500/20",
    text: "text-emerald-400",
    border: "border-emerald-600/40",
  },
};

export const RiskHeatmap: React.FC<RiskHeatmapProps> = ({ stations, onSelectStation }) => {
  return (
    <div className="rounded-lg border border-border bg-card p-4 shadow-sm" data-testid="risk-heatmap">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-foreground">Stockout Risk Heatmap</h3>
          <p className="text-xs text-muted-foreground">
            Multi-horizon Monte Carlo risk assessment & causal stockout drivers
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-red-500" /> Critical
          </span>
          <span className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-amber-500" /> High
          </span>
          <span className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-yellow-500" /> Med
          </span>
          <span className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-full bg-emerald-500" /> Low
          </span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-border/60 text-muted-foreground">
              <th className="pb-2 font-medium">Station</th>
              <th className="pb-2 font-medium">Region</th>
              {FUELS.map((fuel) => (
                <th key={fuel} className="pb-2 text-center font-medium">
                  {fuel}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/40">
            {stations.map((st) => (
              <tr
                key={st.id}
                className="transition-colors hover:bg-muted/30 cursor-pointer"
                onClick={() => onSelectStation?.(st.id)}
              >
                <td className="py-2.5 font-medium text-foreground">{st.name || st.id}</td>
                <td className="py-2.5 capitalize text-muted-foreground">{st.region_id}</td>
                {FUELS.map((fuel) => {
                  const riskCell = st.risk?.[fuel as Fuel];
                  const level: RiskLevel = riskCell?.level || "LOW";
                  const styling = LEVEL_COLORS[level] || LEVEL_COLORS.LOW;
                  const hours = riskCell?.hours_to_stockout;
                  const prob = riskCell?.stockout_prob_lead ?? (riskCell as any)?.stockout_prob;

                  return (
                    <td key={fuel} className="py-1 px-1.5 text-center">
                      <div
                        data-testid={`risk-cell-${st.id}-${fuel}`}
                        className={`inline-flex flex-col items-center justify-center rounded border px-2.5 py-1.5 min-w-[76px] transition-all ${styling.bg} ${styling.border}`}
                      >
                        <span className={`text-[11px] font-bold tracking-tight ${styling.text}`}>
                          {level}
                        </span>
                        <span className="text-[10px] text-muted-foreground">
                          {hours !== null && hours !== undefined && hours < 900
                            ? `${hours.toFixed(1)}h`
                            : "safe"}
                          {prob !== undefined && prob > 0.05
                            ? ` (${Math.round(prob * 100)}%)`
                            : ""}
                        </span>
                      </div>
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
};
