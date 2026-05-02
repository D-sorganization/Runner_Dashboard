// @vitest-environment jsdom
/**
 * Tests for Maxwell/Mobile.tsx — M11 of the mobile EPIC.
 *
 * Covers:
 * 1. Renders the daemon status pill.
 * 2. Shows active tasks from GET /api/maxwell/tasks.
 * 3. Sends a chat message via POST /api/maxwell/chat.
 * 4. Control sheet opens when the settings button is pressed.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MaxwellMobile } from "../Mobile";

afterEach(cleanup);

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const MOCK_STATUS = {
  status: "running",
  http_reachable: true,
  binary_found: true,
  service_running: true,
  service_detail: "active (running)",
  http_detail: "200 OK",
  binary_path: "/usr/local/bin/maxwell",
};

const MOCK_TASKS = {
  tasks: [
    { task_id: "task-abc-001", status: "running", elapsed_seconds: 75 },
    { task_id: "task-abc-002", status: "queued", elapsed_seconds: 12 },
  ],
};

const MOCK_VERSION = { daemon: "1.4.2", contract: "1.4.2" };

const MOCK_CHAT_RESPONSE = { response: "Fleet is healthy. 8 runners online." };

// ---------------------------------------------------------------------------
// Fetch mock helper
// ---------------------------------------------------------------------------

function setupFetch({
  statusOk = true,
  tasksOk = true,
  versionOk = true,
  chatOk = true,
  controlOk = true,
  statusData = MOCK_STATUS as object,
  tasksData = MOCK_TASKS as object,
}: {
  statusOk?: boolean;
  tasksOk?: boolean;
  versionOk?: boolean;
  chatOk?: boolean;
  controlOk?: boolean;
  statusData?: object;
  tasksData?: object;
} = {}) {
  const fetchMock = vi.fn((url: string, options?: RequestInit) => {
    if (url.includes("/api/maxwell/status")) {
      return Promise.resolve({
        ok: statusOk,
        status: statusOk ? 200 : 500,
        json: () => Promise.resolve(statusData),
      } as Response);
    }
    if (url.includes("/api/maxwell/tasks")) {
      return Promise.resolve({
        ok: tasksOk,
        status: tasksOk ? 200 : 500,
        json: () => Promise.resolve(tasksData),
      } as Response);
    }
    if (url.includes("/api/maxwell/version")) {
      return Promise.resolve({
        ok: versionOk,
        status: versionOk ? 200 : 500,
        json: () => Promise.resolve(MOCK_VERSION),
      } as Response);
    }
    if (url.includes("/api/maxwell/chat") && options?.method === "POST") {
      return Promise.resolve({
        ok: chatOk,
        status: chatOk ? 200 : 500,
        // No streaming body in tests — fall through to JSON fallback
        body: null,
        json: () => Promise.resolve(chatOk ? MOCK_CHAT_RESPONSE : { detail: "Daemon error" }),
      } as unknown as Response);
    }
    if (url.includes("/api/maxwell/control") && options?.method === "POST") {
      return Promise.resolve({
        ok: controlOk,
        status: controlOk ? 200 : 500,
        json: () => Promise.resolve(controlOk ? { status: "ok" } : { detail: "Control failed" }),
      } as Response);
    }
    return Promise.resolve({
      ok: false,
      status: 404,
      json: () => Promise.resolve({}),
    } as Response);
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("MaxwellMobile", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    // jsdom does not implement window.matchMedia — provide a minimal stub.
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });

    // Stub sessionStorage
    const store: Record<string, string> = {};
    vi.stubGlobal("sessionStorage", {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => { store[k] = v; },
      removeItem: (k: string) => { delete store[k]; },
      clear: () => { Object.keys(store).forEach((k) => delete store[k]); },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    cleanup();
  });

  // -------------------------------------------------------------------------
  // 1. Status pill
  // -------------------------------------------------------------------------

  it("renders the daemon status pill with correct label", async () => {
    setupFetch();
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByLabelText(/maxwell daemon status: running/i)).toBeInTheDocument();
    });

    const pill = screen.getByLabelText(/maxwell daemon status: running/i);
    expect(pill).toHaveTextContent("running");
  });

  it("shows a stopped pill when daemon is stopped", async () => {
    setupFetch({ statusData: { status: "stopped", http_reachable: false } });
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByLabelText(/maxwell daemon status: stopped/i)).toBeInTheDocument();
    });
  });

  it("shows the daemon version next to the status pill", async () => {
    setupFetch();
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByText(/v1\.4\.2/i)).toBeInTheDocument();
    });
  });

  it("shows the refresh button", async () => {
    setupFetch();
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /refresh maxwell status/i })).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // 2. Active tasks
  // -------------------------------------------------------------------------

  it("renders task cards with task_id, status, and elapsed", async () => {
    setupFetch();
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByLabelText(/Task task-abc-001/i)).toBeInTheDocument();
    });

    expect(screen.getByLabelText(/task-abc-001.*running/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/task-abc-002.*queued/i)).toBeInTheDocument();
    // elapsed for 75s should render as "1m 15s"
    expect(screen.getByText("1m 15s")).toBeInTheDocument();
  });

  it("shows empty state when no tasks", async () => {
    setupFetch({ tasksData: { tasks: [] } });
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByText(/no active tasks/i)).toBeInTheDocument();
    });
  });

  it("shows task list container with role=list", async () => {
    setupFetch();
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByRole("list", { name: /active tasks/i })).toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // 3. Chat interface
  // -------------------------------------------------------------------------

  it("renders the chat textarea and send button", async () => {
    setupFetch();
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByLabelText(/message maxwell/i)).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /send message to maxwell/i })).toBeInTheDocument();
  });

  it("send button is disabled when input is empty", async () => {
    setupFetch();
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /send message to maxwell/i })).toBeDisabled();
    });
  });

  it("sends a chat message and shows the response", async () => {
    const fetchMock = setupFetch();
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByLabelText(/message maxwell/i)).toBeInTheDocument();
    });

    const textarea = screen.getByLabelText(/message maxwell/i);
    fireEvent.change(textarea, { target: { value: "status" } });

    const sendBtn = screen.getByRole("button", { name: /send message to maxwell/i });

    await act(async () => {
      fireEvent.click(sendBtn);
    });

    // User message should appear (there may be multiple "status" texts — chip and bubble)
    await waitFor(() => {
      const bubbles = screen.getAllByText("status");
      expect(bubbles.length).toBeGreaterThanOrEqual(1);
    });

    // POST should have been called
    const chatCalls = (fetchMock.mock.calls as [string, RequestInit?][]).filter(
      ([url, opts]) => url.includes("/api/maxwell/chat") && opts?.method === "POST",
    );
    expect(chatCalls).toHaveLength(1);

    const body = JSON.parse(chatCalls[0][1]!.body as string);
    expect(body.message).toBe("status");
  });

  it("shows chat response after sending", async () => {
    setupFetch();
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByLabelText(/message maxwell/i)).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/message maxwell/i), { target: { value: "status" } });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /send message to maxwell/i }));
    });

    await waitFor(() => {
      expect(screen.getByText(/Fleet is healthy/i)).toBeInTheDocument();
    });
  });

  it("quick-action chips trigger chat when clicked", async () => {
    const fetchMock = setupFetch();
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /ask maxwell: status/i })).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /ask maxwell: status/i }));
    });

    await waitFor(() => {
      const chatCalls = (fetchMock.mock.calls as [string, RequestInit?][]).filter(
        ([url, opts]) => url.includes("/api/maxwell/chat") && opts?.method === "POST",
      );
      expect(chatCalls).toHaveLength(1);
    });
  });

  // -------------------------------------------------------------------------
  // 4. Control sheet
  // -------------------------------------------------------------------------

  it("control sheet opens when the settings button is pressed", async () => {
    setupFetch();
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /open daemon controls/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /open daemon controls/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog", { name: /daemon controls/i })).toBeInTheDocument();
    });
  });

  it("control sheet contains Start, Stop, and Restart buttons", async () => {
    setupFetch();
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /open daemon controls/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /open daemon controls/i }));

    await waitFor(() => {
      // Use exact aria-labels to avoid matching "Restart Maxwell" for "start"
      expect(screen.getByRole("button", { name: /^start maxwell daemon$/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^stop maxwell daemon$/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^restart maxwell daemon$/i })).toBeInTheDocument();
    });
  });

  it("Start is disabled when daemon is already running", async () => {
    setupFetch();
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /open daemon controls/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /open daemon controls/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^start maxwell daemon$/i })).toBeDisabled();
    });
  });

  it("Stop is disabled when daemon is stopped", async () => {
    setupFetch({ statusData: { status: "stopped", http_reachable: false } });
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /open daemon controls/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /open daemon controls/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^stop maxwell daemon$/i })).toBeDisabled();
    });
  });

  it("clicking Restart posts to /api/maxwell/control", async () => {
    const fetchMock = setupFetch();
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /open daemon controls/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /open daemon controls/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^restart maxwell daemon$/i })).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^restart maxwell daemon$/i }));
    });

    await waitFor(() => {
      const controlCalls = (fetchMock.mock.calls as [string, RequestInit?][]).filter(
        ([url, opts]) => url.includes("/api/maxwell/control") && opts?.method === "POST",
      );
      expect(controlCalls).toHaveLength(1);

      const body = JSON.parse(controlCalls[0][1]!.body as string);
      expect(body.action).toBe("restart");
    });
  });

  it("control sheet closes on Escape key", async () => {
    setupFetch();
    render(<MaxwellMobile />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /open daemon controls/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /open daemon controls/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});
