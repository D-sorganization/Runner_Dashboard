// @vitest-environment jsdom
/**
 * Desktop.test.tsx — three-pane desktop Staff Console on /staff (#1446).
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ThreadInfo } from "../threadTypes";
import type { StaffRoleItem } from "../types";

const api = vi.hoisted(() => ({
  fetchThreads: vi.fn(),
  createThread: vi.fn(),
  fetchThreadMessages: vi.fn(),
  postThreadMessage: vi.fn(),
  fetchRoster: vi.fn(),
  fetchGroupCostEstimate: vi.fn(),
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

  it("renders welcome message and suggestion chips when no thread is selected, and clicking a chip pre-fills composer without sending", () => {
    render(<StaffConsoleDesktop roles={ROLES} />);

    expect(screen.getByText("Ask Barb anything, or pick a staff role")).toBeInTheDocument();
    const chipWaiting = screen.getByRole("button", { name: "What's waiting on me?" });
    const chipSummarise = screen.getByRole("button", { name: "Summarise today's fleet status" });
    const chipBlocked = screen.getByRole("button", { name: "Which PRs are blocked?" });

    expect(chipWaiting).toBeInTheDocument();
    expect(chipSummarise).toBeInTheDocument();
    expect(chipBlocked).toBeInTheDocument();

    fireEvent.click(chipWaiting);

    const textarea = screen.getByRole("textbox", { name: /staff conversation input/i });
    expect(textarea).toHaveValue("What's waiting on me?");
    expect(api.postThreadMessage).not.toHaveBeenCalled();
  });


  it("opens the selected role's thread with its history", async () => {
    render(<StaffConsoleDesktop roles={ROLES} />);
    openMaintenance();

    expect(await screen.findByText("All hosts healthy.")).toBeInTheDocument();
    expect(api.fetchThreads).toHaveBeenCalledWith({ role: "maintenance" });
    expect(screen.getByRole("heading", { name: "Conversation with Fleet Maintenance" })).toBeInTheDocument();
    expect(screen.getByTitle("Export thread as Markdown")).toHaveAttribute(
      "href",
      "/api/v1/staff/threads/thr_maint_1/export?format=markdown",
    );
    expect(screen.getByTitle("Export thread as JSON")).toHaveAttribute(
      "href",
      "/api/v1/staff/threads/thr_maint_1/export?format=json",
    );
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
        expect.objectContaining({ body: "compact CT disk" }),
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

  it("holds a Board message over the cost threshold until the user confirms (SC-D7, #1342)", async () => {
    api.fetchThreads.mockResolvedValue({ threads: [{ ...MAINT_THREAD, meta: { group: "board" } }] });
    api.fetchGroupCostEstimate.mockResolvedValue({
      group_id: "board",
      total_cost_usd: 0.84,
      cost_per_seat: { alpha: 0.5, bravo: 0.34 },
      exceeds_threshold: true,
      threshold_usd: 0.5,
    });
    render(<StaffConsoleDesktop roles={ROLES} />);
    openMaintenance();
    await screen.findByText("All hosts healthy.");

    fireEvent.change(screen.getByPlaceholderText(/message fleet maintenance/i), { target: { value: "adopt X?" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    const confirm = await screen.findByRole("alert", { name: /board cost/i });
    expect(confirm).toHaveTextContent("$0.84");
    expect(confirm).toHaveTextContent("$0.50");
    expect(confirm).toHaveTextContent("alpha: $0.50");
    expect(api.postThreadMessage).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /send anyway/i }));
    await waitFor(() =>
      expect(api.postThreadMessage).toHaveBeenCalledWith(
        "thr_maint_1",
        expect.objectContaining({ body: "adopt X?", meta: { confirm_cost: true } }),
        expect.any(String),
      ),
    );
    await waitFor(() => expect(screen.queryByRole("alert", { name: /board cost/i })).not.toBeInTheDocument());
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

  it("starts with the context pane closed on medium windows (#1718)", () => {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) =>
      ({ matches: query.includes("max-width: 1280px"), media: query, addEventListener() {}, removeEventListener() {} }) as unknown as MediaQueryList) as typeof window.matchMedia;
    try {
      render(<StaffConsoleDesktop roles={ROLES} />);
      expect(screen.queryByRole("complementary", { name: "Role context" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /show role context/i }));
      const pane = screen.getByRole("complementary", { name: "Role context" });
      // The overlay covers the header toggle, so it carries its own close button.
      fireEvent.click(within(pane).getByRole("button", { name: /close role context/i }));
      expect(screen.queryByRole("complementary", { name: "Role context" })).not.toBeInTheDocument();
    } finally {
      window.matchMedia = original;
    }
  });

  it("sending from the landing composer opens Barb auto-route thread and delivers message (#1775)", async () => {
    const barbThread: ThreadInfo = {
      id: "thr_barb_1",
      title: "Conversation with Barb",
      kind: "auto",
      participants: ["user:me", "barb"],
      status: "active",
    };
    api.fetchThreads.mockImplementation(({ role }) => {
      if (role === "barb") return Promise.resolve({ threads: [barbThread] });
      return Promise.resolve({ threads: [] });
    });

    render(<StaffConsoleDesktop roles={ROLES} />);

    expect(screen.getByPlaceholderText(/message barb or type \/dispatch/i)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/message barb or type \/dispatch/i), {
      target: { value: "Review issue #11080" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() =>
      expect(api.postThreadMessage).toHaveBeenCalledWith(
        "thr_barb_1",
        expect.objectContaining({ body: "Review issue #11080" }),
        expect.any(String),
      ),
    );
    expect(await screen.findByRole("heading", { name: "Conversation with Barb" })).toBeInTheDocument();
  });

  it("conversation header has a 'New conversation' control that creates a fresh thread (#1775)", async () => {
    const freshThread: ThreadInfo = {
      id: "thr_maint_2",
      title: "Conversation with Fleet Maintenance",
      kind: "direct",
      participants: ["user:me", "maintenance"],
      status: "active",
    };
    api.createThread.mockResolvedValue(freshThread);

    render(<StaffConsoleDesktop roles={ROLES} />);
    openMaintenance();
    await screen.findByText("All hosts healthy.");

    const newBtn = screen.getByRole("button", { name: /new conversation/i });
    expect(newBtn).toBeInTheDocument();

    fireEvent.click(newBtn);

    await waitFor(() =>
      expect(api.createThread).toHaveBeenCalledWith({
        role: "maintenance",
        kind: "direct",
        title: "Conversation with Fleet Maintenance",
      }),
    );
  });
});
