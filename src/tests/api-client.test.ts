import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/types";
import { api, backendUrl } from "@/lib/api/client";

function envelopeResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const okEnvelope = (data: unknown) => ({
  success: true,
  data,
  error: null,
  meta: { request_id: "abc123", timestamp: "2026-01-01T00:00:00Z", api_version: "v1" },
});

const errEnvelope = (
  code: string,
  message: string,
  details: unknown[] = [],
) => ({
  success: false,
  data: null,
  error: { code, message, details },
  meta: { request_id: "abc123", timestamp: "2026-01-01T00:00:00Z", api_version: "v1" },
});

describe("backendUrl", () => {
  const prevOrigin = process.env.NEXT_PUBLIC_BACKEND_ORIGIN;
  const prevPort = process.env.NEXT_PUBLIC_BACKEND_PORT;

  afterEach(() => {
    process.env.NEXT_PUBLIC_BACKEND_ORIGIN = prevOrigin;
    process.env.NEXT_PUBLIC_BACKEND_PORT = prevPort;
  });

  it("uses gateway mode with XTransformPort when no origin is set", () => {
    process.env.NEXT_PUBLIC_BACKEND_ORIGIN = "";
    process.env.NEXT_PUBLIC_BACKEND_PORT = "8000";
    expect(backendUrl("/api/v1/meta")).toBe("/api/v1/meta?XTransformPort=8000");
  });

  it("keeps existing query params in gateway mode", () => {
    process.env.NEXT_PUBLIC_BACKEND_ORIGIN = "";
    process.env.NEXT_PUBLIC_BACKEND_PORT = "8000";
    expect(backendUrl("/api/v1/strategies", { mode: "forex" })).toBe(
      "/api/v1/strategies?mode=forex&XTransformPort=8000"
    );
  });

  it("uses direct origin when configured (module re-import picks up build-time env)", async () => {
    process.env.NEXT_PUBLIC_BACKEND_ORIGIN = "http://localhost:9001";
    vi.resetModules();
    const { backendUrl: urlWithOrigin } = await import("@/lib/api/client");
    expect(urlWithOrigin("/health/live")).toBe("http://localhost:9001/health/live");
  });
});

describe("api client envelope handling", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("unwraps success envelopes and returns data directly", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      envelopeResponse(okEnvelope([{ id: "1" }]))
    );
    const data = await api.get<unknown[]>("/api/v1/strategies");
    expect(data).toEqual([{ id: "1" }]);
  });

  it("throws ApiError with backend code/message on error envelope", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      envelopeResponse(errEnvelope("RULE_VALIDATION_FAILED", "bad rules", [{ path: ".conditions", message: "x" }]), 422)
    );
    await expect(api.post("/api/v1/strategies", {})).rejects.toMatchObject({
      name: "ApiError",
      code: "RULE_VALIDATION_FAILED",
      status: 422,
      details: [{ path: ".conditions", message: "x" }],
      requestId: "abc123",
    });
  });

  it("maps network failures to BACKEND_UNREACHABLE", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(
      new TypeError("Failed to fetch")
    );
    try {
      await api.get("/health/live");
      expect.unreachable("should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).code).toBe("BACKEND_UNREACHABLE");
    }
  });

  it("surfaces error envelopes with HTTP 200-but-success-false", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      envelopeResponse(errEnvelope("NOT_FOUND", "Strategy was not found."), 200)
    );
    await expect(api.get("/api/v1/strategies/xyz")).rejects.toMatchObject({
      code: "NOT_FOUND",
    });
  });

  it("sends no Authorization header when NEXT_PUBLIC_API_TOKEN is unset", async () => {
    const prevToken = process.env.NEXT_PUBLIC_API_TOKEN;
    delete process.env.NEXT_PUBLIC_API_TOKEN;
    vi.resetModules();
    try {
      const { api: freshApi } = await import("@/lib/api/client");
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        envelopeResponse(okEnvelope({}))
      );
      await freshApi.get("/api/v1/meta");
      const [, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock
        .calls[0] as [string, RequestInit];
      const headers = (init.headers ?? {}) as Record<string, string>;
      expect(headers["Authorization"]).toBeUndefined();
    } finally {
      process.env.NEXT_PUBLIC_API_TOKEN = prevToken;
      vi.resetModules();
    }
  });

  it("sends Authorization: Bearer <token> when NEXT_PUBLIC_API_TOKEN is set", async () => {
    const prevToken = process.env.NEXT_PUBLIC_API_TOKEN;
    process.env.NEXT_PUBLIC_API_TOKEN = "test-access-token-123456";
    vi.resetModules();
    try {
      const { api: freshApi } = await import("@/lib/api/client");
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        envelopeResponse(okEnvelope({}))
      );
      await freshApi.get("/api/v1/meta");
      const [, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock
        .calls[0] as [string, RequestInit];
      const headers = (init.headers ?? {}) as Record<string, string>;
      expect(headers["Authorization"]).toBe("Bearer test-access-token-123456");
      // POST bodies keep the JSON content type next to the auth header.
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockClear();
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        envelopeResponse(okEnvelope({}), 201)
      );
      await freshApi.post("/api/v1/strategy-categories", { name: "x" });
      const [, postInit] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock
        .calls[0] as [string, RequestInit];
      const postHeaders = (postInit.headers ?? {}) as Record<string, string>;
      expect(postHeaders["Authorization"]).toBe("Bearer test-access-token-123456");
      expect(postHeaders["Content-Type"]).toBe("application/json");
    } finally {
      process.env.NEXT_PUBLIC_API_TOKEN = prevToken;
      vi.resetModules();
    }
  });
});
