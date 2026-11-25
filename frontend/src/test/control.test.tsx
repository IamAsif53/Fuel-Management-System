import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ControlPage from "@/pages/Control";
import * as clientModule from "@/api/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";

vi.mock("@/api/client", () => ({
  api: vi.fn(),
}));

const mockStatus = {
  demo_controls_enabled: true,
  sim_status: "PAUSED",
  tick: 96,
  sim_time: "2026-01-02T00:00:00",
  scenario_id: "baseline",
  active_events_count: 1,
  active_events: [
    {
      id: 1,
      type: "demand_spike",
      start_tick: 96,
      end_tick: 120,
      status: "ACTIVE",
      parameters: { multiplier: 1.8 },
    },
  ],
  active_faults: [],
  presets: {
    demand_spike_dhaka: {
      title: "Demand Spike — Dhaka",
      description: "Surges retail customer demand in Dhaka region by 1.8x for 24 ticks",
      events: [],
    },
    route_cut_mirpur: {
      title: "Route Cut — Mirpur",
      description: "Blocks route-gazipur-mirpur for 24 ticks",
      events: [],
    },
    combined_crisis: {
      title: "Combined Multi-Vector Crisis",
      description: "Spike + route cut + delay",
      events: [],
    },
  },
  faults: {
    unavailable: "Simulator API Unavailable (HTTP 503)",
    latency: "1.5s Network Delay Injection",
    error_rate: "30% Injected Transient Errors",
  },
};

const mockLogs = {
  logs: [
    {
      id: 10,
      at: "2026-11-25T19:00:00Z",
      actor: "operator",
      action: "control.sim.step",
      status: "SUCCESS",
      result: "Advanced 1 tick -> reached tick 96",
      details: {},
    },
  ],
};

function renderControlPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ControlPage />
      </BrowserRouter>
    </QueryClientProvider>
  );
}

describe("Simulator Control & Chaos Drill Page", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    vi.mocked(clientModule.api).mockImplementation(async (path: string) => {
      if (path === "/control/status") {
        return mockStatus as any;
      }
      if (path === "/control/logs") {
        return mockLogs as any;
      }
      return { status: "ok" } as any;
    });
  });

  it("renders page header, security alert banner, stepping controls, presets, and action log", async () => {
    renderControlPage();

    expect(await screen.findByText(/Simulator Controls & Chaos Injection/i)).toBeInTheDocument();
    expect(screen.getByText(/Organizer Admin Controls — Testing & Demonstration Use Only/i)).toBeInTheDocument();
    expect(screen.getByText(/PAUSED/i)).toBeInTheDocument();
    expect(screen.getByText("96")).toBeInTheDocument();

    // Verify sections
    expect(screen.getByText(/Discrete-Event Simulation Stepping/i)).toBeInTheDocument();
    expect(screen.getByText(/Crisis Scenario Presets/i)).toBeInTheDocument();
    expect(screen.getByText(/Resilience & Chaos Drills/i)).toBeInTheDocument();
    expect(screen.getByText(/Control Operation Audit Log/i)).toBeInTheDocument();

    // Verify buttons
    expect(screen.getByTestId("ctl-run")).toBeInTheDocument();
    expect(screen.getByTestId("ctl-pause")).toBeInTheDocument();
    expect(screen.getByTestId("ctl-step")).toBeInTheDocument();
    expect(screen.getByTestId("ctl-step-10")).toBeInTheDocument();
    expect(screen.getByTestId("ctl-reset")).toBeInTheDocument();
  });

  it("handles stepping, pause, and run operations", async () => {
    renderControlPage();

    const stepBtn = await screen.findByTestId("ctl-step");
    fireEvent.click(stepBtn);

    await waitFor(() => {
      expect(clientModule.api).toHaveBeenCalledWith(
        "/control/sim/step",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ count: 1 }) })
      );
    });

    const runBtn = screen.getByTestId("ctl-run");
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(clientModule.api).toHaveBeenCalledWith("/control/sim/run", expect.objectContaining({ method: "POST" }));
    });
  });

  it("handles reset with confirmation modal", async () => {
    renderControlPage();

    const resetBtn = await screen.findByTestId("ctl-reset");
    fireEvent.click(resetBtn);

    expect(await screen.findByText(/Confirm Scenario Reset/i)).toBeInTheDocument();

    const confirmBtn = screen.getByTestId("ctl-reset-confirm");
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(clientModule.api).toHaveBeenCalledWith("/control/sim/reset", expect.objectContaining({ method: "POST" }));
    });
  });

  it("triggers crisis presets and chaos fault injections", async () => {
    renderControlPage();

    // Crisis preset
    const presetBtn = await screen.findByTestId("ctl-preset-demand_spike_dhaka");
    fireEvent.click(presetBtn);

    await waitFor(() => {
      expect(clientModule.api).toHaveBeenCalledWith(
        "/control/scenario/demand_spike_dhaka",
        expect.objectContaining({ method: "POST" })
      );
    });

    // Chaos fault
    const faultBtn = screen.getByTestId("ctl-chaos-latency");
    fireEvent.click(faultBtn);

    await waitFor(() => {
      expect(clientModule.api).toHaveBeenCalledWith(
        "/control/chaos/latency",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ duration_seconds: 60 }),
        })
      );
    });

    // Clear faults
    const clearBtn = screen.getByTestId("ctl-chaos-clear");
    fireEvent.click(clearBtn);

    await waitFor(() => {
      expect(clientModule.api).toHaveBeenCalledWith("/control/chaos/clear", expect.objectContaining({ method: "POST" }));
    });
  });

  it("renders disabled state when DEMO_CONTROLS is false", async () => {
    vi.mocked(clientModule.api).mockImplementation(async (path: string) => {
      if (path === "/control/status") {
        return { ...mockStatus, demo_controls_enabled: false } as any;
      }
      return { logs: [] } as any;
    });

    renderControlPage();

    expect(await screen.findByText(/Simulator Controls Disabled/i)).toBeInTheDocument();
    expect(screen.queryByTestId("ctl-step")).not.toBeInTheDocument();
  });
});
