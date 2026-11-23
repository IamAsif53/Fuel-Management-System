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

let operatorToken: string | null = null;
export const setToken = (t: string | null) => {
  operatorToken = t;
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
