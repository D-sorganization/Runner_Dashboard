import React from "react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
const { legacyFetchMock } = vi.hoisted(() => ({ legacyFetchMock: vi.fn() }));
vi.mock("../../../lib/api", () => ({ legacyFetch: legacyFetchMock }));
vi.mock("../AgentsSyncPanel", () => ({ AgentsSyncPanel: () => <section><h2>Repository AGENTS.md Sync</h2></section> }));
beforeEach(() => legacyFetchMock.mockReset());

import { ContextPage, type ContextPackInfo, type ContextLesson, type ContextRunRecord } from "../ContextPage";

const samplePack: ContextPackInfo = {
  version: "1.1.0",
  digest: "mock-digest-bcc29c1",
  pin: "fleet-standards@1.1.0#mock-digest-bcc29c1",
  source_path: "staff/standards/FLEET.md",
  is_fallback: false,
};

const sampleSkills = [{ name: "fleet-meaningful-tests", version: "1.0.0", pin: "fleet-meaningful-tests@1.0.0+87ba571a0adb", content_hash: "87ba571a0adb", source_path: "skills/fleet-meaningful-tests/SKILL.md" }];

const sampleLessons: ContextLesson[] = [
  { id: "mem_01", repo: "Runner_Dashboard", review_status: "proposed", author_principal: "staff:fleet-swe:run-101", source_run: "run-101", created_at: "2026-10-22T20:00:00Z", expires_at: "2026-11-21T20:00:00Z", body: { summary: "Use pytest fixtures for db isolation" } },
  { id: "mem_02", repo: "Runner_Dashboard", review_status: "accepted", author_principal: "staff:librarian:run-102", source_run: "run-102", created_at: "2026-10-20T10:00:00Z", expires_at: "2026-11-19T10:00:00Z", body: { summary: "Deterministic sorting on lesson ids" } },
  { id: "mem_03", repo: "Repository_Management", review_status: "quarantined", author_principal: "staff:reviewer:run-103", source_run: "run-103", created_at: "2026-10-15T08:00:00Z", expires_at: "2026-11-14T08:00:00Z", body: { summary: "Conflicting rule on reviewer assignment" }, quarantine_reason: "Direct contradiction with FLEET.md" },
  { id: "mem_04_expired", repo: "Runner_Dashboard", review_status: "retired", author_principal: "staff:swe:run-99", source_run: "run-99", created_at: "2026-08-01T00:00:00Z", expires_at: "2026-08-31T00:00:00Z", body: { summary: "Legacy test harness argument" } },
  { id: "mem_05_barb", repo: "Runner_Dashboard", review_status: "accepted", visibility: "barb-only", author_principal: "barb:internal", source_run: "run-barb", created_at: "2026-10-22T00:00:00Z", expires_at: "2026-11-21T00:00:00Z", body: { summary: "Internal Barb credential hint" } },
];

const sampleRuns: ContextRunRecord[] = [
  {
    id: "run-201", role: "fleet-swe", repo: "Runner_Dashboard", status: "succeeded", created_at: "2026-10-22T21:00:00Z",
    standards_version: "1.1.0", skill_pin: "fleet-meaningful-tests@1.0.0+87ba571a0adb", standards_tokens: 720, lessons_tokens: 240, input_tokens: 4500, output_tokens: 800, cost_usd: 0.045,
    context_stamp: JSON.stringify({
      pack_version: "1.1.0", content_digest: "mock-digest-bcc29c1", requested_pack: "fleet-standards@1.1.0", resolved_pack: "fleet-standards@1.1.0#mock-digest-bcc29c1",
      fallback_status: "ok", token_estimates: { tier0_standards: 720, tier1_skills: 350, tier1_lessons: 240, total_injected: 3733 },
      lesson_ids: ["mem_02"], lessons_skipped: [{ id: "mem_01", reason: "conflict" }],
    }),
  },
  {
    id: "run-202-bloat", role: "researcher", repo: "Repository_Management", status: "failed", created_at: "2026-10-22T20:30:00Z",
    standards_version: "1.0.0", standards_tokens: 1550, lessons_tokens: 700, input_tokens: 8200, output_tokens: 1200, cost_usd: 0.092,
    context_stamp: JSON.stringify({
      pack_version: "1.0.0", content_digest: "mock-digest-aaa111", requested_pack: "fleet-standards@1.0.0", resolved_pack: "fleet-standards@1.0.0#mock-digest-aaa111",
      fallback_status: "ok", token_estimates: { tier0_standards: 1550, tier1_skills: 200, tier1_lessons: 700, total_injected: 2450 },
      lesson_ids: [], lessons_skipped: [{ id: "mem_20", reason: "budget" }],
    }),
  },
];

describe("ContextPage", () => {
  it("announces the Tier-1 registry loading state with clear copy", () => {
    legacyFetchMock.mockImplementation(() => new Promise((resolve) => { setTimeout(() => resolve({ ok: true, status: 200, json: async () => ({ skills: [] }) }), 100); }));
    render(<ContextPage />);

    const loading = screen.getByText(/loading.*skill registry/i);
    expect(loading).toHaveAttribute("role", "status");
    expect(loading).toHaveTextContent(/^Loading Tier-1 skill registry…$/);
  });

  it("renders current pack pin, digest, and normal/fallback indicators", () => {
    render(<ContextPage pack={samplePack} lessons={sampleLessons} runs={sampleRuns} />);
    expect(screen.getByText("fleet-standards@1.1.0#mock-digest-bcc29c1")).toBeInTheDocument();
    expect(screen.getAllByText("1.1.0").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("mock-digest-bcc29c1")).toBeInTheDocument();
    expect(screen.getByText("staff/standards/FLEET.md")).toBeInTheDocument();
    expect(screen.getByText(/2,200 token hard cap/i)).toBeInTheDocument();
  });

  it("flags <builtin> fallback warning when pack is fallback", () => {
    const fallbackPack = { ...samplePack, source_path: "<builtin>", is_fallback: true };
    render(<ContextPage pack={fallbackPack} lessons={[]} runs={[]} />);
    expect(screen.getByText(/builtin fallback warning/i)).toBeInTheDocument();
  });

  it("labels Tier-0 run comparison separately from live AGENTS sync data", () => {
    render(<ContextPage pack={samplePack} lessons={sampleLessons} runs={sampleRuns} />);
    expect(screen.getByRole("heading", { name: "Repository AGENTS.md Sync" })).toBeInTheDocument();
    expect(screen.queryByText(/AGENTS.md sync versions are not exposed/i)).not.toBeInTheDocument();
    expect(screen.getByTestId("repo-status-Runner_Dashboard")).toHaveTextContent(/matches current Tier-0/i);
    expect(screen.getByTestId("repo-status-Repository_Management")).toHaveTextContent(/different Tier-0 version/i);
  });

  it("renders live managed-block AGENTS sync separately from Tier-0 run comparison", () => {
    render(<ContextPage pack={samplePack} lessons={sampleLessons} runs={sampleRuns} />);
    expect(screen.getByRole("heading", { name: "Repository AGENTS.md Sync" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Standards Pack Version at Latest Recorded Run" })).toBeInTheDocument();
    expect(screen.queryByText(/AGENTS.md sync versions are not exposed/i)).not.toBeInTheDocument();
  });

  it("renders lesson queue with provenance, expiration, quarantine, and NEVER shows barb-only entries", () => {
    render(<ContextPage pack={samplePack} lessons={sampleLessons} runs={sampleRuns} />);
    expect(screen.queryByText("Internal Barb credential hint")).not.toBeInTheDocument();
    expect(screen.queryByText("mem_05_barb")).not.toBeInTheDocument();
    expect(screen.getByTestId("count-proposed")).toHaveTextContent("1");
    expect(screen.getByTestId("count-accepted")).toHaveTextContent("1");
    expect(screen.getByTestId("count-quarantined")).toHaveTextContent("1");
    expect(screen.getByTestId("count-retired")).toHaveTextContent("1");
    expect(screen.getByText("Use pytest fixtures for db isolation")).toBeInTheDocument();
    expect(screen.getByText("staff:fleet-swe:run-101")).toBeInTheDocument();
    expect(screen.getByText("run-101")).toBeInTheDocument();
    expect(screen.getByText(/Direct contradiction with FLEET.md/i)).toBeInTheDocument();
    expect(screen.getByText(/expired/i)).toBeInTheDocument();
  });

  it("keeps full-prompt estimates out of the context cap and reports partial coverage", () => {
    render(<ContextPage pack={samplePack} lessons={sampleLessons} runs={sampleRuns} />);
    expect(screen.getByTestId("run-cap-run-201")).toHaveTextContent(/partial/i);
    expect(screen.getByText(/1310 known tokens/i)).toBeInTheDocument();
    expect(screen.getByText(/Full prompt: 3733 \(estimate\)/i)).toBeInTheDocument();
    expect(screen.getByText(/mem_01 \(conflict\)/i)).toBeInTheDocument();
    expect(screen.getByTestId("run-cap-run-202-bloat")).toHaveTextContent(/over-cap/i);
    expect(screen.getByText(/mem_20 \(budget\)/i)).toBeInTheDocument();
    expect(screen.getAllByText(/\(estimate\)/i).length).toBeGreaterThan(0);
  });

  it("shows current Tier-1 registry pins and each run recorded skill pin", () => {
    render(<ContextPage pack={samplePack} skills={sampleSkills} lessons={sampleLessons} runs={sampleRuns} />);
    expect(screen.getByText("Current Tier-1 Skill Packs")).toBeInTheDocument();
    expect(screen.getByText(/current Tier-1 skill registry/i)).toBeInTheDocument();
    expect(screen.queryByText(/Tier-1 pack versions are not reported/i)).not.toBeInTheDocument();
    expect(screen.getByText("fleet-meaningful-tests@1.0.0+87ba571a0adb")).toBeInTheDocument();
    expect(screen.getByText(/Tier-1 skill: fleet-meaningful-tests@1.0.0\+87ba571a0adb/i)).toBeInTheDocument();
  });

  it("reports skill registry failures as unavailable and retries the source", async () => {
    legacyFetchMock.mockResolvedValueOnce({ ok: false, status: 403, json: async () => ({ detail: "Forbidden" }) });
    legacyFetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ skills: sampleSkills }) });
    render(<ContextPage pack={samplePack} lessons={sampleLessons} runs={sampleRuns} />);
    const retry = await screen.findByRole("button", { name: /retry skills/i });
    expect(screen.getByRole("status")).toHaveTextContent(/unavailable.*403/i);
    fireEvent.click(retry);
    expect(await screen.findByText("fleet-meaningful-tests@1.0.0+87ba571a0adb")).toBeInTheDocument();
    expect(legacyFetchMock).toHaveBeenCalledWith("/api/v1/staff/skills");
  });

  it("reports failed lesson telemetry as unavailable instead of an empty queue", async () => {
    legacyFetchMock.mockImplementation((url: string = "") => Promise.resolve(
      url.includes("memory")
        ? { ok: false, status: 403, json: async () => ({ detail: "Forbidden" }) }
        : { ok: true, status: 200, json: async () => url.includes("summary")
          ? { standards_version: "1.1.0", standards_pin: "fleet-standards@1.1.0#digest" }
          : { runs: [] } },
    ));

    render(<ContextPage />);
    expect(await screen.findByText(/lesson review data unavailable/i)).toBeInTheDocument();
    expect(screen.queryByText(/no lessons in review queue/i)).not.toBeInTheDocument();
    const retry = screen.getByRole("button", { name: /retry lesson data/i });
    expect(retry).toBeInTheDocument();
    fireEvent.click(retry);
    await waitFor(() => expect(legacyFetchMock.mock.calls.filter(([url]) => String(url).includes("memory"))).toHaveLength(2));
  });

  it("distinguishes missing per-run token estimates from recorded zero", () => {
    const run: ContextRunRecord = {
      id: "run-legacy", role: "barb", repo: "Runner_Dashboard", status: "succeeded", created_at: "2026-10-23T00:00:00Z",
      context_stamp: JSON.stringify({ token_estimates: { tier0_standards: 100, tier1_lessons: 0 } }),
    };
    render(<ContextPage pack={samplePack} lessons={[]} runs={[run]} />);
    const row = screen.getByRole("row", { name: /run-legacy/i });
    expect(row).toHaveTextContent("100 / Not recorded / 0");
    expect(row).toHaveTextContent("100 known tokens");
    expect(row).toHaveTextContent("Partial");
  });

  it("distinguishes missing usage telemetry from recorded zero values", () => {
    const unknown: ContextRunRecord = {
      id: "run-usage-unknown", role: "barb", repo: "Runner_Dashboard", status: "succeeded", created_at: "2026-10-23T00:00:00Z",
    };
    const recordedZero: ContextRunRecord = {
      ...unknown, id: "run-usage-zero", input_tokens: 0, output_tokens: 0, cost_usd: 0,
    };
    const nullTelemetry: ContextRunRecord = {
      ...unknown, id: "run-usage-null", input_tokens: null, output_tokens: null, cost_usd: null,
    };
    render(<ContextPage pack={samplePack} lessons={[]} runs={[unknown, recordedZero, nullTelemetry]} />);
    expect(screen.getByRole("row", { name: /run-usage-unknown/i })).toHaveTextContent("In: Not recorded / Out: Not recorded");
    expect(screen.getByRole("row", { name: /run-usage-unknown/i })).toHaveTextContent("Cost: Not recorded");
    expect(screen.getByRole("row", { name: /run-usage-zero/i })).toHaveTextContent("In: 0 / Out: 0");
    expect(screen.getByRole("row", { name: /run-usage-zero/i })).toHaveTextContent("Cost: ~$0.000");
    expect(screen.getByRole("row", { name: /run-usage-null/i })).toHaveTextContent("In: Not recorded / Out: Not recorded");
    expect(screen.getByRole("row", { name: /run-usage-null/i })).toHaveTextContent("Cost: Not recorded");
  });

  it("extracts Tier-0 digest from a plus-delimited standards pin", async () => {
    legacyFetchMock.mockImplementation((url: string = "") => Promise.resolve({
      ok: true,
      status: 200,
      json: async () => url.includes("summary")
        ? { standards_version: "1.1.0", standards_pin: "fleet-standards@1.1.0+646df67582c6" }
        : url.includes("skills")
          ? { skills: sampleSkills }
          : url.includes("agents-sync")
            ? { source: "Repository_Management/AGENTS.md", source_ref: "main", status: "current", reason: null, checked_at: "2026-10-23T00:00:00Z", source_digest: "abc", source_blocks: {}, repositories: [] }
            : url.includes("runs") ? { runs: [] } : { entries: [] },
    }));
    render(<ContextPage />);
    expect(await screen.findByText("646df67582c6")).toBeInTheDocument();
  });

  it("marks the page for its scoped responsive layout", () => {
    const { container } = render(<ContextPage pack={samplePack} lessons={[]} runs={[]} />);
    expect(container.querySelector(".context-page")).toBeInTheDocument();
  });

  it("handles empty and error states gracefully", () => {
    const { rerender } = render(<ContextPage pack={samplePack} lessons={[]} runs={[]} />);
    expect(screen.getByText(/no lessons in review queue/i)).toBeInTheDocument();
    expect(screen.getByText(/no recent runs recorded/i)).toBeInTheDocument();

    const onRetry = vi.fn();
    rerender(<ContextPage pack={null} lessons={[]} runs={[]} error="Failed to fetch context state" onRefresh={onRetry} />);
    expect(screen.getByText("Failed to fetch context state")).toBeInTheDocument();
  });
});
