// @vitest-environment jsdom
/**
 * Unit tests for web-vitals reporter (issues #385, #1550).
 *
 * Verifies that web-vitals reports are routed through `apiRequest`
 * and always include the `X-Requested-With: XMLHttpRequest` CSRF header
 * required by backend middleware (`backend/middleware.py::csrf_check`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { onCLS, onINP, onFCP, onLCP } from "web-vitals";
import { initWebVitals, sendWebVitals, buildWebVitalsPayload } from "../webVitals";

vi.mock("web-vitals", () => ({
  onCLS: vi.fn(),
  onINP: vi.fn(),
  onFCP: vi.fn(),
  onLCP: vi.fn(),
}));

const mockFetch = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch);
  window.history.replaceState({}, "", "/test-route");
});

afterEach(() => {
  vi.restoreAllMocks();
  mockFetch.mockReset();
});

function makeResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

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

describe("webVitals reporter", () => {
  it("buildWebVitalsPayload formats metric and route correctly", () => {
    const metric = {
      name: "CLS",
      value: 0.05,
      rating: "good",
      delta: 0.05,
      id: "v5-123",
      navigationType: "navigate",
    };
    const payload = buildWebVitalsPayload(metric, "/custom-route");
    expect(payload).toEqual({
      route: "/custom-route",
      metrics: [
        {
          name: "CLS",
          value: 0.05,
          rating: "good",
          delta: 0.05,
          id: "v5-123",
          navigation_type: "navigate",
        },
      ],
    });
  });

  it("buildWebVitalsPayload preserves zero delta and defaults missing optional fields", () => {
    const metric = {
      name: "LCP",
      value: 1200,
      delta: 0,
    };
    const payload = buildWebVitalsPayload(metric, "/");
    expect(payload.metrics[0].delta).toBe(0);
    expect(payload.metrics[0].rating).toBe("");
    expect(payload.metrics[0].id).toBe("");
    expect(payload.metrics[0].navigation_type).toBe("");
  });

  it("sendWebVitals sends POST to /api/metrics/web-vitals with CSRF header X-Requested-With", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse({ status: "ok" }));

    const metric = {
      name: "INP",
      value: 45,
      rating: "good",
      delta: 45,
      id: "inp-456",
      navigationType: "reload",
    };

    await sendWebVitals(metric);

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/metrics/web-vitals");
    expect(init.method).toBe("POST");

    const headers = headersOf(init);
    expect(headers["x-requested-with"]).toBe("XMLHttpRequest");
    expect(headers["content-type"]).toBe("application/json");

    const body = JSON.parse(init.body as string);
    expect(body).toEqual({
      route: "/test-route",
      metrics: [
        {
          name: "INP",
          value: 45,
          rating: "good",
          delta: 45,
          id: "inp-456",
          navigation_type: "reload",
        },
      ],
    });
  });

  it("sendWebVitals handles non-ok response without throwing", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse({ detail: "Forbidden" }, 403));

    const metric = { name: "FCP", value: 300 };
    await expect(sendWebVitals(metric)).resolves.not.toThrow();
  });

  it("sendWebVitals handles network rejection without throwing", async () => {
    mockFetch.mockRejectedValueOnce(new Error("Network failed"));

    const metric = { name: "FCP", value: 300 };
    await expect(sendWebVitals(metric)).resolves.not.toThrow();
  });

  it("initWebVitals registers sendWebVitals with onCLS, onINP, onFCP, onLCP", () => {
    initWebVitals();

    expect(onCLS).toHaveBeenCalledWith(sendWebVitals);
    expect(onINP).toHaveBeenCalledWith(sendWebVitals);
    expect(onFCP).toHaveBeenCalledWith(sendWebVitals);
    expect(onLCP).toHaveBeenCalledWith(sendWebVitals);
  });
});
