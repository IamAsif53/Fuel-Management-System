import { Route, Routes, Navigate } from "react-router-dom";
import { AppLayout } from "@/components/layout/AppLayout";
import Overview from "@/pages/Overview";
import Network from "@/pages/Network";
import SystemHealth from "@/pages/SystemHealth";
import Recommendations from "@/pages/Recommendations";
import Decisions from "@/pages/Decisions";
import RiskPage from "@/pages/Risk";
import AlertsPage from "@/pages/Alerts";
import WhatIfPage from "@/pages/WhatIf";
import { PlaceholderPage } from "@/pages/PlaceholderPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        {/* Default route redirects to /overview per spec */}
        <Route path="/" element={<Navigate to="/overview" replace />} />

        {/* Phase 03 Pages */}
        <Route path="/overview" element={<Overview />} />
        <Route path="/network" element={<Network />} />
        <Route path="/health" element={<SystemHealth />} />

        {/* Phase 06 Pages */}
        <Route path="/risk" element={<RiskPage />} />
        <Route path="/alerts" element={<AlertsPage />} />

        {/* Phase 04 Pages */}
        <Route path="/recommendations" element={<Recommendations />} />
        <Route path="/decisions" element={<Decisions />} />
        <Route path="/whatif" element={<WhatIfPage />} />
        <Route
          path="/assistant"
          element={
            <PlaceholderPage
              title="AI Operations Assistant"
              phase={9}
              description="Grounded natural language operations copilot for querying live network state and drafting shift summaries."
            />
          }
        />
        <Route
          path="/control"
          element={
            <PlaceholderPage
              title="Simulator Controls (Demo)"
              phase={10}
              description="Interactive simulation stepping, pause/resume, chaos fault injection, and crisis preset execution."
            />
          }
        />

        {/* Catch-all fallback */}
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Route>
    </Routes>
  );
}
