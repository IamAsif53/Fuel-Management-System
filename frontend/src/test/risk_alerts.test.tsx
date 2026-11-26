import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RiskHeatmap } from "@/components/overview/RiskHeatmap";
import { FuelBudgetCard } from "@/components/overview/FuelBudgetCard";
import RiskPage from "@/pages/Risk";
import AlertsPage from "@/pages/Alerts";
import * as client from "@/api/client";
import type { CoverResponse, StationView } from "@/api/types";

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof client>("@/api/client");
  return {
    ...actual,
    fetchRisk: vi.fn(),
    fetchAlerts: vi.fn(),
    acknowledgeAlert: vi.fn(),
    fetchCover: vi.fn(),
    fetchIncidents: vi.fn(),
  };
});

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe("Phase 06 UI — Risk and Alerts Components & Pages", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders RiskHeatmap with risk-cell-<station>-<fuel> testids", () => {
    const mockStations: StationView[] = [
      {
        id: "station-dhaka",
        name: "Dhaka Central",
        region_id: "dhaka",
        status: "OPEN",
        demand_profile: "commercial",
        demand_multiplier: 1.0,
        capacity: { DIESEL: 50000, PETROL: 30000, OCTANE: 20000 },
        inventory: { DIESEL: 5000, PETROL: 15000, OCTANE: 10000 },
        inflight: { DIESEL: 0, PETROL: 0, OCTANE: 0 },
        fill_pct: { DIESEL: 0.1, PETROL: 0.5, OCTANE: 0.5 },
        risk: {
          DIESEL: {
            level: "CRITICAL",
            hours_to_stockout: 2.5,
            stockout_prob_lead: 0.95,
            stockout_prob_horizon: 0.98,
            inventory: 5000,
            inflight: 0,
            fill_pct: 0.1,
            cover_hours: 2.5,
            drivers: [{ signal: "low_inventory", value: 5000, detail: "Low stock" }],
            forecast_model: "seasonal",
          },
          PETROL: {
            level: "LOW",
            hours_to_stockout: 48.0,
            stockout_prob_lead: 0.01,
            stockout_prob_horizon: 0.02,
            inventory: 15000,
            inflight: 0,
            fill_pct: 0.5,
            cover_hours: 48.0,
            drivers: [],
            forecast_model: "seasonal",
          },
          OCTANE: {
            level: "LOW",
            hours_to_stockout: 96.0,
            stockout_prob_lead: 0.0,
            stockout_prob_horizon: 0.0,
            inventory: 10000,
            inflight: 0,
            fill_pct: 0.5,
            cover_hours: 96.0,
            drivers: [],
            forecast_model: "seasonal",
          },
        },
        routes_available: 2,
        single_route: false,
      },
    ];

    render(<RiskHeatmap stations={mockStations} />);

    expect(screen.getByTestId("risk-heatmap")).toBeInTheDocument();
    expect(screen.getByTestId("risk-cell-station-dhaka-DIESEL")).toBeInTheDocument();
    expect(screen.getByTestId("risk-cell-station-dhaka-PETROL")).toBeInTheDocument();
    expect(screen.getByTestId("risk-cell-station-dhaka-OCTANE")).toBeInTheDocument();

    const dieselCell = screen.getByTestId("risk-cell-station-dhaka-DIESEL");
    expect(dieselCell).toHaveTextContent("CRITICAL");
    expect(dieselCell).toHaveTextContent("2.5h");
  });

  it("renders FuelBudgetCard with fuel-budget testid and supply ends tick 212", () => {
    const mockCover: CoverResponse = {
      network: {
        DIESEL: { available: 120000, demand_96: 30000, cover_days: 4.0 },
        PETROL: { available: 50000, demand_96: 25000, cover_days: 2.0 },
        OCTANE: { available: 40000, demand_96: 10000, cover_days: 4.0 },
      },
      regions: {
        dhaka: {
          DIESEL: { available: 80000, demand_96: 20000, cover_days: 4.0 },
          PETROL: { available: 30000, demand_96: 15000, cover_days: 2.0 },
          OCTANE: { available: 20000, demand_96: 5000, cover_days: 4.0 },
        },
      },
      supply_ends_tick: 212,
      scarcity: { DIESEL: false, PETROL: true, OCTANE: false },
    };

    render(<FuelBudgetCard cover={mockCover} currentTick={100} />);

    expect(screen.getByTestId("fuel-budget")).toBeInTheDocument();
    expect(screen.getByText("Network Fuel Budget (F-COVER)")).toBeInTheDocument();
    expect(screen.getByText(/Supply Ends: t=212/i)).toBeInTheDocument();
    expect(screen.getByText("SCARCITY")).toBeInTheDocument();
  });

  it("renders RiskPage and displays risk rows", async () => {
    vi.mocked(client.fetchRisk).mockResolvedValue({
      tick: 40,
      rows: [
        {
          station_id: "station-tongi",
          station_name: "Tongi Station",
          region_id: "dhaka",
          fuel_type: "DIESEL",
          risk_level: "CRITICAL",
          hours_to_stockout: 2.0,
          stockout_prob: 0.95,
          inventory: 4000,
          drivers: ["low_inventory", "approaching_stockout"],
        },
      ],
    });

    renderWithClient(<RiskPage />);

    await waitFor(() => {
      expect(screen.getByTestId("risk-page")).toBeInTheDocument();
      expect(screen.getByText("Tongi Station")).toBeInTheDocument();
      expect(screen.getByText("CRITICAL")).toBeInTheDocument();
      expect(screen.getByText("2.0 h")).toBeInTheDocument();
      expect(screen.getByText("low_inventory")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("risk-region-filter"), { target: { value: "region-dhaka" } });
    await waitFor(() => {
      expect(client.fetchRisk).toHaveBeenCalledWith(
        expect.objectContaining({ region_id: "region-dhaka" })
      );
    });
  });

  it("renders AlertsPage and allows acknowledging an open alert", async () => {
    vi.mocked(client.fetchAlerts).mockResolvedValue({
      items: [
        {
          id: 101,
          fingerprint: "fp-tongi-diesel",
          category: "STOCKOUT_RISK",
          severity: "CRITICAL",
          title: "Critical Stockout Risk at Tongi",
          message: "DIESEL inventory below 4,000 L",
          entity_type: "station",
          entity_id: "station-tongi",
          fuel_type: "DIESEL",
          status: "OPEN",
          opened_tick: 40,
          opened_at: "2026-01-01T10:00:00Z",
        },
      ],
      total: 1,
    });
    vi.mocked(client.fetchIncidents).mockResolvedValue({ items: [] });
    vi.mocked(client.acknowledgeAlert).mockResolvedValue({
      id: 101,
      fingerprint: "fp-tongi-diesel",
      category: "STOCKOUT_RISK",
      severity: "CRITICAL",
      title: "Critical Stockout Risk at Tongi",
      status: "ACKNOWLEDGED",
      opened_at: "2026-01-01T10:00:00Z",
    });

    renderWithClient(<AlertsPage />);

    await waitFor(() => {
      expect(screen.getByTestId("alerts-page")).toBeInTheDocument();
      expect(screen.getByTestId("alert-row-101")).toBeInTheDocument();
      expect(screen.getByText("Critical Stockout Risk at Tongi")).toBeInTheDocument();
      expect(screen.getByTestId("ack-btn-101")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("ack-btn-101"));

    await waitFor(() => {
      expect(client.acknowledgeAlert).toHaveBeenCalledWith(101);
    });

    // Switching scope to All Runs passes all_runs=true to fetchAlerts
    fireEvent.change(screen.getByTestId("alert-scope-filter"), { target: { value: "ALL" } });
    await waitFor(() => {
      expect(client.fetchAlerts).toHaveBeenCalledWith(
        expect.objectContaining({ all_runs: true })
      );
    });
  });
});
