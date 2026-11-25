import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import WhatIfPage from "@/pages/WhatIf";
import * as client from "@/api/client";
import { BrowserRouter } from "react-router-dom";

// Mock client
vi.mock("@/api/client", () => ({
  executeWhatIf: vi.fn(),
}));

// Mock useLiveState
vi.mock("@/hooks/useLiveState", () => ({
  useLiveState: () => ({
    state: {
      tick: 40,
      scarcity: { active: false },
      routes: [
        {
          id: "route-gazipur-mirpur",
          source_depot_id: "depot-gazipur",
          destination_station_id: "station-mirpur",
          transit_ticks: 2,
          max_shipment: 7000,
          status: "AVAILABLE",
        },
        {
          id: "route-gazipur-tongi",
          source_depot_id: "depot-gazipur",
          destination_station_id: "station-tongi",
          transit_ticks: 2,
          max_shipment: 6500,
          status: "AVAILABLE",
        },
      ],
      stations: [
        { id: "station-mirpur", name: "Mirpur Station", region_id: "dhaka" },
        { id: "station-tongi", name: "Tongi Station", region_id: "dhaka" },
      ],
      depots: [
        {
          id: "depot-gazipur",
          name: "Gazipur Depot",
          inventory: { DIESEL: 25000, PETROL: 20000, OCTANE: 15000 },
        },
      ],
    },
    isLoading: false,
    error: null,
  }),
}));

describe("What-If Scenario Simulation Page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders configuration controls with real route options and dark theme styling", () => {
    render(
      <BrowserRouter>
        <WhatIfPage />
      </BrowserRouter>
    );

    expect(screen.getByText("What-If Scenario Simulation")).toBeInTheDocument();
    expect(screen.getByText("⚡ Hypothetical Disruption / Event")).toBeInTheDocument();
    expect(screen.getByText("📦 Candidate Custom Dispatch")).toBeInTheDocument();

    // Verify dynamic route options are populated from live state
    expect(screen.getByRole("option", { name: "route-gazipur-mirpur" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "route-gazipur-tongi" })).toBeInTheDocument();
    expect(screen.getByText(/Corridor:/)).toBeInTheDocument();
  });

  it("executes what-if simulation and displays three scenario comparison cards", async () => {
    vi.mocked(client.executeWhatIf).mockResolvedValueOnce({
      tick: 40,
      disclaimer: "Projection by our model; not executed.",
      scenarios: [
        {
          name: "do_nothing",
          min_station_sl: 1.0,
          unmet: 0,
          overflow: 0,
          stations: [
            {
              station_id: "station-mirpur",
              fuel_type: "DIESEL",
              stockout_prob: 0,
              inventory_p50: [5000, 4800],
              initial_inv: 5000,
              ending_inv: 4800,
              delta_liters: -200,
            },
          ],
        },
        {
          name: "recommended",
          min_station_sl: 1.0,
          unmet: 0,
          overflow: 0,
          stations: [
            {
              station_id: "station-mirpur",
              fuel_type: "DIESEL",
              stockout_prob: 0,
              inventory_p50: [5000, 7000],
              initial_inv: 5000,
              ending_inv: 7000,
              delta_liters: 2000,
            },
          ],
        },
        {
          name: "custom",
          min_station_sl: 1.0,
          unmet: 0,
          overflow: 0,
          stations: [
            {
              station_id: "station-mirpur",
              fuel_type: "DIESEL",
              stockout_prob: 0,
              inventory_p50: [5000, 7200],
              initial_inv: 5000,
              ending_inv: 7200,
              delta_liters: 2200,
            },
          ],
        },
      ],
    });

    render(
      <BrowserRouter>
        <WhatIfPage />
      </BrowserRouter>
    );

    const btn = screen.getByText("Run What-If Simulation ▸");
    fireEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 4, name: /do nothing/i })).toBeInTheDocument();
      expect(screen.getByRole("heading", { level: 4, name: /recommended/i })).toBeInTheDocument();
      expect(screen.getByRole("heading", { level: 4, name: /custom/i })).toBeInTheDocument();
    });

    expect(screen.getByText("Ending: 4,800 L")).toBeInTheDocument();
    expect(screen.getByText("-200 L")).toBeInTheDocument();
    expect(screen.getByText("+2,200 L")).toBeInTheDocument();
  });

  it("displays prominent validation error banner on infeasible dispatch", async () => {
    vi.mocked(client.executeWhatIf).mockRejectedValueOnce(
      new Error("Quantity 8500 L exceeds route 'route-gazipur-mirpur' max shipment limit of 7000 L")
    );

    render(
      <BrowserRouter>
        <WhatIfPage />
      </BrowserRouter>
    );

    const btn = screen.getByText("Run What-If Simulation ▸");
    fireEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByText("❌ Dispatch Validation / Simulation Error")).toBeInTheDocument();
      expect(screen.getByText(/exceeds route 'route-gazipur-mirpur' max shipment limit/)).toBeInTheDocument();
    });
  });
});
