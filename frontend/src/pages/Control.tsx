import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Play,
  Pause,
  StepForward,
  FastForward,
  RotateCcw,
  AlertTriangle,
  Zap,
  Flame,
  ShieldAlert,
  Bug,
  Trash2,
  Clock,
  Activity,
  CheckCircle2,
  XCircle,
  RefreshCw,
  Lock,
  Radio,
  Layers,
} from "lucide-react";
import { api } from "../api/client";
import { ControlStatus, ControlLogEntry } from "../api/types";

export default function ControlPage() {
  const queryClient = useQueryClient();

  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const [notification, setNotification] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [chaosDuration, setChaosDuration] = useState<number>(60);

  // 1. Fetch Control Status
  const { data: status, isLoading: statusLoading } = useQuery<ControlStatus>({
    queryKey: ["control-status"],
    queryFn: () => api<ControlStatus>("/control/status"),
    refetchInterval: 3000,
  });

  // 2. Fetch Control Logs
  const { data: logsData, isFetching: logsFetching, refetch: refetchLogs } = useQuery<{ logs: ControlLogEntry[] }>({
    queryKey: ["control-logs"],
    queryFn: () => api<{ logs: ControlLogEntry[] }>("/control/logs"),
    refetchInterval: 5000,
  });

  const invalidateAll = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["control-status"] }),
      queryClient.invalidateQueries({ queryKey: ["control-logs"] }),
      queryClient.invalidateQueries({ queryKey: ["state"] }),
      queryClient.invalidateQueries({ queryKey: ["system-status"] }),
    ]);
  };

  const handleAction = async (
    name: string,
    fn: () => Promise<unknown>,
    successMsg: string
  ) => {
    if (loadingAction) return;
    setLoadingAction(name);
    setNotification(null);
    try {
      await fn();
      setNotification({ type: "success", message: successMsg });
      await invalidateAll();
    } catch (err: any) {
      const errMsg = err?.message || err?.data?.detail?.message || "Operation failed";
      setNotification({ type: "error", message: `Error: ${errMsg}` });
    } finally {
      setLoadingAction(null);
    }
  };

  // Sim Controls
  const onRun = () =>
    handleAction("run", () => api("/control/sim/run", { method: "POST" }), "Simulator resumed successfully.");

  const onPause = () =>
    handleAction("pause", () => api("/control/sim/pause", { method: "POST" }), "Simulator paused successfully.");

  const onStep = (count: number) =>
    handleAction(
      `step-${count}`,
      () => api("/control/sim/step", { method: "POST", body: JSON.stringify({ count }) }),
      `Advanced simulation by ${count} tick${count > 1 ? "s" : ""}.`
    );

  const onReset = () => {
    setShowResetConfirm(false);
    handleAction("reset", () => api("/control/sim/reset", { method: "POST" }), "Simulation reset to initial scenario.");
  };

  // Crisis Presets
  const onTriggerPreset = (presetKey: string, title: string) =>
    handleAction(
      `preset-${presetKey}`,
      () => api(`/control/scenario/${presetKey}`, { method: "POST" }),
      `Crisis preset '${title}' injected successfully.`
    );

  // Chaos Controls
  const onInjectFault = (faultKey: string, title: string) =>
    handleAction(
      `chaos-${faultKey}`,
      () =>
        api(`/control/chaos/${faultKey}`, {
          method: "POST",
          body: JSON.stringify({ duration_seconds: chaosDuration }),
        }),
      `Fault '${title}' injected for ${chaosDuration}s.`
    );

  const onClearFaults = () =>
    handleAction("clear-faults", () => api("/control/chaos/clear", { method: "POST" }), "All active chaos faults cleared.");

  const onPrepareAutopilotDemo = () =>
    handleAction(
      "prepare-autopilot",
      () => api("/control/demo/prepare-autopilot", { method: "POST" }),
      "Autopilot demo armed: reset baseline, mode=autopilot, plan-now. Watch header banner for freeze/SLO."
    );

  const isEnabled = status?.demo_controls_enabled ?? true;

  if (statusLoading && !status) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="h-10 w-72 rounded bg-surface border border-panel-border" />
        <div className="h-44 rounded-lg bg-panel border border-panel-border" />
        <div className="h-72 rounded-lg bg-panel border border-panel-border" />
      </div>
    );
  }

  return (
    <div className="space-y-6 pb-12" data-testid="control-panel">
      {/* Header */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-ink flex items-center gap-2">
            <Radio className="text-amber-400" size={24} />
            Simulator Controls & Chaos Injection
          </h1>
          <p className="text-xs text-ink-muted">
            Interactive control-room driver: discrete-event stepping, scenario crisis presets, and resilience faults.
          </p>
        </div>

        {/* Sim State Badge */}
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-2 rounded-lg border border-panel-border bg-panel px-3 py-1.5 shadow-sm">
            <span className="text-[11px] uppercase tracking-wider text-ink-muted font-mono">Status:</span>
            <span
              className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs font-bold font-mono ${
                status?.sim_status === "RUNNING"
                  ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                  : status?.sim_status === "PAUSED"
                  ? "bg-amber-500/20 text-amber-400 border border-amber-500/30"
                  : "bg-slate-500/20 text-slate-300 border border-slate-500/30"
              }`}
            >
              <Activity size={12} className={status?.sim_status === "RUNNING" ? "animate-spin" : ""} />
              {status?.sim_status || "UNKNOWN"}
            </span>

            <div className="h-4 w-px bg-panel-border" />

            <span className="text-[11px] uppercase tracking-wider text-ink-muted font-mono">Tick:</span>
            <span className="font-mono text-xs font-bold text-ink">{status?.tick ?? "—"}</span>

            {status?.sim_time && (
              <>
                <div className="h-4 w-px bg-panel-border" />
                <Clock size={12} className="text-ink-muted" />
                <span className="font-mono text-[11px] text-ink-muted">
                  {new Date(status.sim_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Security Banner */}
      {isEnabled ? (
        <div className="rounded-lg border border-red-500/40 bg-red-950/20 p-4 shadow-sm flex items-start gap-3">
          <ShieldAlert className="text-red-400 mt-0.5 shrink-0" size={20} />
          <div className="text-xs space-y-1">
            <h3 className="font-semibold text-red-200 uppercase tracking-wide">
              Organizer Admin Controls — Testing & Demonstration Use Only
            </h3>
            <p className="text-red-300/80 leading-relaxed">
              Actions executed on this page directly invoke simulator administrative endpoints (
              <code className="text-red-200">/admin/*</code>). Modifying simulator ticks or injecting fault vectors alters
              physical inventory consumption, route transit delays, and optimizer decision recommendations in real time.
            </p>
          </div>
        </div>
      ) : (
        <div className="rounded-lg border border-amber-500/40 bg-amber-950/20 p-6 text-center space-y-3">
          <Lock className="mx-auto text-amber-400" size={32} />
          <h2 className="text-base font-semibold text-ink">Simulator Controls Disabled</h2>
          <p className="text-xs text-ink-muted max-w-lg mx-auto">
            Simulator administrative endpoints are currently disabled by policy (<code className="text-amber-400 font-mono">DEMO_CONTROLS=false</code>).
            Set <code className="text-amber-400 font-mono">DEMO_CONTROLS=true</code> in your environment to enable interactive stepping and fault drills.
          </p>
        </div>
      )}

      {/* Notification Toast */}
      {notification && (
        <div
          className={`rounded-lg p-3 text-xs flex items-center justify-between border ${
            notification.type === "success"
              ? "bg-emerald-950/30 text-emerald-200 border-emerald-500/30"
              : "bg-red-950/30 text-red-200 border-red-500/30"
          }`}
        >
          <div className="flex items-center gap-2">
            {notification.type === "success" ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
            <span>{notification.message}</span>
          </div>
          <button
            type="button"
            onClick={() => setNotification(null)}
            className="text-xs opacity-70 hover:opacity-100"
          >
            ✕
          </button>
        </div>
      )}

      {isEnabled && (
        <>
          {/* SECTION 1: SIMULATOR CONTROLS */}
          <div className="rounded-lg border border-panel-border bg-panel p-5 shadow-panel space-y-4">
            <div className="flex items-center justify-between border-b border-panel-border/40 pb-3">
              <div>
                <h2 className="text-sm font-semibold text-ink flex items-center gap-2">
                  <Play size={16} className="text-brand" />
                  Discrete-Event Simulation Stepping
                </h2>
                <p className="text-xs text-ink-muted">
                  Advance ticks, pause discrete cycles, or reset scenario baseline.
                </p>
              </div>
              {status?.active_events_count ? (
                <span className="inline-flex items-center gap-1 rounded bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-400 border border-amber-500/20">
                  <Flame size={12} />
                  {status.active_events_count} Active Event{status.active_events_count > 1 ? "s" : ""}
                </span>
              ) : null}
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              {/* Run */}
              <button
                type="button"
                data-testid="ctl-run"
                disabled={status?.sim_status === "RUNNING" || loadingAction !== null}
                onClick={onRun}
                className="flex items-center justify-center gap-2 rounded-lg bg-emerald-600/20 border border-emerald-500/30 px-4 py-2.5 text-xs font-semibold text-emerald-300 hover:bg-emerald-600/30 disabled:opacity-40 transition"
              >
                <Play size={14} className={loadingAction === "run" ? "animate-spin" : ""} />
                <span>Run</span>
              </button>

              {/* Pause */}
              <button
                type="button"
                data-testid="ctl-pause"
                disabled={status?.sim_status === "PAUSED" || loadingAction !== null}
                onClick={onPause}
                className="flex items-center justify-center gap-2 rounded-lg bg-amber-600/20 border border-amber-500/30 px-4 py-2.5 text-xs font-semibold text-amber-300 hover:bg-amber-600/30 disabled:opacity-40 transition"
              >
                <Pause size={14} className={loadingAction === "pause" ? "animate-spin" : ""} />
                <span>Pause</span>
              </button>

              {/* Step x1 */}
              <button
                type="button"
                data-testid="ctl-step"
                disabled={loadingAction !== null}
                onClick={() => onStep(1)}
                className="flex items-center justify-center gap-2 rounded-lg bg-surface border border-panel-border px-4 py-2.5 text-xs font-semibold text-ink hover:bg-surface/80 hover:border-brand/40 disabled:opacity-40 transition"
              >
                <StepForward size={14} className={loadingAction === "step-1" ? "animate-spin" : ""} />
                <span>Step ×1</span>
              </button>

              {/* Step x10 */}
              <button
                type="button"
                data-testid="ctl-step-10"
                disabled={loadingAction !== null}
                onClick={() => onStep(10)}
                className="flex items-center justify-center gap-2 rounded-lg bg-surface border border-panel-border px-4 py-2.5 text-xs font-semibold text-ink hover:bg-surface/80 hover:border-brand/40 disabled:opacity-40 transition"
              >
                <FastForward size={14} className={loadingAction === "step-10" ? "animate-spin" : ""} />
                <span>Step ×10</span>
              </button>

              {/* Reset */}
              <button
                type="button"
                data-testid="ctl-reset"
                disabled={loadingAction !== null}
                onClick={() => setShowResetConfirm(true)}
                className="flex items-center justify-center gap-2 rounded-lg bg-red-600/20 border border-red-500/30 px-4 py-2.5 text-xs font-semibold text-red-300 hover:bg-red-600/30 disabled:opacity-40 transition"
              >
                <RotateCcw size={14} className={loadingAction === "reset" ? "animate-spin" : ""} />
                <span>Reset…</span>
              </button>
            </div>

            <div className="rounded-lg border border-brand/30 bg-brand/5 p-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="space-y-1">
                <h3 className="text-xs font-semibold text-ink flex items-center gap-2">
                  <Radio size={14} className="text-brand" />
                  Prepare Autopilot Demo
                </h3>
                <p className="text-[11px] text-ink-muted leading-relaxed max-w-2xl">
                  Clears chaos, resets to a healthy baseline, steps 2 ticks, enables Autopilot, and triggers Plan Now.
                  Production guards stay intact — large plans may auto-dispatch a ≤7k L subset; the header banner shows
                  SLO freeze / four-eyes reasons.
                </p>
              </div>
              <button
                type="button"
                data-testid="ctl-prepare-autopilot"
                disabled={loadingAction !== null}
                onClick={onPrepareAutopilotDemo}
                className="shrink-0 flex items-center justify-center gap-2 rounded-lg bg-brand/20 border border-brand/40 px-4 py-2.5 text-xs font-semibold text-brand hover:bg-brand/30 disabled:opacity-40 transition"
              >
                <Layers size={14} className={loadingAction === "prepare-autopilot" ? "animate-spin" : ""} />
                <span>Arm Autopilot Demo</span>
              </button>
            </div>
          </div>

          {/* SECTION 2: CRISIS PRESETS */}
          <div className="rounded-lg border border-panel-border bg-panel p-5 shadow-panel space-y-4">
            <div className="border-b border-panel-border/40 pb-3 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-ink flex items-center gap-2">
                  <Flame size={16} className="text-amber-400" />
                  Crisis Scenario Presets (21_API_CONTRACTS.md §2.8)
                </h2>
                <p className="text-xs text-ink-muted">
                  Inject deterministic disruption vectors to test optimization and contingency failover.
                </p>
              </div>
              <span className="text-[11px] text-ink-muted font-mono">8 Documented Presets</span>
            </div>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
              {status?.presets &&
                Object.entries(status.presets).map(([key, preset]) => (
                  <div
                    key={key}
                    className="flex flex-col justify-between rounded-lg border border-panel-border bg-surface/40 p-4 transition hover:border-panel-border/80"
                  >
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-bold text-ink">{preset.title}</span>
                        <Zap size={14} className="text-amber-400" />
                      </div>
                      <p className="text-[11px] text-ink-muted leading-relaxed">{preset.description}</p>
                    </div>

                    <button
                      type="button"
                      data-testid={`ctl-preset-${key}`}
                      disabled={loadingAction !== null}
                      onClick={() => onTriggerPreset(key, preset.title)}
                      className="mt-4 flex w-full items-center justify-center gap-1.5 rounded bg-surface border border-panel-border px-3 py-1.5 text-xs font-medium text-ink hover:bg-brand/20 hover:border-brand hover:text-brand disabled:opacity-40 transition"
                    >
                      {loadingAction === `preset-${key}` ? (
                        <RefreshCw size={12} className="animate-spin" />
                      ) : (
                        <Flame size={12} />
                      )}
                      <span>Inject Preset</span>
                    </button>
                  </div>
                ))}
            </div>
          </div>

          {/* SECTION 3: CHAOS CONTROLS */}
          <div className="rounded-lg border border-panel-border bg-panel p-5 shadow-panel space-y-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between border-b border-panel-border/40 pb-3">
              <div>
                <h2 className="text-sm font-semibold text-ink flex items-center gap-2">
                  <Bug size={16} className="text-rose-400" />
                  Resilience & Chaos Drills (F1–F18 Fault Vectors)
                </h2>
                <p className="text-xs text-ink-muted">
                  Inject communication faults, latency, and telemetry corruption to verify circuit breakers.
                </p>
              </div>

              <div className="flex items-center gap-3">
                {/* Duration Picker */}
                <div className="flex items-center gap-1 rounded-lg border border-panel-border bg-surface p-1">
                  <span className="px-2 text-[11px] text-ink-muted font-mono">Duration:</span>
                  {[30, 60, 120].map((d) => (
                    <button
                      key={d}
                      type="button"
                      onClick={() => setChaosDuration(d)}
                      className={`rounded px-2.5 py-0.5 text-xs font-mono font-medium transition ${
                        chaosDuration === d
                          ? "bg-brand text-ink-contrast"
                          : "text-ink-muted hover:text-ink"
                      }`}
                    >
                      {d}s
                    </button>
                  ))}
                </div>

                {/* Clear Faults */}
                <button
                  type="button"
                  data-testid="ctl-chaos-clear"
                  disabled={loadingAction !== null}
                  onClick={onClearFaults}
                  className="flex items-center gap-1.5 rounded-lg border border-panel-border bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-emerald-600/20 hover:border-emerald-500/40 hover:text-emerald-300 disabled:opacity-40 transition"
                >
                  <Trash2 size={13} />
                  <span>Clear Faults</span>
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {status?.faults &&
                Object.entries(status.faults).map(([key, label]) => (
                  <div
                    key={key}
                    className="flex flex-col justify-between rounded-lg border border-panel-border bg-surface/30 p-3.5"
                  >
                    <div>
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-[11px] uppercase tracking-wider text-rose-400">{key}</span>
                        <AlertTriangle size={13} className="text-rose-400" />
                      </div>
                      <p className="mt-1 text-xs font-medium text-ink leading-snug">{label}</p>
                    </div>

                    <button
                      type="button"
                      data-testid={`ctl-chaos-${key}`}
                      disabled={loadingAction !== null}
                      onClick={() => onInjectFault(key, label)}
                      className="mt-3 flex w-full items-center justify-center gap-1.5 rounded bg-surface border border-panel-border px-2.5 py-1.5 text-[11px] font-medium text-ink hover:bg-rose-500/20 hover:border-rose-500/50 hover:text-rose-300 disabled:opacity-40 transition"
                    >
                      {loadingAction === `chaos-${key}` ? (
                        <RefreshCw size={11} className="animate-spin" />
                      ) : (
                        <Zap size={11} />
                      )}
                      <span>Inject Fault</span>
                    </button>
                  </div>
                ))}
            </div>
          </div>

          {/* SECTION 4: LAST ACTION LOG */}
          <div className="rounded-lg border border-panel-border bg-panel p-5 shadow-panel space-y-3">
            <div className="flex items-center justify-between border-b border-panel-border/40 pb-3">
              <div>
                <h2 className="text-sm font-semibold text-ink flex items-center gap-2">
                  <Layers size={16} className="text-ink-muted" />
                  Control Operation Audit Log
                </h2>
                <p className="text-xs text-ink-muted">
                  Immutable record of recent operator actions, crisis triggers, and simulator stepping.
                </p>
              </div>

              <button
                type="button"
                data-testid="ctl-refresh-log"
                onClick={() => refetchLogs()}
                className="flex items-center gap-1.5 rounded bg-surface border border-panel-border px-2.5 py-1 text-xs text-ink-muted hover:text-ink transition"
              >
                <RefreshCw size={12} className={logsFetching ? "animate-spin" : ""} />
                <span>Refresh Log</span>
              </button>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs font-mono">
                <thead>
                  <tr className="border-b border-panel-border/60 text-ink-muted">
                    <th className="py-2 px-3 font-semibold">Timestamp</th>
                    <th className="py-2 px-3 font-semibold">Action</th>
                    <th className="py-2 px-3 font-semibold">Actor</th>
                    <th className="py-2 px-3 font-semibold">Status</th>
                    <th className="py-2 px-3 font-semibold">Result Details</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-panel-border/30">
                  {logsData?.logs && logsData.logs.length > 0 ? (
                    logsData.logs.map((log) => (
                      <tr key={log.id} data-testid="ctl-log-row" className="hover:bg-surface/30 transition">
                        <td className="py-2 px-3 text-ink-muted whitespace-nowrap">
                          {new Date(log.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                        </td>
                        <td className="py-2 px-3 text-brand font-semibold whitespace-nowrap">{log.action}</td>
                        <td className="py-2 px-3 text-ink-muted">{log.actor}</td>
                        <td className="py-2 px-3">
                          <span
                            className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-bold ${
                              log.status === "SUCCESS"
                                ? "bg-emerald-500/20 text-emerald-300"
                                : "bg-red-500/20 text-red-300"
                            }`}
                          >
                            {log.status === "SUCCESS" ? "✓ OK" : "✗ FAIL"}
                          </span>
                        </td>
                        <td className="py-2 px-3 text-ink max-w-md truncate font-sans text-[11px]">
                          {log.result || JSON.stringify(log.details)}
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={5} className="py-6 text-center text-ink-muted font-sans">
                        No simulator control actions recorded yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {/* Confirmation Modal for Reset */}
      {showResetConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="w-full max-w-md rounded-lg border border-red-500/40 bg-panel p-6 shadow-2xl space-y-4">
            <div className="flex items-center gap-3 text-red-400">
              <AlertTriangle size={24} />
              <h3 className="text-base font-bold text-ink">Confirm Scenario Reset</h3>
            </div>
            <p className="text-xs text-ink-muted leading-relaxed">
              Resetting will wipe all active shipments, in-flight consignments, injected disruptions, and simulated
              inventory changes, returning the simulator to <strong className="text-ink">Day 1 00:00 (tick 0)</strong>.
            </p>
            <div className="flex justify-end gap-2 pt-2 border-t border-panel-border/40">
              <button
                type="button"
                data-testid="ctl-reset-cancel"
                onClick={() => setShowResetConfirm(false)}
                className="rounded-lg bg-surface border border-panel-border px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface/80 transition"
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="ctl-reset-confirm"
                onClick={onReset}
                className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-500 transition shadow-sm"
              >
                Confirm Reset
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
