// @vitest-environment jsdom
/**
 * Behaviour tests for pages/MaxwellPage.tsx — extracted from the legacy
 * App.tsx monolith (decomposition #836, pass 6).
 *
 * Covers:
 * 1. Smoke render (stubs the on-mount tasks/version fetches).
 * 2. Status stat row + contract version after the version fetch resolves.
 * 3. Start control shown when stopped; invokes onControl({action:"start"}).
 * 4. Stop/Restart shown when running.
 * 5. Refresh button invokes onRefresh.
 * 6. Recent-tasks table renders fetched rows; offline state when unreachable.
 * 7. Error banner renders the error string.
 */
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MaxwellPage, MaxwellTab, type MaxwellStatus } from "../MaxwellPage";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  try {
    sessionStorage.clear();
  } catch {
    /* ignore */
  }
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(""),
    body: null,
  } as unknown as Response;
}

function stubMaxwellFetch(
  opts: { tasks?: unknown[]; contract?: string; chatReply?: string } = {},
): void {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (String(url).includes("/api/maxwell/tasks")) {
        return Promise.resolve(jsonResponse({ tasks: opts.tasks || [] }));
      }
      if (String(url).includes("/api/maxwell/version")) {
        return Promise.resolve(jsonResponse({ contract: opts.contract || "" }));
      }
      if (String(url).includes("/api/maxwell/chat")) {
        // No `.body` reader → component falls back to r.text().
        return Promise.resolve({
          ok: true,
          status: 200,
          body: null,
          text: () => Promise.resolve(opts.chatReply ?? "pong"),
          json: () => Promise.resolve({}),
        } as unknown as Response);
      }
      return Promise.resolve(jsonResponse({}));
    }),
  );
}

const RUNNING: MaxwellStatus = {
  status: "running",
  http_reachable: true,
  binary_found: true,
  binary_path: "/usr/bin/maxwell",
};

const STOPPED: MaxwellStatus = {
  status: "stopped",
  http_reachable: false,
  binary_found: false,
};

describe("MaxwellTab", () => {
  it("renders without throwing (smoke test)", () => {
    stubMaxwellFetch();
    expect(() =>
      render(
        <MaxwellTab
          status={STOPPED}
          loading={false}
          onControl={() => Promise.resolve()}
        />,
      ),
    ).not.toThrow();
  });

  it("renders the status stat row and contract version", async () => {
    stubMaxwellFetch({ contract: "v1.2.3" });
    render(
      <MaxwellTab
        status={RUNNING}
        loading={false}
        onControl={() => Promise.resolve()}
      />,
    );
    expect(screen.getByText("Status")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByText("reachable")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("v1.2.3")).toBeInTheDocument());
  });

  it("shows Start when stopped and dispatches start", () => {
    stubMaxwellFetch();
    const onControl = vi.fn(() => Promise.resolve());
    render(
      <MaxwellTab status={STOPPED} loading={false} onControl={onControl} />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Start Maxwell daemon" }),
    );
    expect(onControl).toHaveBeenCalledWith({ action: "start" });
  });

  it("dispatches stop when running", () => {
    stubMaxwellFetch();
    const onControl = vi.fn(() => new Promise<void>(() => {}));
    render(
      <MaxwellTab status={RUNNING} loading={false} onControl={onControl} />,
    );
    expect(
      screen.getByRole("button", { name: "Restart Maxwell daemon" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Stop Maxwell daemon" }),
    );
    expect(onControl).toHaveBeenCalledWith({ action: "stop" });
  });

  it("dispatches restart when running", () => {
    stubMaxwellFetch();
    const onControl = vi.fn(() => new Promise<void>(() => {}));
    render(
      <MaxwellTab status={RUNNING} loading={false} onControl={onControl} />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Restart Maxwell daemon" }),
    );
    expect(onControl).toHaveBeenCalledWith({ action: "restart" });
  });

  it("reports a control failure to the operator", async () => {
    stubMaxwellFetch();
    const onControl = vi.fn(() =>
      Promise.reject(new Error("systemctl denied")),
    );
    render(
      <MaxwellTab status={STOPPED} loading={false} onControl={onControl} />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Start Maxwell daemon" }),
    );
    await waitFor(() =>
      expect(screen.getByText(/systemctl denied/)).toBeInTheDocument(),
    );
  });

  it("reports an unreachable daemon when the chat request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (String(url).includes("/api/maxwell/chat"))
          return Promise.reject(new Error("boom"));
        return Promise.resolve(
          jsonResponse(
            String(url).includes("version") ? { contract: "" } : { tasks: [] },
          ),
        );
      }),
    );
    render(
      <MaxwellTab
        status={RUNNING}
        loading={false}
        onControl={() => Promise.resolve()}
      />,
    );
    const textarea = screen.getByPlaceholderText("Message Maxwell...");
    fireEvent.change(textarea, { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(
        screen.getByText(
          "Maxwell-Daemon is unreachable. Check daemon status above, then retry.",
        ),
      ).toBeInTheDocument(),
    );
  });

  it("Refresh invokes onRefresh", () => {
    stubMaxwellFetch();
    const onRefresh = vi.fn();
    render(
      <MaxwellTab
        status={RUNNING}
        loading={false}
        onRefresh={onRefresh}
        onControl={() => Promise.resolve()}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh Maxwell status" }),
    );
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("renders fetched recent tasks", async () => {
    stubMaxwellFetch({
      tasks: [
        {
          id: "task-one-zzzz",
          status: "done",
          repo: "x/y",
          created_at: "2026-06-02T10:00:00Z",
        },
      ],
    });
    render(
      <MaxwellTab
        status={RUNNING}
        loading={false}
        onControl={() => Promise.resolve()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText("task-one")).toBeInTheDocument(),
    );
    expect(screen.getByText("done")).toBeInTheDocument();
    expect(screen.getByText("x/y")).toBeInTheDocument();
  });

  it("shows offline task message when daemon unreachable", async () => {
    stubMaxwellFetch();
    render(
      <MaxwellTab
        status={STOPPED}
        loading={false}
        onControl={() => Promise.resolve()}
      />,
    );
    // The offline message replaces the loading state once the on-mount task
    // fetch resolves.
    await waitFor(() =>
      expect(
        screen.getByText("Maxwell-Daemon offline — no task history"),
      ).toBeInTheDocument(),
    );
  });

  it("survives rejected mount fetches (tasks/version) without crashing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("network down"))),
    );
    render(
      <MaxwellTab
        status={RUNNING}
        loading={false}
        onControl={() => Promise.resolve()}
      />,
    );
    // Contract falls back to "unknown" and tasks resolve to empty — no throw.
    await waitFor(() =>
      expect(screen.getByText("No tasks yet")).toBeInTheDocument(),
    );
    expect(screen.getByText("unknown")).toBeInTheDocument();
  });

  it("recovers from corrupt persisted chat history", () => {
    try {
      sessionStorage.setItem("maxwellMobileChatHistory", "{not json");
    } catch {
      /* ignore */
    }
    stubMaxwellFetch();
    expect(() =>
      render(
        <MaxwellTab
          status={RUNNING}
          loading={false}
          onControl={() => Promise.resolve()}
        />,
      ),
    ).not.toThrow();
  });

  it("renders an error banner", () => {
    stubMaxwellFetch();
    render(
      <MaxwellTab
        status={STOPPED}
        loading={false}
        error="daemon exploded"
        onControl={() => Promise.resolve()}
      />,
    );
    expect(screen.getByText("daemon exploded")).toBeInTheDocument();
  });

  it("sends a chat message and renders the reply", async () => {
    stubMaxwellFetch({ chatReply: "fleet is healthy" });
    render(
      <MaxwellTab
        status={RUNNING}
        loading={false}
        onControl={() => Promise.resolve()}
      />,
    );
    const textarea = screen.getByPlaceholderText("Message Maxwell...");
    fireEvent.change(textarea, { target: { value: "status?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    // Operator message echoes immediately; Maxwell reply arrives after fetch.
    expect(screen.getByText("status?")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText("fleet is healthy")).toBeInTheDocument(),
    );
  });

  it("offers quick-action chips that send canned prompts", async () => {
    stubMaxwellFetch({ chatReply: "ok" });
    render(
      <MaxwellTab
        status={RUNNING}
        loading={false}
        onControl={() => Promise.resolve()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "status" }));
    await waitFor(() =>
      expect(screen.getAllByText("ok").length).toBeGreaterThanOrEqual(1),
    );
  });

  it("sends on Enter (without Shift) and streams a chunked reply", async () => {
    // Provide a ReadableStream-like body so the streaming pump path is exercised.
    const chunks = [
      new TextEncoder().encode("hel"),
      new TextEncoder().encode("lo"),
    ];
    let i = 0;
    const reader = {
      read: () =>
        i < chunks.length
          ? Promise.resolve({ done: false, value: chunks[i++] })
          : Promise.resolve({ done: true, value: undefined }),
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (String(url).includes("/api/maxwell/chat")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            body: { getReader: () => reader },
            text: () => Promise.resolve(""),
            json: () => Promise.resolve({}),
          } as unknown as Response);
        }
        return Promise.resolve(
          jsonResponse(
            String(url).includes("version") ? { contract: "" } : { tasks: [] },
          ),
        );
      }),
    );
    render(
      <MaxwellTab
        status={RUNNING}
        loading={false}
        onControl={() => Promise.resolve()}
      />,
    );
    const textarea = screen.getByPlaceholderText("Message Maxwell...");
    fireEvent.change(textarea, { target: { value: "hi" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    await waitFor(() => expect(screen.getByText("hello")).toBeInTheDocument());
  });

  it("surfaces a Retry control and unreachable hint when offline", () => {
    stubMaxwellFetch();
    render(
      <MaxwellTab
        status={STOPPED}
        loading={false}
        onControl={() => Promise.resolve()}
      />,
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText(
        "Daemon unreachable; retry before sending commands",
      ),
    ).toBeInTheDocument();
  });

  it("Retry re-fetches and invokes onRefresh when offline", async () => {
    const fetchSpy = vi.fn((url: string) =>
      Promise.resolve(
        jsonResponse(
          String(url).includes("version") ? { contract: "" } : { tasks: [] },
        ),
      ),
    );
    vi.stubGlobal("fetch", fetchSpy);
    const onRefresh = vi.fn();
    render(
      <MaxwellTab
        status={STOPPED}
        loading={false}
        onRefresh={onRefresh}
        onControl={() => Promise.resolve()}
      />,
    );
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    fetchSpy.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRefresh).toHaveBeenCalled();
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
  });

  it("reveals a Latest scroll-to-bottom control when scrolled up", async () => {
    stubMaxwellFetch({ tasks: [], chatReply: "x" });
    render(
      <MaxwellTab
        status={RUNNING}
        loading={false}
        onControl={() => Promise.resolve()}
      />,
    );
    // Seed a message so the chat list is scrollable, then simulate scrolling up.
    const textarea = screen.getByPlaceholderText("Message Maxwell...");
    fireEvent.change(textarea, { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByText("x")).toBeInTheDocument());
    const list = document.querySelector(
      ".maxwell-chat-messages",
    ) as HTMLElement;
    // Force geometry so isNearChatBottom() returns false on scroll.
    Object.defineProperty(list, "scrollHeight", {
      value: 1000,
      configurable: true,
    });
    Object.defineProperty(list, "clientHeight", {
      value: 100,
      configurable: true,
    });
    Object.defineProperty(list, "scrollTop", {
      value: 0,
      configurable: true,
      writable: true,
    });
    fireEvent.scroll(list);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Scroll to bottom of chat" }),
      ).toBeInTheDocument(),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Scroll to bottom of chat" }),
    );
  });
});

describe("MaxwellPage", () => {
  it("owns status loading and control calls outside the legacy App", async () => {
    const fetchSpy = vi.fn((url: string, options?: RequestInit) => {
      if (url.includes("/api/maxwell/status")) {
        return Promise.resolve(jsonResponse(RUNNING));
      }
      if (url.includes("/api/maxwell/control") && options?.method === "POST") {
        return Promise.resolve(jsonResponse({ ok: true }));
      }
      return Promise.resolve(
        jsonResponse(
          url.includes("version") ? { contract: "" } : { tasks: [] },
        ),
      );
    });
    vi.stubGlobal("fetch", fetchSpy);

    render(<MaxwellPage />);

    await waitFor(() =>
      expect(screen.getByText("running")).toBeInTheDocument(),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Stop Maxwell daemon" }),
    );

    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/maxwell/control",
        expect.objectContaining({
          body: JSON.stringify({ action: "stop" }),
          method: "POST",
        }),
      ),
    );
  });
});
