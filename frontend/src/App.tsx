import { Route, Routes, Navigate } from "react-router-dom";
import { AppLayout } from "@/components/layout/AppLayout";
import Overview from "@/pages/Overview";
import Network from "@/pages/Network";
import SystemHealth from "@/pages/SystemHealth";
import Recommendations from "@/pages/Recommendations";
import Decisions from "@/pages/Decisions";
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

        {/* Future Phase Placeholders */}
        <Route
          path="/risk"
          element={
            <PlaceholderPage
              title="Risk Engine"
              phase={6}
              description="Real-time multi-horizon stockout probability heatmap, survival curves, and causal driver attribution."
            />
          }
        />
        {/* Phase 04 Pages */}
        <Route path="/recommendations" element={<Recommendations />} />
        <Route path="/decisions" element={<Decisions />} />
        <Route
          path="/alerts"
          element={
            <PlaceholderPage
              title="Alerts & Incidents"
              phase={6}
              description="Fingerprinted anomaly alerts, severity classification, and active mitigation workflows."
            />
          }
        />
        <Route
          path="/whatif"
          element={
            <PlaceholderPage
              title="What-if Analysis"
              phase={8}
              description="Counterfactual digital twin simulation, multi-scenario impact comparison, and stress testing."
            />
          }
        />
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
