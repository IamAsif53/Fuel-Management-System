import React, { useEffect, useState } from "react";
import { acknowledgeAlert, fetchAlerts, fetchIncidents } from "@/api/client";
import type { Alert, Incident, Severity } from "@/api/types";
import { useLiveState } from "@/hooks/useLiveState";
import { LoadingSkeleton } from "@/components/LoadingSkeleton";
import { ErrorState } from "@/components/ErrorState";

const SEVERITY_STYLES: Record<Severity, { badge: string; dot: string }> = {
  CRITICAL: { badge: "bg-red-500/20 border-red-600/40 text-red-400", dot: "bg-red-500" },
  WARNING: { badge: "bg-amber-500/20 border-amber-600/40 text-amber-400", dot: "bg-amber-500" },
  INFO: { badge: "bg-blue-500/15 border-blue-600/30 text-blue-400", dot: "bg-blue-500" },
};

export default function AlertsPage() {
  const { state } = useLiveState();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [activeTab, setActiveTab] = useState<"OPEN" | "ACKNOWLEDGED" | "ALL">("OPEN");
  const [severityFilter, setSeverityFilter] = useState<string>("");
  const [ackingId, setAckingId] = useState<number | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const loadData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [alertsRes, incidentsRes] = await Promise.all([
        fetchAlerts({
          status: activeTab,
          severity: severityFilter || undefined,
          limit: 100,
        }),
        fetchIncidents({ limit: 20 }),
      ]);
      setAlerts(alertsRes.items || []);
      setIncidents(incidentsRes.items || []);
    } catch (err: any) {
      setError(err?.message || "Failed to load alerts & incidents");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [state?.tick, activeTab, severityFilter]);

  const handleAck = async (id: number) => {
    setAckingId(id);
    try {
      await acknowledgeAlert(id);
      setToastMessage(`Alert #${id} acknowledged.`);
      setTimeout(() => setToastMessage(null), 3500);
      await loadData();
    } catch (err: any) {
      setToastMessage(`Failed to acknowledge alert #${id}: ${err.message}`);
      setTimeout(() => setToastMessage(null), 4000);
    } finally {
      setAckingId(null);
    }
  };

  return (
    <div className="space-y-6" data-testid="alerts-page">
      {/* Toast Feedback */}
      {toastMessage && (
        <div className="fixed bottom-4 right-4 z-50 rounded-md border border-border bg-card px-4 py-2.5 text-xs font-medium text-foreground shadow-lg animate-in fade-in slide-in-from-bottom-2">
          {toastMessage}
        </div>
      )}

      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Alerts & Incidents</h1>
          <p className="text-sm text-muted-foreground">
            Active operational alerts, automated deduplication, severity escalation, and incident timelines
          </p>
        </div>

        {/* Tab & Severity Filter */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-md border border-border bg-background p-0.5 text-xs">
            <button
              onClick={() => setActiveTab("OPEN")}
              className={`rounded px-2.5 py-1 font-medium transition-colors ${
                activeTab === "OPEN" ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              Open
            </button>
            <button
              onClick={() => setActiveTab("ACKNOWLEDGED")}
              className={`rounded px-2.5 py-1 font-medium transition-colors ${
                activeTab === "ACKNOWLEDGED" ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              Acknowledged
            </button>
            <button
              onClick={() => setActiveTab("ALL")}
              className={`rounded px-2.5 py-1 font-medium transition-colors ${
                activeTab === "ALL" ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              All
            </button>
          </div>

          <select
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value)}
            className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="">All Severities</option>
            <option value="CRITICAL">Critical</option>
            <option value="WARNING">Warning</option>
            <option value="INFO">Info</option>
          </select>
        </div>
      </div>

      {/* Incidents Banner if open incidents exist */}
      {incidents.filter((inc) => inc.status !== "RESOLVED").length > 0 && (
        <div className="rounded-lg border border-amber-600/40 bg-amber-500/10 p-4">
          <h3 className="text-sm font-semibold text-amber-400">Active System Incidents</h3>
          <div className="mt-2 space-y-2">
            {incidents
              .filter((inc) => inc.status !== "RESOLVED")
              .map((inc) => (
                <div key={inc.id} className="text-xs text-foreground flex items-center justify-between">
                  <div>
                    <span className="font-semibold">{inc.component}:</span> {inc.summary || inc.kind}
                  </div>
                  <span className="rounded bg-amber-500/20 px-2 py-0.5 text-[10px] font-bold text-amber-400">
                    {inc.status}
                  </span>
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Alerts Table */}
      {isLoading && alerts.length === 0 ? (
        <LoadingSkeleton count={3} />
      ) : error && alerts.length === 0 ? (
        <ErrorState title="Alerts Unavailable" message={error} onRetry={loadData} />
      ) : (
        <div className="rounded-lg border border-border bg-card shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-muted/30 border-b border-border/80 text-muted-foreground">
                <tr>
                  <th className="py-3 px-4 font-medium">Severity</th>
                  <th className="py-3 px-3 font-medium">Category</th>
                  <th className="py-3 px-4 font-medium">Title & Message</th>
                  <th className="py-3 px-3 font-medium">Entity</th>
                  <th className="py-3 px-3 font-medium">Fuel</th>
                  <th className="py-3 px-3 font-medium">Status</th>
                  <th className="py-3 px-4 font-medium text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {alerts.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="py-8 text-center text-muted-foreground">
                      No alerts match the selected status & severity.
                    </td>
                  </tr>
                ) : (
                  alerts.map((alert) => {
                    const styling = SEVERITY_STYLES[alert.severity as Severity] || SEVERITY_STYLES.INFO;
                    return (
                      <tr
                        key={alert.id}
                        data-testid={`alert-row-${alert.id}`}
                        className="transition-colors hover:bg-muted/20"
                      >
                        <td className="py-3 px-4">
                          <span
                            className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-[11px] font-bold ${styling.badge}`}
                          >
                            <span className={`h-1.5 w-1.5 rounded-full ${styling.dot}`} />
                            {alert.severity}
                          </span>
                        </td>
                        <td className="py-3 px-3 font-medium text-muted-foreground">
                          {alert.category}
                        </td>
                        <td className="py-3 px-4 max-w-sm">
                          <div className="font-semibold text-foreground">{alert.title}</div>
                          {alert.message && (
                            <div className="mt-0.5 text-[11px] text-muted-foreground line-clamp-2">
                              {alert.message}
                            </div>
                          )}
                        </td>
                        <td className="py-3 px-3 text-muted-foreground">
                          {alert.entity_id || alert.entity_type || "—"}
                        </td>
                        <td className="py-3 px-3 font-medium text-foreground">
                          {alert.fuel_type || "—"}
                        </td>
                        <td className="py-3 px-3">
                          <span
                            className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                              alert.status === "OPEN"
                                ? "bg-red-500/10 text-red-400"
                                : alert.status === "ACKNOWLEDGED"
                                ? "bg-blue-500/10 text-blue-400"
                                : "bg-emerald-500/10 text-emerald-400"
                            }`}
                          >
                            {alert.status}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-right">
                          {alert.status === "OPEN" && (
                            <button
                              data-testid={`ack-btn-${alert.id}`}
                              disabled={ackingId === alert.id}
                              onClick={() => handleAck(alert.id)}
                              className="rounded border border-border bg-background px-2.5 py-1 text-[11px] font-medium text-foreground hover:bg-muted transition-colors disabled:opacity-50"
                            >
                              {ackingId === alert.id ? "..." : "Acknowledge"}
                            </button>
                          )}
                          {alert.status === "ACKNOWLEDGED" && (
                            <span className="text-[10px] text-muted-foreground">
                              Ack by {alert.acknowledged_by || "operator"}
                            </span>
                          )}
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
