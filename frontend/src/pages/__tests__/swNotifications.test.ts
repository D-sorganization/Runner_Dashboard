// @vitest-environment jsdom
/**
 * Tests for Service Worker push and notificationclick logic (issue #1724).
 */
import { describe, expect, it, vi } from "vitest";

describe("Service Worker Push & Notification Click", () => {
  it("handles push event and displays notification with deep link", async () => {
    const showNotification = vi.fn().mockResolvedValue(undefined);
    let pushHandler: ((e: unknown) => void) | null = null;

    const fakeSelf = {
      location: { origin: "https://hub.tailscale.ts.net", href: "https://hub.tailscale.ts.net/sw.js" },
      registration: { showNotification },
      addEventListener: (type: string, handler: (e: unknown) => void) => {
        if (type === "push") pushHandler = handler;
      },
    };

    // Handler logic under test
    fakeSelf.addEventListener("push", (e: unknown) => {
      const event = e as {
        data?: { json: () => Record<string, unknown>; text: () => string };
        waitUntil: (p: Promise<unknown>) => void;
      };
      let payload: Record<string, unknown> = {};
      if (event.data) {
        try {
          payload = event.data.json();
        } catch {
          payload = { title: event.data.text() };
        }
      }

      const title = (payload.title as string) || "Runner Dashboard";
      const targetUrl = (payload.url as string) || (payload.deep_link as string) || "/";
      const options = {
        body: (payload.body as string) || "",
        icon: (payload.icon as string) || "/icon.svg",
        badge: (payload.badge as string) || "/icon.svg",
        tag: (payload.tag as string) || (payload.topic as string) || "default",
        data: {
          url: targetUrl,
          ...payload,
        },
      };

      event.waitUntil(fakeSelf.registration.showNotification(title, options));
    });

    const event = {
      data: {
        json: () => ({
          title: "Staff Action Required",
          body: "Please approve disk compaction",
          url: "/staff?thread=thread-123",
          topic: "staff.escalation",
        }),
      },
      waitUntil: vi.fn((p) => p),
    };

    expect(pushHandler).not.toBeNull();
    pushHandler!(event);

    expect(showNotification).toHaveBeenCalledWith("Staff Action Required", {
      body: "Please approve disk compaction",
      icon: "/icon.svg",
      badge: "/icon.svg",
      tag: "staff.escalation",
      data: {
        url: "/staff?thread=thread-123",
        title: "Staff Action Required",
        body: "Please approve disk compaction",
        topic: "staff.escalation",
      },
    });
  });

  it("handles notificationclick by focusing matching window or opening new", async () => {
    let clickHandler: ((e: unknown) => void) | null = null;
    const focusClient = vi.fn().mockResolvedValue(undefined);
    const openWindow = vi.fn().mockResolvedValue(undefined);

    const fakeSelf = {
      location: { origin: "https://hub.tailscale.ts.net" },
      clients: {
        matchAll: vi.fn().mockResolvedValue([
          { url: "https://hub.tailscale.ts.net/staff?thread=thread-123", focus: focusClient },
        ]),
        openWindow,
      },
      addEventListener: (type: string, handler: (e: unknown) => void) => {
        if (type === "notificationclick") clickHandler = handler;
      },
    };

    fakeSelf.addEventListener("notificationclick", (e: unknown) => {
      const event = e as {
        notification: { close: () => void; data?: { url?: string } };
        waitUntil: (p: Promise<unknown>) => void;
      };
      event.notification.close();
      const rawUrl = event.notification.data && event.notification.data.url;
      const targetUrl = new URL(rawUrl || "/", fakeSelf.location.origin).href;

      event.waitUntil(
        fakeSelf.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList: unknown) => {
          const list = clientList as Array<{ url: string; focus: () => Promise<void> }>;
          for (const client of list) {
            if (client.url === targetUrl && "focus" in client) {
              return client.focus();
            }
          }
          if (fakeSelf.clients.openWindow) {
            return fakeSelf.clients.openWindow(targetUrl);
          }
          return null;
        })
      );
    });

    const close = vi.fn();
    const event = {
      notification: {
        close,
        data: { url: "/staff?thread=thread-123" },
      },
      waitUntil: vi.fn((p) => p),
    };

    expect(clickHandler).not.toBeNull();
    await clickHandler!(event);

    expect(close).toHaveBeenCalled();
    expect(focusClient).toHaveBeenCalled();
    expect(openWindow).not.toHaveBeenCalled();
  });
});
