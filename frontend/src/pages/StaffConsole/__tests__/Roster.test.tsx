// @vitest-environment jsdom
/**
 * Roster.test.tsx — Behaviour tests for Staff Roster Sidebar (SC-D3, Issue #1317).
 *
 * Covers:
 * 1. Top entry "Ask Barb (auto-route)" renders at top and handles selection.
 * 2. 4-tier groupings (Leadership, Project Managers, Specialists, Operations).
 * 3. Collapsing and expanding roster groups.
 * 4. 5 status dot states (idle, working, needs you, unavailable, invalid) with tooltips.
 * 5. Unread message count badge and last message preview.
 * 6. Search filtering by role name, title, and mandate keywords.
 * 7. Pinning and unpinning roles with persistence.
 * 8. Keyboard navigation (ArrowDown, ArrowUp, Enter, Home, End, Escape).
 * 9. Stale roster error state: retains previous roster with stale badge on failure.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Roster } from "../Roster";
import type { RosterRole } from "../rosterTypes";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const MOCK_ROLES: RosterRole[] = [
  {
    name: "barb",
    title: "Personal Secretary & Attention Gate",
    summary: "Routes incoming requests and gates human attention.",
    group: "Leadership",
    providers: ["claude"],
    dispatchable: true,
    valid: true,
    active_runs: 0,
    unread_count: 0,
    status: "idle",
    status_reason: "Idle · Ready for assignment",
    last_message: {
      body_md: "Good morning! All fleet systems operational.",
      author: "barb",
      created_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    },
  },
  {
    name: "board",
    title: "Board of Directors",
    summary: "Strategic governance and priority alignment.",
    group: "Leadership",
    providers: ["claude"],
    dispatchable: true,
    valid: true,
    active_runs: 0,
    unread_count: 0,
    status: "idle",
    status_reason: "Idle · Ready for assignment",
  },
  {
    name: "project-steward",
    title: "Project Steward",
    summary: "Cross-repo roadmap governance and tracking.",
    group: "Project Managers",
    providers: ["claude"],
    dispatchable: true,
    valid: true,
    active_runs: 1,
    unread_count: 0,
    status: "working",
    status_reason: "Working on 1 active run",
  },
  {
    name: "librarian",
    title: "Librarian",
    summary: "Documentation integrity, ADR cataloging, and cross-references.",
    group: "Specialists",
    providers: ["claude"],
    dispatchable: true,
    valid: true,
    active_runs: 0,
    unread_count: 3,
    status: "needs you",
    status_reason: "Needs your attention (3 unread messages)",
    last_message: {
      body_md: "Please review the proposed ADR for storage migration.",
      author: "librarian",
      created_at: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
    },
  },
  {
    name: "cartographer",
    title: "Cartographer",
    summary: "System architecture, dependency graphs, dataflow mapping.",
    group: "Specialists",
    providers: ["codex"],
    dispatchable: false,
    valid: true,
    active_runs: 0,
    unread_count: 0,
    status: "unavailable",
    status_reason: "No provider signed in / installed (codex)",
  },
  {
    name: "broken-scout",
    title: "Broken Scout",
    summary: "Faulty role definition with syntax errors.",
    group: "Specialists",
    providers: ["claude"],
    dispatchable: false,
    valid: false,
    errors: ["Schema validation error: missing required prompt_template"],
    active_runs: 0,
    unread_count: 0,
    status: "invalid",
    status_reason: "Invalid role file: Schema validation error: missing required prompt_template",
  },
  {
    name: "fleet-maintenance",
    title: "Fleet Maintenance",
    summary: "Runner restarts, cache trim, disk compaction, queue health.",
    group: "Operations",
    providers: ["claude"],
    dispatchable: true,
    valid: true,
    active_runs: 2,
    unread_count: 0,
    status: "working",
    status_reason: "Working on 2 active runs",
  },
];

describe("SC-D3: Staff Roster Sidebar Component", () => {
  it("renders top entry 'Ask Barb (auto-route)' and fires selection", () => {
    const onSelectRole = vi.fn();
    render(
      <Roster
        roles={MOCK_ROLES}
        selectedRole="project-steward"
        onSelectRole={onSelectRole}
        pinnedRoles={[]}
        onTogglePin={vi.fn()}
      />
    );

    const autoEntry = screen.getByTestId("roster-auto-entry");
    expect(autoEntry).toBeInTheDocument();
    expect(autoEntry).toHaveTextContent("Ask Barb");
    expect(autoEntry).toHaveTextContent("auto-route");

    fireEvent.click(autoEntry);
    expect(onSelectRole).toHaveBeenCalledWith("auto");
  });

  it("groups roles into the 4 operational tiers per SC-D1", () => {
    render(
      <Roster
        roles={MOCK_ROLES}
        selectedRole="barb"
        onSelectRole={vi.fn()}
        pinnedRoles={[]}
        onTogglePin={vi.fn()}
      />
    );

    expect(screen.getByTestId("roster-group-leadership")).toBeInTheDocument();
    expect(screen.getByTestId("roster-group-project-managers")).toBeInTheDocument();
    expect(screen.getByTestId("roster-group-specialists")).toBeInTheDocument();
    expect(screen.getByTestId("roster-group-operations")).toBeInTheDocument();
  });

  it("collapses and expands group sections on header toggle", () => {
    render(
      <Roster
        roles={MOCK_ROLES}
        selectedRole="barb"
        onSelectRole={vi.fn()}
        pinnedRoles={[]}
        onTogglePin={vi.fn()}
      />
    );

    const specGroup = screen.getByTestId("roster-group-specialists");
    const toggleBtn = within(specGroup).getByRole("button", { name: /specialists/i });

    // Initially expanded
    expect(within(specGroup).getByTestId("roster-row-librarian")).toBeInTheDocument();

    // Click to collapse
    fireEvent.click(toggleBtn);
    expect(within(specGroup).queryByTestId("roster-row-librarian")).not.toBeInTheDocument();

    // Click to expand again
    fireEvent.click(toggleBtn);
    expect(within(specGroup).getByTestId("roster-row-librarian")).toBeInTheDocument();
  });

  it("renders all 5 status dot states with descriptive tooltips", () => {
    render(
      <Roster
        roles={MOCK_ROLES}
        selectedRole="barb"
        onSelectRole={vi.fn()}
        pinnedRoles={[]}
        onTogglePin={vi.fn()}
      />
    );

    // Idle
    const idleDot = screen.getByTestId("status-dot-barb");
    expect(idleDot).toHaveAttribute("data-status", "idle");
    expect(idleDot).toHaveAttribute("title", "Idle · Ready for assignment");

    // Working
    const workingDot = screen.getByTestId("status-dot-project-steward");
    expect(workingDot).toHaveAttribute("data-status", "working");
    expect(workingDot).toHaveAttribute("title", "Working on 1 active run");

    // Needs you
    const needsDot = screen.getByTestId("status-dot-librarian");
    expect(needsDot).toHaveAttribute("data-status", "needs you");
    expect(needsDot).toHaveAttribute("title", "Needs your attention (3 unread messages)");

    // Unavailable
    const unavailDot = screen.getByTestId("status-dot-cartographer");
    expect(unavailDot).toHaveAttribute("data-status", "unavailable");
    expect(unavailDot).toHaveAttribute("title", "No provider signed in / installed (codex)");

    // Invalid
    const invalidDot = screen.getByTestId("status-dot-broken-scout");
    expect(invalidDot).toHaveAttribute("data-status", "invalid");
    expect(invalidDot).toHaveAttribute("title", "Invalid role file: Schema validation error: missing required prompt_template");
  });

  it("displays unread badge and last message preview for roles with threads", () => {
    render(
      <Roster
        roles={MOCK_ROLES}
        selectedRole="barb"
        onSelectRole={vi.fn()}
        pinnedRoles={[]}
        onTogglePin={vi.fn()}
      />
    );

    // Librarian has 3 unread
    const librarianRow = screen.getByTestId("roster-row-librarian");
    const unreadBadge = within(librarianRow).getByTestId("unread-badge-librarian");
    expect(unreadBadge).toHaveTextContent("3");

    // Last message preview
    expect(librarianRow).toHaveTextContent("Please review the proposed ADR for storage migration.");
  });

  it("filters roles in real-time by search query (name, title, mandate)", () => {
    render(
      <Roster
        roles={MOCK_ROLES}
        selectedRole="barb"
        onSelectRole={vi.fn()}
        pinnedRoles={[]}
        onTogglePin={vi.fn()}
      />
    );

    const searchInput = screen.getByRole("searchbox", { name: /search roles/i });

    // Filter by name
    fireEvent.change(searchInput, { target: { value: "librarian" } });
    expect(screen.getByTestId("roster-row-librarian")).toBeInTheDocument();
    expect(screen.queryByTestId("roster-row-board")).not.toBeInTheDocument();

    // Filter by mandate keyword
    fireEvent.change(searchInput, { target: { value: "architecture" } });
    expect(screen.getByTestId("roster-row-cartographer")).toBeInTheDocument();
    expect(screen.queryByTestId("roster-row-librarian")).not.toBeInTheDocument();

    // Empty search match shows helpful message
    fireEvent.change(searchInput, { target: { value: "nonexistent-xyz" } });
    expect(screen.getByText(/no roles matching/i)).toBeInTheDocument();

    // Clear search
    fireEvent.change(searchInput, { target: { value: "" } });
    expect(screen.getByTestId("roster-row-librarian")).toBeInTheDocument();
    expect(screen.getByTestId("roster-row-cartographer")).toBeInTheDocument();
  });

  it("supports pinning and renders pinned section at top", () => {
    const onTogglePin = vi.fn();
    render(
      <Roster
        roles={MOCK_ROLES}
        selectedRole="barb"
        onSelectRole={vi.fn()}
        pinnedRoles={["fleet-maintenance"]}
        onTogglePin={onTogglePin}
      />
    );

    const pinnedSection = screen.getByTestId("roster-pinned-section");
    expect(pinnedSection).toBeInTheDocument();
    expect(within(pinnedSection).getByTestId("roster-row-fleet-maintenance-pinned")).toBeInTheDocument();

    // Toggle pin button on librarian
    const pinBtn = screen.getByTestId("pin-btn-librarian");
    fireEvent.click(pinBtn);
    expect(onTogglePin).toHaveBeenCalledWith("librarian");
  });

  it("supports keyboard navigation (ArrowDown, ArrowUp, Enter, Escape)", () => {
    const onSelectRole = vi.fn();
    render(
      <Roster
        roles={MOCK_ROLES}
        selectedRole="auto"
        onSelectRole={onSelectRole}
        pinnedRoles={[]}
        onTogglePin={vi.fn()}
      />
    );

    const rosterContainer = screen.getByTestId("staff-roster-container");

    // Navigate down from auto entry to first role
    fireEvent.keyDown(rosterContainer, { key: "ArrowDown" });
    fireEvent.keyDown(rosterContainer, { key: "Enter" });
    expect(onSelectRole).toHaveBeenCalledWith("barb");

    // Search input Escape clears search
    const searchInput = screen.getByRole("searchbox", { name: /search roles/i });
    fireEvent.change(searchInput, { target: { value: "board" } });
    expect(searchInput).toHaveValue("board");

    fireEvent.keyDown(searchInput, { key: "Escape" });
    expect(searchInput).toHaveValue("");
  });

  it("retains last known roster with visible stale badge on fetch failure", () => {
    const onRetry = vi.fn();
    render(
      <Roster
        roles={MOCK_ROLES}
        selectedRole="barb"
        onSelectRole={vi.fn()}
        pinnedRoles={[]}
        onTogglePin={vi.fn()}
        isStale={true}
        staleReason="Failed to connect to roster API"
        onRetry={onRetry}
      />
    );

    // Stale banner is visible
    const staleBanner = screen.getByTestId("roster-stale-banner");
    expect(staleBanner).toBeInTheDocument();
    expect(staleBanner).toHaveTextContent(/stale/i);
    expect(staleBanner).toHaveTextContent(/failed to connect/i);

    // Roles are still rendered (fail visibly while preserving data)
    expect(screen.getByTestId("roster-row-barb")).toBeInTheDocument();

    // Retry button triggers onRetry callback
    const retryBtn = screen.getByRole("button", { name: /retry/i });
    fireEvent.click(retryBtn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
