// @vitest-environment jsdom
/**
 * Behaviour tests for pages/FleetCommand coordination panels (#1233, epic #1192).
 *
 * 4. Messages: Active work "Message" prefills the form; send POSTs with CSRF; inbox renders.
 * 5. Claims: check shows the holder; a 409 claim shows held_by; release POSTs.
 * 6. Dispatch: reuses Staff Assign (dry-run preview, then run) and links to the Staff tab run.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FleetCommandPage } from "../FleetCommand";
import { operatorSession } from "../FleetCommand/fleetApi";
import { stubFetch, writes, headerOf, openSection } from "./fleetCommandFixtures";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
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
    await waitFor(() => expect(screen.getByTestId("dispatch-run-link")).toHaveAttribute("href", "/?run=run-77"));
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
