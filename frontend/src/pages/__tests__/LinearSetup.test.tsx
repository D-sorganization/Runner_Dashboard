// @vitest-environment jsdom
/**
 * Tests for LinearSetup.tsx — issue #728 E3.
 *
 * Covers:
 * 1. Renders without throwing (smoke test).
 * 2. Shows loading skeleton while fetch is pending.
 * 3. Renders workspace list on successful fetch.
 * 4. Shows error message when API call fails.
 * 5. Renders webhook URL section.
 * 6. Copy webhook URL button triggers clipboard write.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LinearSetup } from "../LinearSetup";

afterEach(cleanup);

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

const MOCK_WORKSPACES = {
  workspaces: [
    {
      id: "ws-001",
      auth_kind: "oauth",
      auth_status: "active",
      teams_filter: ["engineering"],
      trigger_label: "linear",
      default_repository: "runner-dashboard",
      prefer_source: "linear",
    },
  ],
};

const EMPTY_WORKSPACES = { workspaces: [] };

function makeFetch(
  data: object = MOCK_WORKSPACES,
  ok: boolean = true,
  status: number = 200,
) {
  return vi.fn((_url: string) =>
    Promise.resolve({
      ok,
      status,
      json: () => Promise.resolve(data),
    } as Response),
  );
}

describe("LinearSetup", () => {
  it("renders without throwing (smoke test)", () => {
    global.fetch = makeFetch();
    expect(() => render(<LinearSetup />)).not.toThrow();
  });

  it("shows loading skeleton while fetch is pending", async () => {
    // Never resolves — component is stuck in loading state.
    global.fetch = vi.fn(() => new Promise<Response>(() => {}));
    const { container } = render(<LinearSetup />);
    // Skeleton is rendered while loading
    const busyEl = container.querySelector("[aria-busy='true']");
    expect(busyEl).not.toBeNull();
    expect(busyEl?.getAttribute("aria-label")).toMatch(/loading/i);
  });

  it("renders workspace list after successful fetch", async () => {
    global.fetch = makeFetch(MOCK_WORKSPACES);
    render(<LinearSetup />);
    await waitFor(() => {
      // Should no longer show loading state
      expect(document.querySelector("[aria-busy='true']")).toBeNull();
    });
    // Webhook URL section should be visible
    expect(screen.getByText(/Dashboard Webhook URL/i)).toBeInTheDocument();
    expect(document.querySelector("[data-touch-primitive='Badge']")).toHaveTextContent("active");
  });

  it("renders empty workspace list without crashing", async () => {
    global.fetch = makeFetch(EMPTY_WORKSPACES);
    render(<LinearSetup />);
    await waitFor(() => {
      expect(document.querySelector("[aria-busy='true']")).toBeNull();
    });
    // The page should still render the webhook URL section
    expect(screen.getByText(/Dashboard Webhook URL/i)).toBeInTheDocument();
    expect(screen.getByText(/No workspaces configured/i)).toBeInTheDocument();
    expect(document.querySelector(".empty-state")).toBeInTheDocument();
  });

  it("shows error message when API call fails (HTTP error)", async () => {
    global.fetch = makeFetch({}, false, 500);
    render(<LinearSetup />);
    await waitFor(() => {
      expect(document.querySelector("[aria-busy='true']")).toBeNull();
    });
    // Error message visible
    const errorEl = document.body.textContent;
    expect(errorEl).toMatch(/HTTP 500|failed|error/i);
    expect(document.querySelector(".empty-state")).toHaveAttribute("data-variant", "error");
  });

  it("shows error message when fetch throws network error", async () => {
    global.fetch = vi.fn(() => Promise.reject(new Error("Network error")));
    render(<LinearSetup />);
    await waitFor(() => {
      expect(document.querySelector("[aria-busy='true']")).toBeNull();
    });
    expect(document.body.textContent).toMatch(/network error|failed/i);
  });

  it("renders copy webhook URL button", async () => {
    global.fetch = makeFetch(MOCK_WORKSPACES);
    render(<LinearSetup />);
    await waitFor(() => {
      expect(document.querySelector("[aria-busy='true']")).toBeNull();
    });
    const copyBtn = screen.getByText(/copy/i);
    expect(copyBtn).toBeInTheDocument();
    expect(copyBtn).toHaveAttribute("data-touch-primitive", "TouchButton");
  });

  it("clicking copy triggers clipboard.writeText and shows confirmation", async () => {
    global.fetch = makeFetch(MOCK_WORKSPACES);
    // jsdom may not provide navigator.clipboard — set it up as needed
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: writeTextMock },
      writable: true,
      configurable: true,
    });
    render(<LinearSetup />);
    await waitFor(() => {
      expect(document.querySelector("[aria-busy='true']")).toBeNull();
    });
    const copyBtn = screen.getByText(/copy/i);
    await act(async () => {
      fireEvent.click(copyBtn);
    });
    expect(writeTextMock).toHaveBeenCalledOnce();
    await waitFor(() => {
      expect(screen.getByText(/copied to clipboard/i)).toBeInTheDocument();
    });
  });
});
