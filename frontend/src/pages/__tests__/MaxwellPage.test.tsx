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
 * 8. Provider-status view (#1338): no chat on this page; chat with Maxwell
 *    lives in the Staff Console (#1330), which the page links to.
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
import { MaxwellPage, MaxwellTab } from "../MaxwellPage";
import {
  jsonResponse,
  RUNNING,
  STOPPED,
  stubMaxwellFetch,
} from "./maxwellTestUtils";

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

  it("words a failed start without the raw Error prefix (#1718)", async () => {
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
    expect(
      await screen.findByText("Could not start Maxwell: systemctl denied"),
    ).toBeInTheDocument();
  });

  it("says the daemon is not listening instead of the raw socket error (#1718)", () => {
    stubMaxwellFetch();
    render(
      <MaxwellTab
        status={{
          ...STOPPED,
          http_reachable: false,
          http_detail: "All connection attempts failed",
        }}
        loading={false}
        onControl={vi.fn()}
      />,
    );
    expect(screen.queryByText("All connection attempts failed")).toBeNull();
    expect(screen.getByText("not listening")).toBeInTheDocument();
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

  it("is a provider-status view that sends chat to the Staff Console (#1338)", () => {
    const fetchSpy = vi.fn((url: string) =>
      Promise.resolve(
        jsonResponse(
          String(url).includes("version") ? { contract: "" } : { tasks: [] },
        ),
      ),
    );
    vi.stubGlobal("fetch", fetchSpy);
    render(
      <MaxwellTab
        status={RUNNING}
        loading={false}
        onControl={() => Promise.resolve()}
      />,
    );

    expect(screen.queryByText("Maxwell Chat")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", {
        name: /chat with maxwell in the staff console/i,
      }),
    ).toHaveAttribute("href", "/");
    expect(
      fetchSpy.mock.calls.some(([url]) =>
        String(url).includes("/api/maxwell/chat"),
      ),
    ).toBe(false);
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
