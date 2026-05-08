// @vitest-environment jsdom
/**
 * Unit tests for the typed API client (issue #376).
 *
 * Covers:
 * 1. Successful GET request returns parsed JSON.
 * 2. Successful POST request sends JSON body with correct headers.
 * 3. 4xx response throws ApiClientError with structured detail.
 * 4. 5xx response throws ApiClientError with HTTP status.
 * 5. AbortSignal is forwarded to fetch.
 * 6. X-Requested-With header is always sent.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiClientError } from "../api";

const mockFetch = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch);
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

describe("api client", () => {
  it("runs.list — GET /api/runs and returns parsed JSON", async () => {
    const payload = { runs: [{ id: 1, name: "CI" }] };
    mockFetch.mockResolvedValueOnce(makeResponse(payload));

    const result = await api.runs.list({ per_page: 10 });
    expect(result).toEqual(payload);
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/runs");
    expect(url).toContain("per_page=10");
  });

  it("always sends X-Requested-With header", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse({ runs: [] }));
    await api.runs.list();
    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["X-Requested-With"]).toBe("XMLHttpRequest");
  });

  it("queue.diagnose — GET /api/queue/diagnose", async () => {
    const payload = { stale_count: 3, details: "ok", recommendations: [] };
    mockFetch.mockResolvedValueOnce(makeResponse(payload));

    const result = await api.queue.diagnose();
    expect(result.stale_count).toBe(3);
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/queue/diagnose");
  });

  it("queue.cancelWorkflow — POST with JSON body", async () => {
    mockFetch.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await api.queue.cancelWorkflow({ workflow_name: "ci-standard", repo: "MyRepo" });
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/queue/cancel-workflow");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ workflow_name: "ci-standard", repo: "MyRepo" });
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("throws ApiClientError on 4xx with detail", async () => {
    mockFetch.mockResolvedValue(makeResponse({ detail: "Not found" }, 404));

    const err = await api.runners.list().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiClientError);
    expect((err as ApiClientError).status).toBe(404);
    expect((err as ApiClientError).detail).toBe("Not found");
  });

  it("throws ApiClientError with status on non-JSON 5xx", async () => {
    mockFetch.mockResolvedValueOnce(new Response("Internal Server Error", { status: 500 }));

    const err = await api.stats.get().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiClientError);
    expect((err as ApiClientError).status).toBe(500);
  });

  it("forwards AbortSignal to fetch", async () => {
    const controller = new AbortController();
    mockFetch.mockResolvedValueOnce(makeResponse({ runs: [] }));

    await api.runs.list({ signal: controller.signal });
    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(init.signal).toBe(controller.signal);
  });

  it("agentRemediation.providers — calls /api/agents/providers", async () => {
    const payload = { providers: {}, availability: {} };
    mockFetch.mockResolvedValueOnce(makeResponse(payload));

    await api.agentRemediation.providers();
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/agents/providers");
  });
});
