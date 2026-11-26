import { Outlet } from "react-router-dom";
import { useLiveState } from "@/hooks/useLiveState";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";
import { DegradedBanner } from "./DegradedBanner";
import { AutopilotStatusBanner } from "./AutopilotStatusBanner";
import { ToastContainer } from "@/components/Toast";
import { CommandPalette } from "@/components/CommandPalette";

export function AppLayout() {
  const { state, connection } = useLiveState();

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-bg text-ink antialiased">
      <Header state={state} connection={connection} />

      <DegradedBanner state={state} connection={connection} />
      <AutopilotStatusBanner state={state} />

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <Sidebar />

        <main className="min-w-0 flex-1 overflow-y-auto p-5 sm:p-6 lg:p-7">
          <Outlet />
        </main>
      </div>

      <ToastContainer />
      <CommandPalette />

      <footer
        data-testid="app-version-footer"
        className="shrink-0 border-t border-bg-border bg-bg-panel/80 px-4 py-1.5 text-center text-xs text-ink-muted select-none backdrop-blur-sm"
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
