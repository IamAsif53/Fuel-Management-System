import { Outlet } from "react-router-dom";
import { useLiveState } from "@/hooks/useLiveState";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";
import { DegradedBanner } from "./DegradedBanner";

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
      <DegradedBanner state={state} />

      {/* Main Body */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* Navigation Sidebar */}
        <Sidebar />

        {/* Scrollable Page Viewport */}
        <main className="min-w-0 flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
