import { useState } from "react";
import { Link } from "react-router-dom";
import { Activity, Bell, Inbox, Play, Bot, UserCheck } from "lucide-react";
import clsx from "clsx";
import type { WorldState } from "@/api/types";
import type { ConnectionState } from "@/hooks/useLiveState";
import { setControlMode, setControlPolicy, triggerPlanNow } from "@/api/client";
import { ClockBadge } from "./ClockBadge";
import { ConnectionDot } from "./ConnectionDot";
import { EngineModeBadge } from "./EngineModeBadge";

interface HeaderProps {
  state: WorldState | null;
  connection: ConnectionState;
}

export function Header({ state, connection }: HeaderProps) {
  const [isChangingMode, setIsChangingMode] = useState(false);
  const [isChangingPolicy, setIsChangingPolicy] = useState(false);
  const [isPlanning, setIsPlanning] = useState(false);
  const [showAutopilotConfirm, setShowAutopilotConfirm] = useState(false);

  const openAlerts = state?.alerts_open_count
    ? Object.values(state.alerts_open_count).reduce((acc, c) => acc + c, 0)
    : 0;

  const pendingRecsCount = state?.pending_recommendations?.length || 0;
  const currentMode = state?.operating_mode || "advisory";
  const currentPolicy = state?.policy || "heuristic";

  const handleToggleMode = async () => {
    if (currentMode === "advisory") {
      setShowAutopilotConfirm(true);
    } else {
      try {
        setIsChangingMode(true);
        await setControlMode("advisory");
      } catch (err) {
        console.error("Failed to set mode", err);
      } finally {
        setIsChangingMode(false);
      }
    }
  };

  const confirmAutopilot = async () => {
    setShowAutopilotConfirm(false);
    try {
      setIsChangingMode(true);
      await setControlMode("autopilot");
    } catch (err) {
      console.error("Failed to set autopilot mode", err);
    } finally {
      setIsChangingMode(false);
    }
  };

  const handlePolicyChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const newPolicy = e.target.value;
    try {
      setIsChangingPolicy(true);
      await setControlPolicy(newPolicy);
    } catch (err) {
      console.error("Failed to set policy", err);
    } finally {
      setIsChangingPolicy(false);
    }
  };

  const handlePlanNow = async () => {
    try {
      setIsPlanning(true);
      await triggerPlanNow();
    } catch (err) {
      console.error("Plan now failed", err);
    } finally {
      setIsPlanning(false);
    }
  };

  return (
    <>
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-bg-border bg-bg-panel px-4 sm:px-6">
        {/* Brand logo */}
        <Link to="/overview" className="flex items-center gap-2 hover:opacity-90">
          <Activity className="text-brand" size={20} aria-hidden />
          <span className="font-semibold tracking-wide text-ink text-sm sm:text-base">
            Fuel Ops Console
          </span>
        </Link>

        {/* Clock Badge */}
        <div className="ml-2 hidden sm:block">
          <ClockBadge state={state} connection={connection} />
        </div>

        {/* Engine Mode */}
        <div className="hidden md:block">
          <EngineModeBadge mode={state?.engine_mode} />
        </div>

        {/* Controls Section */}
        <div className="flex items-center gap-2 ml-2">
          {/* Advisory / Autopilot Switch */}
          <button
            onClick={handleToggleMode}
            disabled={isChangingMode}
            className={clsx(
              "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium border transition-colors",
              currentMode === "autopilot"
                ? "bg-brand/15 border-brand text-brand hover:bg-brand/25"
                : "bg-bg-raised border-bg-border text-ink-muted hover:text-ink hover:bg-bg-raised/80"
            )}
            title={`Click to switch to ${currentMode === "autopilot" ? "Advisory" : "Autopilot"}`}
          >
            {currentMode === "autopilot" ? (
              <>
                <Bot size={13} className="text-brand" />
                <span className="hidden sm:inline">Autopilot</span>
              </>
            ) : (
              <>
                <UserCheck size={13} />
                <span className="hidden sm:inline">Advisory</span>
              </>
            )}
          </button>

          {/* Policy Selector */}
          <div className="relative inline-flex items-center">
            <select
              aria-label="Policy Selector"
              value={currentPolicy}
              onChange={handlePolicyChange}
              disabled={isChangingPolicy}
              className="bg-bg-raised border border-bg-border text-xs rounded px-2 py-0.5 text-ink-muted hover:text-ink focus:outline-none focus:border-brand cursor-pointer"
            >
              <option value="heuristic">Heuristic</option>
              <option value="optimizer">Optimizer</option>
            </select>
          </div>

          {/* Plan Now Button */}
          <button
            onClick={handlePlanNow}
            disabled={isPlanning}
            className={clsx(
              "flex items-center gap-1 rounded bg-bg-raised hover:bg-brand hover:text-white border border-bg-border px-2 py-1 text-xs font-medium text-ink-muted transition-colors",
              isPlanning && "opacity-50 cursor-not-allowed"
            )}
            title="Trigger an immediate planning cycle"
          >
            <Play size={11} className={clsx(isPlanning && "animate-spin")} />
            <span className="hidden sm:inline">Plan Now</span>
          </button>
        </div>

        {/* Right side items */}
        <div className="ml-auto flex items-center gap-3">
          {/* Pending Recommendations Badge */}
          {pendingRecsCount > 0 && (
            <Link
              to="/recommendations"
              className="flex items-center gap-1.5 rounded-md border border-brand/40 bg-brand/10 px-2 py-0.5 text-xs font-semibold text-brand hover:bg-brand/20 transition-colors"
              title={`${pendingRecsCount} pending recommendations awaiting review`}
            >
              <Inbox size={13} />
              <span>{pendingRecsCount}</span>
            </Link>
          )}

          {/* Alerts count */}
          {openAlerts > 0 && (
            <div
              className="flex items-center gap-1.5 rounded-md border border-risk-high/30 bg-risk-high/15 px-2 py-0.5 text-xs font-semibold text-risk-high"
              title={`${openAlerts} open alerts`}
            >
              <Bell size={13} />
              <span>{openAlerts}</span>
            </div>
          )}

          {/* Connection status */}
          <ConnectionDot connection={connection} />
        </div>
      </header>

      {/* Autopilot Confirmation Modal */}
      {showAutopilotConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-xl border border-brand/40 bg-bg-panel p-6 shadow-2xl">
            <div className="flex items-center gap-3 text-brand">
              <Bot size={24} />
              <h3 className="text-lg font-semibold text-ink">Enable Autopilot Mode?</h3>
            </div>
            <p className="mt-3 text-sm text-ink-muted leading-relaxed">
              In Autopilot mode, low-risk fuel allocations are automatically approved and dispatched
              to the simulator without requiring operator intervention. High-risk dispatches or
              severe capacity breaches will still be surfaced for supervisory review.
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setShowAutopilotConfirm(false)}
                className="rounded-lg border border-bg-border px-4 py-2 text-sm font-medium text-ink-muted hover:bg-bg-raised hover:text-ink"
              >
                Cancel
              </button>
              <button
                onClick={confirmAutopilot}
                className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand/90"
              >
                Confirm & Enable Autopilot
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
