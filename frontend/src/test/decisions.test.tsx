import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Recommendations from "@/pages/Recommendations";
import Decisions from "@/pages/Decisions";
import { Header } from "@/components/layout/Header";
import type { Decision, WorldState } from "@/api/types";
import * as client from "@/api/client";
import * as liveStateHook from "@/hooks/useLiveState";

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof client>("@/api/client");
  return {
    ...actual,
    fetchRecommendations: vi.fn(),
    fetchDecisions: vi.fn(),
    approveDecision: vi.fn(),
    rejectDecision: vi.fn(),
    triggerPlanNow: vi.fn(),
    setControlMode: vi.fn(),
    setControlPolicy: vi.fn(),
    dispatchManualAllocation: vi.fn(),
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

const mockRecommendations: Decision[] = [
  {
    id: 101,
    tick: 24,
    policy: "heuristic",
    status: "PENDING_APPROVAL",
    confidence: 0.92,
    degraded: false,
    narrative: "Station Mirpur projected to stock out within 4.5 hours.",
    lines: [
      {
        id: 201,
        decision_id: 101,
        line_no: 1,
        source_depot_id: "depot-gazipur",
        destination_station_id: "station-mirpur",
        route_id: "route-gazipur-mirpur",
        fuel_type: "DIESEL",
        quantity: 4000,
        idempotency_key: "fsip-101-1",
        status: "PLANNED",
        reason: "Stockout coverage",
      },
    ],
  },
];

const mockDecisions: Decision[] = [
  {
    id: 100,
    tick: 20,
    policy: "heuristic",
    status: "EXECUTED",
    confidence: 0.95,
    approved_by: "autopilot",
    lines: [
      {
        id: 200,
        decision_id: 100,
        line_no: 1,
        source_depot_id: "depot-dhaka",
        destination_station_id: "station-banani",
        route_id: "route-dhaka-banani",
        fuel_type: "OCTANE",
        quantity: 5000,
        idempotency_key: "fsip-100-1",
        status: "ARRIVED",
        received_liters: 5000,
        actual_arrival_tick: 22,
      },
    ],
    outcome: {
      decision_id: 100,
      evaluated_tick: 24,
      hit: true,
    },
  },
];

describe("Phase 04 UI — Recommendations & Decisions Workflow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(liveStateHook, "useLiveState").mockReturnValue({
      state: {
        sim_run_id: 1,
        tick: 24,
        sim_status: "RUNNING",
        operating_mode: "advisory",
        policy: "heuristic",
        alerts_open_count: { INFO: 0, WARNING: 0, CRITICAL: 0 },
        pending_recommendations: [{ decision_id: 101 }],
      } as unknown as WorldState,
      connection: "live",
      error: null,
      refetch: vi.fn(),
      isLoading: false,
    });
  });

  it("renders recommendations with line details and allows approval", async () => {
    vi.mocked(client.fetchRecommendations).mockResolvedValueOnce(mockRecommendations);
    vi.mocked(client.approveDecision).mockResolvedValueOnce({
      ...mockRecommendations[0],
      status: "APPROVED",
    });

    renderWithClient(<Recommendations />);

    // Header and card
    expect(await screen.findByText("Prescriptive Recommendations")).toBeInTheDocument();
    expect(await screen.findByText("#DEC-101")).toBeInTheDocument();
    expect(screen.getByText("Station Mirpur projected to stock out within 4.5 hours.")).toBeInTheDocument();
    expect(screen.getByText("92% Confidence")).toBeInTheDocument();

    // Line details
    expect(screen.getByText("depot-gazipur")).toBeInTheDocument();
    expect(screen.getByText("station-mirpur")).toBeInTheDocument();
    expect(screen.getByText("DIESEL")).toBeInTheDocument();

    // Click Approve
    const approveBtn = screen.getByText("Approve & Dispatch");
    fireEvent.click(approveBtn);

    await waitFor(() => {
      expect(client.approveDecision).toHaveBeenCalledWith(
        101,
        expect.objectContaining({
          line_ids: [201],
          quantities: { "201": 4000 },
          reviewed_driver: true,
        })
      );
    });
  });

  it("renders decisions history and shows outcome indicators", async () => {
    vi.mocked(client.fetchDecisions).mockResolvedValueOnce({
      items: mockDecisions,
      total: 1,
    });

    renderWithClient(<Decisions />);

    expect(await screen.findByText("Decisions History & Audit Ledger")).toBeInTheDocument();
    expect(await screen.findByText("#DEC-100")).toBeInTheDocument();
    expect(screen.getByText("Target Realised (Hit)")).toBeInTheDocument();
    expect(screen.getByText(/5,000 L/)).toBeInTheDocument();

    // Click to expand line details
    fireEvent.click(screen.getByText("#DEC-100"));
    expect(await screen.findByText("route-dhaka-banani")).toBeInTheDocument();
    expect(screen.getByText("ARRIVED")).toBeInTheDocument();
  });

  it("renders Header operating mode toggle and prompts on autopilot", async () => {
    const mockState = {
      tick: 30,
      sim_status: "RUNNING",
      operating_mode: "advisory",
      policy: "heuristic",
      alerts_open_count: { INFO: 0, WARNING: 0, CRITICAL: 0 },
      pending_recommendations: [{ decision_id: 101 }],
    } as unknown as WorldState;

    renderWithClient(<Header state={mockState} connection="live" />);

    const modeBtn = screen.getByText("Advisory");
    fireEvent.click(modeBtn);

    // Autopilot confirmation modal
    expect(await screen.findByText("Enable Autopilot Mode?")).toBeInTheDocument();
    const confirmBtn = screen.getByText("Confirm & Enable Autopilot");
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(client.setControlMode).toHaveBeenCalledWith("autopilot");
    });
  });
});
