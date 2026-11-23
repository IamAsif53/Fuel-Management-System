import { useState } from "react";
import { useLiveState } from "@/hooks/useLiveState";
import { LoadingSkeleton } from "@/components/LoadingSkeleton";
import { ErrorState } from "@/components/ErrorState";
import { StationCard } from "@/components/network/StationCard";
import { DepotCard } from "@/components/network/DepotCard";
import { RoutesTable } from "@/components/network/RoutesTable";
import { Fuel, Building2, GitFork } from "lucide-react";

type TabType = "stations" | "depots" | "routes";

export default function Network() {
  const { state, error, refetch, isLoading } = useLiveState();
  const [activeTab, setActiveTab] = useState<TabType>("stations");

  if (isLoading && !state) {
    return (
      <div className="space-y-6">
        <div className="flex h-10 w-64 animate-pulse rounded bg-panel-border/30" />
        <LoadingSkeleton count={3} />
      </div>
    );
  }

  if (error && !state) {
    return (
      <div className="py-12">
        <ErrorState
          title="Network data unavailable"
          message={error.message || "Could not retrieve live network state."}
          onRetry={refetch}
        />
      </div>
    );
  }

  const stations = state?.stations ?? [];
  const depots = state?.depots ?? [];
  const routes = state?.routes ?? [];
  const allocations = state?.allocations_active ?? [];
  const supplyUpcoming = state?.supply_upcoming ?? [];

  return (
    <div className="space-y-6">
      {/* Page Header and Tab Bar */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between border-b border-panel-border/60 pb-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-ink">
            Network & Inventory Infrastructure
          </h1>
          <p className="mt-1 text-xs text-ink-muted">
            Detailed asset telemetry, inventory storage levels, and transit pipelines.
          </p>
        </div>

        {/* Tab Controls */}
        <div className="flex items-center rounded-lg border border-panel-border bg-panel p-1">
          <button
            type="button"
            data-testid="tab-stations"
            onClick={() => setActiveTab("stations")}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition ${
              activeTab === "stations"
                ? "bg-accent text-white shadow-sm"
                : "text-ink-muted hover:text-ink hover:bg-surface/50"
            }`}
          >
            <Fuel size={14} />
            <span>Stations</span>
            <span className="ml-1 rounded-full bg-black/20 px-1.5 py-0.2 text-[10px]">
              {stations.length}
            </span>
          </button>

          <button
            type="button"
            data-testid="tab-depots"
            onClick={() => setActiveTab("depots")}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition ${
              activeTab === "depots"
                ? "bg-accent text-white shadow-sm"
                : "text-ink-muted hover:text-ink hover:bg-surface/50"
            }`}
          >
            <Building2 size={14} />
            <span>Depots</span>
            <span className="ml-1 rounded-full bg-black/20 px-1.5 py-0.2 text-[10px]">
              {depots.length}
            </span>
          </button>

          <button
            type="button"
            data-testid="tab-routes"
            onClick={() => setActiveTab("routes")}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition ${
              activeTab === "routes"
                ? "bg-accent text-white shadow-sm"
                : "text-ink-muted hover:text-ink hover:bg-surface/50"
            }`}
          >
            <GitFork size={14} />
            <span>Routes</span>
            <span className="ml-1 rounded-full bg-black/20 px-1.5 py-0.2 text-[10px]">
              {routes.length}
            </span>
          </button>
        </div>
      </div>

      {/* Tab Content */}
      {activeTab === "stations" && (
        <div data-testid="stations-tab-content" className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {stations.map((st) => (
            <StationCard key={st.id} station={st} allocations={allocations} />
          ))}
        </div>
      )}

      {activeTab === "depots" && (
        <div data-testid="depots-tab-content" className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {depots.map((dp) => (
            <DepotCard key={dp.id} depot={dp} supplyUpcoming={supplyUpcoming} />
          ))}
        </div>
      )}

      {activeTab === "routes" && (
        <div data-testid="routes-tab-content">
          <RoutesTable routes={routes} allocations={allocations} />
        </div>
      )}
    </div>
  );
}
