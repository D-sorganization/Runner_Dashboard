// @vitest-environment jsdom
/**
 * Behaviour tests for pages/QuickDispatch — the global "⚡ Quick Dispatch"
 * popover extracted from the legacy App.tsx (decomposition #836, pass 9).
 *
 * Covers: trigger render, lazy repo/provider fetch on open, prompt/repo
 * validation, the dispatch POST envelope (incl. model field for model-capable
 * providers), rate-limit handling and network-error handling.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QuickDispatchPopover } from "../QuickDispatch";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

interface FetchOpts {
  repos?: unknown;
  providers?: Record<string, unknown>;
  dispatchOk?: boolean;
  dispatchStatus?: number;
  dispatchDetail?: string;
  dispatchReject?: boolean;
}

function mockFetch(opts: FetchOpts = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn((url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url.includes("/api/agents/quick-dispatch")) {
      if (opts.dispatchReject) return Promise.reject(new Error("network"));
      const ok = opts.dispatchOk !== false;
      return Promise.resolve({
        ok,
        status: opts.dispatchStatus ?? (ok ? 200 : 500),
        json: () =>
          Promise.resolve(ok ? { dispatched: 1 } : { detail: opts.dispatchDetail }),
      } as Response);
    }
    if (url.includes("/api/repos")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            repos: opts.repos ?? [{ full_name: "org/alpha" }, { full_name: "org/beta" }],
          }),
      } as Response);
    }
    // /api/agents/providers
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({ providers: opts.providers ?? { claude_code_cli: {}, jules_api: {} } }),
    } as Response);
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}

async function openPopover() {
  fireEvent.click(screen.getByRole("button", { name: "Open Quick Dispatch" }));
  await screen.findByRole("dialog", { name: "Quick Dispatch" });
}

describe("QuickDispatchPopover", () => {
  it("renders only the trigger when closed", () => {
    mockFetch();
    render(<QuickDispatchPopover />);
    const trigger = screen.getByRole("button", { name: "Open Quick Dispatch" });
    expect(trigger).toBeInTheDocument();
    expect(trigger).toHaveAttribute("data-touch-primitive", "TouchButton");
    expect(trigger).toHaveClass("quick-dispatch__trigger");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens the dialog and lazily loads repos + providers", async () => {
    const { fn } = mockFetch();
    render(<QuickDispatchPopover />);
    await openPopover();
    expect(screen.getByRole("dialog", { name: "Quick Dispatch" })).toHaveClass(
      "quick-dispatch__popover",
    );
    await waitFor(() => {
      const urls = fn.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.includes("/api/repos"))).toBe(true);
      expect(urls.some((u) => u.includes("/api/agents/providers"))).toBe(true);
    });
    await waitFor(() =>
      expect(screen.getByRole("option", { name: "org/alpha" })).toBeInTheDocument(),
    );
  });

  it("rejects a prompt shorter than 10 characters", async () => {
    mockFetch();
    render(<QuickDispatchPopover />);
    await openPopover();
    await screen.findByRole("option", { name: "org/alpha" });
    fireEvent.change(screen.getByPlaceholderText("Describe the task for the agent…"), {
      target: { value: "short" },
    });
    fireEvent.click(screen.getByRole("button", { name: "⚡ Dispatch" }));
    const message = await screen.findByText("Prompt must be at least 10 characters.");
    expect(message).toBeInTheDocument();
    expect(message).toHaveClass("quick-dispatch__status--error");
  });

  it("dispatches with a model field for a model-capable provider", async () => {
    const { calls } = mockFetch();
    render(<QuickDispatchPopover />);
    await openPopover();
    await screen.findByRole("option", { name: "org/alpha" });
    fireEvent.change(screen.getByPlaceholderText("Describe the task for the agent…"), {
      target: { value: "please do the thing properly" },
    });
    fireEvent.click(screen.getByRole("button", { name: "⚡ Dispatch" }));
    const success = await screen.findByText("✓ Dispatched!");
    expect(success).toHaveClass("quick-dispatch__status--success");
    const dispatch = calls.find((c) => c.url.includes("/api/agents/quick-dispatch"));
    expect(dispatch).toBeTruthy();
    const body = JSON.parse(String(dispatch!.init!.body));
    expect(body.repository).toBe("org/alpha");
    expect(body.prompt).toBe("please do the thing properly");
    expect(body.provider).toBe("claude_code_cli");
    expect(body.task_kind).toBe("adhoc");
    expect(body.model).toBe("claude-sonnet-4-6");
  });

  it("surfaces a rate-limit message on 429", async () => {
    mockFetch({ dispatchOk: false, dispatchStatus: 429 });
    render(<QuickDispatchPopover />);
    await openPopover();
    await screen.findByRole("option", { name: "org/alpha" });
    fireEvent.change(screen.getByPlaceholderText("Describe the task for the agent…"), {
      target: { value: "please do the thing properly" },
    });
    fireEvent.click(screen.getByRole("button", { name: "⚡ Dispatch" }));
    expect(
      await screen.findByText("Rate limited. Try again in a moment."),
    ).toBeInTheDocument();
  });

  it("surfaces a network error when the request rejects", async () => {
    mockFetch({ dispatchReject: true });
    render(<QuickDispatchPopover />);
    await openPopover();
    await screen.findByRole("option", { name: "org/alpha" });
    fireEvent.change(screen.getByPlaceholderText("Describe the task for the agent…"), {
      target: { value: "please do the thing properly" },
    });
    fireEvent.click(screen.getByRole("button", { name: "⚡ Dispatch" }));
    expect(
      await screen.findByText("Network error. Please try again."),
    ).toBeInTheDocument();
  });

  it("closes when Escape is pressed", async () => {
    mockFetch();
    render(<QuickDispatchPopover />);
    await openPopover();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
