// @vitest-environment jsdom
/**
 * Tests for Fleet/LabelGuide.tsx — issue #757.
 *
 * Covers:
 * 1. Renders loading state before fetch resolves.
 * 2. Renders taxonomy table after successful fetch.
 * 3. Shows all four canonical labels.
 * 4. Shows workflow class section.
 * 5. Shows error state when API call fails.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LabelGuide } from "../LabelGuide";

afterEach(cleanup);

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

const MOCK_GUIDANCE = {
  taxonomy: {
    "d-sorg-fleet-nvme": {
      purpose: "NVMe tier label",
      workload: "Heavy IO builds",
      avoid_for: "Lightweight governance",
      runs_on_snippet:
        "runs-on: [self-hosted, Linux, X64, d-sorg-fleet-nvme]",
    },
    "d-sorg-fleet-fast-io": {
      purpose: "Fast IO label",
      workload: "CI test suites",
      avoid_for: "Docs checks",
      runs_on_snippet:
        "runs-on: [self-hosted, Linux, X64, d-sorg-fleet-fast-io]",
    },
    "d-sorg-fleet-docker": {
      purpose: "Docker label",
      workload: "Container builds",
      avoid_for: "Maintenance workflows",
      runs_on_snippet:
        "runs-on: [self-hosted, Linux, X64, d-sorg-fleet-docker]",
    },
    "d-sorg-fleet-bulk": {
      purpose: "Bulk/HDD label",
      workload: "Governance checks",
      avoid_for: "Docker builds",
      runs_on_snippet:
        "runs-on: [self-hosted, Linux, X64, d-sorg-fleet-bulk]",
    },
  },
  neutral_labels: ["d-sorg-fleet", "self-hosted"],
  workflow_classes: {
    bulk: {
      description: "Lightweight maintenance workflows",
      recommended_labels: ["d-sorg-fleet-bulk"],
      forbidden_labels: ["d-sorg-fleet-docker"],
    },
    docker: {
      description: "Container build workflows",
      recommended_labels: ["d-sorg-fleet-docker"],
      forbidden_labels: ["d-sorg-fleet-bulk"],
    },
    "fast-io": {
      description: "Heavy CI test suites",
      recommended_labels: ["d-sorg-fleet-fast-io"],
      forbidden_labels: ["d-sorg-fleet-bulk"],
    },
  },
  generated_at: "2026-05-28T00:00:00Z",
};

describe("LabelGuide", () => {
  it("shows loading state initially", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})) // never resolves
    );
    render(<LabelGuide />);
    expect(screen.getByText(/loading label guidance/i)).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("renders taxonomy table after successful fetch", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve(MOCK_GUIDANCE),
        })
      )
    );
    render(<LabelGuide />);
    await waitFor(() =>
      expect(screen.getByText("Runner Label Guide")).toBeInTheDocument()
    );
    vi.unstubAllGlobals();
  });

  it("shows all four canonical labels", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve(MOCK_GUIDANCE),
        })
      )
    );
    render(<LabelGuide />);
    await waitFor(() =>
      expect(screen.getByText("d-sorg-fleet-nvme")).toBeInTheDocument()
    );
    expect(screen.getByText("d-sorg-fleet-fast-io")).toBeInTheDocument();
    expect(screen.getByText("d-sorg-fleet-docker")).toBeInTheDocument();
    expect(screen.getByText("d-sorg-fleet-bulk")).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("renders workflow class cards", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve(MOCK_GUIDANCE),
        })
      )
    );
    render(<LabelGuide />);
    await waitFor(() =>
      expect(screen.getByText("Workflow Class Routing")).toBeInTheDocument()
    );
    expect(screen.getByText("bulk")).toBeInTheDocument();
    expect(screen.getByText("docker")).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("shows error state when fetch fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("network error")))
    );
    render(<LabelGuide />);
    await waitFor(() =>
      expect(screen.getByText(/failed to load label guidance/i)).toBeInTheDocument()
    );
    vi.unstubAllGlobals();
  });

  it("shows error state when API returns non-ok status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: false,
          status: 500,
          json: () => Promise.resolve({}),
        })
      )
    );
    render(<LabelGuide />);
    await waitFor(() =>
      expect(screen.getByText(/failed to load label guidance/i)).toBeInTheDocument()
    );
    vi.unstubAllGlobals();
  });
});
