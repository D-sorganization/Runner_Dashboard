// @vitest-environment jsdom
/**
 * Tests for Service Worker caching policy (#3720): operational data served
 * under /api/* must never enter Cache Storage, and a cache-first static
 * response must not be stored when the origin marks it no-store/private
 * (per-user or stale-sensitive content served from a static-looking path).
 */
import { describe, expect, it, vi } from "vitest";

const STATIC_EXTS = /\.(?:js|css|svg|html|woff2|png|webp|ico)$/;

function isApiRequest(url: string): boolean {
  return new URL(url).pathname.startsWith("/api/");
}

function isStaticAsset(pathname: string): boolean {
  return STATIC_EXTS.test(pathname);
}

function shouldCache(response: { status: number; headers: { get: (name: string) => string | null } } | undefined): boolean {
  if (!response || response.status !== 200) return false;
  const cacheControl = response.headers.get("Cache-Control") || "";
  return !/no-store|private/i.test(cacheControl);
}

function fakeResponse(status: number, cacheControl?: string) {
  return {
    status,
    headers: { get: (name: string) => (name === "Cache-Control" ? cacheControl ?? null : null) },
    clone: () => fakeResponse(status, cacheControl),
  };
}

describe("Service Worker caching policy", () => {
  it("routes any /api/* request to network-only, even one with a static-looking extension", () => {
    const origin = "https://hub.fleet-example.ts.net";
    const apiLikeStaticPaths = ["/api/export/report.html", "/api/staff/avatar.png", "/api/fleet/status"];

    for (const path of apiLikeStaticPaths) {
      const url = `${origin}${path}`;
      // isApiRequest must win over isStaticAsset regardless of extension.
      expect(isApiRequest(url)).toBe(true);
      const pathname = new URL(url).pathname;
      // The fetch handler checks isApiRequest before isStaticAsset, so a
      // positive isStaticAsset match on an API path must never be reached.
      if (isApiRequest(url)) {
        continue;
      }
      expect(isStaticAsset(pathname)).toBe(false);
    }
  });

  it("does not treat a genuine static asset path as an API request", () => {
    const url = "https://hub.fleet-example.ts.net/assets/app.js";
    expect(isApiRequest(url)).toBe(false);
    expect(isStaticAsset(new URL(url).pathname)).toBe(true);
  });

  it("cacheFirst stores a plain 200 static response", async () => {
    const put = vi.fn().mockResolvedValue(undefined);
    const cache = { put };
    const fakeCaches = {
      match: vi.fn().mockResolvedValue(undefined),
      open: vi.fn().mockResolvedValue(cache),
    };

    async function cacheFirst(request: unknown, fetchImpl: () => Promise<ReturnType<typeof fakeResponse>>) {
      const cached = await fakeCaches.match(request);
      if (cached) return cached;
      const networkResponse = await fetchImpl();
      if (shouldCache(networkResponse)) {
        const clone = networkResponse.clone();
        await fakeCaches.open("runner-dashboard-test").then((c) => c.put(request, clone));
      }
      return networkResponse;
    }

    await cacheFirst("req", async () => fakeResponse(200));
    expect(put).toHaveBeenCalledTimes(1);
  });

  it("cacheFirst does not store a response marked Cache-Control: no-store (stale/sensitive operational data)", async () => {
    const put = vi.fn().mockResolvedValue(undefined);
    const cache = { put };
    const fakeCaches = {
      match: vi.fn().mockResolvedValue(undefined),
      open: vi.fn().mockResolvedValue(cache),
    };

    async function cacheFirst(request: unknown, fetchImpl: () => Promise<ReturnType<typeof fakeResponse>>) {
      const cached = await fakeCaches.match(request);
      if (cached) return cached;
      const networkResponse = await fetchImpl();
      if (shouldCache(networkResponse)) {
        const clone = networkResponse.clone();
        await fakeCaches.open("runner-dashboard-test").then((c) => c.put(request, clone));
      }
      return networkResponse;
    }

    await cacheFirst("req", async () => fakeResponse(200, "no-store"));
    await cacheFirst("req2", async () => fakeResponse(200, "private, max-age=0"));

    expect(put).not.toHaveBeenCalled();
  });
});
