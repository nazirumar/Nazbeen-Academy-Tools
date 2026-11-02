/**
 * HTTP client for the FastAPI backend with a consistent envelope contract.
 *
 * Two deployment modes (selected via NEXT_PUBLIC_BACKEND_ORIGIN):
 * - Gateway mode (default, empty origin): requests go to the same origin and
 *   the edge gateway routes them to the backend using the XTransformPort
 *   query parameter. Used in the sandbox preview and single-domain deploys.
 * - Direct mode: NEXT_PUBLIC_BACKEND_ORIGIN="http://localhost:8000" — calls
 *   hit the backend directly (requires CORS allow-list on the backend).
 */

import {
  ApiError,
  type ApiEnvelope,
  type ApiErrorDetail,
} from "./types";

const ORIGIN = (process.env.NEXT_PUBLIC_BACKEND_ORIGIN ?? "").replace(/\/$/, "");
const PORT = process.env.NEXT_PUBLIC_BACKEND_PORT ?? "8000";
const REQUEST_TIMEOUT_MS = 20_000;

/**
 * Optional deployment access token (Phase 9).
 *
 * Set NEXT_PUBLIC_API_TOKEN when the backend requires bearer authentication
 * (the backend mandates it in APP_ENV=production). Every request then sends
 * `Authorization: Bearer <token>`.
 *
 * HONEST SECURITY NOTE: this value is part of the public client bundle — it
 * gates the UI like a site password (stops strangers and drive-by scanners)
 * but must NOT be treated as a per-user secret. For per-owner access control
 * configure API_AUTH_TOKENS with `token:owner` bindings server-side, and for
 * fully private deployments keep the backend off the public internet (see
 * docs/operations.md). The token is never logged by this client and is
 * stripped from thrown error messages.
 */
const API_TOKEN = (process.env.NEXT_PUBLIC_API_TOKEN ?? "").trim();

export function backendUrl(path: string, query?: Record<string, string>): string {
  const search = new URLSearchParams(query ?? {});
  if (!ORIGIN) {
    // Gateway mode: route through the same origin to the backend port.
    if (!search.has("XTransformPort")) search.set("XTransformPort", PORT);
  }
  const qs = search.toString();
  const base = `${ORIGIN}${path}`;
  return qs ? `${base}?${qs}` : base;
}

function toApiError(payload: unknown, status: number, fallback: string): ApiError {
  if (
    payload &&
    typeof payload === "object" &&
    "error" in payload &&
    payload.error &&
    typeof payload.error === "object"
  ) {
    const err = payload.error as {
      code?: string;
      message?: string;
      details?: ApiErrorDetail[];
    };
    const meta = (payload as { meta?: { request_id?: string | null } }).meta;
    return new ApiError({
      code: err.code ?? "UNKNOWN_ERROR",
      message: err.message ?? fallback,
      status,
      details: err.details ?? [],
      requestId: meta?.request_id ?? null,
    });
  }
  return new ApiError({ code: "UNKNOWN_ERROR", message: fallback, status });
}

async function request<T>(
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  options: { body?: unknown; query?: Record<string, string> } = {},
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  // Security: never log the token; only its presence is surfaced in errors.
  const headers: Record<string, string> = {};
  if (API_TOKEN) headers["Authorization"] = `Bearer ${API_TOKEN}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch(backendUrl(path, options.query), {
      method,
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: controller.signal,
      cache: "no-store",
    });
  } catch {
    clearTimeout(timer);
    throw new ApiError({
      code: "BACKEND_UNREACHABLE",
      message:
        "The analysis backend is unreachable. Start it with: make backend " +
        "(or docker compose up). Check the System tab for details.",
      status: 0,
    });
  }
  clearTimeout(timer);

  let payload: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  const envelope = payload as ApiEnvelope<T> | null;
  if (!response.ok || !envelope || typeof envelope.success !== "boolean") {
    throw toApiError(payload, response.status, `Request failed (${response.status}).`);
  }
  if (!envelope.success || envelope.data === null) {
    const err = envelope.error;
    throw new ApiError({
      code: err?.code ?? "UNKNOWN_ERROR",
      message: err?.message ?? "Request failed.",
      status: response.status,
      details: err?.details ?? [],
      requestId: envelope.meta?.request_id ?? null,
    });
  }
  return envelope.data;
}

export const api = {
  get<T>(path: string, query?: Record<string, string>) {
    return request<T>("GET", path, { query });
  },
  post<T>(path: string, body?: unknown) {
    return request<T>("POST", path, { body });
  },
  patch<T>(path: string, body: unknown) {
    return request<T>("PATCH", path, { body });
  },
  delete<T>(path: string) {
    return request<T>("DELETE", path);
  },
};
