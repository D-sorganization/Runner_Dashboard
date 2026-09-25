// @vitest-environment jsdom
/**
 * Desktop.test.tsx — three-pane desktop Staff Console on /staff (#1446).
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ThreadInfo } from "../threadTypes";
import type { StaffRoleItem } from "../types";

const api = vi.hoisted(() => ({
  fetchThreads: vi.fn(),
  createThread: vi.fn(),
  fetchThreadMessages: vi.fn(),
  postThreadMessage: vi.fn(),
  fetchRoster: vi.fn(),
}));

vi.mock("../../Staff/staffApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../Staff/staffApi")>()),
  ...api,
}));

import { StaffConsoleDesktop } from "../Desktop";

const ROLES: StaffRoleItem[] = [
  { name: "barb", title: "Barb", summary: "Secretary", group: "leadership", valid: true },
  { name: "maintenance", title: "Fleet Maintenance", summary: "Hosts", group: "operations", valid: true },
];

const MAINT_THREAD: ThreadInfo = {
  id: "thr_maint_1",
  title: "Conversation with Fleet Maintenance",
  kind: "direct",
  participants: ["user:me", "maintenance"],
  status: "active",
};

beforeEach(() => {
  api.fetchThreads.mockResolvedValue({ threads: [MAINT_THREAD] });
  api.fetchThreadMessages.mockResolvedValue({
    messages: [
      {
        id: "m1",
        thread_id: "thr_maint_1",
        author: "maintenance",
        author_kind: "staff",
        kind: "text",
        body_md: "All hosts healthy.",
        delivery: "complete",
        seq: 1,
      },
    ],
  });
  api.postThreadMessage.mockResolvedValue({ message: { id: "m2" } });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function openMaintenance() {
  fireEvent.click(screen.getByTestId("roster-row-maintenance"));
}

describe("StaffConsoleDesktop", () => {
  it("renders the roster, an empty conversation pane and the context pane", () => {
    render(<StaffConsoleDesktop roles={ROLES} />);

    expect(screen.getByTestId("staff-roster-sidebar")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Staff conversation" })).toHaveTextContent(
      /pick a role or ask barb/i,
    );
    expect(screen.getByRole("complementary", { name: "Role context" })).toBeInTheDocument();
    expect(api.fetchThreads).not.toHaveBeenCalled();
  });

  it("opens the selected role's thread with its history", async () => {
    render(<StaffConsoleDesktop roles={ROLES} />);
    openMaintenance();

    expect(await screen.findByText("All hosts healthy.")).toBeInTheDocument();
    expect(api.fetchThreads).toHaveBeenCalledWith({ role: "maintenance" });
    expect(screen.getByRole("heading", { name: "Conversation with Fleet Maintenance" })).toBeInTheDocument();
  });

  it("posts a composed message to the resolved thread", async () => {
    render(<StaffConsoleDesktop roles={ROLES} />);
    openMaintenance();
    await screen.findByText("All hosts healthy.");

    fireEvent.change(screen.getByPlaceholderText(/message fleet maintenance/i), {
      target: { value: "compact CT disk" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() =>
      expect(api.postThreadMessage).toHaveBeenCalledWith(
        "thr_maint_1",
        expect.objectContaining({ body_md: "compact CT disk" }),
        expect.any(String),
      ),
    );
  });

  it("shows a failed send as an alert", async () => {
    api.postThreadMessage.mockRejectedValue(new Error("500 Internal Server Error"));
    render(<StaffConsoleDesktop roles={ROLES} />);
    openMaintenance();
    await screen.findByText("All hosts healthy.");

    fireEvent.change(screen.getByPlaceholderText(/message fleet maintenance/i), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(await screen.findByTestId("staff-console-error-send")).toHaveTextContent("500 Internal Server Error");
  });

  it("loads its own roster and shows a roster failure", async () => {
    api.fetchRoster.mockRejectedValue(new Error("403 missing staff.read"));
    render(<StaffConsoleDesktop />);
    expect(await screen.findByTestId("roster-error-banner")).toHaveTextContent("403 missing staff.read");
    expect(api.fetchRoster).toHaveBeenCalled();
  });

  it("collapses and restores the context pane", () => {
    render(<StaffConsoleDesktop roles={ROLES} />);

    fireEvent.click(screen.getByRole("button", { name: /hide role context/i }));
    expect(screen.queryByRole("complementary", { name: "Role context" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /show role context/i }));
    expect(screen.getByRole("complementary", { name: "Role context" })).toBeInTheDocument();
  });
});
