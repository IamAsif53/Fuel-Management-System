import { useEffect, useState } from "react";
import { useLiveState } from "@/hooks/useLiveState";
import type { CoverResponse, DepotView, StationView } from "@/api/types";
import { fetchCover } from "@/api/client";
import { KpiGrid } from "@/components/overview/KpiGrid";
import { NetworkGraph } from "@/components/overview/NetworkGraph";
import { InventorySummary } from "@/components/overview/InventorySummary";
import { RiskHeatmap } from "@/components/overview/RiskHeatmap";
import { FuelBudgetCard } from "@/components/overview/FuelBudgetCard";
import { EntityDrawer } from "@/components/overview/EntityDrawer";
import { LoadingSkeleton } from "@/components/LoadingSkeleton";
import { ErrorState } from "@/components/ErrorState";

export default function Overview() {
  const { state, error, refetch, isLoading } = useLiveState();
  const [selectedEntity, setSelectedEntity] = useState<DepotView | StationView | null>(null);
  const [cover, setCover] = useState<CoverResponse | null>(null);

  useEffect(() => {
    let mounted = true;
    fetchCover()
      .then((data) => {
        if (mounted) setCover(data);
      })
      .catch(() => {
        // Fallback gracefully
      });
    return () => {
      mounted = false;
    };
  }, [state?.tick]);

  if (isLoading && !state) {
    return (
      <div className="space-y-6">
        <LoadingSkeleton count={3} />
      </div>
    );
  }

  if (error && !state) {
    return (
      <div className="py-12">
        <ErrorState
          title="Overview Unavailable"
          message={error.message || "Failed to load live simulation state."}
          onRetry={refetch}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* 1. KPI Cards Row */}
      <section aria-label="System Key Performance Indicators">
        <KpiGrid state={state} />
      </section>

      {/* 2. Main Network Topology Graph (Dominant Visual) */}
      <section aria-label="Supply Network Graph">
        <NetworkGraph
          state={state}
          onSelectEntity={(entity) => setSelectedEntity(entity)}
        />
      </section>

      {/* 3. Fuel Budget Card (F-COVER) */}
      <section aria-label="Network Fuel Budget">
        <FuelBudgetCard cover={cover} currentTick={state?.tick || 0} />
      </section>

      {/* 4. Risk Heatmap */}
      <section aria-label="Risk Heatmap">
        <RiskHeatmap
          stations={state?.stations || []}
          onSelectStation={(id) => {
            const st = (state?.stations || []).find((s) => s.id === id);
            if (st) setSelectedEntity(st);
          }}
        />
      </section>

      {/* 5. Compact Inventory Summary Table */}
      <section aria-label="Inventory Summary">
        <InventorySummary
          state={state}
          onSelectEntity={(entity) => setSelectedEntity(entity)}
        />
      </section>

      {/* 6. Entity Detail Drawer */}
      <EntityDrawer
        entity={selectedEntity}
        onClose={() => setSelectedEntity(null)}
      />
    </div>
  );
}
