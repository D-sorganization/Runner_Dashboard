// @vitest-environment jsdom
/**
 * Roster.test.tsx — Unit tests for Staff Console Roster sidebar.
 *
 * Implements SC-D3 (Issue #1317) under Epic SC-D (#1350).
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Roster } from "../Roster";
import type { StaffRoleItem } from "../types";

const MOCK_ROLES: StaffRoleItem[] = [
  {
    name: "barb",
    title: "Executive Secretary",
    summary: "Secretary & attention gate; routes to specialists.",
    group: "leadership",
    valid: true,
    dispatchable: true,
    active_runs: 0,
    providers: ["anthropic"],
  },
  {
    name: "board",
    title: "Board of Directors",
    summary: "Collective priority council and governance.",
    group: "leadership",
    valid: true,
    active_runs: 1,
    providers: ["anthropic"],
  },
  {
    name: "project-steward",
    title: "Project Steward",
    summary: "Cross-repo planning, backlog tracking, and roadmap.",
    group: "project_managers",
    valid: true,
    caller_unread_count: 2,
    last_message_preview: "Backlog sprint updated",
    last_message_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(), // 5m ago
  },
  {
    name: "librarian",
    title: "Librarian",
    summary: "Documentation integrity, ADR cataloging, cross-references.",
    group: "specialists",
    valid: true,
    active_runs: 0,
  },
  {
    name: "fleet-maintenance",
    title: "Fleet Maintenance",
    summary: "Runner restarts, cache trim, disk compaction, queue health.",
    group: "operations",
    valid: true,
    active_runs: 0,
  },
  {
    name: "held-specialist",
    title: "Held Specialist",
    summary: "Specialist currently on operational hold.",
    group: "specialists",
    valid: true,
    holds: ["quarantine"],
  },
  {
    name: "budget-exhausted-role",
    title: "Overbudget Role",
    summary: "Role that reached daily budget cap.",
    group: "operations",
    valid: true,
    budget: {
      daily_limit: 10,
      spend_today: 10,
    },
  },
  {
    name: "no-provider-role",
    title: "Unauthenticated Role",
    summary: "Role with missing provider CLI credentials.",
    group: "operations",
    valid: true,
    providers: ["custom-llm"],
  },
  {
    name: "invalid-role",
    title: "Broken Role",
    summary: "Malformed role definition with validation error.",
    group: "specialists",
    valid: false,
    error: "invalid role file",
    errors: ["YAML parse error at line 4"],
  },
];

afterEach(cleanup);

describe("Staff Console Roster Sidebar (SC-D3)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders Ask Barb (auto-route) top entry and allows selection", () => {
    const onSelectRole = vi.fn();
    render(<Roster roles={MOCK_ROLES} onSelectRole={onSelectRole} />);

    // Check top entry
    const barbEntry = screen.getByTestId("roster-row-barb");
    expect(barbEntry).toBeInTheDocument();
    expect(screen.getByText("Ask Barb (auto-route)")).toBeInTheDocument();

    // Clicking Ask Barb triggers onSelectRole
    fireEvent.click(barbEntry);
    expect(onSelectRole).toHaveBeenCalledWith("barb");
  });

  it("groups roles into Leadership, Project Managers, Specialists, Operations per SC-D1", () => {
    render(<Roster roles={MOCK_ROLES} />);

    expect(screen.getByTestId("roster-group-leadership")).toBeInTheDocument();
    expect(screen.getByTestId("roster-group-project_managers")).toBeInTheDocument();
    expect(screen.getByTestId("roster-group-specialists")).toBeInTheDocument();
    expect(screen.getByTestId("roster-group-operations")).toBeInTheDocument();

    expect(screen.getByText("Board of Directors")).toBeInTheDocument();
    expect(screen.getByText("Project Steward")).toBeInTheDocument();
    expect(screen.getByText("Librarian")).toBeInTheDocument();
    expect(screen.getByText("Fleet Maintenance")).toBeInTheDocument();
  });

  it("renders status dots for idle, working, needs_you, unavailable, and invalid", () => {
    const availableProviders = { anthropic: true, "custom-llm": false };
    render(
      <Roster roles={MOCK_ROLES} availableProviders={availableProviders} />
    );

    // Idle
    const librarianDot = screen.getByTestId("status-dot-librarian");
    expect(librarianDot).toHaveAttribute("data-status", "idle");

    // Working (active_runs = 1)
    const boardDot = screen.getByTestId("status-dot-board");
    expect(boardDot).toHaveAttribute("data-status", "working");

    // Needs you (caller_unread_count = 2)
    const stewardDot = screen.getByTestId("status-dot-project-steward");
    expect(stewardDot).toHaveAttribute("data-status", "needs_you");

    // Unavailable: held
    const heldDot = screen.getByTestId("status-dot-held-specialist");
    expect(heldDot).toHaveAttribute("data-status", "unavailable");
    expect(screen.getByTestId("status-reason-held-specialist")).toHaveTextContent("held: quarantine");

    // Unavailable: budget reached
    const budgetDot = screen.getByTestId("status-dot-budget-exhausted-role");
    expect(budgetDot).toHaveAttribute("data-status", "unavailable");
    expect(screen.getByTestId("status-reason-budget-exhausted-role")).toHaveTextContent("budget reached");

    // Unavailable: no provider signed in
    const provDot = screen.getByTestId("status-dot-no-provider-role");
    expect(provDot).toHaveAttribute("data-status", "unavailable");
    expect(screen.getByTestId("status-reason-no-provider-role")).toHaveTextContent("no provider signed in");

    // Invalid: validation error
    const invalidDot = screen.getByTestId("status-dot-invalid-role");
    expect(invalidDot).toHaveAttribute("data-status", "invalid");
    expect(screen.getByTestId("status-reason-invalid-role")).toHaveTextContent("invalid role file");
  });

  it("displays unread count badge and last message preview with relative age", () => {
    render(<Roster roles={MOCK_ROLES} />);

    const badge = screen.getByTestId("unread-badge-project-steward");
    expect(badge).toHaveTextContent("2");
    expect(screen.getByText(/Backlog sprint updated/)).toBeInTheDocument();
    expect(screen.getByText(/5m ago/)).toBeInTheDocument();
  });

  it("filters roles by name, title, and mandate summary", () => {
    render(<Roster roles={MOCK_ROLES} />);

    const searchInput = screen.getByTestId("roster-search-input");

    // Search by title
    fireEvent.change(searchInput, { target: { value: "Librarian" } });
    expect(screen.getByText("Librarian")).toBeInTheDocument();
    expect(screen.queryByText("Fleet Maintenance")).not.toBeInTheDocument();

    // Search by mandate summary keyword
    fireEvent.change(searchInput, { target: { value: "backlog" } });
    expect(screen.getByText("Project Steward")).toBeInTheDocument();
    expect(screen.queryByText("Librarian")).not.toBeInTheDocument();

    // Search nonexistent keyword
    fireEvent.change(searchInput, { target: { value: "nonexistent-xyz" } });
    expect(screen.getByTestId("roster-empty-search")).toHaveTextContent(
      'No roles match "nonexistent-xyz"'
    );

    // Clear search
    const clearBtn = screen.getByTestId("roster-search-clear");
    fireEvent.click(clearBtn);
    expect(screen.getByText("Librarian")).toBeInTheDocument();
  });

  it("pins and unpins roles and persists in localStorage", () => {
    render(<Roster roles={MOCK_ROLES} />);

    // Initially no pinned group
    expect(screen.queryByTestId("roster-group-pinned")).not.toBeInTheDocument();

    // Pin librarian
    const pinLibrarianBtn = screen.getByTestId("pin-button-librarian");
    fireEvent.click(pinLibrarianBtn);

    // Pinned group appears
    expect(screen.getByTestId("roster-group-pinned")).toBeInTheDocument();

    // Librarian should be saved in localStorage
    const saved = JSON.parse(localStorage.getItem("staff-console:pinned-roles") || "[]");
    expect(saved).toContain("librarian");

    // Unpin librarian
    const pinButtons = screen.getAllByTestId("pin-button-librarian");
    fireEvent.click(pinButtons[0]);
    const updated = JSON.parse(localStorage.getItem("staff-console:pinned-roles") || "[]");
    expect(updated).not.toContain("librarian");
  });

  it("collapses and expands role groups on header click and persists state", () => {
    render(<Roster roles={MOCK_ROLES} />);

    const operationsHeader = screen.getByTestId("group-header-operations");
    expect(screen.getByText("Fleet Maintenance")).toBeInTheDocument();

    // Click to collapse
    fireEvent.click(operationsHeader);
    expect(screen.queryByText("Fleet Maintenance")).not.toBeInTheDocument();

    const storedCollapsed = JSON.parse(
      localStorage.getItem("staff-console:collapsed-groups") || "{}"
    );
    expect(storedCollapsed.operations).toBe(true);

    // Click to expand again
    fireEvent.click(operationsHeader);
    expect(screen.getByText("Fleet Maintenance")).toBeInTheDocument();
  });

  it("displays stale badge when isStale is true while preserving last known roster", () => {
    render(<Roster roles={MOCK_ROLES} isStale={true} />);

    expect(screen.getByTestId("roster-stale-badge")).toHaveTextContent("Stale Data");
    expect(screen.getByText("Librarian")).toBeInTheDocument();
  });

  it("displays stale badge when isError is true but previous roles exist", () => {
    render(<Roster roles={MOCK_ROLES} isError={true} errorMessage="503 Service Unavailable" />);

    expect(screen.getByTestId("roster-stale-badge")).toHaveTextContent("Stale Data");
    expect(screen.getByText("Project Steward")).toBeInTheDocument();
  });

  it("displays error banner when isError is true and no previous roles exist", () => {
    render(<Roster roles={[]} isError={true} errorMessage="Roster fetch timeout" />);

    expect(screen.getByTestId("roster-error-banner")).toHaveTextContent("Roster fetch timeout");
  });

  it("supports keyboard navigation through visible roles with Arrow keys and Enter", () => {
    const onSelectRole = vi.fn();
    render(<Roster roles={MOCK_ROLES} onSelectRole={onSelectRole} />);

    const sidebar = screen.getByTestId("staff-roster-sidebar");

    // Navigate down to first item (barb)
    fireEvent.keyDown(sidebar, { key: "ArrowDown" });
    // Press Enter to select
    fireEvent.keyDown(sidebar, { key: "Enter" });
    expect(onSelectRole).toHaveBeenCalledWith("barb");

    // Navigate down again to next item
    fireEvent.keyDown(sidebar, { key: "ArrowDown" });
    fireEvent.keyDown(sidebar, { key: "Enter" });
    expect(onSelectRole).toHaveBeenCalled();

    // Navigate up wraps to bottom
    fireEvent.keyDown(sidebar, { key: "ArrowUp" });
    fireEvent.keyDown(sidebar, { key: "Enter" });
    expect(onSelectRole).toHaveBeenCalled();
  });

  it("supports keyboard selection directly on RosterRow with Space and Enter", () => {
    const onSelectRole = vi.fn();
    render(<Roster roles={MOCK_ROLES} onSelectRole={onSelectRole} />);

    const librarianRow = screen.getByTestId("roster-row-librarian");
    fireEvent.keyDown(librarianRow, { key: " " });
    expect(onSelectRole).toHaveBeenCalledWith("librarian");

    fireEvent.keyDown(librarianRow, { key: "Enter" });
    expect(onSelectRole).toHaveBeenCalledWith("librarian");
  });

  it("supports keyboard toggle on RosterGroup header with Space and Enter", () => {
    render(<Roster roles={MOCK_ROLES} />);

    const specialistsHeader = screen.getByTestId("group-header-specialists");
    expect(screen.getByText("Librarian")).toBeInTheDocument();

    // Press Space on header to collapse
    fireEvent.keyDown(specialistsHeader, { key: " " });
    expect(screen.queryByText("Librarian")).not.toBeInTheDocument();

    // Press Enter on header to expand
    fireEvent.keyDown(specialistsHeader, { key: "Enter" });
    expect(screen.getByText("Librarian")).toBeInTheDocument();
  });

  it("shifts keyboard focus from search input to first role on ArrowDown", () => {
    const onSelectRole = vi.fn();
    render(<Roster roles={MOCK_ROLES} onSelectRole={onSelectRole} />);

    const searchInput = screen.getByTestId("roster-search-input");
    fireEvent.keyDown(searchInput, { key: "ArrowDown" });

    const sidebar = screen.getByTestId("staff-roster-sidebar");
    fireEvent.keyDown(sidebar, { key: "Enter" });
    expect(onSelectRole).toHaveBeenCalledWith("barb");
  });

  it("formats relative time correctly for recent, hours and days", () => {
    const customRoles: StaffRoleItem[] = [
      {
        name: "role-hours",
        title: "Role Hours",
        group: "specialists",
        valid: true,
        last_message_preview: "Processed 2h ago",
        last_message_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
      },
      {
        name: "role-days",
        title: "Role Days",
        group: "specialists",
        valid: true,
        last_message_preview: "Processed 3d ago",
        last_message_at: new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString(),
      },
    ];

    render(<Roster roles={customRoles} />);
    expect(screen.getByText(/2h ago/)).toBeInTheDocument();
    expect(screen.getByText(/3d ago/)).toBeInTheDocument();
  });
});
