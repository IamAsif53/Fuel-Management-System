import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Recommendations from "@/pages/Recommendations";
import { ExplainPanel } from "@/components/ExplainPanel";
import type { Decision } from "@/api/types";
import * as client from "@/api/client";
import * as liveStateHook from "@/hooks/useLiveState";

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof client>("@/api/client");
  return {
    ...actual,
    fetchRecommendations: vi.fn(),
    approveDecision: vi.fn(),
    rejectDecision: vi.fn(),
  };
});

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

const mockBigDecision: Decision = {
  id: 42,
  tick: 140,
  policy: "optimizer@1.0.0",
  status: "PENDING_APPROVAL",
  confidence: 0.81,
  degraded: false,
  narrative: "Ship 6,000 L DIESEL to station-mirpur to avert stockout.",
  lines: [
    {
      id: 101,
      decision_id: 42,
      line_no: 1,
      source_depot_id: "depot-gazipur",
      destination_station_id: "station-mirpur",
      route_id: "route-gazipur-mirpur",
      fuel_type: "DIESEL",
      quantity: 6000.0,
      status: "PLANNED",
      reason: "Projected stockout coverage",
      idempotency_key: "fsip-1-42-1",
    },
  ],
  explanation: {
    headline: "Ship 6,000 L DIESEL from depot-gazipur to station-mirpur",
    why_at_risk: [
      {
        signal: "projected_stockout",
        value: "6.2 h",
        detail: "inventory 8,400 L vs expected demand",
      },
    ],
    constraints: ["depot dispatch remaining verified", "station ullage room checked"],
    expected_impact: {
      stockout_prob_before: 0.72,
      stockout_prob_after: 0.19,
      unmet_before: 2300,
      unmet_after: 150,
    },
    confidence: 0.81,
    confidence_factors: { forecast_coverage: 0.85 },
    alternatives: [
      {
        name: "do nothing",
        stockout_prob_after: 0.72,
        why_not: "Leaves station vulnerable to stockout",
      },
    ],
    policy: "optimizer@1.0.0",
    forecast_model: "lightgbm@1.2.0",
    degraded: false,
  },
};

describe("Phase 09 ExplainPanel & F-TRUST Cognitive Forcing", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(liveStateHook, "useLiveState").mockReturnValue({
      state: { sim_run_id: 1, tick: 140, engine_mode: "NORMAL" } as any,
      connection: "live",
      error: null,
      refetch: async () => {},
      isLoading: false,
    });
  });

  it("renders ExplainPanel with all required sections", () => {
    renderWithProviders(<ExplainPanel decision={mockBigDecision} isExpanded={true} />);

    expect(screen.getByText(/Why this recommendation\?/i)).toBeInTheDocument();
    expect(screen.getByText(/Primary Risk Signals/i)).toBeInTheDocument();
    expect(screen.getByText(/Expected Impact/i)).toBeInTheDocument();
    expect(screen.getByText(/Verified System Constraints/i)).toBeInTheDocument();
    expect(screen.getByText(/Evaluated Alternatives/i)).toBeInTheDocument();
  });

  it("enforces F-TRUST: big recommendation (>5000L) disables Approve until Why is expanded and reviewed is checked", async () => {
    vi.mocked(client.fetchRecommendations).mockResolvedValue([mockBigDecision]);
    vi.mocked(client.approveDecision).mockResolvedValue({ ...mockBigDecision, status: "APPROVED" });

    renderWithProviders(<Recommendations />);

    // Wait for card to appear
    await waitFor(() => {
      expect(screen.getByTestId("rec-card-42")).toBeInTheDocument();
    });

    const approveBtn = screen.getByTestId("approve-42");
    const whyBtn = screen.getByTestId("why-42");

    // Initially, Approve button is disabled because line is 6,000 L (> 5,000 L)
    expect(approveBtn).toBeDisabled();

    // Expand the Why drawer
    fireEvent.click(whyBtn);

    // Cognitive forcing checkbox must now be visible
    const checkbox = await screen.findByTestId("reviewed-42");
    expect(checkbox).toBeInTheDocument();
    expect(checkbox).not.toBeChecked();

    // Still disabled without checking the box
    expect(screen.getByTestId("approve-42")).toBeDisabled();

    // Check the box
    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();

    // Now Approve button is enabled!
    expect(screen.getByTestId("approve-42")).not.toBeDisabled();

    // Click approve
    fireEvent.click(screen.getByTestId("approve-42"));

    await waitFor(() => {
      expect(client.approveDecision).toHaveBeenCalledWith(
        42,
        expect.objectContaining({
          reviewed_driver: true,
        })
      );
    });
  });
});
