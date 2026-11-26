import { useState, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { useLiveState } from "@/hooks/useLiveState";
import { LoadingSkeleton } from "@/components/LoadingSkeleton";
import { ErrorState } from "@/components/ErrorState";
import { StationCard } from "@/components/network/StationCard";
import { DepotCard } from "@/components/network/DepotCard";
import { RoutesTable } from "@/components/network/RoutesTable";
import { TimeTravelSlider } from "@/components/network/TimeTravelSlider";
import { Fuel, Building2, GitFork } from "lucide-react";
import { api } from "@/api/client";
import type { WorldState } from "@/api/types";
import clsx from "clsx";

type TabType = "stations" | "depots" | "routes";

export default function Network() {
  const { state, error, refetch, isLoading } = useLiveState();
  const [searchParams] = useSearchParams();
  const highlightStation = searchParams.get("station");
  const highlightDepot = searchParams.get("depot");
  const [activeTab, setActiveTab] = useState<TabType>(
    highlightDepot ? "depots" : "stations"
  );
  const scrolledRef = useRef<string | null>(null);

  // Phase 17: Simulation Time-Travel Replay State
  const [isReplaying, setIsReplaying] = useState(false);
  const [availableTicks, setAvailableTicks] = useState<number[]>([]);
  const [selectedTick, setSelectedTick] = useState<number | null>(null);
  const [historicalState, setHistoricalState] = useState<WorldState | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);

  useEffect(() => {
    // Fetch available historical ticks
    api<{ ticks: number[]; total: number }>("/state/history/ticks")
      .then((res) => {
        if (res && Array.isArray(res.ticks)) {
          setAvailableTicks(res.ticks);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (highlightDepot) {
      setActiveTab("depots");
    } else if (highlightStation) {
      setActiveTab("stations");
    }
  }, [highlightStation, highlightDepot]);

  useEffect(() => {
    const targetId = highlightDepot
      ? `depot-card-${highlightDepot}`
      : highlightStation
        ? `station-card-${highlightStation}`
        : null;
    if (!targetId || scrolledRef.current === targetId) return;
    const t = window.setTimeout(() => {
      const el = document.querySelector(`[data-testid="${targetId}"]`);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        scrolledRef.current = targetId;
      }
    }, 120);
    return () => window.clearTimeout(t);
  }, [highlightStation, highlightDepot, activeTab, state]);

  const handleSelectTick = async (tick: number) => {
    setSelectedTick(tick);
    setIsLoadingHistory(true);
    try {
      const snap = await api<WorldState>(`/state/history?tick=${tick}`);
      setHistoricalState(snap);
    } catch (err) {
      // Keep previous or fallback
    } finally {
      setIsLoadingHistory(false);
    }
  };

  const handleToggleReplay = (enable: boolean) => {
    setIsReplaying(enable);
    if (enable) {
      const initialTick = state?.tick ?? (availableTicks.length > 0 ? availableTicks[availableTicks.length - 1] : 0);
      handleSelectTick(initialTick);
    } else {
      setHistoricalState(null);
      setSelectedTick(null);
    }
  };

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

  const activeState = isReplaying && historicalState ? historicalState : state;
  const stations = activeState?.stations ?? [];
  const depots = activeState?.depots ?? [];
  const routes = activeState?.routes ?? [];
  const allocations = activeState?.allocations_active ?? [];
  const supplyUpcoming = activeState?.supply_upcoming ?? [];

  return (
    <div className="space-y-6">
      {/* Time-Travel Replay Scrubber */}
      <TimeTravelSlider
        isReplaying={isReplaying}
        selectedTick={selectedTick}
        availableTicks={availableTicks}
        onSelectTick={handleSelectTick}
        onToggleReplay={handleToggleReplay}
        isLoading={isLoadingHistory}
      />

      {/* Page Header and Tab Bar */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between border-b border-panel-border/60 pb-4">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-ink">
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
            <div
              key={st.id}
              className={clsx(
                highlightStation === st.id &&
                  "rounded-lg ring-2 ring-brand ring-offset-2 ring-offset-bg"
              )}
            >
              <StationCard station={st} allocations={allocations} />
            </div>
          ))}
        </div>
      )}

      {activeTab === "depots" && (
        <div data-testid="depots-tab-content" className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {depots.map((dp) => (
            <div
              key={dp.id}
              className={clsx(
                highlightDepot === dp.id &&
                  "rounded-lg ring-2 ring-brand ring-offset-2 ring-offset-bg"
              )}
            >
              <DepotCard depot={dp} supplyUpcoming={supplyUpcoming} />
            </div>
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
