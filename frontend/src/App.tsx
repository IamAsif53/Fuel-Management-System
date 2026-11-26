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
import AssistantPage from "@/pages/Assistant";
import ControlPage from "@/pages/Control";

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
        <Route path="/system" element={<Navigate to="/health" replace />} />
        <Route path="/system-health" element={<Navigate to="/health" replace />} />

        {/* Phase 06 Pages */}
        <Route path="/risk" element={<RiskPage />} />
        <Route path="/alerts" element={<AlertsPage />} />

        {/* Phase 04 Pages */}
        <Route path="/recommendations" element={<Recommendations />} />
        <Route path="/decisions" element={<Decisions />} />
        <Route path="/decisions/:decisionId" element={<Decisions />} />
        <Route path="/whatif" element={<WhatIfPage />} />
        <Route path="/what-if" element={<Navigate to="/whatif" replace />} />
        <Route path="/assistant" element={<AssistantPage />} />
        <Route path="/control" element={<ControlPage />} />

        {/* Catch-all fallback */}
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Route>
    </Routes>
  );
}
