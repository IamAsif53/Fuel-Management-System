import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Assistant from "@/pages/Assistant";
import * as client from "@/api/client";
import * as liveStateHook from "@/hooks/useLiveState";

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof client>("@/api/client");
  return {
    ...actual,
    askAssistant: vi.fn(),
    fetchSummary: vi.fn(),
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

describe("Phase 09 Ops Assistant Page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(liveStateHook, "useLiveState").mockReturnValue({
      state: { sim_run_id: 1, tick: 140, engine_mode: "NORMAL", degraded: false } as any,
      connection: "live",
      error: null,
      refetch: async () => {},
      isLoading: false,
    });
  });

  it("renders assistant header, suggested prompts, and welcome message", () => {
    renderWithProviders(<Assistant />);

    expect(screen.getByRole("heading", { name: /Operations Assistant/i })).toBeInTheDocument();
    expect(screen.getByText(/Why is Cox's Bazar at risk\?/i)).toBeInTheDocument();
    expect(screen.getByText(/Generate Shift Summary/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Ask about station inventory/i)).toBeInTheDocument();
  });

  it("submits question and renders answer, engine badge, and source chips", async () => {
    vi.mocked(client.askAssistant).mockResolvedValue({
      answer: "station-coxsbazar is at CRITICAL risk due to surge.",
      engine: "rules",
      sources: [{ type: "station", id: "station-coxsbazar" }],
      generated_at: "2026-11-25T00:00:00Z",
    });

    renderWithProviders(<Assistant />);

    const input = screen.getByPlaceholderText(/Ask about station inventory/i);
    const askBtn = screen.getByRole("button", { name: /^ask$/i });

    fireEvent.change(input, { target: { value: "Why is Cox's Bazar at risk?" } });
    fireEvent.click(askBtn);

    await waitFor(() => {
      expect(client.askAssistant).toHaveBeenCalledWith("Why is Cox's Bazar at risk?");
      expect(screen.getByText(/station-coxsbazar is at CRITICAL risk/i)).toBeInTheDocument();
      expect(screen.getByText(/station:station-coxsbazar/i)).toBeInTheDocument();
    });
  });

  it("generates and displays shift summary with KPIs", async () => {
    vi.mocked(client.fetchSummary).mockResolvedValue({
      text: "Shift Summary: Overall service level 99.40%. Dispatched 48,000 L.",
      engine: "template",
      stats: {
        window_ticks: 96,
        service_level: 0.994,
        unmet_liters: 150.0,
        total_dispatched_liters: 48000.0,
        decisions_count: 12,
        autopilot_count: 10,
        manual_count: 2,
        alerts_opened: 3,
        alerts_resolved: 2,
        incidents_count: 0,
        active_policy: "optimizer",
      },
    });

    renderWithProviders(<Assistant />);

    const summaryBtn = screen.getByRole("button", { name: /generate shift summary/i });
    fireEvent.click(summaryBtn);

    await waitFor(() => {
      expect(client.fetchSummary).toHaveBeenCalledWith(96);
      expect(screen.getByText(/Executive Shift Summary/i)).toBeInTheDocument();
      expect(screen.getAllByText(/99.40%/i).length).toBeGreaterThanOrEqual(1);
    });
  });

  it("handles assistant network/server error gracefully without attributing to rules engine", async () => {
    vi.mocked(client.askAssistant).mockRejectedValue(new Error("Internal Server Error"));

    renderWithProviders(<Assistant />);

    const input = screen.getByPlaceholderText(/Ask about station inventory/i);
    const askBtn = screen.getByRole("button", { name: /^ask$/i });

    fireEvent.change(input, { target: { value: "Why is Cox's Bazar at risk?" } });
    fireEvent.click(askBtn);

    await waitFor(() => {
      expect(screen.getByText(/Error: Internal Server Error/i)).toBeInTheDocument();
    });

    // Verify error banner is visible and retry advice is shown
    expect(screen.getByText(/Please check platform network connectivity and retry/i)).toBeInTheDocument();
  });
});
