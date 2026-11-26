import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Network from "@/pages/Network";
import * as liveStateHook from "@/hooks/useLiveState";
import * as apiClient from "@/api/client";
import { mockWorldState } from "./fixtures/mockState";

function renderWithClient(ui: React.ReactElement, initialEntries: string[] = ["/network"]) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>
    </MemoryRouter>
  );
}

describe("Network Page", () => {
  beforeEach(() => {
    vi.spyOn(liveStateHook, "useLiveState").mockReturnValue({
      state: mockWorldState,
      connection: "live",
      error: null,
      refetch: vi.fn(),
      isLoading: false,
    });

    vi.spyOn(apiClient, "api").mockResolvedValue({
      station: mockWorldState.stations[0],
      history: [
        { tick: 1, inventory: { DIESEL: 10000, PETROL: 8000, OCTANE: 5000 } },
        { tick: 2, inventory: { DIESEL: 9500, PETROL: 7800, OCTANE: 4800 } },
      ],
      demand: [],
      forecast: [],
    });
  });

  it("renders default Stations tab with all station cards", () => {
    renderWithClient(<Network />);

    expect(screen.getByText("Network & Inventory Infrastructure")).toBeInTheDocument();
    expect(screen.getByTestId("stations-tab-content")).toBeInTheDocument();

    // Check all 4 stations from mock
    expect(screen.getByTestId("station-card-station-mirpur")).toBeInTheDocument();
    expect(screen.getByTestId("station-card-station-tongi")).toBeInTheDocument();
    expect(screen.getByTestId("station-card-station-karnaphuli")).toBeInTheDocument();
    expect(screen.getByTestId("station-card-station-coxsbazar")).toBeInTheDocument();
  });

  it("displays fuel inventory and in-flight allocations on station card", () => {
    renderWithClient(<Network />);

    const mirpurCard = screen.getByTestId("station-card-station-mirpur");
    expect(mirpurCard).toHaveTextContent("Mirpur Station");
    expect(mirpurCard).toHaveTextContent("region-dhaka");
    expect(mirpurCard).toHaveTextContent("DIESEL");
    expect(mirpurCard).toHaveTextContent("PETROL");
    expect(mirpurCard).toHaveTextContent("OCTANE");
    // Station Mirpur has in-transit allocation (+2,000 L)
    expect(mirpurCard).toHaveTextContent("+2,000 L");
  });

  it("switches to Depots tab and displays depot cards with supply timeline", () => {
    renderWithClient(<Network />);

    const depotsTabBtn = screen.getByTestId("tab-depots");
    fireEvent.click(depotsTabBtn);

    expect(screen.getByTestId("depots-tab-content")).toBeInTheDocument();
    expect(screen.getByTestId("depot-card-depot-gazipur")).toBeInTheDocument();
    expect(screen.getByTestId("depot-card-depot-patiya")).toBeInTheDocument();

    const gazipurCard = screen.getByTestId("depot-card-depot-gazipur");
    expect(gazipurCard).toHaveTextContent("Gazipur Depot");
    expect(gazipurCard).toHaveTextContent("Supply Arrivals Timeline");
    expect(gazipurCard).toHaveTextContent("SCHEDULED");
  });

  it("switches to Routes tab and displays all routes with status and transit", () => {
    renderWithClient(<Network />);

    const routesTabBtn = screen.getByTestId("tab-routes");
    fireEvent.click(routesTabBtn);

    expect(screen.getByTestId("routes-tab-content")).toBeInTheDocument();
    expect(screen.getByText("Supply Distribution Routes")).toBeInTheDocument();

    // Check routes from mock
    expect(screen.getByTestId("route-row-route-gazipur-mirpur")).toBeInTheDocument();
    expect(screen.getByTestId("route-row-route-gazipur-tongi")).toBeInTheDocument();
    expect(screen.getByTestId("route-row-route-patiya-coxsbazar")).toBeInTheDocument();

    // Check disrupted route styling
    const disruptedRow = screen.getByTestId("route-row-route-patiya-karnaphuli");
    expect(disruptedRow).toHaveTextContent("DISRUPTED");
  });
});
