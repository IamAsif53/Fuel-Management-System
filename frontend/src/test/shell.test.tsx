import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConnectionDot } from "@/components/layout/ConnectionDot";
import { ClockBadge } from "@/components/layout/ClockBadge";
import { DegradedBanner } from "@/components/layout/DegradedBanner";
import { EngineModeBadge } from "@/components/layout/EngineModeBadge";
import type { WorldState } from "@/api/types";

describe("Application Shell Components", () => {
  it("renders ConnectionDot with LIVE, POLLING, and OFFLINE states", () => {
    const { rerender } = render(<ConnectionDot connection="live" />);
    expect(screen.getByTestId("connection")).toHaveTextContent("LIVE");

    rerender(<ConnectionDot connection="polling" />);
    expect(screen.getByTestId("connection")).toHaveTextContent("POLLING");

    rerender(<ConnectionDot connection="offline" />);
    expect(screen.getByTestId("connection")).toHaveTextContent("OFFLINE");
  });

  it("renders ClockBadge with clock_label and running status", () => {
    const mockState = {
      tick: 42,
      tick_minutes: 15,
      clock_label: "Day 1 10:30 · t=42",
      sim_status: "RUNNING",
    } as unknown as WorldState;

    render(<ClockBadge state={mockState} />);
    const clock = screen.getByTestId("sim-clock");
    expect(clock).toHaveTextContent("Day 1 10:30 · t=42");
    expect(clock).toHaveTextContent("RUNNING");
  });

  it("renders EngineModeBadge with expected styling and text", () => {
    const { rerender } = render(<EngineModeBadge mode="NORMAL" />);
    expect(screen.getByTestId("engine-mode")).toHaveTextContent("NORMAL");

    rerender(<EngineModeBadge mode="SAFE_HOLD" />);
    expect(screen.getByTestId("engine-mode")).toHaveTextContent("SAFE HOLD");
  });

  it("renders DegradedBanner only when state is degraded", () => {
    const healthyState = {
      degraded: false,
      engine_mode: "NORMAL",
      degraded_reasons: [],
    } as unknown as WorldState;

    const { rerender } = render(<DegradedBanner state={healthyState} />);
    expect(screen.queryByTestId("degraded-banner")).toBeNull();

    const degradedState = {
      degraded: true,
      engine_mode: "DEGRADED",
      degraded_reasons: ["redis_down", "sim_stale"],
      tick: 100,
      age_seconds: 12.5,
    } as unknown as WorldState;

    rerender(<DegradedBanner state={degradedState} />);
    const banner = screen.getByTestId("degraded-banner");
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent("SYSTEM DEGRADED");
    expect(banner).toHaveTextContent("redis_down");
    expect(banner).toHaveTextContent("sim_stale");
    expect(banner).toHaveTextContent("t=100");
    expect(banner).toHaveTextContent("12.5s old");
  });
});
