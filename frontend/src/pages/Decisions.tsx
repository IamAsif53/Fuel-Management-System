import { useEffect, useState } from "react";
import {
  History,
  CheckCircle2,
  XCircle,
  Clock,
  Filter,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  TrendingUp,
  AlertCircle,
  Truck,
} from "lucide-react";
import clsx from "clsx";
import { fetchDecisions } from "@/api/client";
import type { Decision, DecisionStatus } from "@/api/types";
import { useLiveState } from "@/hooks/useLiveState";
import { formatNumber } from "@/lib/format";
import { LoadingSkeleton } from "@/components/LoadingSkeleton";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { ExplainPanel } from "@/components/ExplainPanel";
import { exportToCSV, exportToJSON } from "@/lib/export";

export default function Decisions() {
  const { state } = useLiveState();
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [statusFilter, setStatusFilter] = useState<string>("ALL");
  const [policyFilter, setPolicyFilter] = useState<string>("ALL");

  // Expanded rows: decisionId -> boolean
  const [expandedDecisions, setExpandedDecisions] = useState<Record<number, boolean>>({});

  const loadDecisions = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const res = await fetchDecisions({
        sim_run_id: state?.sim_run_id,
        status: statusFilter === "ALL" ? undefined : statusFilter,
        policy: policyFilter === "ALL" ? undefined : policyFilter,
        limit: 50,
        offset: 0,
      });
      setDecisions(res.items);
      setTotalCount(res.total);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load decisions");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadDecisions();

    const handleEvent = (e: Event) => {
      const custom = e as CustomEvent<{ type: string }>;
      if (
        custom.detail?.type === "decision.created" ||
        custom.detail?.type === "decision.approved" ||
        custom.detail?.type === "decision.rejected" ||
        custom.detail?.type === "allocation.delivered" ||
        custom.detail?.type === "allocation.failed"
      ) {
        loadDecisions();
      }
    };

    window.addEventListener("fsip-event", handleEvent);
    return () => window.removeEventListener("fsip-event", handleEvent);
  }, [state?.sim_run_id, statusFilter, policyFilter]);

  const toggleExpand = (id: number) => {
    setExpandedDecisions((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const renderStatusPill = (status: DecisionStatus | string) => {
    switch (status) {
      case "APPROVED":
      case "EXECUTED":
        return (
          <span className="flex items-center gap-1 rounded bg-risk-low/15 px-2 py-0.5 text-xs font-semibold text-risk-low">
            <CheckCircle2 size={12} />
            {status}
          </span>
        );
      case "REJECTED":
        return (
          <span className="flex items-center gap-1 rounded bg-risk-critical/15 px-2 py-0.5 text-xs font-semibold text-risk-critical">
            <XCircle size={12} />
            REJECTED
          </span>
        );
      case "PENDING_APPROVAL":
      case "PROPOSED":
        return (
          <span className="flex items-center gap-1 rounded bg-risk-high/15 px-2 py-0.5 text-xs font-semibold text-risk-high">
            <Clock size={12} />
            PENDING
          </span>
        );
      case "EXPIRED":
      case "SUPERSEDED":
        return (
          <span className="rounded bg-bg-raised px-2 py-0.5 text-xs font-medium text-ink-muted">
            {status}
          </span>
        );
      default:
        return (
          <span className="rounded bg-bg-raised px-2 py-0.5 text-xs font-medium text-ink-muted">
            {status}
          </span>
        );
    }
  };

  const renderOutcomeBadge = (dec: Decision) => {
    if (!dec.outcome) {
      return (
        <span className="text-[11px] text-ink-faint">
          Realisation pending
        </span>
      );
    }

    if (dec.outcome.hit === true) {
      return (
        <span className="flex items-center gap-1 rounded bg-risk-low/15 px-2 py-0.5 text-[11px] font-semibold text-risk-low">
          <TrendingUp size={12} />
          Target Realised (Hit)
        </span>
      );
    }

    if (dec.outcome.hit === false) {
      return (
        <span className="flex items-center gap-1 rounded bg-risk-critical/15 px-2 py-0.5 text-[11px] font-semibold text-risk-critical">
          <AlertCircle size={12} />
          Missed Target
        </span>
      );
    }

    return (
      <span className="text-[11px] text-ink-faint">
        Evaluating outcome...
      </span>
    );
  };

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-ink flex items-center gap-2">
            <History className="text-brand" size={24} />
            Decisions History & Audit Ledger
          </h1>
          <p className="text-xs text-ink-muted mt-1">
            Tamper-evident log of all algorithm-proposed, operator-approved, and manual allocations.
          </p>
        </div>

        <button
          onClick={loadDecisions}
          disabled={isLoading}
          className="flex items-center gap-1.5 self-start sm:self-auto rounded-lg border border-bg-border bg-bg-panel px-3 py-1.5 text-xs font-medium text-ink-muted hover:bg-bg-raised hover:text-ink transition-colors"
        >
          <RefreshCw size={13} className={clsx(isLoading && "animate-spin")} />
          Refresh Ledger
        </button>
      </div>

      {/* Filter Toolbar */}
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-bg-border bg-bg-panel p-3 text-xs">
        <div className="flex items-center gap-1.5 text-ink-muted font-medium mr-2">
          <Filter size={14} className="text-brand" />
          Filter:
        </div>

        <div className="flex items-center gap-2">
          <label className="text-ink-faint">Status:</label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded border border-bg-border bg-bg-raised px-2.5 py-1 text-xs text-ink focus:border-brand focus:outline-none"
          >
            <option value="ALL">All Statuses</option>
            <option value="EXECUTED">EXECUTED</option>
            <option value="APPROVED">APPROVED</option>
            <option value="REJECTED">REJECTED</option>
            <option value="PENDING_APPROVAL">PENDING_APPROVAL</option>
            <option value="SUPERSEDED">SUPERSEDED</option>
            <option value="EXPIRED">EXPIRED</option>
            <option value="FAILED">FAILED</option>
          </select>
        </div>

        <div className="flex items-center gap-2">
          <label className="text-ink-faint">Policy:</label>
          <select
            value={policyFilter}
            onChange={(e) => setPolicyFilter(e.target.value)}
            className="rounded border border-bg-border bg-bg-raised px-2.5 py-1 text-xs text-ink focus:border-brand focus:outline-none"
          >
            <option value="ALL">All Policies</option>
            <option value="heuristic">Heuristic</option>
            <option value="optimizer">Optimizer</option>
            <option value="manual">Manual Dispatch</option>
          </select>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => exportToCSV("fsip-decisions", decisions)}
            className="rounded border border-bg-border bg-bg-raised px-2.5 py-1 text-xs text-ink hover:bg-bg-border transition-colors"
            title="Export filtered decisions to CSV"
          >
            Export CSV
          </button>
          <button
            onClick={() => exportToJSON("fsip-decisions", decisions)}
            className="rounded border border-bg-border bg-bg-raised px-2.5 py-1 text-xs text-ink hover:bg-bg-border transition-colors"
            title="Export filtered decisions to JSON"
          >
            Export JSON
          </button>
          <div className="text-xs text-ink-muted ml-2">
            Showing <span className="font-semibold text-ink">{decisions.length}</span> of{" "}
            <span className="font-semibold text-ink">{totalCount}</span> entries
          </div>
        </div>
      </div>

      {/* Loading & Error States */}
      {isLoading && decisions.length === 0 && <LoadingSkeleton />}
      {error && !isLoading && (
        <ErrorState message={error} onRetry={loadDecisions} />
      )}

      {/* Empty State */}
      {!isLoading && !error && decisions.length === 0 && (
        <EmptyState
          title="No Decisions Found"
          message="No historical decisions match the currently applied status and policy filters."
        />
      )}

      {/* Decisions List */}
      <div className="space-y-3">
        {decisions.map((dec) => {
          const isExpanded = expandedDecisions[dec.id] ?? false;
          const totalLiters = (dec.lines || []).reduce((acc, ln) => acc + ln.quantity, 0);

          return (
            <div
              key={dec.id}
              className="rounded-xl border border-bg-border bg-bg-panel shadow-sm transition-all overflow-hidden"
            >
              {/* Row Summary (Click to expand) */}
              <div
                onClick={() => toggleExpand(dec.id)}
                className="flex flex-wrap items-center justify-between gap-3 p-4 cursor-pointer hover:bg-bg-raised/40 transition-colors"
              >
                <div className="flex items-center gap-3">
                  <button className="text-ink-faint hover:text-ink">
                    {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  </button>

                  <span className="font-mono text-xs font-bold text-ink">
                    #DEC-{dec.id}
                  </span>

                  <span className="rounded bg-bg-raised px-2 py-0.5 font-mono text-[11px] text-ink-muted">
                    Tick {dec.tick}
                  </span>

                  <span className="rounded bg-bg-raised px-2 py-0.5 text-[11px] font-semibold text-ink-muted capitalize">
                    {dec.policy}
                  </span>

                  {renderStatusPill(dec.status)}
                </div>

                <div className="flex items-center gap-4 text-xs">
                  <div className="flex items-center gap-1.5 text-ink-muted">
                    <Truck size={14} className="text-brand shrink-0" />
                    <span>
                      <strong className="text-ink">{formatNumber(totalLiters)} L</strong> across{" "}
                      {(dec.lines || []).length} lines
                    </span>
                  </div>

                  {renderOutcomeBadge(dec)}

                  {dec.approved_by && (
                    <span className="text-[11px] text-ink-muted">
                      By: <span className="font-semibold text-ink">{dec.approved_by}</span>
                    </span>
                  )}
                  {dec.rejected_reason && (
                    <span className="text-[11px] text-risk-critical max-w-xs truncate">
                      Reason: {dec.rejected_reason}
                    </span>
                  )}
                </div>
              </div>

              {/* Collapsible Details */}
              {isExpanded && (
                <div className="border-t border-bg-border/60 bg-bg-raised/30 p-4 space-y-3">
                  {dec.narrative && (
                    <div className="text-xs text-ink-muted bg-bg-panel rounded-lg p-3 border border-bg-border/40 leading-relaxed">
                      <span className="font-semibold text-ink">Narrative: </span>
                      {dec.narrative}
                    </div>
                  )}

                  {/* Lines Table */}
                  <div className="overflow-x-auto rounded-lg border border-bg-border/60 bg-bg-panel">
                    <table className="w-full text-left text-xs">
                      <thead>
                        <tr className="border-b border-bg-border text-ink-faint bg-bg-raised/50">
                          <th className="py-2 px-3 font-medium">Line #</th>
                          <th className="py-2 px-3 font-medium">Source Depot</th>
                          <th className="py-2 px-3 font-medium">Destination Station</th>
                          <th className="py-2 px-3 font-medium">Route</th>
                          <th className="py-2 px-3 font-medium">Fuel</th>
                          <th className="py-2 px-3 font-medium">Allocated (L)</th>
                          <th className="py-2 px-3 font-medium">Received (L)</th>
                          <th className="py-2 px-3 font-medium">Status</th>
                          <th className="py-2 px-3 font-medium">Arrival Tick</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-bg-border/40">
                        {(dec.lines || []).map((line) => (
                          <tr key={line.id} className="hover:bg-bg-raised/20">
                            <td className="py-2.5 px-3 font-mono text-ink-faint">{line.line_no}</td>
                            <td className="py-2.5 px-3 font-mono font-medium text-ink">{line.source_depot_id}</td>
                            <td className="py-2.5 px-3 font-mono font-medium text-ink">{line.destination_station_id}</td>
                            <td className="py-2.5 px-3 font-mono text-ink-muted">{line.route_id}</td>
                            <td className="py-2.5 px-3">
                              <span
                                className={clsx(
                                  "rounded px-1.5 py-0.5 text-[11px] font-bold",
                                  line.fuel_type === "DIESEL" && "bg-amber-500/15 text-amber-500",
                                  line.fuel_type === "OCTANE" && "bg-cyan-500/15 text-cyan-500",
                                  line.fuel_type === "PETROL" && "bg-emerald-500/15 text-emerald-500"
                                )}
                              >
                                {line.fuel_type}
                              </span>
                            </td>
                            <td className="py-2.5 px-3 font-mono font-bold text-ink">
                              {formatNumber(line.quantity)}
                            </td>
                            <td className="py-2.5 px-3 font-mono text-ink-muted">
                              {line.received_liters != null ? `${formatNumber(line.received_liters)}` : "—"}
                            </td>
                            <td className="py-2.5 px-3">
                              <span
                                className={clsx(
                                  "rounded px-2 py-0.5 text-[10px] font-semibold",
                                  line.status === "ARRIVED" && "bg-risk-low/15 text-risk-low",
                                  line.status === "IN_TRANSIT" && "bg-brand/15 text-brand",
                                  line.status === "FAILED" && "bg-risk-critical/15 text-risk-critical",
                                  line.status === "CANCELLED" && "bg-bg-raised text-ink-faint",
                                  line.status === "SUBMITTED" && "bg-amber-500/15 text-amber-500"
                                )}
                              >
                                {line.status}
                              </span>
                            </td>
                            <td className="py-2.5 px-3 font-mono text-ink-muted">
                              {line.actual_arrival_tick != null
                                ? `Tick ${line.actual_arrival_tick}`
                                : line.expected_arrival_tick != null
                                ? `Exp. ${line.expected_arrival_tick}`
                                : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {/* Structured Explanation Panel */}
                  <ExplainPanel decision={dec} isExpanded={true} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
