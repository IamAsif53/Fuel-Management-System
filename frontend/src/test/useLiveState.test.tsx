import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useLiveState } from "@/hooks/useLiveState";
import * as apiClient from "@/api/client";
import { mockWorldState } from "./fixtures/mockState";

// Mock EventSource
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners: Record<string, ((event: unknown) => void)[]> = {};

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
    // closed
  }
}

describe("useLiveState Hook", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
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
    vi.restoreAllMocks();
  });

  function wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  }

  it("subscribes to EventSource and transitions to live on open", async () => {
    const { result } = renderHook(() => useLiveState(), { wrapper });

    // Wait for EventSource instance to be created
    await waitFor(() => {
      expect(MockEventSource.instances.length).toBeGreaterThan(0);
    });

    const es = MockEventSource.instances[0];

    // Simulate SSE connection open
    act(() => {
      es.simulateOpen();
    });

    await waitFor(() => {
      expect(result.current.connection).toBe("live");
    });
  });

  it("triggers state fetch on state.updated SSE event", async () => {
    const apiSpy = vi.spyOn(apiClient, "apiWithMeta");
    const { result } = renderHook(() => useLiveState(), { wrapper });

    await waitFor(() => {
      expect(MockEventSource.instances.length).toBeGreaterThan(0);
    });

    const es = MockEventSource.instances[0];
    act(() => {
      es.simulateOpen();
    });

    const initialCalls = apiSpy.mock.calls.length;

    // Simulate state.updated event
    act(() => {
      es.simulateEvent("state.updated");
    });

    await waitFor(() => {
      expect(apiSpy.mock.calls.length).toBeGreaterThan(initialCalls);
    });

    expect(result.current.state?.tick).toBe(mockWorldState.tick);
  });

  it("falls back to polling on SSE error", async () => {
    const { result } = renderHook(() => useLiveState(), { wrapper });

    await waitFor(() => {
      expect(MockEventSource.instances.length).toBeGreaterThan(0);
    });

    const es = MockEventSource.instances[0];
    act(() => {
      es.simulateOpen();
    });

    // Simulate failure
    act(() => {
      es.simulateError();
    });

    await waitFor(() => {
      expect(result.current.connection).toBe("polling");
    });
  });
});
