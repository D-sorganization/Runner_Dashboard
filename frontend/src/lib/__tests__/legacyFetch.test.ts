// @vitest-environment jsdom
/**
 * Unit tests for the legacy fetch shim (issue #829).
 *
 * `legacyFetch` is the migration bridge that routes the hand-rolled raw
 * fetch() calls in `legacy/App.tsx` through the typed api.ts module so that
 * every legacy request — GET and state-changing alike — carries the
 * `X-Requested-With` CSRF sentinel header the backend enforces
 * (backend/middleware.py::csrf_check). It keeps the native fetch contract
 * (returns a Response) so the existing `.then(r => r.json())` / `r.ok`
 * chains in the legacy file are untouched (no restructuring — that is #836).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { legacyFetch, apiRequest, ApiClientError } from "../api";

const mockFetch = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch);
});

afterEach(() => {
  vi.restoreAllMocks();
  mockFetch.mockReset();
});

function headersOf(init: RequestInit): Record<string, string> {
  const h = init.headers;
  if (h instanceof Headers) {
    const out: Record<string, string> = {};
    h.forEach((v, k) => {
      out[k.toLowerCase()] = v;
    });
    return out;
  }
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries((h ?? {}) as Record<string, string>)) {
    out[k.toLowerCase()] = v;
  }
  return out;
}

describe("legacyFetch", () => {
  it("injects X-Requested-With on a bare GET", async () => {
    mockFetch.mockResolvedValueOnce(new Response("{}", { status: 200 }));
    await legacyFetch("/api/stats");
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/stats");
    expect(headersOf(init)["x-requested-with"]).toBe("XMLHttpRequest");
  });

  it("injects X-Requested-With on a POST that omitted it", async () => {
    mockFetch.mockResolvedValueOnce(new Response("{}", { status: 200 }));
    await legacyFetch("/api/diagnostics/restart-service", { method: "POST" });
    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(headersOf(init)["x-requested-with"]).toBe("XMLHttpRequest");
  });

  it("preserves caller-supplied headers and body", async () => {
    mockFetch.mockResolvedValueOnce(new Response("{}", { status: 200 }));
    await legacyFetch("/api/heavy-tests/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Custom": "1" },
      body: JSON.stringify({ repo: "R" }),
    });
    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    const h = headersOf(init);
    expect(h["content-type"]).toBe("application/json");
    expect(h["x-custom"]).toBe("1");
    expect(h["x-requested-with"]).toBe("XMLHttpRequest");
    expect(init.body).toBe(JSON.stringify({ repo: "R" }));
  });

  it("does not override a caller-supplied X-Requested-With", async () => {
    mockFetch.mockResolvedValueOnce(new Response("{}", { status: 200 }));
    await legacyFetch("/api/x", { headers: { "X-Requested-With": "custom" } });
    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(headersOf(init)["x-requested-with"]).toBe("custom");
  });

  it("returns the native Response so legacy .then(r => r.json()) chains keep working", async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    const r = await legacyFetch("/api/stats");
    expect(r).toBeInstanceOf(Response);
    expect(await r.json()).toEqual({ ok: true });
  });

  it("forwards an AbortSignal", async () => {
    const c = new AbortController();
    mockFetch.mockResolvedValueOnce(new Response("{}", { status: 200 }));
    await legacyFetch("/api/stats", { signal: c.signal });
    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(init.signal).toBe(c.signal);
  });
});

describe("apiRequest (typed bridge export)", () => {
  it("is exported for typed callers and throws ApiClientError on 4xx", async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "nope" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const err = await apiRequest("/api/x", { method: "POST" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiClientError);
    expect((err as ApiClientError).status).toBe(400);
    expect((err as ApiClientError).detail).toBe("nope");
  });
});
