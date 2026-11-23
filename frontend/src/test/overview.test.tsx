import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { NetworkGraph } from "@/components/overview/NetworkGraph";
import { KpiGrid } from "@/components/overview/KpiGrid";
import { InventorySummary } from "@/components/overview/InventorySummary";
import { mockWorldState } from "./fixtures/mockState";

describe("Overview Dashboard Components", () => {
  describe("NetworkGraph", () => {
    it("renders exactly 2 depots, 4 stations, and 6 routes from fixture", () => {
      render(<NetworkGraph state={mockWorldState} />);

      expect(screen.getByTestId("network-graph")).toBeInTheDocument();

      // 2 depots
      expect(screen.getByTestId("node-depot-gazipur")).toBeInTheDocument();
      expect(screen.getByTestId("node-depot-patiya")).toBeInTheDocument();

      // 4 stations
      expect(screen.getByTestId("node-station-mirpur")).toBeInTheDocument();
      expect(screen.getByTestId("node-station-tongi")).toBeInTheDocument();
      expect(screen.getByTestId("node-station-karnaphuli")).toBeInTheDocument();
      expect(screen.getByTestId("node-station-coxsbazar")).toBeInTheDocument();

      // 6 routes
      expect(screen.getByTestId("edge-route-gazipur-mirpur")).toBeInTheDocument();
      expect(screen.getByTestId("edge-route-gazipur-tongi")).toBeInTheDocument();
      expect(screen.getByTestId("edge-route-patiya-karnaphuli")).toBeInTheDocument();
      expect(screen.getByTestId("edge-route-patiya-coxsbazar")).toBeInTheDocument();
      expect(screen.getByTestId("edge-route-gazipur-karnaphuli")).toBeInTheDocument();
      expect(screen.getByTestId("edge-route-patiya-mirpur")).toBeInTheDocument();
    });

    it("applies correct edge styling for disrupted and scheduled routes", () => {
      render(<NetworkGraph state={mockWorldState} />);

      // route-patiya-karnaphuli is DISRUPTED -> red solid
      const disruptedEdge = screen.getByTestId("edge-route-patiya-karnaphuli");
      const disruptedPath = disruptedEdge.querySelector("path");
      expect(disruptedPath).toHaveAttribute("stroke", "#EF4444");
      expect(disruptedPath).toHaveAttribute("stroke-width", "3");

      // route-gazipur-karnaphuli is SCHEDULED_DISRUPTION -> dashed amber
      const scheduledEdge = screen.getByTestId("edge-route-gazipur-karnaphuli");
      const scheduledPath = scheduledEdge.querySelector("path");
      expect(scheduledPath).toHaveAttribute("stroke", "#EAB308");
      expect(scheduledPath).toHaveAttribute("stroke-dasharray", "6 4");

      // route-gazipur-mirpur is AVAILABLE -> neutral
      const availableEdge = screen.getByTestId("edge-route-gazipur-mirpur");
      const availablePath = availableEdge.querySelector("path");
      expect(availablePath).toHaveAttribute("stroke", "#24314F");
    });

    it("renders in-transit allocation dots along the route", () => {
      render(<NetworkGraph state={mockWorldState} />);

      // alloc 101 on route-patiya-coxsbazar
      const allocDot = screen.getByTestId("allocation-101");
      expect(allocDot).toBeInTheDocument();
      const circle = allocDot.querySelector("circle");
      expect(circle).toBeInTheDocument();
      // OCTANE fuel color
      expect(circle).toHaveAttribute("fill", "#F472B6");
    });

    it("invokes onSelectEntity when a depot or station node is clicked", () => {
      const handleSelect = vi.fn();
      render(<NetworkGraph state={mockWorldState} onSelectEntity={handleSelect} />);

      const depotNode = screen.getByTestId("node-depot-gazipur");
      fireEvent.click(depotNode);
      expect(handleSelect).toHaveBeenCalledWith(
        expect.objectContaining({ id: "depot-gazipur" })
      );

      const stationNode = screen.getByTestId("node-station-coxsbazar");
      fireEvent.click(stationNode);
      expect(handleSelect).toHaveBeenCalledWith(
        expect.objectContaining({ id: "station-coxsbazar" })
      );
    });

    it("handles missing/null state without crashing", () => {
      render(<NetworkGraph state={null} />);
      expect(screen.getByTestId("network-graph")).toBeInTheDocument();
    });
  });

  describe("KpiGrid", () => {
    it("renders actual metrics from WorldState", () => {
      render(<KpiGrid state={mockWorldState} />);

      expect(screen.getByTestId("kpi-service-level")).toHaveTextContent("99.5%");
      expect(screen.getByTestId("kpi-unmet-liters")).toHaveTextContent("0 L");
      // 1 station at risk (Cox's Bazar has CRITICAL/HIGH risk)
      expect(screen.getByTestId("kpi-stations-at-risk")).toHaveTextContent("1");
      // 4 open alerts (1 INFO + 2 WARNING + 1 CRITICAL)
      expect(screen.getByTestId("kpi-open-alerts")).toHaveTextContent("4");
      expect(screen.getByTestId("kpi-pending-recs")).toHaveTextContent("1");
      expect(screen.getByTestId("kpi-allocation-failures")).toHaveTextContent("0");
      expect(screen.getByTestId("kpi-overflow-waste")).toHaveTextContent("0 L");
      expect(screen.getByTestId("kpi-engine-mode")).toHaveTextContent("NORMAL");
    });

    it("renders placeholders when state is null", () => {
      render(<KpiGrid state={null} />);
      expect(screen.getByTestId("kpi-service-level")).toHaveTextContent("—");
      expect(screen.getByTestId("kpi-unmet-liters")).toHaveTextContent("—");
      expect(screen.getByTestId("kpi-engine-mode")).toHaveTextContent("—");
    });
  });

  describe("InventorySummary", () => {
    it("renders compact inventory table with depots and stations", () => {
      render(<InventorySummary state={mockWorldState} />);

      expect(screen.getByTestId("inventory-summary")).toBeInTheDocument();
      expect(screen.getByText("Gazipur Depot")).toBeInTheDocument();
      expect(screen.getByText("Mirpur Station")).toBeInTheDocument();
      expect(screen.getByText("Cox's Bazar Station")).toBeInTheDocument();
    });
  });
});
