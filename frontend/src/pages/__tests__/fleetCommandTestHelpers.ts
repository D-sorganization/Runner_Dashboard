/**
 * Shared fixtures and test helpers for Fleet Command page tests.
 */
import { fireEvent, screen } from "@testing-library/react";
import { vi } from "vitest";

export const BOARD = {
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

export const PRIORITIES = { available: true, board: BOARD, directives: [], portfolios: [], generated_at: "x" };
export const MEETINGS = {
  available: true,
  meetings: [
    { date: "2026-09-21", files: ["consensus.md"], has_consensus: true },
    { date: "2026-09-14", files: ["consensus.md"], has_consensus: true },
  ],
};
export const OLD_MEETING = {
  available: true,
  date: "2026-09-14",
  consensus: { ...BOARD, active: [{ ...BOARD.active[0], item: "Staff Hub", tracking: "Runner_Dashboard#1192" }] },
};

export const DIRECTIVE = {
  id: "d1",
  text: "Finish the coordination API first",
  repo: "Runner_Dashboard",
  priority: 1,
  expires: "2099-01-01T00:00:00Z",
  set_by: "dieter",
  set_on: "2026-09-20T00:00:00Z",
};

export const SESSIONS = {
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

export const ROSTER = {
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

export type Reply = { status: number; body: unknown } | undefined;
export type Handler = (url: string, opts?: RequestInit) => Reply;

export function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) });
}

/** fetch stub routed by URL; unknown routes 404 so orthogonal panels degrade. */
export function stubFetch(extra: Handler = () => undefined) {
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

export function writes(fetchMock: ReturnType<typeof vi.fn>, method: string) {
  return fetchMock.mock.calls.filter(([, o]) => (o as RequestInit | undefined)?.method === method) as [
    string,
    RequestInit,
  ][];
}

export function headerOf(opts: RequestInit, name: string): string | undefined {
  return (opts.headers as Record<string, string>)[name];
}

export function openSection(name: string) {
  fireEvent.click(screen.getByRole("tab", { name }));
}
