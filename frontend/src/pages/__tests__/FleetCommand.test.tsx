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
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FleetCommandPage } from "../FleetCommand";
import { operatorSession } from "../FleetCommand/fleetApi";
import {
  DIRECTIVE,
  headerOf,
  openSection,
  stubFetch,
  writes,
} from "./fleetCommandTestHelpers";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

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
    expect(within(run).getByRole("link", { name: "Open run" })).toHaveAttribute("href", "/?run=run-1");

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
