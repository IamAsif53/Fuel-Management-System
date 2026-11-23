import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import SystemHealth from "@/pages/SystemHealth";
import * as apiClient from "@/api/client";
import type { SystemStatus } from "@/api/types";

const mockSystemStatus: SystemStatus = {
  overall: "Healthy",
  engine_mode: "advisory",
  components: {
    backend_api: {
      status: "Healthy",
      p95_ms: 45.2,
      error_rate: 0.0,
    },
    database: {
      status: "Healthy",
    },
    redis: {
      status: "Healthy",
    },
    fuel_simulator: {
      status: "Healthy",
      latency_ms: 12,
    },
    prediction_service: {
      status: "Healthy",
      latency_ms: 18,
    },
    decision_engine: {
      status: "Healthy",
      latency_ms: 22,
      last_sync_age_s: 1.2,
      engine_mode: "advisory",
      tick: 42,
    },
    notifier: {
      status: "Healthy",
    },
  },
  versions: {
    api: "1.0.0",
    engine: "1.0.0",
    intelligence: "1.0.0",
    frontend: "1.0.0",
    git_sha: "abcdef1",
  },
  p95_latency_ms: 45.2,
  error_rate: 0.0,
  slo: [
    {
      name: "API Availability",
      target: 0.999,
      current: 1.0,
      budget_remaining: 1.0,
    },
    {
      name: "p95 Latency",
      target: 500.0,
      current: 45.2,
      budget_remaining: 1.0,
    },
  ],
};

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("SystemHealth Page", () => {
  beforeEach(() => {
    vi.spyOn(apiClient, "api").mockResolvedValue(mockSystemStatus);
  });

  it("renders page header and overall healthy status pill", async () => {
    renderWithClient(<SystemHealth />);

    expect(await screen.findByText("System Observability & Health")).toBeInTheDocument();
    expect(screen.getByTestId("overall-status-pill")).toHaveTextContent("Healthy");
    expect(screen.getByTestId("health-engine-mode")).toHaveTextContent("Mode: advisory");
  });

  it("renders summary metrics cards (p95, error rate, engine tick)", async () => {
    renderWithClient(<SystemHealth />);

    expect(await screen.findByTestId("p95-latency-value")).toHaveTextContent("45.2");
    expect(screen.getByTestId("error-rate-value")).toHaveTextContent("0.00%");
    expect(screen.getByText("Tick 42")).toBeInTheDocument();
  });

  it("renders all subsystem component cards", async () => {
    renderWithClient(<SystemHealth />);

    expect(await screen.findByTestId("component-backend_api")).toBeInTheDocument();
    expect(screen.getByTestId("component-database")).toBeInTheDocument();
    expect(screen.getByTestId("component-redis")).toBeInTheDocument();
    expect(screen.getByTestId("component-fuel_simulator")).toBeInTheDocument();
    expect(screen.getByTestId("component-prediction_service")).toBeInTheDocument();
    expect(screen.getByTestId("component-decision_engine")).toBeInTheDocument();
    expect(screen.getByTestId("component-notifier")).toBeInTheDocument();
  });

  it("renders Service Level Objectives (SLOs) section", async () => {
    renderWithClient(<SystemHealth />);

    expect(await screen.findByText("Service Level Objectives (SLO)")).toBeInTheDocument();
    expect(screen.getByText("API Availability")).toBeInTheDocument();
    expect(screen.getByText("p95 Latency")).toBeInTheDocument();
  });

  it("displays error banner when API is unreachable", async () => {
    vi.spyOn(apiClient, "api").mockRejectedValue(new Error("Network Error"));

    renderWithClient(<SystemHealth />);

    expect(await screen.findByText("System Telemetry Unavailable")).toBeInTheDocument();
    expect(screen.getByText("Retry Connection")).toBeInTheDocument();
  });
});
