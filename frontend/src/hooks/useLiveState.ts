/**
 * Hook for live state synchronization via Server-Sent Events (SSE) and fallback polling.
 * Follows 22_UI_SPEC.md §4 and TASK 3.2 requirements.
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiWithMeta } from "@/api/client";
import type { WorldState } from "@/api/types";

export type ConnectionState = "live" | "polling" | "offline";

export interface UseLiveStateResult {
  state: WorldState | null;
  connection: ConnectionState;
  error: Error | null;
  refetch: () => Promise<void>;
  isLoading: boolean;
}

const STATE_QUERY_KEY = ["state"];
const FALLBACK_POLL_INTERVAL_MS = 2000;
const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30000;

// Module-level state tracking for the singleton SSE manager
class LiveStateManager {
  private static instance: LiveStateManager | null = null;

  private es: EventSource | null = null;
  private isSseConnected = false;
  private isHttpOk = true;
  private subscribers = new Set<(conn: ConnectionState, err: Error | null) => void>();
  private pollTimer: NodeJS.Timeout | null = null;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private backoffMs = INITIAL_BACKOFF_MS;
  private currentEtag: string | null = null;
  private latestTick = -1;
  private currentConnection: ConnectionState = "polling";
  private lastError: Error | null = null;
  private isFetching = false;
  private queryClientSetter:
    | ((updater: (old: WorldState | null | undefined) => WorldState | null) => void)
    | null = null;

  public static getInstance(): LiveStateManager {
    if (!LiveStateManager.instance) {
      LiveStateManager.instance = new LiveStateManager();
    }
    return LiveStateManager.instance;
  }

  public registerClient(
    setQueryData: (updater: (old: WorldState | null | undefined) => WorldState | null) => void
  ) {
    this.queryClientSetter = setQueryData;
  }

  public subscribe(callback: (conn: ConnectionState, err: Error | null) => void): () => void {
    this.subscribers.add(callback);
    callback(this.currentConnection, this.lastError);

    if (this.subscribers.size === 1) {
      this.start();
    }

    return () => {
      this.subscribers.delete(callback);
      if (this.subscribers.size === 0) {
        this.stop();
      }
    };
  }

  public getConnection(): ConnectionState {
    return this.currentConnection;
  }

  public getError(): Error | null {
    return this.lastError;
  }

  private setConnection(conn: ConnectionState, err: Error | null = null) {
    this.currentConnection = conn;
    this.lastError = err;
    for (const sub of this.subscribers) {
      sub(conn, err);
    }
  }

  private updateConnectionState(error: Error | null = null) {
    let nextState: ConnectionState;
    if (this.isSseConnected) {
      nextState = "live";
      error = null;
    } else if (this.isHttpOk) {
      nextState = "polling";
    } else {
      nextState = "offline";
      if (!error) {
        error = this.lastError || new Error("API unreachable");
      }
    }
    this.setConnection(nextState, error);
  }

  private start() {
    this.connectSSE();
    // Do an immediate fetch to prime the state
    this.fetchLatestState();
  }

  private stop() {
    this.disconnectSSE();
    this.stopPolling();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private connectSSE() {
    if (this.es) {
      return;
    }

    try {
      // Use native browser EventSource (handles Last-Event-ID automatically)
      const es = new EventSource("/api/stream");
      this.es = es;

      es.onopen = () => {
        this.isSseConnected = true;
        this.backoffMs = INITIAL_BACKOFF_MS;
        this.stopPolling();
        this.updateConnectionState(null);
      };

      es.addEventListener("state.updated", () => {
        this.fetchLatestState();
      });

      const sseEvents = [
        "decision.created",
        "decision.approved",
        "decision.rejected",
        "allocation.submitted",
        "allocation.delivered",
        "allocation.failed",
        "engine.mode_changed",
        "engine.policy_changed",
      ];

      for (const evName of sseEvents) {
        es.addEventListener(evName, (e: MessageEvent) => {
          this.fetchLatestState();
          if (typeof window !== "undefined") {
            try {
              const data = JSON.parse(e.data);
              window.dispatchEvent(
                new CustomEvent("fsip-event", {
                  detail: { type: evName, data },
                })
              );
            } catch {
              /* ignore parse error */
            }
          }
        });
      }

      es.onerror = () => {
        this.handleSseFailure();
      };
    } catch (err: unknown) {
      this.handleSseFailure(err instanceof Error ? err : new Error("Failed to create EventSource"));
    }
  }

  private disconnectSSE() {
    this.isSseConnected = false;
    if (this.es) {
      this.es.onopen = null;
      this.es.onerror = null;
      this.es.close();
      this.es = null;
    }
  }

  private handleSseFailure(err?: Error) {
    this.disconnectSSE();

    // Start fallback polling if not already active
    this.startPolling();
    this.updateConnectionState(err || null);

    // Immediately attempt a poll to know if HTTP is available
    this.fetchLatestState();

    // Schedule exponential backoff SSE reconnect
    if (!this.reconnectTimer) {
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        this.backoffMs = Math.min(this.backoffMs * 1.5, MAX_BACKOFF_MS);
        this.connectSSE();
      }, this.backoffMs);
    }
  }

  private startPolling() {
    if (this.pollTimer) return;
    this.pollTimer = setInterval(() => {
      this.fetchLatestState();
    }, FALLBACK_POLL_INTERVAL_MS);
  }

  private stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  public async fetchLatestState(): Promise<WorldState | null> {
    if (this.isFetching) return null;
    this.isFetching = true;

    try {
      const meta = await apiWithMeta<WorldState>("/state", {
        etag: this.currentEtag || undefined,
        timeoutMs: 5000,
      });

      this.isHttpOk = true;

      if (meta.etag) {
        this.currentEtag = meta.etag;
      }

      if (meta.notModified) {
        // If 304, our cached state is fresh
        this.updateConnectionState(null);
        return null;
      }

      if (meta.data) {
        const newState = meta.data;
        // Monotonic check: do not overwrite newer state with an older response
        if (newState.tick !== undefined && newState.tick >= this.latestTick) {
          this.latestTick = newState.tick;
          if (this.queryClientSetter) {
            this.queryClientSetter(() => newState);
          }
        }

        this.updateConnectionState(null);
        return newState;
      }

      this.updateConnectionState(null);
      return null;
    } catch (err: unknown) {
      const error = err instanceof Error ? err : new Error("Failed to fetch state");
      this.isHttpOk = false;
      this.updateConnectionState(error);
      return null;
    } finally {
      this.isFetching = false;
    }
  }

  public reset() {
    this.stop();
    this.subscribers.clear();
    this.isSseConnected = false;
    this.isHttpOk = true;
    this.currentConnection = "polling";
    this.lastError = null;
    this.currentEtag = null;
    this.latestTick = -1;
    this.backoffMs = INITIAL_BACKOFF_MS;
    this.isFetching = false;
    this.queryClientSetter = null;
  }
}

export function useLiveState(): UseLiveStateResult {
  const queryClient = useQueryClient();
  const manager = LiveStateManager.getInstance();

  const [connection, setConnection] = useState<ConnectionState>(manager.getConnection());
  const [managerError, setManagerError] = useState<Error | null>(manager.getError());
  const isMounted = useRef(true);

  // Wire TanStack Query to manage the cache
  const { data: state, error: queryError, isLoading, refetch: queryRefetch } = useQuery<WorldState | null>({
    queryKey: STATE_QUERY_KEY,
    queryFn: async () => {
      const res = await apiWithMeta<WorldState>("/state");
      return res.data;
    },
    staleTime: Infinity, // Updates are pushed/polled actively
    refetchOnWindowFocus: true,
  });

  useEffect(() => {
    isMounted.current = true;
    manager.registerClient((updater) => {
      queryClient.setQueryData<WorldState | null>(STATE_QUERY_KEY, updater);
    });

    const unsubscribe = manager.subscribe((newConn, newErr) => {
      if (isMounted.current) {
        setConnection(newConn);
        setManagerError(newErr);
      }
    });

    return () => {
      isMounted.current = false;
      unsubscribe();
    };
  }, [manager, queryClient]);

  const refetch = useCallback(async () => {
    const fresh = await manager.fetchLatestState();
    if (!fresh) {
      await queryRefetch();
    }
  }, [manager, queryRefetch]);

  return {
    state: state ?? null,
    connection,
    error: (queryError as Error | null) || managerError,
    refetch,
    isLoading: isLoading && !state,
  };
}

// For unit testing purposes
export function _resetLiveStateManagerForTests() {
  LiveStateManager.getInstance().reset();
}
