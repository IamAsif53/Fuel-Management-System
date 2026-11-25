import { Outlet } from "react-router-dom";
import { useLiveState } from "@/hooks/useLiveState";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";
import { DegradedBanner } from "./DegradedBanner";
import { ToastContainer } from "@/components/Toast";
import { CommandPalette } from "@/components/CommandPalette";

export function AppLayout() {
  const { state, connection } = useLiveState();

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-bg text-ink antialiased">
      {/* SIMULATED strip: permanently fixed at the very top */}
      <div
        data-testid="simulated-strip"
        className="shrink-0 bg-amber-500 py-0.5 text-center text-[11px] font-bold tracking-wider text-black select-none"
      >
        SIMULATED ENVIRONMENT – NOT REAL FUEL INFRASTRUCTURE
      </div>

      {/* Header */}
      <Header state={state} connection={connection} />

      {/* Degraded Banner */}
      <DegradedBanner state={state} connection={connection} />

      {/* Main Body */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* Navigation Sidebar */}
        <Sidebar />

        {/* Scrollable Page Viewport */}
        <main className="min-w-0 flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>

      {/* Real-time Toast Notifications */}
      <ToastContainer />

      {/* Global Command Palette (Ctrl+K) */}
      <CommandPalette />

      {/* Release Version Footer (Phase 13) */}
      <footer
        data-testid="app-version-footer"
        className="shrink-0 border-t border-bg-border bg-bg-panel px-4 py-1 text-center text-xs text-ink-muted select-none"
      >
        <span>FSIP {import.meta.env.VITE_APP_VERSION || "v1.0.0"}</span>
        <span className="mx-2">·</span>
        <span className="font-mono">
          sha:{(import.meta.env.VITE_GIT_SHA || "local").slice(0, 7)}
        </span>
      </footer>
    </div>
  );
}
