// @vitest-environment jsdom
/**
 * #4276 (WP20) call-site tests for the dispatch form: G1-04 (one idempotency key per
 * intent) and G1-12 (no stale preview beside a live Dispatch button).
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AdvancedDispatchForm } from "../AdvancedDispatchForm";
import type { RosterResponse } from "../staffApi";

const REQUESTS = "/api/v1/staff/requests";

const ROSTER = {
  machine: "hub-a",
  active_runs: 0,
  providers: { claude: true, codex: true },
  roles: [
    {
      name: "night-watch",
      title: "Night Watch",
      providers: ["claude", "codex"],
      retired: false,
      dispatchable: true,
    },
  ],
} as unknown as RosterResponse;

const PLANNED = {
  state: "planned",
  kind: "staff.dispatch",
  action: "staff.dispatch",
  plan: {
    dry_run: true,
    machine: "hub-a",
    forwarded_to: null,
    plan: {
      role: "night-watch",
      provider: "claude",
      model: null,
      repo: "Tools",
      target_kind: "issue",
      target_ref: "7",
      prompt: "Fix #7.",
      argv: ["claude", "-p"],
      branch: "staff/night-watch-7",
      lease_ritual: true,
      consolidation: { mode: "serial", reason: "few PRs", threshold: { open_prs: 6 } },
    },
  },
};

type Reply = { status: number; body: unknown };
type Sent = { key: string; body: { dry_run: boolean; prompt: string } };

function response(reply: Reply) {
  return { ok: reply.status < 300, status: reply.status, json: () => Promise.resolve(reply.body) };
}

/** Stub `fetch`; each POST to the requests endpoint is answered by `next` and recorded. */
function stubRequests(next: (sent: Sent) => Reply | Promise<Reply>): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(REQUESTS);
      const headers = init?.headers as Record<string, string>;
      const record = { key: headers["Idempotency-Key"], body: JSON.parse(String(init?.body)) };
      sent.push(record);
      return response(await next(record));
    }),
  );
  return sent;
}

function renderForm(onDispatched = vi.fn()) {
  render(<AdvancedDispatchForm roster={ROSTER} onDispatched={onDispatched} />);
  fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "fix it" } });
  return onDispatched;
}

const dispatchButton = () => screen.getByRole("button", { name: /^dispatch/i });
const SERVER_ERROR: Reply = { status: 500, body: { detail: "boom" } };
const EXECUTED: Reply = { status: 201, body: { state: "executed", kind: "staff.dispatch", run_id: "run-1" } };

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AdvancedDispatchForm dispatch (useSubmit, #4276)", () => {
  it("double-click sends one request and disables the button while it is in flight", async () => {
    let finish!: (reply: Reply) => void;
    const sent = stubRequests(() => new Promise<Reply>((resolve) => (finish = resolve)));
    const onDispatched = renderForm();

    fireEvent.click(dispatchButton());
    fireEvent.click(dispatchButton());

    await waitFor(() => expect(dispatchButton()).toBeDisabled());
    expect(dispatchButton()).toHaveTextContent("Dispatching…");
    expect(sent).toHaveLength(1);

    finish(EXECUTED);
    await waitFor(() => expect(onDispatched).toHaveBeenCalledWith("run-1"));
    expect(sent).toHaveLength(1);
  });

  it("HTTP 500 renders failure text and starts no run", async () => {
    stubRequests(() => SERVER_ERROR);
    const onDispatched = renderForm();

    fireEvent.click(dispatchButton());

    await waitFor(() => expect(screen.getByTestId("dispatch-error")).toHaveTextContent("boom"));
    expect(onDispatched).not.toHaveBeenCalled();
    expect(dispatchButton()).toBeEnabled();
  });

  it("retrying the same request reuses its idempotency key", async () => {
    const sent = stubRequests(() => (sent.length === 1 ? SERVER_ERROR : EXECUTED));
    const onDispatched = renderForm();

    fireEvent.click(dispatchButton());
    await waitFor(() => expect(screen.getByTestId("dispatch-error")).toBeInTheDocument());
    fireEvent.click(dispatchButton());

    await waitFor(() => expect(onDispatched).toHaveBeenCalledWith("run-1"));
    expect(sent).toHaveLength(2);
    expect(sent[0].key).toBeTruthy();
    expect(sent[1].key).toBe(sent[0].key);
  });

  it("an edited request after a failure is a new intent with a new key", async () => {
    const sent = stubRequests(() => (sent.length === 1 ? SERVER_ERROR : EXECUTED));
    const onDispatched = renderForm();

    fireEvent.click(dispatchButton());
    await waitFor(() => expect(screen.getByTestId("dispatch-error")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "fix something else" } });
    fireEvent.click(dispatchButton());

    await waitFor(() => expect(onDispatched).toHaveBeenCalledWith("run-1"));
    expect(sent[1].body.prompt).toBe("fix something else");
    expect(sent[1].key).not.toBe(sent[0].key);
  });
});

describe("AdvancedDispatchForm preview stays in step with the form (G1-12, #4276)", () => {
  async function previewed() {
    stubRequests(() => ({ status: 200, body: PLANNED }));
    renderForm();
    fireEvent.click(screen.getByRole("button", { name: /^preview$/i }));
    await waitFor(() => expect(screen.getByTestId("assign-plan")).toBeInTheDocument());
  }

  it.each([
    ["Provider", "codex"],
    ["Model", "some-model"],
    ["Machine", "node-b"],
  ])("changing %s removes the previewed plan", async (label, value) => {
    await previewed();

    fireEvent.change(screen.getByLabelText(label), { target: { value } });

    expect(screen.queryByTestId("assign-plan")).not.toBeInTheDocument();
  });
});
