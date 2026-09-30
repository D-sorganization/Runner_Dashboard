// @vitest-environment jsdom
/**
 * roleReadiness.test.tsx — the Staff Console never invents role readiness (#1804, BR-11).
 *
 * These tests mount the production components (desktop and mobile) with no seeded
 * data and a stubbed `fetch`, so the roster, providers, schedule, board, runs and
 * work-item queries all go through the real hooks and API client.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StaffConsoleDesktop } from "../Desktop";
import { StaffConsoleMobile } from "../Mobile";
import { ContextPane } from "../ContextPane";
import type { StaffRoleItem } from "../types";

const ROLES: StaffRoleItem[] = [
  { name: "barb", title: "Barb", group: "leadership", valid: true, providers: ["claude"] },
  {
    name: "night-watch",
    title: "Night Watch",
    summary: "Sweeps overnight",
    group: "operations",
    valid: true,
    providers: ["claude"],
    schedule: "0 22 * * *",
    active_runs: 0,
  },
  { name: "codex-only", title: "Codex Only", group: "specialists", valid: true, providers: ["codex"] },
  { name: "held-role", title: "Held Role", group: "specialists", valid: true, providers: ["claude"], schedule: "0 6 * * *" },
  { name: "gemini-role", title: "Gemini Role", group: "specialists", valid: true, providers: ["gemini"] },
];

const THREAD = {
  id: "thr_nw",
  title: "Conversation with Night Watch",
  kind: "direct",
  participants: ["user:me", "night-watch"],
  status: "active",
};

interface FetchScenario {
  providers?: Record<string, boolean> | "error";
  schedule?: "error";
  putStatus?: number;
}

type Call = { url: string; method: string; body?: unknown };

function installFetch(scenario: FetchScenario = {}) {
  const calls: Call[] = [];
  let nightWatchEnabled = true;
  const reply = (body: unknown, status = 200) =>
    Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => body });
  const fetchMock = vi.fn((input: unknown, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ url, method, body });
    const path = url.split("?")[0];
    if (path.endsWith("/api/v1/staff/roster")) return reply({ machine: "hub", roles: ROLES, active_runs: 1 });
    if (path.endsWith("/api/v1/staff/providers")) {
      if (scenario.providers === "error") return reply({ detail: "providers probe failed" }, 503);
      return reply({ providers: scenario.providers ?? { claude: true, codex: false } });
    }
    if (path.endsWith("/api/v1/staff/schedule")) {
      if (scenario.schedule === "error") return reply({ detail: "scheduler offline" }, 503);
      return reply({
        machine: "hub",
        generated_at: "2026-09-29T10:00:00Z",
        enabled: true,
        running: true,
        roles: [
          { role: "night-watch", schedule: "0 22 * * *", enabled: nightWatchEnabled, hold: null, next_fire: "2026-09-29T22:00:00Z" },
          { role: "held-role", schedule: "0 6 * * *", enabled: true, hold: "release freeze", next_fire: "2026-09-30T06:00:00Z" },
        ],
      });
    }
    if (path.endsWith("/api/v1/staff/board")) return reply({ machine: "hub", online: ["hub"], offline: ["LaptopNode"] });
    if (path.endsWith("/api/v1/staff/runs")) {
      return reply({
        count: 1,
        runs: [{ id: "run-7", role: "night-watch", status: "running", repo: "UpstreamDrift", thread_id: "thr_nw" }],
      });
    }
    if (path.endsWith("/api/v1/staff/work-items")) {
      if (url.includes("thread_id=thr_nw")) {
        return reply({ work_items: [{ id: "wi-2", title: "Thread follow-up", state: "open" }] });
      }
      if (url.includes("owner_role=night-watch")) {
        return reply({ work_items: [{ id: "wi-1", title: "Fix red CI", state: "in_progress" }] });
      }
      return reply({ work_items: [] });
    }
    if (path.endsWith("/api/v1/staff/threads")) return reply({ threads: [THREAD] });
    if (path.endsWith("/api/v1/staff/threads/thr_nw")) return reply({ thread: THREAD, messages: [] });
    if (path.endsWith("/api/v1/staff/roles/night-watch/schedule") && method === "PUT") {
      const status = scenario.putStatus ?? 200;
      if (status !== 200) return reply({ detail: "missing scope staff.holds.write" }, status);
      nightWatchEnabled = Boolean((body as { enabled: boolean }).enabled);
      return reply({ role: "night-watch", enabled: nightWatchEnabled });
    }
    return reply({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function withQueryClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  window.history.replaceState(null, "", "/staff");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function openNightWatchDesktop() {
  fireEvent.click(await screen.findByTestId("roster-row-night-watch"));
  await screen.findByRole("heading", { name: "Conversation with Night Watch" });
}

describe("Desktop roster readiness from the mounted data path (#1804)", () => {
  it("renders ready, unavailable, held and unknown roles distinctly", async () => {
    installFetch();
    withQueryClient(<StaffConsoleDesktop />);

    await waitFor(() => expect(screen.getByTestId("status-dot-night-watch")).toHaveAttribute("data-status", "idle"));
    expect(screen.getByTestId("status-dot-codex-only")).toHaveAttribute("data-status", "unavailable");
    expect(screen.getByTestId("status-reason-codex-only")).toHaveTextContent("no provider installed");
    await waitFor(() => expect(screen.getByTestId("status-dot-held-role")).toHaveAttribute("data-status", "held"));
    expect(screen.getByTestId("status-reason-held-role")).toHaveTextContent("release freeze");
    expect(screen.getByTestId("status-dot-gemini-role")).toHaveAttribute("data-status", "unknown");
  });

  it("never shows Idle when provider availability is unavailable", async () => {
    installFetch({ providers: "error" });
    withQueryClient(<StaffConsoleDesktop />);

    await screen.findByTestId("status-dot-night-watch");
    await waitFor(() => expect(screen.getByTestId("status-dot-night-watch")).toHaveAttribute("data-status", "unknown"));
    expect(screen.queryAllByTestId(/^status-dot-/).some((d) => d.getAttribute("data-status") === "idle")).toBe(false);
  });

  it("does not invent readiness for seeded roles before availability loads", () => {
    installFetch();
    withQueryClient(<StaffConsoleDesktop roles={ROLES} />);

    expect(screen.getByTestId("status-dot-night-watch")).toHaveAttribute("data-status", "unknown");
  });
});

describe("Desktop context pane from real data (#1804)", () => {
  it("shows provider readiness without claiming sign-in, offline nodes, runs and work items", async () => {
    installFetch();
    withQueryClient(<StaffConsoleDesktop />);
    await openNightWatchDesktop();

    const pane = screen.getByRole("complementary", { name: "Context pane" });
    await waitFor(() => expect(within(pane).getByTestId("context-provider-claude")).toHaveAttribute("data-readiness", "installed"));
    expect(pane).not.toHaveTextContent(/signed in/i);
    expect(within(pane).getByTestId("context-offline-nodes")).toHaveTextContent("LaptopNode");
    await within(pane).findByText("run-7");
    await within(pane).findByText("Fix red CI");

    fireEvent.click(within(pane).getByRole("tab", { name: "Thread" }));
    await within(pane).findByText("Thread follow-up");
    expect(within(pane).getByTestId("context-linked-runs")).toHaveTextContent("run-7");
  });

  it("persists a schedule change through the role schedule API", async () => {
    const calls = installFetch();
    withQueryClient(<StaffConsoleDesktop />);
    await openNightWatchDesktop();

    const toggle = await screen.findByRole("switch", { name: "Toggle schedule" });
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).toHaveAttribute("aria-checked", "true");
    fireEvent.click(toggle);

    await waitFor(() =>
      expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/api/v1/staff/roles/night-watch/schedule"))).toBe(true),
    );
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.body).toMatchObject({ enabled: false });
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "false"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("rolls the switch back and explains a refused schedule change", async () => {
    installFetch({ putStatus: 403 });
    withQueryClient(<StaffConsoleDesktop />);
    await openNightWatchDesktop();

    const toggle = await screen.findByRole("switch", { name: "Toggle schedule" });
    await waitFor(() => expect(toggle).toBeEnabled());
    fireEvent.click(toggle);

    expect(await screen.findByText(/missing scope staff\.holds\.write/)).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-checked", "true");
  });

  it("disables the switch with a reason when the schedule source is unavailable", async () => {
    installFetch({ schedule: "error" });
    withQueryClient(<StaffConsoleDesktop />);
    await openNightWatchDesktop();

    const toggle = await screen.findByRole("switch", { name: "Toggle schedule" });
    await waitFor(() => expect(screen.getByTestId("context-schedule-reason")).toHaveTextContent(/unavailable/i));
    expect(toggle).toBeDisabled();
    expect(screen.queryByText("Active")).not.toBeInTheDocument();
  });
});

describe("Mobile context drawer from real data (#1804)", () => {
  it("populates runs and work items for the open role", async () => {
    installFetch();
    withQueryClient(<StaffConsoleMobile />);

    fireEvent.click(await screen.findByTestId("staff-mobile-role-night-watch"));
    fireEvent.click(await screen.findByRole("button", { name: "Role Details" }));

    const drawer = await screen.findByTestId("staff-mobile-context-drawer");
    await within(drawer).findByText("run-7");
    await within(drawer).findByText("Fix red CI");
    await waitFor(() => expect(within(drawer).getByTestId("context-provider-claude")).toHaveAttribute("data-readiness", "installed"));
  });
});

describe("ContextPane schedule switch without a handler (#1804)", () => {
  it("renders read-only with a reason instead of an enabled inert switch", () => {
    render(
      <ContextPane
        role={{
          name: "night-watch",
          title: "Night Watch",
          mandate: "",
          providers: [],
          schedule: { cron: "0 22 * * *", enabled: true },
        }}
      />,
    );

    expect(screen.getByRole("switch", { name: "Toggle schedule" })).toBeDisabled();
    expect(screen.getByTestId("context-schedule-reason")).toHaveTextContent(/read-only/i);
  });
});
