// @vitest-environment jsdom
/**
 * Behaviour tests for pages/FleetCommand — the Fleet Command tab (#1233, epic #1192).
 *
 * Covers:
 * 1. Priorities: board date, active priorities with GitHub tracking links,
 *    deferred backlog, disagreement flags; "no board meeting yet"; meeting history.
 * 2. Directives: edit + add + expire, PUT of the whole list with the CSRF header; 404 degrades.
 * 3. Active work: board sessions merged with staff runs, conflicts highlighted,
 *    repo filter, staff-run deep link, available:false degrades.
 * 4. Messages: Active work "Message" prefills the form; send POSTs with CSRF; inbox renders.
 * 5. Claims: check shows the holder; a 409 claim shows held_by; release POSTs.
 * 6. Dispatch: reuses Staff Assign (dry-run preview, then run) and links to the Staff tab run.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FleetCommandPage } from "../FleetCommand";
import { operatorSession } from "../FleetCommand/fleetApi";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const BOARD = {
  date: "2026-09-21",
  metadata: {},
  active: [
    {
      rank: 1,
      item: "Coordination API",
      project: "Runner_Dashboard",
      scope: "sessions, claims, briefing",
      assigned_to: "claude",
      tracking: "#1229",
      acceptance: "",
    },
    {
      rank: 2,
      item: "Glass model",
      project: "Tools_Private",
      scope: "",
      assigned_to: "",
      tracking: "",
      acceptance: "",
    },
  ],
  deferred: [{ item: "Mobile polish", project: "Runner_Dashboard", reason: "capacity", reassess: "2026-10-01" }],
  borda: [],
  disagreements: ["Codex wants the glass model first"],
};

const PRIORITIES = { available: true, board: BOARD, directives: [], portfolios: [], generated_at: "x" };
const MEETINGS = {
  available: true,
  meetings: [
    { date: "2026-09-21", files: ["consensus.md"], has_consensus: true },
    { date: "2026-09-14", files: ["consensus.md"], has_consensus: true },
  ],
};
const OLD_MEETING = {
  available: true,
  date: "2026-09-14",
  consensus: { ...BOARD, active: [{ ...BOARD.active[0], item: "Staff Hub", tracking: "Runner_Dashboard#1192" }] },
};

const DIRECTIVE = {
  id: "d1",
  text: "Finish the coordination API first",
  repo: "Runner_Dashboard",
  priority: 1,
  expires: "2099-01-01T00:00:00Z",
  set_by: "dieter",
  set_on: "2026-09-20T00:00:00Z",
};

const SESSIONS = {
  available: true,
  complete: true,
  sessions: [
    {
      session: "s-1",
      agent: "codex",
      repo: "Tools",
      issue: 42,
      branch: "feat/x",
      paths: ["a.py"],
      goals: { api: "ship" },
      expires: "2099-01-01T00:00:00Z",
      source: "board",
    },
    {
      session: "s-2",
      agent: "gemini",
      repo: "Runner_Dashboard",
      issue: 7,
      branch: "fix/y",
      paths: [],
      goals: {},
      expires: "2099-01-01T00:00:00Z",
      source: "board",
    },
  ],
  staff_runs: [
    {
      id: "run-1",
      role: "night-watch",
      provider: "claude",
      machine: "Desk",
      repo: "Tools",
      target: "#42",
      status: "running",
      source: "staff",
    },
  ],
  messages: [],
  conflicts: [],
  warnings: [],
};

const ROSTER = {
  machine: "Desk",
  active_runs: 0,
  providers: { claude: true },
  roles: [
    {
      name: "night-watch",
      title: "Night Watch",
      summary: "",
      playbook: "",
      providers: ["claude"],
      model: null,
      schedule: null,
      window: null,
      repos: [],
      budget: { usd_per_run: null, usd_per_day: null },
      permissions: {},
      reports_to: null,
      holds: [],
      surface: null,
      retired: false,
      dispatchable: true,
      source_path: "",
      active_runs: 0,
    },
  ],
};

type Reply = { status: number; body: unknown } | undefined;
type Handler = (url: string, opts?: RequestInit) => Reply;

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) });
}

/** fetch stub routed by URL; unknown routes 404 so orthogonal panels degrade. */
function stubFetch(extra: Handler = () => undefined) {
  const fetchMock = vi.fn((url: string, opts?: RequestInit) => {
    const custom = extra(url, opts);
    if (custom) return jsonResponse(custom.status, custom.body);
    if (url === "/api/priorities") return jsonResponse(200, PRIORITIES);
    if (url === "/api/priorities/meetings") return jsonResponse(200, MEETINGS);
    if (url === "/api/priorities/meetings/2026-09-14") return jsonResponse(200, OLD_MEETING);
    if (url === "/api/priorities/directives") return jsonResponse(200, { directives: [DIRECTIVE] });
    if (url === "/api/coordination/sessions") return jsonResponse(200, SESSIONS);
    if (url === "/api/staff/roster" || url === "/api/v1/staff/roster") return jsonResponse(200, ROSTER);
    return jsonResponse(404, { detail: "Not Found" });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function writes(fetchMock: ReturnType<typeof vi.fn>, method: string) {
  return fetchMock.mock.calls.filter(([, o]) => (o as RequestInit | undefined)?.method === method) as [
    string,
    RequestInit,
  ][];
}

function headerOf(opts: RequestInit, name: string): string | undefined {
  return (opts.headers as Record<string, string>)[name];
}

function openSection(name: string) {
  fireEvent.click(screen.getByRole("tab", { name }));
}

describe("FleetCommandPage — priorities", () => {
  it("shows the latest board meeting with tracking links, deferred items and disagreements", async () => {
    stubFetch();
    render(<FleetCommandPage />);
    await waitFor(() => expect(screen.getByTestId("priorities-date")).toHaveTextContent("2026-09-21"));
    const table = screen.getByTestId("priorities-active");
    expect(within(table).getByText("Coordination API")).toBeInTheDocument();
    expect(within(table).getByText("claude")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "#1229" })).toHaveAttribute(
      "href",
      "https://github.com/D-sorganization/Runner_Dashboard/issues/1229",
    );
    expect(screen.getByTestId("priorities-deferred")).toHaveTextContent("Mobile polish");
    expect(screen.getByTestId("priorities-disagreements")).toHaveTextContent("1 disagreement flag");
    expect(screen.getByTestId("priorities-disagreements")).toHaveTextContent("Codex wants the glass model first");
  });

  it("shows the no-board-meeting empty state when the backend reports none", async () => {
    stubFetch((url) =>
      url === "/api/priorities"
        ? {
            status: 200,
            body: { available: false, reason: "no board meeting has a consensus.md yet", board: null, directives: [] },
          }
        : undefined,
    );
    render(<FleetCommandPage />);
    await waitFor(() => expect(screen.getByTestId("priorities-empty")).toBeInTheDocument());
    expect(screen.getByText("No board meeting yet")).toBeInTheDocument();
    expect(screen.getByTestId("priorities-empty")).toHaveTextContent("no board meeting has a consensus.md yet");
    // Directives are orthogonal and still load.
    await waitFor(() => expect(screen.getByTestId("directive-0")).toBeInTheDocument());
  });

  it("meeting history selector loads an older meeting's consensus", async () => {
    const fetchMock = stubFetch();
    render(<FleetCommandPage />);
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Board meeting" })).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox", { name: "Board meeting" }), { target: { value: "2026-09-14" } });
    await waitFor(() => expect(screen.getByText("Staff Hub")).toBeInTheDocument());
    expect(screen.getByTestId("priorities-date")).toHaveTextContent("2026-09-14");
    expect(screen.getByRole("link", { name: "Runner_Dashboard#1192" })).toHaveAttribute(
      "href",
      "https://github.com/D-sorganization/Runner_Dashboard/issues/1192",
    );
    expect(fetchMock.mock.calls.some(([u]) => u === "/api/priorities/meetings/2026-09-14")).toBe(true);
  });

  it("renders 'not available on this node' when the priorities routes 404", async () => {
    stubFetch((url) =>
      url.startsWith("/api/priorities") ? { status: 404, body: { detail: "Not Found" } } : undefined,
    );
    render(<FleetCommandPage />);
    await waitFor(() => expect(screen.getByTestId("fleet-priorities-unavailable")).toBeInTheDocument());
    expect(screen.getByTestId("fleet-directives-unavailable")).toBeInTheDocument();
  });
});

describe("FleetCommandPage — directives", () => {
  it("edits, adds and expires directives, then PUTs the list with the CSRF header", async () => {
    const fetchMock = stubFetch((url, opts) =>
      url === "/api/priorities/directives" && opts?.method === "PUT"
        ? { status: 200, body: JSON.parse(String(opts.body)) }
        : undefined,
    );
    render(<FleetCommandPage />);
    await waitFor(() => expect(screen.getByTestId("directive-0")).toBeInTheDocument());
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByRole("combobox", { name: "Directive 1 priority" }), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Add directive" }));
    expect(save).toBeDisabled(); // the new row has no text yet
    fireEvent.change(screen.getByRole("textbox", { name: "Directive 2 text" }), {
      target: { value: "No bulk queue cancels" },
    });
    fireEvent.change(screen.getByLabelText("Directive 2 expiry"), { target: { value: "2026-10-01" } });
    fireEvent.click(save);

    await waitFor(() => expect(writes(fetchMock, "PUT")).toHaveLength(1));
    const [url, opts] = writes(fetchMock, "PUT")[0];
    expect(url).toBe("/api/priorities/directives");
    expect(headerOf(opts, "X-Requested-With")).toBe("XMLHttpRequest");
    const body = JSON.parse(String(opts.body));
    expect(body.directives).toEqual([
      { ...DIRECTIVE, set_by: undefined, priority: 2 }, // set_by is server-assigned (#1243)
      { text: "No bulk queue cancels", repo: "*", priority: 3, expires: "2026-10-01T23:59:59Z" },
    ]);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved 2 active directives."));

    fireEvent.click(screen.getByRole("button", { name: "Expire directive 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes(fetchMock, "PUT")).toHaveLength(2));
    const second = JSON.parse(String(writes(fetchMock, "PUT")[1][1].body));
    expect(second.directives.map((d: { text: string }) => d.text)).toEqual(["No bulk queue cancels"]);
  });
});

describe("FleetCommandPage — active work", () => {
  it("merges board sessions and staff runs, highlights conflicts and filters by repo", async () => {
    stubFetch();
    render(<FleetCommandPage />);
    openSection("Active work");
    await waitFor(() => expect(screen.getByTestId("active-work-table")).toBeInTheDocument());
    const codex = screen.getByTestId("work-board:s-1");
    expect(codex).toHaveTextContent("codex");
    expect(codex).toHaveTextContent("feat/x");
    expect(codex).toHaveTextContent("api: ship");
    expect(within(codex).getByRole("link", { name: "#42" })).toHaveAttribute(
      "href",
      "https://github.com/D-sorganization/Tools/issues/42",
    );
    const run = screen.getByTestId("work-staff:run-1");
    expect(run).toHaveTextContent("night-watch");
    expect(run).toHaveTextContent("claude @ Desk");
    expect(within(run).getByRole("link", { name: "Open run" })).toHaveAttribute("href", "/t/staff?run=run-1");

    // Same repo + same issue → both rows flagged.
    expect(codex).toHaveClass("fleet-cmd__conflict");
    expect(screen.getByTestId("conflict-board:s-1")).toHaveTextContent("same issue #42 as night-watch");
    expect(screen.getByTestId("active-work-conflicts")).toHaveTextContent("2 in conflict");
    expect(screen.getByTestId("work-board:s-2")).not.toHaveClass("fleet-cmd__conflict");

    fireEvent.change(screen.getByRole("combobox", { name: "Filter by repo" }), {
      target: { value: "Runner_Dashboard" },
    });
    expect(screen.queryByTestId("work-board:s-1")).not.toBeInTheDocument();
    expect(screen.getByTestId("work-board:s-2")).toBeInTheDocument();
    expect(screen.queryByTestId("active-work-conflicts")).not.toBeInTheDocument();
  });

  it("shows 'not available on this node' when coordination is unavailable", async () => {
    stubFetch((url) =>
      url === "/api/coordination/sessions"
        ? { status: 200, body: { available: false, reason: "STAFF_RM_ROOT is not configured" } }
        : undefined,
    );
    render(<FleetCommandPage />);
    openSection("Active work");
    await waitFor(() => expect(screen.getByTestId("fleet-active-work-unavailable")).toBeInTheDocument());
    expect(screen.getByTestId("fleet-active-work-unavailable")).toHaveTextContent("STAFF_RM_ROOT is not configured");
  });
});

describe("FleetCommandPage — messages", () => {
  it("prefills from Active work and sends with the CSRF header", async () => {
    const fetchMock = stubFetch((url, opts) =>
      url.startsWith("/api/coordination/") && opts?.method === "POST" ? { status: 200, body: { ok: true } } : undefined,
    );
    render(<FleetCommandPage />);
    openSection("Active work");
    await waitFor(() => expect(screen.getByRole("button", { name: "Message s-2" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Message s-2" }));

    expect(screen.getByLabelText("To")).toHaveValue("s-2");
    expect(screen.getByLabelText("Repo")).toHaveValue("Runner_Dashboard");
    fireEvent.change(screen.getByLabelText("Issue"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Please rebase on main" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(writes(fetchMock, "POST")).toHaveLength(2));
    expect(writes(fetchMock, "POST")[0][0]).toBe("/api/coordination/presence");
    const [url, opts] = writes(fetchMock, "POST")[1];
    expect(url).toBe("/api/coordination/messages");
    expect(headerOf(opts, "X-Requested-With")).toBe("XMLHttpRequest");
    expect(JSON.parse(String(opts.body))).toEqual({
      session: operatorSession(),
      repo: "Runner_Dashboard",
      to: "s-2",
      text: "Please rebase on main",
    });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Sent to s-2."));
  });

  it("shows a session's inbox with messages and conflicts as plain text", async () => {
    stubFetch((url) =>
      url === "/api/coordination/inbox?session=s-1&repo=Tools"
        ? {
            status: 200,
            body: {
              available: true,
              complete: true,
              messages: [{ id: "m1", session: "s-9", repo: "Tools", recipient: "*", text: "<b>heads up</b>", at: "t" }],
              conflicts: [{ session: "s-3", agent: "grok", issue: 5, branch: "b", paths: ["a.py ↔ a.py"], goals: [] }],
            },
          }
        : undefined,
    );
    render(<FleetCommandPage />);
    openSection("Messages");
    fireEvent.change(screen.getByRole("textbox", { name: "Inbox session" }), { target: { value: "s-1" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Inbox repo" }), { target: { value: "Tools" } });
    fireEvent.click(screen.getByRole("button", { name: "Show inbox" }));
    await waitFor(() => expect(screen.getByTestId("message-m1")).toBeInTheDocument());
    expect(screen.getByTestId("message-m1")).toHaveTextContent("<b>heads up</b>");
    expect(screen.getByTestId("message-m1")).toHaveTextContent("everyone in Tools");
    expect(screen.getByTestId("messages-inbox")).toHaveTextContent("grok");
  });
});

describe("FleetCommandPage — claims", () => {
  it("checks a claim, surfaces a 409 holder and releases", async () => {
    const fetchMock = stubFetch((url, opts) => {
      if (url === "/api/coordination/claims?repo=Tools&issue=42") {
        return {
          status: 200,
          body: {
            available: true,
            held: true,
            agent: "codex",
            reason: "lease active",
            expires_at: "2099-01-01T00:00:00Z",
          },
        };
      }
      if (url === "/api/coordination/claims" && opts?.method === "POST") {
        return {
          status: 409,
          body: { detail: { error: "issue is claimed", held_by: "codex", guidance: "pick another issue" } },
        };
      }
      if (url === "/api/coordination/claims/release") return { status: 200, body: { ok: true } };
      return undefined;
    });
    render(<FleetCommandPage />);
    openSection("Claims");
    fireEvent.change(screen.getByRole("textbox", { name: "Claim repo" }), { target: { value: "Tools" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Claim issue" }), { target: { value: "#42" } });
    fireEvent.click(screen.getByRole("button", { name: "Check claim" }));
    await waitFor(() => expect(screen.getByTestId("claim-status")).toHaveTextContent("claimed by codex"));

    fireEvent.click(screen.getByRole("button", { name: "Claim" }));
    await waitFor(() =>
      expect(screen.getByTestId("claim-error")).toHaveTextContent(
        "issue is claimed — held by codex — pick another issue",
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "Release" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Released Tools#42."));
    const release = writes(fetchMock, "POST").find(([u]) => u === "/api/coordination/claims/release");
    expect(release).toBeDefined();
    expect(headerOf(release![1], "X-Requested-With")).toBe("XMLHttpRequest");
    expect(JSON.parse(String(release![1].body))).toEqual({
      repo: "Tools",
      issue: 42,
      session: operatorSession(),
      reason: "work completed",
    });
  });
});

describe("FleetCommandPage — dispatch", () => {
  it("previews with dry_run then dispatches and links to the Staff tab run", async () => {
    const fetchMock = stubFetch((url, opts) => {
      if (url !== "/api/staff/night-watch/run" && url !== "/api/v1/staff/night-watch/run") return undefined;
      const body = JSON.parse(String(opts?.body));
      return body.dry_run
        ? {
            status: 200,
            body: {
              dry_run: true,
              machine: "local",
              plan: {
                role: "night-watch",
                provider: "claude",
                model: null,
                repo: "Tools",
                target_kind: "issue",
                target_ref: "42",
                prompt: "p",
                argv: ["claude"],
                branch: "staff/nw-42",
                lease_ritual: true,
              },
            },
          }
        : { status: 200, body: { dry_run: false, machine: "local", run: { id: "run-77" } } };
    });
    render(<FleetCommandPage />);
    openSection("Dispatch");
    await waitFor(() => expect(screen.getByLabelText("Role")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Repo"), { target: { value: "Tools" } });
    fireEvent.change(screen.getByLabelText("Issue #"), { target: { value: "42" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(screen.getByTestId("assign-plan")).toBeInTheDocument());
    expect(screen.getByTestId("plan-branch")).toHaveTextContent("staff/nw-42");

    fireEvent.click(screen.getByRole("button", { name: "Dispatch" }));
    await waitFor(() => expect(screen.getByTestId("dispatch-run-link")).toHaveAttribute("href", "/t/staff?run=run-77"));
    const posts = writes(fetchMock, "POST").map(([, o]) => JSON.parse(String(o.body)).dry_run);
    expect(posts).toEqual([true, false]);
  });

  it("shows 'not available' when the Staff Hub is absent", async () => {
    stubFetch((url) => (url === "/api/staff/roster" || url === "/api/v1/staff/roster" ? { status: 404, body: { detail: "Not Found" } } : undefined));
    render(<FleetCommandPage />);
    openSection("Dispatch");
    await waitFor(() => expect(screen.getByTestId("fleet-dispatch-unavailable")).toBeInTheDocument());
  });
});
