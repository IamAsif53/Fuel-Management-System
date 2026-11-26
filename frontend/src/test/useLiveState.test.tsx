import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useLiveState, _resetLiveStateManagerForTests } from "@/hooks/useLiveState";
import * as apiClient from "@/api/client";
import { mockWorldState } from "./fixtures/mockState";

// Mock EventSource
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners: Record<string, ((event: unknown) => void)[]> = {};
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(event: string, cb: (event: unknown) => void) {
    if (!this.listeners[event]) this.listeners[event] = [];
    this.listeners[event].push(cb);
  }

  removeEventListener(event: string, cb: (event: unknown) => void) {
    if (this.listeners[event]) {
      this.listeners[event] = this.listeners[event].filter((l) => l !== cb);
    }
  }

  simulateOpen() {
    if (this.onopen) this.onopen();
  }

  simulateEvent(event: string, data?: unknown) {
    if (this.listeners[event]) {
      for (const cb of this.listeners[event]) {
        cb({ data });
      }
    }
  }

  simulateError() {
    if (this.onerror) this.onerror();
  }

  close() {
    this.closed = true;
  }
}

describe("useLiveState Hook & Connection Semantics", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    _resetLiveStateManagerForTests();
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
    MockEventSource.instances = [];
    (global as unknown as { EventSource: unknown }).EventSource = MockEventSource;

    vi.spyOn(apiClient, "apiWithMeta").mockResolvedValue({
      data: mockWorldState,
      status: 200,
      notModified: false,
      etag: '"test-etag-1"',
      tick: mockWorldState.tick,
      degraded: false,
    });
  });

  afterEach(() => {
    _resetLiveStateManagerForTests();
    vi.restoreAllMocks();
  });

  function wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  }

  it("1. initial successful state fetch primes query cache and establishes SSE", async () => {
    const { result } = renderHook(() => useLiveState(), { wrapper });

    await waitFor(() => {
      expect(MockEventSource.instances.length).toBe(1);
    });

    const es = MockEventSource.instances[0];
    act(() => {
      es.simulateOpen();
    });

    await waitFor(() => {
      expect(result.current.connection).toBe("live");
      expect(result.current.state?.tick).toBe(mockWorldState.tick);
      expect(result.current.error).toBeNull();
    });
  });

  it("2. state.updated SSE event triggers an immediate state refetch", async () => {
    const apiSpy = vi.spyOn(apiClient, "apiWithMeta");
    const { result } = renderHook(() => useLiveState(), { wrapper });

    await waitFor(() => {
      expect(MockEventSource.instances.length).toBe(1);
    });

    const es = MockEventSource.instances[0];
    act(() => {
      es.simulateOpen();
    });

    const initialCalls = apiSpy.mock.calls.length;

    act(() => {
      es.simulateEvent("state.updated");
    });

    await waitFor(() => {
      expect(apiSpy.mock.calls.length).toBeGreaterThan(initialCalls);
    });
    expect(result.current.state?.tick).toBe(mockWorldState.tick);
  });

  it("3. SSE failure with HTTP polling available transitions to polling", async () => {
    const { result } = renderHook(() => useLiveState(), { wrapper });

    await waitFor(() => {
      expect(MockEventSource.instances.length).toBe(1);
    });

    const es = MockEventSource.instances[0];
    act(() => {
      es.simulateOpen();
    });

    await waitFor(() => {
      expect(result.current.connection).toBe("live");
    });

    // Simulate SSE disconnection
    act(() => {
      es.simulateError();
    });

    await waitFor(() => {
      expect(result.current.connection).toBe("polling");
    });
  });

  it("4. both SSE and HTTP failing transitions connection to offline", async () => {
    // Configure HTTP to fail
    vi.spyOn(apiClient, "apiWithMeta").mockRejectedValue(new Error("502 Bad Gateway"));

    const { result } = renderHook(() => useLiveState(), { wrapper });

    await waitFor(() => {
      expect(MockEventSource.instances.length).toBe(1);
    });

    const es = MockEventSource.instances[0];

    // Trigger SSE error while HTTP is failing
    act(() => {
      es.simulateError();
    });

    await waitFor(() => {
      expect(result.current.connection).toBe("offline");
      expect(result.current.error).toBeTruthy();
    });
  });

  it("5. HTTP recovery restores polling while SSE remains disconnected", async () => {
    const apiSpy = vi.spyOn(apiClient, "apiWithMeta");
    apiSpy.mockRejectedValue(new Error("Connection refused"));

    const { result } = renderHook(() => useLiveState(), { wrapper });

    await waitFor(() => {
      expect(MockEventSource.instances.length).toBe(1);
    });

    const es = MockEventSource.instances[0];
    act(() => {
      es.simulateError();
    });

    await waitFor(() => {
      expect(result.current.connection).toBe("offline");
    });

    // Now HTTP recovers (e.g. API is back up)
    apiSpy.mockResolvedValue({
      data: mockWorldState,
      status: 200,
      notModified: false,
      etag: '"test-etag-recovered"',
      tick: mockWorldState.tick + 1,
      degraded: false,
    });

    // Trigger a poll
    await act(async () => {
      await result.current.refetch();
    });

    await waitFor(() => {
      expect(result.current.connection).toBe("polling");
    });
  });

  it("6. SSE recovery restores live connection state", async () => {
    const { result } = renderHook(() => useLiveState(), { wrapper });

    await waitFor(() => {
      expect(MockEventSource.instances.length).toBe(1);
    });

    const es1 = MockEventSource.instances[0];
    act(() => {
      es1.simulateError();
    });

    await waitFor(() => {
      expect(result.current.connection).toBe("polling");
    });

    // Wait for reconnect timer to spawn the new EventSource instance
    await waitFor(
      () => {
        expect(MockEventSource.instances.length).toBeGreaterThan(1);
      },
      { timeout: 2500 }
    );

    const es2 = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      es2.simulateOpen();
    });

    await waitFor(() => {
      expect(result.current.connection).toBe("live");
    });
  });

  it("7. does not create duplicate EventSource instances when multiple consumers mount", async () => {
    const hook1 = renderHook(() => useLiveState(), { wrapper });
    const hook2 = renderHook(() => useLiveState(), { wrapper });

    await waitFor(() => {
      expect(MockEventSource.instances.length).toBe(1);
    });

    expect(hook1.result.current.connection).toBe(hook2.result.current.connection);

    hook1.unmount();
    // es remains open because hook2 is still active
    expect(MockEventSource.instances[0].closed).toBe(false);

    hook2.unmount();
    // After all unmount, es is closed
    expect(MockEventSource.instances[0].closed).toBe(true);
  });

  it("8. monotonic tick check protects against stale out-of-order state overwrites", async () => {
    const apiSpy = vi.spyOn(apiClient, "apiWithMeta");

    const { result } = renderHook(() => useLiveState(), { wrapper });

    // Initial state has tick = mockWorldState.tick (e.g. 42)
    await waitFor(() => {
      expect(result.current.state?.tick).toBe(mockWorldState.tick);
    });

    // Advance state to tick 50
    const advancedState = { ...mockWorldState, tick: 50 };
    apiSpy.mockResolvedValueOnce({
      data: advancedState,
      status: 200,
      notModified: false,
      etag: '"test-etag-50"',
      tick: 50,
      degraded: false,
    });

    await act(async () => {
      await result.current.refetch();
    });

    await waitFor(() => {
      expect(result.current.state?.tick).toBe(50);
    });

    // Now an older response (tick 30) arrives out-of-order
    const staleState = { ...mockWorldState, tick: 30 };
    apiSpy.mockResolvedValueOnce({
      data: staleState,
      status: 200,
      notModified: false,
      etag: '"test-etag-30"',
      tick: 30,
      degraded: false,
    });

    await act(async () => {
      await result.current.refetch();
    });

    // Stale response must NOT overwrite tick 50
    expect(result.current.state?.tick).toBe(50);
  });

  it("9. accepts tick reset when sim_run_id increments (reset.detected SSE) and rejects older runs", async () => {
    const apiSpy = vi.spyOn(apiClient, "apiWithMeta");
    const { result } = renderHook(() => useLiveState(), { wrapper });

    await waitFor(() => {
      expect(MockEventSource.instances.length).toBe(1);
    });
    const es = MockEventSource.instances[0];
    act(() => {
      es.simulateOpen();
    });

    // Advance current run to sim_run_id=10, tick=50
    apiSpy.mockResolvedValueOnce({
      data: { ...mockWorldState, sim_run_id: 10, tick: 50 },
      status: 200,
      notModified: false,
      etag: '"10-50"',
      tick: 50,
      degraded: false,
    });
    await act(async () => {
      await result.current.refetch();
    });
    expect(result.current.state?.sim_run_id).toBe(10);
    expect(result.current.state?.tick).toBe(50);

    // Simulator reset increments sim_run_id to 11 with tick=0 -> SSE reset.detected triggers live update
    apiSpy.mockResolvedValueOnce({
      data: { ...mockWorldState, sim_run_id: 11, tick: 0 },
      status: 200,
      notModified: false,
      etag: '"11-0"',
      tick: 0,
      degraded: false,
    });
    act(() => {
      es.simulateEvent("reset.detected", JSON.stringify({ sim_run_id: 11, tick: 0 }));
    });

    await waitFor(() => {
      expect(result.current.state?.sim_run_id).toBe(11);
      expect(result.current.state?.tick).toBe(0);
    });

    // Late message from old sim_run_id=10 must NOT overwrite sim_run_id=11
    apiSpy.mockResolvedValueOnce({
      data: { ...mockWorldState, sim_run_id: 10, tick: 55 },
      status: 200,
      notModified: false,
      etag: '"10-55"',
      tick: 55,
      degraded: false,
    });
    await act(async () => {
      await result.current.refetch();
    });
    expect(result.current.state?.sim_run_id).toBe(11);
    expect(result.current.state?.tick).toBe(0);
  });
});
