import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { SystemStatus, HealthStatus } from "@/api/types";
import { StatusPill } from "@/components/StatusPill";
import {
  Server,
  Database,
  Cpu,
  Activity,
  Layers,
  Radio,
  ExternalLink,
  RefreshCw,
  Clock,
  Gauge,
  AlertOctagon,
  CheckCircle,
} from "lucide-react";

const COMPONENT_METADATA: Record<
  string,
  { label: string; description: string; icon: typeof Server }
> = {
  backend_api: {
    label: "Backend Core API",
    description: "FastAPI REST and SSE streaming gateway",
    icon: Server,
  },
  database: {
    label: "TimescaleDB / Postgres",
    description: "Persistent telemetry and hypertable storage",
    icon: Database,
  },
  redis: {
    label: "Redis Cache & Pub/Sub",
    description: "Real-time state broadcast and event distribution",
    icon: Radio,
  },
  fuel_simulator: {
    label: "Fuel Simulator",
    description: "Discrete-event simulation engine",
    icon: Activity,
  },
  prediction_service: {
    label: "Prediction Service",
    description: "Demand forecasting and ML inference model",
    icon: Cpu,
  },
  decision_engine: {
    label: "Decision Engine",
    description: "Optimization and dispatch heuristics solver",
    icon: Layers,
  },
  notifier: {
    label: "Notification Dispatcher",
    description: "Alert webhook and telemetry notifier",
    icon: Radio,
  },
};

export default function SystemHealth() {
  const { data, error, isLoading, refetch, isFetching } = useQuery<SystemStatus>({
    queryKey: ["system-status"],
    queryFn: () => api<SystemStatus>("/system/status"),
    refetchInterval: 3000,
  });

  if (isLoading && !data) {
    return (
      <div className="space-y-6">
        <div className="h-8 w-64 animate-pulse rounded bg-panel-border/30" />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-24 animate-pulse rounded-lg bg-panel border border-panel-border/30" />
          ))}
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-6 text-center">
        <AlertOctagon size={32} className="mx-auto mb-2 text-red-400" />
        <h2 className="text-base font-semibold text-ink">System Telemetry Unavailable</h2>
        <p className="mt-1 text-xs text-ink-muted">
          Unable to establish connection to the core API monitoring endpoint.
        </p>
        <button
          type="button"
          onClick={() => refetch()}
          className="mt-4 inline-flex items-center gap-1.5 rounded bg-panel-border px-3 py-1.5 text-xs font-medium text-ink hover:bg-panel-border/80 transition"
        >
          <RefreshCw size={12} />
          <span>Retry Connection</span>
        </button>
      </div>
    );
  }

  const overallStatus: HealthStatus = (data?.overall as HealthStatus) || "Unknown";
  const components = data?.components || {};
  const sloList = data?.slo || [];
  const versions = data?.versions || {};
  const p95Latency = data?.p95_latency_ms ?? 0;
  const errorRate = data?.error_rate ?? 0;

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between border-b border-panel-border/60 pb-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold tracking-tight text-ink">
              System Observability & Health
            </h1>
            <div data-testid="overall-status-pill">
              <StatusPill status={overallStatus} />
            </div>
          </div>
          <p className="mt-1 text-xs text-ink-muted">
            Continuous health telemetry, latency measurements, and SLO compliance across microservices.
          </p>
        </div>

        <div className="flex items-center gap-3">
          {data?.engine_mode && (
            <span
              data-testid="health-engine-mode"
              className="rounded border border-panel-border bg-panel px-2.5 py-1 font-mono text-xs text-ink"
            >
              Mode: <strong className="text-accent">{data.engine_mode}</strong>
            </span>
          )}
          <button
            type="button"
            onClick={() => refetch()}
            title="Refresh status"
            className="flex items-center gap-1.5 rounded-md border border-panel-border bg-panel px-3 py-1.5 text-xs font-medium text-ink transition hover:bg-surface/50"
          >
            <RefreshCw size={13} className={isFetching ? "animate-spin text-accent" : "text-ink-muted"} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Metrics Summary Grid */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {/* Overall Status Card */}
        <div className="rounded-lg border border-panel-border bg-panel p-4 shadow-panel">
          <div className="flex items-center justify-between text-xs text-ink-muted">
            <span>Overall Status</span>
            <Activity size={16} className="text-ink-muted" />
          </div>
          <div className="mt-2 flex items-center gap-2">
            <span className="text-lg font-bold text-ink">{overallStatus}</span>
          </div>
          <p className="mt-1 text-[11px] text-ink-muted">Platform operational status</p>
        </div>

        {/* p95 Latency Card */}
        <div className="rounded-lg border border-panel-border bg-panel p-4 shadow-panel">
          <div className="flex items-center justify-between text-xs text-ink-muted">
            <span>Core API p95 Latency</span>
            <Gauge size={16} className="text-ink-muted" />
          </div>
          <div className="mt-2 flex items-baseline gap-1">
            <span
              data-testid="p95-latency-value"
              className={`text-xl font-bold font-mono ${
                p95Latency < 200
                  ? "text-emerald-400"
                  : p95Latency < 500
                  ? "text-amber-400"
                  : "text-red-400"
              }`}
            >
              {p95Latency.toFixed(1)}
            </span>
            <span className="font-mono text-xs text-ink-muted">ms</span>
          </div>
          <p className="mt-1 text-[11px] text-ink-muted">Rolling 100-sample p95</p>
        </div>

        {/* Error Rate Card */}
        <div className="rounded-lg border border-panel-border bg-panel p-4 shadow-panel">
          <div className="flex items-center justify-between text-xs text-ink-muted">
            <span>API Error Rate</span>
            <AlertOctagon size={16} className="text-ink-muted" />
          </div>
          <div className="mt-2 flex items-baseline gap-1">
            <span
              data-testid="error-rate-value"
              className={`text-xl font-bold font-mono ${
                errorRate === 0
                  ? "text-emerald-400"
                  : errorRate < 0.01
                  ? "text-amber-400"
                  : "text-red-400"
              }`}
            >
              {(errorRate * 100).toFixed(2)}%
            </span>
          </div>
          <p className="mt-1 text-[11px] text-ink-muted">5xx error ratio</p>
        </div>

        {/* Engine Sync Card */}
        <div className="rounded-lg border border-panel-border bg-panel p-4 shadow-panel">
          <div className="flex items-center justify-between text-xs text-ink-muted">
            <span>Decision Engine</span>
            <Clock size={16} className="text-ink-muted" />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-base font-bold text-ink font-mono">
              Tick {components.decision_engine?.tick ?? "—"}
            </span>
            {components.decision_engine?.last_sync_age_s != null && (
              <span className="font-mono text-xs text-ink-muted">
                ({components.decision_engine.last_sync_age_s.toFixed(1)}s ago)
              </span>
            )}
          </div>
          <p className="mt-1 text-[11px] text-ink-muted">Last synchronized cycle</p>
        </div>
      </div>

      {/* Service Level Objectives (SLOs) */}
      {sloList.length > 0 && (
        <div className="rounded-lg border border-panel-border bg-panel p-5 shadow-panel">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-ink">Service Level Objectives (SLO)</h3>
              <p className="text-xs text-ink-muted">
                Platform performance and availability targets
              </p>
            </div>
            <span className="rounded bg-surface px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-ink-muted">
              Rolling 30m window
            </span>
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {sloList.map((slo) => {
              const isLatency = slo.name.toLowerCase().includes("latency");
              const isPassing = isLatency
                ? slo.current <= slo.target
                : slo.current >= slo.target;

              return (
                <div key={slo.name} className="rounded border border-panel-border/40 bg-surface/30 p-3.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium text-ink">{slo.name}</span>
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono font-semibold text-ink">
                        {isLatency ? `${slo.current.toFixed(0)} ms` : `${(slo.current * 100).toFixed(2)}%`}
                      </span>
                      <span className="text-ink-muted">
                        / {isLatency ? `≤${slo.target} ms` : `≥${(slo.target * 100).toFixed(1)}%`}
                      </span>
                      {isPassing ? (
                        <CheckCircle size={14} className="text-emerald-400 ml-1" />
                      ) : (
                        <AlertOctagon size={14} className="text-red-400 ml-1" />
                      )}
                    </div>
                  </div>

                  <div className="mt-2.5 h-2 w-full rounded-full bg-surface overflow-hidden">
                    <div
                      className={`h-full transition-all duration-500 ${
                        isPassing ? "bg-emerald-400" : "bg-red-400"
                      }`}
                      style={{
                        width: isLatency
                          ? `${Math.min(100, (slo.target / Math.max(1, slo.current)) * 100)}%`
                          : `${Math.min(100, (slo.current / slo.target) * 100)}%`,
                      }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Component Health Grid */}
      <div className="space-y-3">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-ink-muted">
          Subsystem Components
        </h3>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Object.entries(components).map(([key, comp]) => {
            const meta = COMPONENT_METADATA[key] || {
              label: key,
              description: "System component",
              icon: Server,
            };
            const Icon = meta.icon;

            return (
              <div
                key={key}
                data-testid={`component-${key}`}
                className="flex flex-col rounded-lg border border-panel-border bg-panel p-4 shadow-panel transition hover:border-panel-border/80"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2.5">
                    <div className="rounded-md bg-surface p-2 text-accent">
                      <Icon size={18} />
                    </div>
                    <div>
                      <h4 className="text-sm font-semibold text-ink">{meta.label}</h4>
                      <p className="text-[11px] text-ink-muted">{meta.description}</p>
                    </div>
                  </div>
                  <StatusPill status={(comp.status as HealthStatus) || "Unknown"} />
                </div>

                {/* Subsystem Details */}
                <div className="mt-4 space-y-1.5 border-t border-panel-border/40 pt-3 font-mono text-xs">
                  {comp.latency_ms != null && (
                    <div className="flex justify-between text-ink-muted">
                      <span className="font-sans">Latency</span>
                      <span className="text-ink">{comp.latency_ms} ms</span>
                    </div>
                  )}

                  {comp.p95_ms != null && (
                    <div className="flex justify-between text-ink-muted">
                      <span className="font-sans">p95 Response</span>
                      <span className="text-ink">{comp.p95_ms.toFixed(1)} ms</span>
                    </div>
                  )}

                  {comp.error_rate != null && (
                    <div className="flex justify-between text-ink-muted">
                      <span className="font-sans">Error Rate</span>
                      <span className="text-ink">{(comp.error_rate * 100).toFixed(2)}%</span>
                    </div>
                  )}

                  {comp.engine_mode != null && (
                    <div className="flex justify-between text-ink-muted">
                      <span className="font-sans">Engine Mode</span>
                      <span className="text-ink font-semibold">{String(comp.engine_mode)}</span>
                    </div>
                  )}

                  {comp.tick != null && (
                    <div className="flex justify-between text-ink-muted">
                      <span className="font-sans">Synced Tick</span>
                      <span className="text-ink font-semibold">{comp.tick}</span>
                    </div>
                  )}

                  {comp.error && (
                    <div className="mt-2 rounded bg-red-500/10 p-2 text-xs text-red-400 font-sans">
                      {comp.error}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Incidents Table */}
      {data?.incidents && data.incidents.length > 0 && (
        <div className="rounded-lg border border-panel-border bg-panel p-5 shadow-panel space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-ink">Active & Recent Incidents</h3>
            <span className="font-mono text-xs text-ink-muted">{data.incidents.length} recorded</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-panel-border/60 text-ink-muted">
                  <th className="pb-2 font-medium">ID</th>
                  <th className="pb-2 font-medium">Component</th>
                  <th className="pb-2 font-medium">Kind</th>
                  <th className="pb-2 font-medium">Status</th>
                  <th className="pb-2 font-medium">Summary</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-panel-border/30">
                {data.incidents.map((inc) => (
                  <tr key={inc.id} className="text-ink">
                    <td className="py-2 font-mono text-ink-muted">#{inc.id}</td>
                    <td className="py-2 font-semibold text-accent">{inc.component || "platform"}</td>
                    <td className="py-2 font-mono">{inc.kind || "DEPENDENCY_DOWN"}</td>
                    <td className="py-2">
                      <span className={`rounded px-2 py-0.5 font-mono text-[10px] font-bold ${
                        inc.status === "OPEN" ? "bg-red-500/20 text-red-400 border border-red-500/40" : "bg-emerald-500/20 text-emerald-400"
                      }`}>
                        {inc.status}
                      </span>
                    </td>
                    <td className="py-2 text-ink-muted">{inc.summary || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* External Observability & Version Footer */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between rounded-lg border border-panel-border/60 bg-surface/30 px-5 py-4 text-xs text-ink-muted">
        <div>
          <span className="font-medium text-ink">System Build Versions: </span>
          <span className="font-mono">
            api={versions.api || "dev"} · engine={versions.engine || "dev"} · intelligence={versions.intelligence || "dev"} · sha={versions.git_sha || "unknown"}
          </span>
        </div>

        <div className="flex items-center gap-4">
          <a
            href="http://localhost:3001"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-accent hover:underline"
          >
            <span>Ops Dashboard</span>
            <ExternalLink size={12} />
          </a>
          <a
            href="http://localhost:3001/explore"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-accent hover:underline"
          >
            <span>Service Traces</span>
            <ExternalLink size={12} />
          </a>
        </div>
      </div>
    </div>
  );
}
