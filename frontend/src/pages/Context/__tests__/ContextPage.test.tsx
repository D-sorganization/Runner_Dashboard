import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ContextPage, type ContextPackInfo, type ContextLesson, type ContextRunRecord } from "../ContextPage";

const samplePack: ContextPackInfo = {
  version: "1.1.0",
  digest: "bcc29c1fe01f2345",
  pin: "fleet-standards@1.1.0#bcc29c1fe01f",
  source_path: "staff/standards/FLEET.md",
  is_fallback: false,
};

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
    standards_version: "1.1.0", standards_tokens: 720, lessons_tokens: 240, input_tokens: 4500, output_tokens: 800, cost_usd: 0.045,
    context_stamp: JSON.stringify({
      pack_version: "1.1.0", content_digest: "bcc29c1fe01f2345", requested_pack: "fleet-standards@1.1.0", resolved_pack: "fleet-standards@1.1.0#bcc29c1fe01f",
      fallback_status: "ok", token_estimates: { tier0_standards: 720, tier1_skills: 350, tier1_lessons: 240, total_injected: 1310 },
      lesson_ids: ["mem_02"], lessons_skipped: [{ id: "mem_01", reason: "conflict" }],
    }),
  },
  {
    id: "run-202-bloat", role: "researcher", repo: "Repository_Management", status: "failed", created_at: "2026-10-22T20:30:00Z",
    standards_version: "1.0.0", standards_tokens: 1550, lessons_tokens: 700, input_tokens: 8200, output_tokens: 1200, cost_usd: 0.092,
    context_stamp: JSON.stringify({
      pack_version: "1.0.0", content_digest: "aaa111bbb222", requested_pack: "fleet-standards@1.0.0", resolved_pack: "fleet-standards@1.0.0#aaa111",
      fallback_status: "ok", token_estimates: { tier0_standards: 1550, tier1_skills: 200, tier1_lessons: 700, total_injected: 2450 },
      lesson_ids: [], lessons_skipped: [{ id: "mem_20", reason: "budget" }],
    }),
  },
];

describe("ContextPage", () => {
  it("renders current pack pin, digest, and normal/fallback indicators", () => {
    render(<ContextPage pack={samplePack} lessons={sampleLessons} runs={sampleRuns} />);
    expect(screen.getByText("fleet-standards@1.1.0#bcc29c1fe01f")).toBeInTheDocument();
    expect(screen.getAllByText("1.1.0").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("bcc29c1fe01f2345")).toBeInTheDocument();
    expect(screen.getByText("staff/standards/FLEET.md")).toBeInTheDocument();
    expect(screen.getByText(/2,200 token hard cap/i)).toBeInTheDocument();
  });

  it("flags <builtin> fallback warning when pack is fallback", () => {
    const fallbackPack = { ...samplePack, source_path: "<builtin>", is_fallback: true };
    render(<ContextPage pack={fallbackPack} lessons={[]} runs={[]} />);
    expect(screen.getByText(/builtin fallback warning/i)).toBeInTheDocument();
  });

  it("renders per-repo AGENTS.md version lag table correctly", () => {
    render(<ContextPage pack={samplePack} lessons={sampleLessons} runs={sampleRuns} />);
    expect(screen.getByTestId("repo-status-Runner_Dashboard")).toHaveTextContent(/up to date/i);
    expect(screen.getByTestId("repo-status-Repository_Management")).toHaveTextContent(/lagging/i);
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

  it("renders per-run context size breakdown, flags over-cap runs, and honest cost estimates", () => {
    render(<ContextPage pack={samplePack} lessons={sampleLessons} runs={sampleRuns} />);
    expect(screen.getByTestId("run-cap-run-201")).toHaveTextContent(/ok/i);
    expect(screen.getByText(/mem_01 \(conflict\)/i)).toBeInTheDocument();
    expect(screen.getByTestId("run-cap-run-202-bloat")).toHaveTextContent(/over-cap/i);
    expect(screen.getByText(/mem_20 \(budget\)/i)).toBeInTheDocument();
    expect(screen.getAllByText(/\(estimate\)/i).length).toBeGreaterThan(0);
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
