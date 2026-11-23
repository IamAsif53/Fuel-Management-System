import { useEffect, useState } from "react";
import {
  Inbox,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ArrowRight,
  RefreshCw,
  Plus,
  ShieldAlert,
  Bot,
  UserCheck,
} from "lucide-react";
import clsx from "clsx";
import {
  approveDecision,
  dispatchManualAllocation,
  fetchRecommendations,
  rejectDecision,
} from "@/api/client";
import type { Decision, Fuel } from "@/api/types";
import { useLiveState } from "@/hooks/useLiveState";
import { formatNumber } from "@/lib/format";
import { LoadingSkeleton } from "@/components/LoadingSkeleton";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";

export default function Recommendations() {
  const { state } = useLiveState();
  const [recommendations, setRecommendations] = useState<Decision[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Line selection & modification state: decisionId -> { [lineId]: boolean } and { [lineId]: number }
  const [selectedLines, setSelectedLines] = useState<Record<number, Record<number, boolean>>>({});
  const [modifiedQuantities, setModifiedQuantities] = useState<Record<number, Record<number, number>>>({});

  // Action in-flight tracking
  const [actionInProgress, setActionInProgress] = useState<Record<number, boolean>>({});

  // Reject modal state
  const [rejectingDecision, setRejectingDecision] = useState<Decision | null>(null);
  const [rejectReasonCode, setRejectReasonCode] = useState<string>("NOT_NEEDED");
  const [rejectComment, setRejectComment] = useState<string>("");

  // Cognitive forcing confirmation modal (for low confidence or high quantity)
  const [pendingApproval, setPendingApproval] = useState<Decision | null>(null);

  // Manual dispatch modal state
  const [showManualModal, setShowManualModal] = useState(false);
  const [manualDepot, setManualDepot] = useState("");
  const [manualStation, setManualStation] = useState("");
  const [manualRoute, setManualRoute] = useState("");
  const [manualFuel, setManualFuel] = useState<Fuel>("DIESEL");
  const [manualQuantity, setManualQuantity] = useState(3000);
  const [manualComment, setManualComment] = useState("Urgent manual dispatch");
  const [isSubmittingManual, setIsSubmittingManual] = useState(false);

  const loadRecommendations = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const items = await fetchRecommendations(state?.sim_run_id);
      setRecommendations(items);

      // Initialize line selection and quantities
      const initialSelection: Record<number, Record<number, boolean>> = {};
      const initialQuantities: Record<number, Record<number, number>> = {};
      for (const dec of items) {
        initialSelection[dec.id] = {};
        initialQuantities[dec.id] = {};
        for (const line of dec.lines || []) {
          initialSelection[dec.id][line.id] = true;
          initialQuantities[dec.id][line.id] = line.quantity;
        }
      }
      setSelectedLines(initialSelection);
      setModifiedQuantities(initialQuantities);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load recommendations");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadRecommendations();

    const handleEvent = (e: Event) => {
      const custom = e as CustomEvent<{ type: string }>;
      if (
        custom.detail?.type === "decision.created" ||
        custom.detail?.type === "decision.approved" ||
        custom.detail?.type === "decision.rejected"
      ) {
        loadRecommendations();
      }
    };

    window.addEventListener("fsip-event", handleEvent);
    return () => window.removeEventListener("fsip-event", handleEvent);
  }, [state?.sim_run_id]);

  const toggleLine = (decisionId: number, lineId: number) => {
    setSelectedLines((prev) => ({
      ...prev,
      [decisionId]: {
        ...prev[decisionId],
        [lineId]: !prev[decisionId]?.[lineId],
      },
    }));
  };

  const updateQuantity = (decisionId: number, lineId: number, qty: number) => {
    setModifiedQuantities((prev) => ({
      ...prev,
      [decisionId]: {
        ...prev[decisionId],
        [lineId]: qty,
      },
    }));
  };

  const handleApproveClick = (decision: Decision) => {
    const confidence = decision.confidence ?? 1.0;
    // Trigger cognitive forcing if confidence < 0.70 or marked degraded
    if (confidence < 0.70 || decision.degraded) {
      setPendingApproval(decision);
    } else {
      executeApprove(decision);
    }
  };

  const executeApprove = async (decision: Decision) => {
    setPendingApproval(null);
    const decId = decision.id;
    const selected = selectedLines[decId] || {};
    const quantities = modifiedQuantities[decId] || {};

    const lineIds = Object.keys(selected)
      .filter((lid) => selected[Number(lid)])
      .map(Number);

    if (lineIds.length === 0) {
      alert("Please select at least one line to approve.");
      return;
    }

    const modifiedPayload: Record<string, number> = {};
    for (const lid of lineIds) {
      if (quantities[lid] !== undefined) {
        modifiedPayload[String(lid)] = quantities[lid];
      }
    }

    try {
      setActionInProgress((prev) => ({ ...prev, [decId]: true }));
      await approveDecision(decId, {
        line_ids: lineIds,
        quantities: modifiedPayload,
        reviewed_driver: true,
      });
      // Remove approved item from list
      setRecommendations((prev) => prev.filter((d) => d.id !== decId));
    } catch (err: unknown) {
      alert(`Approval failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setActionInProgress((prev) => ({ ...prev, [decId]: false }));
    }
  };

  const handleRejectClick = (decision: Decision) => {
    setRejectingDecision(decision);
    setRejectReasonCode("NOT_NEEDED");
    setRejectComment("");
  };

  const executeReject = async () => {
    if (!rejectingDecision) return;
    const decId = rejectingDecision.id;
    try {
      setActionInProgress((prev) => ({ ...prev, [decId]: true }));
      await rejectDecision(decId, {
        reason_code: rejectReasonCode,
        comment: rejectComment,
      });
      setRecommendations((prev) => prev.filter((d) => d.id !== decId));
      setRejectingDecision(null);
    } catch (err: unknown) {
      alert(`Rejection failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setActionInProgress((prev) => ({ ...prev, [decId]: false }));
    }
  };

  const handleManualSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!manualDepot || !manualStation || !manualRoute) {
      alert("Please select depot, station, and route.");
      return;
    }
    try {
      setIsSubmittingManual(true);
      await dispatchManualAllocation({
        source_depot_id: manualDepot,
        destination_station_id: manualStation,
        route_id: manualRoute,
        fuel_type: manualFuel,
        quantity: manualQuantity,
        comment: manualComment,
      });
      setShowManualModal(false);
      loadRecommendations();
    } catch (err: unknown) {
      alert(`Manual dispatch failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setIsSubmittingManual(false);
    }
  };

  const operatingMode = state?.operating_mode || "advisory";

  return (
    <div className="space-y-6">
      {/* Top Banner & Actions */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-ink flex items-center gap-2">
            <Inbox className="text-brand" size={24} />
            Prescriptive Recommendations
          </h1>
          <p className="text-xs text-ink-muted mt-1">
            Algorithmically generated fuel rebalancing allocations awaiting operator sign-off.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={loadRecommendations}
            disabled={isLoading}
            className="flex items-center gap-1.5 rounded-lg border border-bg-border bg-bg-panel px-3 py-1.5 text-xs font-medium text-ink-muted hover:bg-bg-raised hover:text-ink transition-colors"
          >
            <RefreshCw size={13} className={clsx(isLoading && "animate-spin")} />
            Refresh
          </button>

          <button
            onClick={() => setShowManualModal(true)}
            className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand/90 transition-colors shadow-sm"
          >
            <Plus size={14} />
            Manual Dispatch
          </button>
        </div>
      </div>

      {/* Operating Mode Informational Banner */}
      <div
        className={clsx(
          "flex items-center gap-3 rounded-lg border p-3.5 text-xs",
          operatingMode === "autopilot"
            ? "border-brand/30 bg-brand/10 text-ink"
            : "border-bg-border bg-bg-panel text-ink"
        )}
      >
        {operatingMode === "autopilot" ? (
          <Bot size={18} className="text-brand shrink-0" />
        ) : (
          <UserCheck size={18} className="text-brand shrink-0" />
        )}
        <div className="flex-1">
          <span className="font-semibold uppercase tracking-wider text-[11px] text-brand mr-2">
            {operatingMode} Mode Active:
          </span>
          {operatingMode === "autopilot"
            ? "Low-risk routine replenishment recommendations are automatically dispatched on cycle. High-risk alerts or constrained routes remain listed here for manual confirmation."
            : "All proposed shipment allocations require explicit operator review and sign-off before being dispatched to the delivery network."}
        </div>
      </div>

      {/* Loading & Error States */}
      {isLoading && recommendations.length === 0 && <LoadingSkeleton />}
      {error && !isLoading && (
        <ErrorState message={error} onRetry={loadRecommendations} />
      )}

      {/* Empty State */}
      {!isLoading && !error && recommendations.length === 0 && (
        <EmptyState
          title="Inbox Clean — No Pending Recommendations"
          message="All retail stations have sufficient fuel inventory and projected stockout probabilities are below the dispatch threshold."
        />
      )}

      {/* Recommendations Cards Grid */}
      <div className="space-y-4">
        {recommendations.map((rec) => {
          const confidence = rec.confidence ?? 1.0;
          const confPct = Math.round(confidence * 100);
          const selMap = selectedLines[rec.id] || {};
          const qtyMap = modifiedQuantities[rec.id] || {};
          const isActing = actionInProgress[rec.id] || false;

          const totalSelectedLiters = (rec.lines || []).reduce((acc, ln) => {
            if (selMap[ln.id]) {
              return acc + (qtyMap[ln.id] ?? ln.quantity);
            }
            return acc;
          }, 0);

          return (
            <div
              key={rec.id}
              className="rounded-xl border border-bg-border bg-bg-panel p-5 shadow-sm transition-all hover:border-bg-border/80"
            >
              {/* Card Header */}
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-bg-border/60 pb-3">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs font-bold text-ink">
                    #DEC-{rec.id}
                  </span>
                  <span className="rounded bg-bg-raised px-2 py-0.5 font-mono text-[11px] text-ink-muted">
                    Tick {rec.tick}
                  </span>
                  <span className="rounded bg-bg-raised px-2 py-0.5 text-[11px] font-semibold text-ink-muted capitalize">
                    {rec.policy}
                  </span>
                  {rec.degraded && (
                    <span className="flex items-center gap-1 rounded bg-risk-high/15 px-2 py-0.5 text-[11px] font-semibold text-risk-high">
                      <AlertTriangle size={12} />
                      Degraded State
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-3">
                  {/* Confidence Pill */}
                  <div
                    className={clsx(
                      "flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold",
                      confPct >= 85 && "bg-risk-low/15 text-risk-low",
                      confPct >= 70 && confPct < 85 && "bg-risk-high/15 text-risk-high",
                      confPct < 70 && "bg-risk-critical/15 text-risk-critical"
                    )}
                    title={`Decision confidence score: ${confidence.toFixed(2)}`}
                  >
                    <span>{confPct}% Confidence</span>
                  </div>

                  {/* Expiry */}
                  {rec.expires_at_tick && (
                    <span className="text-xs text-ink-faint">
                      Expires tick {rec.expires_at_tick}
                    </span>
                  )}
                </div>
              </div>

              {/* Narrative / Context */}
              {rec.narrative && (
                <div className="mt-3 rounded-lg bg-bg-raised/60 p-3 text-xs leading-relaxed text-ink-muted">
                  <span className="font-semibold text-ink">Rationale: </span>
                  {rec.narrative}
                </div>
              )}

              {/* Lines Table */}
              <div className="mt-4 overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-bg-border text-ink-faint">
                      <th className="pb-2 font-medium w-8">Select</th>
                      <th className="pb-2 font-medium">Source Depot</th>
                      <th className="pb-2 font-medium">Destination Station</th>
                      <th className="pb-2 font-medium">Route</th>
                      <th className="pb-2 font-medium">Fuel</th>
                      <th className="pb-2 font-medium w-32">Allocated Liters</th>
                      <th className="pb-2 font-medium">Reason</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-bg-border/40">
                    {(rec.lines || []).map((line) => {
                      const isSelected = selMap[line.id] ?? true;
                      const currentQty = qtyMap[line.id] ?? line.quantity;

                      return (
                        <tr
                          key={line.id}
                          className={clsx(
                            "transition-colors",
                            !isSelected && "opacity-40"
                          )}
                        >
                          <td className="py-2.5">
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => toggleLine(rec.id, line.id)}
                              className="rounded border-bg-border text-brand focus:ring-brand cursor-pointer"
                            />
                          </td>
                          <td className="py-2.5 font-mono text-ink font-medium">
                            {line.source_depot_id}
                          </td>
                          <td className="py-2.5 font-mono text-ink font-medium">
                            {line.destination_station_id}
                          </td>
                          <td className="py-2.5 font-mono text-ink-muted">
                            {line.route_id}
                          </td>
                          <td className="py-2.5">
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
                          <td className="py-2.5">
                            <div className="flex items-center gap-1">
                              <input
                                type="number"
                                min={100}
                                step={100}
                                value={currentQty}
                                disabled={!isSelected}
                                onChange={(e) =>
                                  updateQuantity(rec.id, line.id, Number(e.target.value))
                                }
                                className="w-24 rounded border border-bg-border bg-bg-raised px-2 py-1 font-mono text-xs text-ink focus:border-brand focus:outline-none"
                              />
                              <span className="text-ink-faint">L</span>
                            </div>
                          </td>
                          <td className="py-2.5 text-ink-muted max-w-xs truncate">
                            {line.reason || "Projected stockout coverage"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Action Footer */}
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-bg-border/60 pt-3">
                <div className="text-xs text-ink-muted">
                  Total Dispatch:{" "}
                  <span className="font-mono font-bold text-ink">
                    {formatNumber(totalSelectedLiters)} L
                  </span>{" "}
                  across{" "}
                  <span className="font-semibold text-ink">
                    {Object.values(selMap).filter(Boolean).length}
                  </span>{" "}
                  selected lines
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => handleRejectClick(rec)}
                    disabled={isActing}
                    className="flex items-center gap-1.5 rounded-lg border border-bg-border bg-bg-raised px-3 py-1.5 text-xs font-semibold text-ink-muted hover:border-risk-critical/40 hover:text-risk-critical transition-colors"
                  >
                    <XCircle size={14} />
                    Reject
                  </button>

                  <button
                    onClick={() => handleApproveClick(rec)}
                    disabled={isActing || totalSelectedLiters === 0}
                    className={clsx(
                      "flex items-center gap-1.5 rounded-lg bg-risk-low px-4 py-1.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-risk-low/90",
                      (isActing || totalSelectedLiters === 0) && "opacity-50 cursor-not-allowed"
                    )}
                  >
                    <CheckCircle2 size={14} className={clsx(isActing && "animate-spin")} />
                    Approve & Dispatch
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Reject Reason Modal */}
      {rejectingDecision && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-xl border border-bg-border bg-bg-panel p-6 shadow-2xl">
            <h3 className="text-base font-semibold text-ink flex items-center gap-2">
              <XCircle size={18} className="text-risk-critical" />
              Reject Recommendation #DEC-{rejectingDecision.id}
            </h3>
            <p className="mt-2 text-xs text-ink-muted">
              Record a mandatory operator justification for audit and policy feedback loops.
            </p>

            <div className="mt-4 space-y-3">
              <div>
                <label className="block text-xs font-medium text-ink-muted mb-1">
                  Rejection Reason Code
                </label>
                <select
                  value={rejectReasonCode}
                  onChange={(e) => setRejectReasonCode(e.target.value)}
                  className="w-full rounded border border-bg-border bg-bg-raised px-3 py-1.5 text-xs text-ink focus:border-brand focus:outline-none"
                >
                  <option value="NOT_NEEDED">NOT_NEEDED: Sufficient local buffer</option>
                  <option value="WRONG_PRIORITY">WRONG_PRIORITY: Another station is more critical</option>
                  <option value="DATA_LOOKS_WRONG">DATA_LOOKS_WRONG: Disagree with sensor or projection</option>
                  <option value="PREFER_OTHER_ROUTE">PREFER_OTHER_ROUTE: Road conditions prefer alternative</option>
                  <option value="OTHER">OTHER: Specific justification below</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-ink-muted mb-1">
                  Operator Notes / Comments
                </label>
                <textarea
                  rows={3}
                  value={rejectComment}
                  onChange={(e) => setRejectComment(e.target.value)}
                  placeholder="Additional context on rejection rationale..."
                  className="w-full rounded border border-bg-border bg-bg-raised p-2 text-xs text-ink focus:border-brand focus:outline-none"
                />
              </div>
            </div>

            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setRejectingDecision(null)}
                className="rounded-lg border border-bg-border px-3.5 py-1.5 text-xs font-medium text-ink-muted hover:bg-bg-raised hover:text-ink"
              >
                Cancel
              </button>
              <button
                onClick={executeReject}
                className="rounded-lg bg-risk-critical px-4 py-1.5 text-xs font-semibold text-white hover:bg-risk-critical/90"
              >
                Confirm Rejection
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Cognitive Forcing Warning Modal */}
      {pendingApproval && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-xl border border-risk-high/40 bg-bg-panel p-6 shadow-2xl">
            <div className="flex items-center gap-3 text-risk-high">
              <ShieldAlert size={24} />
              <h3 className="text-base font-semibold text-ink">Supervisory Caution Required</h3>
            </div>
            <p className="mt-3 text-xs leading-relaxed text-ink-muted">
              This recommendation has a confidence score of{" "}
              <span className="font-bold text-risk-high">
                {Math.round((pendingApproval.confidence ?? 0) * 100)}%
              </span>{" "}
              {pendingApproval.degraded && "and was generated during degraded simulator telemetry"}.
              Please confirm driver availability, route safety conditions, and receiver tank capacity
              before executing dispatch.
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setPendingApproval(null)}
                className="rounded-lg border border-bg-border px-3.5 py-1.5 text-xs font-medium text-ink-muted hover:bg-bg-raised hover:text-ink"
              >
                Back to Review
              </button>
              <button
                onClick={() => executeApprove(pendingApproval)}
                className="rounded-lg bg-brand px-4 py-1.5 text-xs font-semibold text-white hover:bg-brand/90"
              >
                Override & Dispatch
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Manual Allocation Dispatch Modal */}
      {showManualModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-lg rounded-xl border border-bg-border bg-bg-panel p-6 shadow-2xl">
            <h3 className="text-base font-semibold text-ink flex items-center gap-2">
              <Plus size={18} className="text-brand" />
              Manual Operator Fuel Dispatch
            </h3>
            <p className="mt-1 text-xs text-ink-muted">
              Manually dispatch an urgent off-schedule tanker shipment to prevent immediate stockout.
            </p>

            <form onSubmit={handleManualSubmit} className="mt-4 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-ink-muted mb-1">
                    Source Depot
                  </label>
                  <select
                    value={manualDepot}
                    onChange={(e) => setManualDepot(e.target.value)}
                    required
                    className="w-full rounded border border-bg-border bg-bg-raised px-2.5 py-1.5 text-xs text-ink focus:border-brand focus:outline-none"
                  >
                    <option value="">Select Depot...</option>
                    {(state?.depots || []).map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name} ({d.id})
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-medium text-ink-muted mb-1">
                    Destination Station
                  </label>
                  <select
                    value={manualStation}
                    onChange={(e) => setManualStation(e.target.value)}
                    required
                    className="w-full rounded border border-bg-border bg-bg-raised px-2.5 py-1.5 text-xs text-ink focus:border-brand focus:outline-none"
                  >
                    <option value="">Select Station...</option>
                    {(state?.stations || []).map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name} ({s.id})
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-ink-muted mb-1">
                    Route
                  </label>
                  <select
                    value={manualRoute}
                    onChange={(e) => setManualRoute(e.target.value)}
                    required
                    className="w-full rounded border border-bg-border bg-bg-raised px-2.5 py-1.5 text-xs text-ink focus:border-brand focus:outline-none"
                  >
                    <option value="">Select Route...</option>
                    {(state?.routes || []).map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.id} (max {formatNumber(r.max_shipment)}L)
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-medium text-ink-muted mb-1">
                    Fuel Type
                  </label>
                  <select
                    value={manualFuel}
                    onChange={(e) => setManualFuel(e.target.value as Fuel)}
                    className="w-full rounded border border-bg-border bg-bg-raised px-2.5 py-1.5 text-xs text-ink focus:border-brand focus:outline-none"
                  >
                    <option value="DIESEL">DIESEL</option>
                    <option value="PETROL">PETROL</option>
                    <option value="OCTANE">OCTANE</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-ink-muted mb-1">
                  Shipment Quantity (Liters)
                </label>
                <input
                  type="number"
                  min={100}
                  step={100}
                  value={manualQuantity}
                  onChange={(e) => setManualQuantity(Number(e.target.value))}
                  required
                  className="w-full rounded border border-bg-border bg-bg-raised px-2.5 py-1.5 font-mono text-xs text-ink focus:border-brand focus:outline-none"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-ink-muted mb-1">
                  Operator Dispatch Reason
                </label>
                <input
                  type="text"
                  value={manualComment}
                  onChange={(e) => setManualComment(e.target.value)}
                  placeholder="Justification for emergency dispatch"
                  className="w-full rounded border border-bg-border bg-bg-raised px-2.5 py-1.5 text-xs text-ink focus:border-brand focus:outline-none"
                />
              </div>

              <div className="mt-6 flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowManualModal(false)}
                  className="rounded-lg border border-bg-border px-3.5 py-1.5 text-xs font-medium text-ink-muted hover:bg-bg-raised hover:text-ink"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSubmittingManual}
                  className={clsx(
                    "flex items-center gap-1.5 rounded-lg bg-brand px-4 py-1.5 text-xs font-semibold text-white hover:bg-brand/90",
                    isSubmittingManual && "opacity-50 cursor-not-allowed"
                  )}
                >
                  <ArrowRight size={13} className={clsx(isSubmittingManual && "animate-spin")} />
                  Dispatch Allocation
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
