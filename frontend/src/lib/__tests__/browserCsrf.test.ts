import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

beforeEach(() => vi.resetModules());
afterEach(() => vi.unstubAllGlobals());
const rejected = () =>
  new Response("{}", { status: 403, headers: { "X-CSRF-Required": "1" } });
const path = "/api/v1/staff/threads/th_one/messages";
const init = {
  method: "POST",
  body: "{}",
  headers: { "Idempotency-Key": "same-operation" },
};

describe("browser CSRF fetch", () => {
  it("refreshes only after a pre-execution rejection and preserves the operation identity", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(rejected())
      .mockResolvedValueOnce(Response.json({ token: "proof" }))
      .mockResolvedValue(Response.json({ accepted: true }));
    vi.stubGlobal("fetch", fetch);
    const { fetchWithBrowserCsrf } = await import("../browserCsrf");
    expect((await fetchWithBrowserCsrf(path, init)).ok).toBe(true);
    expect(fetch.mock.calls[1][0]).toBe("/api/auth/browser-csrf");
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({
      thread_id: "th_one",
    });
    const retried = fetch.mock.calls[2][1];
    expect(retried.body).toBe(init.body);
    expect(retried.headers.get("Idempotency-Key")).toBe("same-operation");
    expect(retried.headers.get("X-CSRF-Token")).toBe("proof");
    await fetchWithBrowserCsrf(path, init);
    expect(fetch).toHaveBeenCalledTimes(4);
    expect(fetch.mock.calls[3][1].headers.get("X-CSRF-Token")).toBe("proof");
  });

  it("never retries an ordinary denial", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response("{}", { status: 403 }));
    vi.stubGlobal("fetch", fetch);
    const { fetchWithBrowserCsrf } = await import("../browserCsrf");
    expect((await fetchWithBrowserCsrf(path, init)).status).toBe(403);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("does not send browser proofs or recovery requests to another origin", async () => {
    const fetch = vi.fn().mockResolvedValue(rejected());
    vi.stubGlobal("fetch", fetch);
    const { fetchWithBrowserCsrf } = await import("../browserCsrf");
    await fetchWithBrowserCsrf(`https://other.invalid${path}`, init);
    expect(fetch).toHaveBeenCalledExactlyOnceWith(
      `https://other.invalid${path}`,
      init,
    );
  });

  it("retries at most once even if the refreshed proof is rejected", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(rejected())
      .mockResolvedValueOnce(Response.json({ token: "proof" }))
      .mockResolvedValue(rejected());
    vi.stubGlobal("fetch", fetch);
    const { fetchWithBrowserCsrf } = await import("../browserCsrf");
    expect((await fetchWithBrowserCsrf(path, init)).status).toBe(403);
    expect(fetch).toHaveBeenCalledTimes(3);
  });
});
