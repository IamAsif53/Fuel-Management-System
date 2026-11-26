import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { api, apiWithMeta, ApiError, setToken, clearAuth, getToken } from "@/api/client";

describe("api client", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    clearAuth();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
    clearAuth();
  });

  it("performs GET request and parses JSON", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "Content-Type": "application/json" }),
      json: async () => ({ foo: "bar" }),
    });

    const data = await api<{ foo: string }>("/test");
    expect(data).toEqual({ foo: "bar" });
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/test",
      expect.objectContaining({
        headers: expect.objectContaining({
          Accept: "application/json",
        }),
      })
    );
  });

  it("sends Authorization header when signed in", async () => {
    setToken("secret-token-123", {
      username: "operator",
      actor: "operator",
      role: "operator",
    });
    expect(getToken()).toBe("secret-token-123");

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "Content-Type": "application/json" }),
      json: async () => ({ status: "ok" }),
    });

    await api("/secure");
    const [, init] = (global.fetch as any).mock.calls[0];
    expect(init.headers.Authorization).toBe("Bearer secret-token-123");
  });

  it("handles 304 Not Modified correctly in apiWithMeta", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 304,
      headers: new Headers({
        ETag: '"etag-123"',
        "X-Data-Tick": "42",
        "X-Degraded": "false",
      }),
    });

    const res = await apiWithMeta("/state", { etag: '"etag-123"' });
    expect(res.notModified).toBe(true);
    expect(res.status).toBe(304);
    expect(res.etag).toBe('"etag-123"');
    expect(res.tick).toBe(42);
    expect(res.degraded).toBe(false);
    expect(res.data).toBeNull();
  });

  it("parses structured error response from detail", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      statusText: "Bad Request",
      headers: new Headers({ "Content-Type": "application/json" }),
      json: async () => ({
        detail: {
          code: "INVALID_TICK",
          message: "Tick must be non-negative",
          data: { tick: -1 },
        },
      }),
    });

    try {
      await api("/error-test");
      expect.fail("Should have thrown ApiError");
    } catch (err: unknown) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(400);
      expect(apiErr.code).toBe("INVALID_TICK");
      expect(apiErr.message).toBe("Tick must be non-negative");
      expect(apiErr.data).toEqual({ tick: -1 });
    }
  });

  it("handles network error when fetch throws", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("Failed to fetch"));

    try {
      await api("/network-fail");
      expect.fail("Should have thrown ApiError");
    } catch (err: unknown) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(0);
      expect(apiErr.code).toBe("NETWORK_ERROR");
      expect(apiErr.message).toBe("Failed to fetch");
    }
  });

  it("does not send Authorization when signed out", async () => {
    clearAuth();
    let capturedHeaders: HeadersInit | undefined;
    global.fetch = vi.fn().mockImplementation(async (_url: RequestInfo | URL, init?: RequestInit) => {
      capturedHeaders = init?.headers;
      return {
        ok: true,
        status: 200,
        headers: new Headers({ "Content-Type": "application/json" }),
        json: async () => ({ status: "ok" }),
      } as Response;
    });

    await api("/state");
    expect((capturedHeaders as Record<string, string>)["Authorization"]).toBeUndefined();
  });
});
