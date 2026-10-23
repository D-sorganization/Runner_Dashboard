import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AgentsSyncPanel, type AgentsSyncReport } from "../AgentsSyncPanel";

const sampleReport: AgentsSyncReport = {
  source: "Repository_Management/AGENTS.md",
  source_ref: "GitHub Contents API default branch",
  status: "lagging",
  reason: null,
  checked_at: "2026-10-23T06:00:00+00:00",
  source_digest: "abc-source-digest",
  source_blocks: { "network-api-hygiene": "abc-block-digest" },
  repositories: [
    { repo: "AffineDrift", status: "lagging", changed_blocks: ["network-api-hygiene"], expected_digest: "abc", observed_digest: "def", expected_blocks: {}, observed_blocks: {}, last_seen_at: "2026-10-23T05:59:00+00:00" },
    { repo: "UpstreamDrift", status: "current", changed_blocks: [], expected_digest: "abc", observed_digest: "abc", expected_blocks: {}, observed_blocks: {}, last_seen_at: "2026-10-23T05:59:00+00:00" },
    { repo: "Tools_Private", status: "unavailable", changed_blocks: [], expected_digest: "abc", observed_digest: null, expected_blocks: {}, observed_blocks: null, last_seen_at: null },
  ],
};

describe("AgentsSyncPanel", () => {
  it("shows content-based sync results and the unavailable state", async () => {
    render(<AgentsSyncPanel load={vi.fn().mockResolvedValue(sampleReport)} />);
    expect(await screen.findByText("Repository_Management/AGENTS.md", { exact: false })).toBeInTheDocument();
    expect(screen.getByTestId("agents-status-AffineDrift")).toHaveTextContent("Drift detected");
    expect(screen.getByText("network-api-hygiene")).toBeInTheDocument();
    expect(screen.getByText(/expected sha-256 abc-source-digest/i)).toBeInTheDocument();
    expect(screen.getByText("def")).toBeInTheDocument();
    expect(screen.getAllByText(/last seen: 2026-10-23T05:59:00\+00:00/i)).toHaveLength(2);
    expect(screen.getByTestId("agents-status-UpstreamDrift")).toHaveTextContent("In sync");
    expect(screen.getByTestId("agents-status-Tools_Private")).toHaveTextContent("Unavailable");
  });

  it("shows access failure as unavailable and retries on request", async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error("Repository metadata is unavailable (HTTP 403)."))
      .mockResolvedValueOnce(sampleReport);
    render(<AgentsSyncPanel load={load} />);
    expect(await screen.findByText(/unavailable \(http 403\)/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByTestId("agents-status-UpstreamDrift")).toHaveTextContent("In sync"));
    expect(load).toHaveBeenCalledTimes(2);
  });
});
