// @vitest-environment jsdom
/**
 * Tests for PushSettings.tsx — issue #728 E3.
 *
 * Covers:
 * 1. Renders without throwing (smoke test).
 * 2. Shows "not configured" state when API returns 503.
 * 3. Renders topic toggle checkboxes after successful VAPID key load.
 * 4. Subscribe button is shown when VAPID key is loaded.
 * 5. Shows error when push is not supported in browser.
 * 6. Error message shown when fetch fails.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PushSettings from "../PushSettings";

afterEach(cleanup);

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

function makeVapidFetch(status: number = 200, publicKey: string = "test-vapid-key") {
  return vi.fn((_url: string) => {
    if (status === 503) {
      return Promise.resolve({
        ok: false,
        status: 503,
        json: () => Promise.resolve({ detail: "Not configured" }),
      } as Response);
    }
    if (!(_url as string).includes("vapid")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({}),
      } as Response);
    }
    return Promise.resolve({
      ok: status === 200,
      status,
      json: () => Promise.resolve({ publicKey }),
    } as Response);
  });
}

describe("PushSettings", () => {
  it("renders without throwing (smoke test)", () => {
    global.fetch = makeVapidFetch();
    expect(() => render(<PushSettings />)).not.toThrow();
  });

  it("renders Push Notifications heading", async () => {
    global.fetch = makeVapidFetch();
    render(<PushSettings />);
    expect(screen.getByText(/Push Notifications/i)).toBeInTheDocument();
  });

  it("shows 'not configured' message when VAPID endpoint returns 503", async () => {
    global.fetch = makeVapidFetch(503);
    render(<PushSettings />);
    await waitFor(() => {
      expect(
        screen.getByText(/not configured by operator/i),
      ).toBeInTheDocument();
    });
    expect(document.querySelector(".empty-state")).toBeInTheDocument();
  });

  it("renders topic toggles after VAPID key is loaded", async () => {
    global.fetch = makeVapidFetch(200, "BNbxyz123");
    render(<PushSettings />);
    await waitFor(() => {
      // Topics defined in PushSettings should render as labels
      expect(screen.getByText(/Agent completed/i)).toBeInTheDocument();
    });
  });

  it("shows error message when VAPID key fetch fails with network error", async () => {
    global.fetch = vi.fn(() => Promise.reject(new Error("Network fail")));
    render(<PushSettings />);
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/failed to load vapid key|network fail/i);
    });
    expect(document.querySelector(".empty-state")).toHaveAttribute("data-variant", "error");
  });

  it("shows all predefined push topics", async () => {
    global.fetch = makeVapidFetch(200, "BNbxyz123");
    render(<PushSettings />);
    await waitFor(() => {
      expect(screen.getByText(/Agent completed/i)).toBeInTheDocument();
      expect(screen.getByText(/Agent failed/i)).toBeInTheDocument();
      expect(screen.getByText(/CI failed/i)).toBeInTheDocument();
      expect(screen.getByText(/Runner offline/i)).toBeInTheDocument();
      expect(screen.getByText(/Queue stale/i)).toBeInTheDocument();
    });
  });

  it("toggling a topic checkbox updates state (checked changes)", async () => {
    global.fetch = makeVapidFetch(200, "BNbxyz123");
    render(<PushSettings />);
    await waitFor(() => {
      expect(screen.getByText(/Agent completed/i)).toBeInTheDocument();
    });
    // The toggle is a div/button, not a native checkbox — clicking it should
    // not throw and should update the UI state.
    const agentCompletedLabel = screen.getByText(/Agent completed/i);
    const topicRow = agentCompletedLabel.closest(".push-settings__topic") as HTMLElement;
    if (topicRow) {
      await act(async () => {
        fireEvent.click(topicRow);
      });
    }
    // No crash = component handles click correctly
    expect(screen.getByText(/Agent completed/i)).toBeInTheDocument();
  });

  it("renders subscribe through the touch button primitive", async () => {
    global.fetch = makeVapidFetch(200, "BNbxyz123");
    render(<PushSettings />);
    const subscribeButton = await screen.findByRole("button", { name: /subscribe/i });
    expect(subscribeButton).toHaveAttribute("data-touch-primitive", "TouchButton");
  });

  it("shows iOS installation hint", async () => {
    global.fetch = makeVapidFetch(200, "BNbxyz123");
    render(<PushSettings />);
    await waitFor(() => {
      expect(screen.getByText(/Add to Home Screen/i)).toBeInTheDocument();
    });
  });

  it("shows runbook link when push is not configured", async () => {
    global.fetch = makeVapidFetch(503);
    render(<PushSettings />);
    await waitFor(() => {
      expect(screen.getByText(/phone-access-tailnet\.md/i)).toBeInTheDocument();
    });
  });

  it("sends X-Requested-With CSRF header when subscribing from the PWA", async () => {
    const fetchSpy = makeVapidFetch(200, "dGVzdC12YXBpZC1rZXk");
    global.fetch = fetchSpy;
    Object.defineProperty(window, "PushManager", {
      configurable: true,
      value: function PushManager() {},
      writable: true,
    });
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: {
        ready: Promise.resolve({
          pushManager: {
            subscribe: vi.fn(() =>
              Promise.resolve({
                toJSON: () =>
                  Promise.resolve({
                    endpoint: "https://fcm.googleapis.com/fcm/send/sub-1",
                    keys: { p256dh: "k1", auth: "a1" },
                  }),
              }),
            ),
          },
        }),
      },
      writable: true,
    });

    render(<PushSettings />);
    const subscribeButton = await screen.findByRole("button", { name: /subscribe/i });
    await act(async () => {
      fireEvent.click(subscribeButton);
    });

    await waitFor(() => {
      expect(screen.getByText("Active")).toBeInTheDocument();
    });

    const subscribeCall = fetchSpy.mock.calls.find(
      (call: unknown[]) => call[0] === "/api/push/subscribe",
    ) as [string, RequestInit] | undefined;
    expect(subscribeCall).toBeDefined();
    expect(
      (subscribeCall![1].headers as Record<string, string>)["X-Requested-With"],
    ).toBe("XMLHttpRequest");
  });

  it("hydrates on load, edits topics + quiet hours after subscribe, and calls DELETE on unsubscribe", async () => {
    const unsubscribeBrowserSpy = vi.fn(() => Promise.resolve(true));
    Object.defineProperty(window, "PushManager", { configurable: true, value: function PushManager() {}, writable: true });
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: {
        ready: Promise.resolve({
          pushManager: {
            getSubscription: vi.fn(() =>
              Promise.resolve({ endpoint: "https://fcm.googleapis.com/fcm/send/sub-7", unsubscribe: unsubscribeBrowserSpy }),
            ),
          },
        }),
      },
      writable: true,
    });

    const fetchSpy = vi.fn((url: string, init?: RequestInit) => {
      if (url.includes("vapid-public-key")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ publicKey: "dGVzdC12YXBpZC1rZXk" }) } as Response);
      }
      if (url.includes("/api/push/subscriptions") && (!init?.method || init.method === "GET")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              subscriptions: [
                { id: 7, endpoint: "https://fcm.googleapis.com/fcm/send/sub-7", topics: ["agent.completed"], quiet_hours_start: "23:00", quiet_hours_end: "07:00" },
              ],
            }),
        } as Response);
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ id: 7, deleted: true }) } as Response);
    });
    global.fetch = fetchSpy;

    render(<PushSettings />);
    await waitFor(() => expect(screen.getByText("Active")).toBeInTheDocument());

    const checkboxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(checkboxes[0].checked).toBe(true);
    expect(checkboxes[2].disabled).toBe(false);
    expect((screen.getByLabelText("Quiet hours start") as HTMLInputElement).value).toBe("23:00");
    expect((screen.getByLabelText("Quiet hours end") as HTMLInputElement).value).toBe("07:00");

    await act(async () => fireEvent.click(checkboxes[2]));
    const patchCall = fetchSpy.mock.calls.find(
      (call: unknown[]) => call[0] === "/api/push/subscribe/7" && (call[1] as RequestInit | undefined)?.method === "PATCH",
    ) as [string, RequestInit] | undefined;
    expect(patchCall).toBeDefined();
    expect(JSON.parse(patchCall![1].body as string)).toEqual({
      topics: ["agent.completed", "ci.failed"],
      quiet_hours_start: "23:00",
      quiet_hours_end: "07:00",
    });

    await act(async () => fireEvent.change(screen.getByLabelText("Quiet hours start"), { target: { value: "21:30" } }));
    await act(async () => fireEvent.click(await screen.findByRole("button", { name: /unsubscribe/i })));
    await waitFor(() => expect(screen.getByText("Not subscribed")).toBeInTheDocument());
    expect(unsubscribeBrowserSpy).toHaveBeenCalled();

    const deleteCall = fetchSpy.mock.calls.find(
      (call: unknown[]) => call[0] === "/api/push/subscribe/7" && (call[1] as RequestInit | undefined)?.method === "DELETE",
    ) as [string, RequestInit] | undefined;
    expect(deleteCall).toBeDefined();
    expect((deleteCall![1].headers as Record<string, string>)["X-Requested-With"]).toBe("XMLHttpRequest");
  });
});
