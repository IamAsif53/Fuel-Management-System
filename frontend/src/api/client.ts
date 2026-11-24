/**
 * Typed API client for FSIP Frontend.
 * Wraps browser fetch with timeout, error normalization, standard response handling,
 * and 304 Not Modified support.
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public data?: unknown
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let operatorToken: string | null = typeof window !== "undefined"
  ? localStorage.getItem("fsip_operator_token") || "change-me-operator"
  : "change-me-operator";

export const setToken = (t: string | null) => {
  operatorToken = t;
  if (typeof window !== "undefined") {
    if (t) localStorage.setItem("fsip_operator_token", t);
    else localStorage.removeItem("fsip_operator_token");
  }
};

export interface RequestOptions extends RequestInit {
  timeoutMs?: number;
  etag?: string;
}

export interface MetaResponse<T> {
  data: T | null;
  status: number;
  notModified: boolean;
  etag: string | null;
  tick: number | null;
  degraded: boolean;
}

const BASE_URL = "/api";

/**
 * Perform a typed HTTP request to the /api backend.
 */
export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const meta = await apiWithMeta<T>(path, options);
  if (meta.notModified) {
    // 304 Not Modified without cache should not happen for a raw value request
    throw new ApiError(304, "NOT_MODIFIED", "Resource not modified");
  }
  return meta.data as T;
}

/**
 * Perform a typed HTTP request and return full metadata (ETag, 304 Not Modified, Headers).
 */
export async function apiWithMeta<T>(
  path: string,
  options: RequestOptions = {}
): Promise<MetaResponse<T>> {
  const { timeoutMs = 10000, etag, signal, headers: customHeaders, ...init } = options;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => {
    controller.abort(new Error("Request timed out"));
  }, timeoutMs);

  const combinedSignal = signal
    ? anySignal([signal, controller.signal])
    : controller.signal;

  const headers: Record<string, string> = {
    Accept: "application/json",
    ...((customHeaders as Record<string, string>) || {}),
  };

  if (!(init.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  if (operatorToken) {
    headers.Authorization = `Bearer ${operatorToken}`;
  }

  if (etag) {
    headers["If-None-Match"] = etag;
  }

  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const url = `${BASE_URL}${normalizedPath}`;

  try {
    const res = await fetch(url, {
      ...init,
      headers,
      signal: combinedSignal,
    });

    clearTimeout(timeoutId);

    const resEtag = res.headers.get("ETag");
    const tickHeader = res.headers.get("X-Data-Tick");
    const degradedHeader = res.headers.get("X-Degraded");
    const tick = tickHeader ? parseInt(tickHeader, 10) : null;
    const degraded = degradedHeader === "true";

    if (res.status === 304) {
      return {
        data: null,
        status: 304,
        notModified: true,
        etag: resEtag || etag || null,
        tick,
        degraded,
      };
    }

    if (!res.ok) {
      let code = `HTTP_${res.status}`;
      let message = res.statusText || "Request failed";
      let errorData: unknown = undefined;

      try {
        const body = await res.json();
        if (body && typeof body === "object") {
          const detail = (body as Record<string, unknown>).detail;
          if (detail && typeof detail === "object") {
            const d = detail as Record<string, unknown>;
            code = (d.code as string) || code;
            message = (d.message as string) || message;
            errorData = d.data;
          } else if (typeof detail === "string") {
            message = detail;
          } else if ((body as Record<string, unknown>).error) {
            const err = (body as Record<string, unknown>).error as Record<string, unknown>;
            code = (err.code as string) || code;
            message = (err.message as string) || message;
          }
        }
      } catch {
        /* Non-JSON response body */
      }

      throw new ApiError(res.status, code, message, errorData);
    }

    // 204 No Content
    if (res.status === 204) {
      return {
        data: null,
        status: 204,
        notModified: false,
        etag: resEtag,
        tick,
        degraded,
      };
    }

    const data = (await res.json()) as T;
    return {
      data,
      status: res.status,
      notModified: false,
      etag: resEtag,
      tick,
      degraded,
    };
  } catch (err: unknown) {
    clearTimeout(timeoutId);

    if (err instanceof ApiError) {
      throw err;
    }

    if (err instanceof Error) {
      if (err.name === "AbortError" || err.message.includes("timed out")) {
        throw new ApiError(0, "TIMEOUT", "Request timed out");
      }
      throw new ApiError(0, "NETWORK_ERROR", err.message || "Network request failed");
    }

    throw new ApiError(0, "UNKNOWN_ERROR", "An unexpected error occurred");
  }
}

/**
 * Combine multiple AbortSignals so that any abort trigger cancels the request.
 */
function anySignal(signals: AbortSignal[]): AbortSignal {
  const controller = new AbortController();

  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort(signal.reason);
      return controller.signal;
    }
    signal.addEventListener("abort", () => controller.abort(signal.reason), {
      once: true,
    });
  }

  return controller.signal;
}

// ---------------------------------------------------------------- Decision & Control API Helpers
import type {
  Alert,
  AlertsResponse,
  AuditResponse,
  CoverResponse,
  Decision,
  DecisionsResponse,
  ForecastSeries,
  Fuel,
  IncidentsResponse,
  RecommendationsResponse,
  RiskResponse,
} from "./types";

export async function fetchRecommendations(simRunId?: number): Promise<Decision[]> {
  const q = simRunId ? `?sim_run_id=${simRunId}` : "";
  const res = await api<RecommendationsResponse>(`/recommendations${q}`);
  return res.items;
}

export async function fetchDecisions(params?: {
  sim_run_id?: number;
  status?: string;
  policy?: string;
  from_tick?: number;
  limit?: number;
  offset?: number;
}): Promise<DecisionsResponse> {
  const sp = new URLSearchParams();
  if (params?.sim_run_id !== undefined) sp.set("sim_run_id", String(params.sim_run_id));
  if (params?.status) sp.set("status", params.status);
  if (params?.policy) sp.set("policy", params.policy);
  if (params?.from_tick !== undefined) sp.set("from_tick", String(params.from_tick));
  if (params?.limit !== undefined) sp.set("limit", String(params.limit));
  if (params?.offset !== undefined) sp.set("offset", String(params.offset));
  const query = sp.toString() ? `?${sp.toString()}` : "";
  return api<DecisionsResponse>(`/decisions${query}`);
}

export async function fetchDecisionById(id: number): Promise<Decision> {
  return api<Decision>(`/decisions/${id}`);
}

export async function approveDecision(
  id: number,
  body?: {
    line_ids?: number[];
    quantities?: Record<string, number>;
    reviewed_driver?: boolean;
  }
): Promise<Decision> {
  return api<Decision>(`/decisions/${id}/approve`, {
    method: "POST",
    body: JSON.stringify(body || {}),
  });
}

export async function rejectDecision(
  id: number,
  body?: {
    reason_code?: string;
    comment?: string;
  }
): Promise<Decision> {
  return api<Decision>(`/decisions/${id}/reject`, {
    method: "POST",
    body: JSON.stringify(body || {}),
  });
}

export async function triggerPlanNow(): Promise<{ accepted: boolean; details: unknown }> {
  return api<{ accepted: boolean; details: unknown }>("/plan-now", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function setControlMode(mode: string): Promise<{ status: string; mode: string }> {
  return api<{ status: string; mode: string }>("/control/mode", {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
}

export async function setControlPolicy(
  policy: string,
  scarcityPolicy?: string
): Promise<{ status: string; policy: string }> {
  return api<{ status: string; policy: string }>("/control/policy", {
    method: "POST",
    body: JSON.stringify({ policy, scarcity_policy: scarcityPolicy }),
  });
}

export async function dispatchManualAllocation(body: {
  source_depot_id: string;
  destination_station_id: string;
  route_id: string;
  fuel_type: Fuel;
  quantity: number;
  comment?: string;
}): Promise<unknown> {
  return api("/manual-allocation", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function fetchAuditLogs(params?: {
  action?: string;
  actor?: string;
  limit?: number;
  offset?: number;
}): Promise<AuditResponse> {
  const sp = new URLSearchParams();
  if (params?.action) sp.set("action", params.action);
  if (params?.actor) sp.set("actor", params.actor);
  if (params?.limit !== undefined) sp.set("limit", String(params.limit));
  if (params?.offset !== undefined) sp.set("offset", String(params.offset));
  const query = sp.toString() ? `?${sp.toString()}` : "";
  return api<AuditResponse>(`/audit${query}`);
}

// ---------------------------------------------------------------- Risk, Alerts, Forecast, Cover, Incidents API Helpers

export async function fetchRisk(params?: {
  region_id?: string;
  fuel?: string;
  min_level?: string;
}): Promise<RiskResponse> {
  const sp = new URLSearchParams();
  if (params?.region_id) sp.set("region_id", params.region_id);
  if (params?.fuel) sp.set("fuel", params.fuel);
  if (params?.min_level) sp.set("min_level", params.min_level);
  const query = sp.toString() ? `?${sp.toString()}` : "";
  return api<RiskResponse>(`/risk${query}`);
}

export async function fetchAlerts(params?: {
  status?: string;
  category?: string;
  severity?: string;
  limit?: number;
}): Promise<AlertsResponse> {
  const sp = new URLSearchParams();
  if (params?.status) sp.set("status", params.status);
  if (params?.category) sp.set("category", params.category);
  if (params?.severity) sp.set("severity", params.severity);
  if (params?.limit !== undefined) sp.set("limit", String(params.limit));
  const query = sp.toString() ? `?${sp.toString()}` : "";
  return api<AlertsResponse>(`/alerts${query}`);
}

export async function acknowledgeAlert(id: number): Promise<Alert> {
  return api<Alert>(`/alerts/${id}/ack`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function fetchForecast(params: {
  station_id: string;
  fuel: string;
  horizon?: number;
  history?: number;
}): Promise<ForecastSeries> {
  const sp = new URLSearchParams();
  sp.set("station_id", params.station_id);
  sp.set("fuel", params.fuel);
  if (params.horizon !== undefined) sp.set("horizon", String(params.horizon));
  if (params.history !== undefined) sp.set("history", String(params.history));
  return api<ForecastSeries>(`/forecast?${sp.toString()}`);
}

export async function fetchCover(): Promise<CoverResponse> {
  return api<CoverResponse>("/cover");
}

export async function fetchIncidents(params?: {
  status?: string;
  limit?: number;
}): Promise<IncidentsResponse> {
  const sp = new URLSearchParams();
  if (params?.status) sp.set("status", params.status);
  if (params?.limit !== undefined) sp.set("limit", String(params.limit));
  const query = sp.toString() ? `?${sp.toString()}` : "";
  return api<IncidentsResponse>(`/incidents${query}`);
}

export async function fetchReconciliationReport(params?: {
  from_tick?: number;
  to_tick?: number;
}): Promise<import("./types").ReconciliationReport> {
  const sp = new URLSearchParams();
  if (params?.from_tick !== undefined) sp.set("from_tick", String(params.from_tick));
  if (params?.to_tick !== undefined) sp.set("to_tick", String(params.to_tick));
  const query = sp.toString() ? `?${sp.toString()}` : "";
  return api<import("./types").ReconciliationReport>(`/reports/reconciliation${query}`);
}

