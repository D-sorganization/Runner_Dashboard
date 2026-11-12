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
  it("puts chat first while keeping staff selection and context available on demand", async () => {
    render(<StaffConsoleDesktop roles={ROLES} chatFirst />);
    expect(screen.getByRole("heading", { level: 2, name: "Ask Barb" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "What would you like to get done?" })).toBeInTheDocument();
    expect(
      screen.queryByTestId("staff-roster-sidebar"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("complementary", { name: "Role context" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: /staff conversation input/i }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Choose staff" }));
    expect(screen.getByTestId("staff-roster-sidebar")).toBeInTheDocument();
    openMaintenance();
    expect(await screen.findByText("All hosts healthy.")).toBeInTheDocument();
    expect(
      screen.queryByTestId("staff-roster-sidebar"),
    ).not.toBeInTheDocument();
    expect(api.postThreadMessage).not.toHaveBeenCalled();
  });

  it("offers project-specific Barb starters that prefill and keep focus in the composer (#2408)", () => {
    render(<StaffConsoleDesktop roles={ROLES} chatFirst />);

    const upstreamStarter = screen.getByRole("button", { name: "Start UpstreamDrift" });
    fireEvent.click(upstreamStarter);
    const textarea = screen.getByRole("textbox", { name: /staff conversation input/i }) as HTMLTextAreaElement;
    expect(textarea.value).toContain("Triage UpstreamDrift");
    expect(textarea.value).toContain("review open issues and pull requests in the UpstreamDrift repository");
    expect(textarea.value).toContain("wait for my approval before dispatch");
    expect(textarea.value).toContain("registered workflows");
    expect(textarea).toHaveFocus();
    expect(api.postThreadMessage).not.toHaveBeenCalled();

    // Blur to the starter button, clear the draft, and click the same starter again:
    // focus and prefilled text must return to the composer even on repeat clicks (#2408).
    upstreamStarter.focus();
    expect(upstreamStarter).toHaveFocus();
    fireEvent.change(textarea, { target: { value: "" } });
    fireEvent.click(upstreamStarter);
    expect(textarea.value).toContain("Triage UpstreamDrift");
    expect(textarea).toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: "Start AffineDrift" }));
    expect(textarea.value).toContain("Triage AffineDrift");
    expect(textarea.value).toContain("review open issues and pull requests in the AffineDrift repository");
    expect(textarea).toHaveFocus();
    expect(api.postThreadMessage).not.toHaveBeenCalled();
  });

  it("renders the roster, an empty conversation pane and the context pane", () => {
    render(<StaffConsoleDesktop roles={ROLES} />);

    expect(screen.getByRole("heading", { level: 2, name: "Staff Console" })).toBeInTheDocument();
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

    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to send message: 500 Internal Server Error");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /staff conversation input/i })).toHaveValue("hello");
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

  it("starts a fresh conversation for the open role via New conversation, even though one already exists (#1775)", async () => {
    render(<StaffConsoleDesktop roles={ROLES} />);
    openMaintenance();
    await screen.findByText("All hosts healthy.");

    const fresh: ThreadInfo = {
      id: "thr_maint_fresh",
      title: "Conversation with Fleet Maintenance",
      kind: "direct",
      participants: ["user:me", "maintenance"],
      status: "active",
    };
    api.createThread.mockResolvedValue(fresh);

    fireEvent.click(screen.getByRole("button", { name: /new conversation/i }));

    await waitFor(() =>
      expect(api.createThread).toHaveBeenCalledWith({
        role: "maintenance",
        kind: "direct",
        title: "Conversation with Fleet Maintenance",
      }),
    );
    // The active thread switched to the freshly created one, not the reused old thread.
    await waitFor(() =>
      expect(screen.getByTitle("Export thread as Markdown")).toHaveAttribute(
        "href",
        "/api/v1/staff/threads/thr_maint_fresh/export?format=markdown",
      ),
    );
  });

  it("keeps ?thread= in the URL in sync with the open conversation (#1783)", async () => {
    window.history.replaceState(null, "", "/staff?section=console&thread=thr_old");
    render(<StaffConsoleDesktop roles={ROLES} />);
    openMaintenance();
    await screen.findByText("All hosts healthy.");

    const fresh: ThreadInfo = {
      id: "thr_maint_url",
      title: "Conversation with Fleet Maintenance",
      kind: "direct",
      participants: ["user:me", "maintenance"],
      status: "active",
    };
    api.createThread.mockResolvedValue(fresh);
    fireEvent.click(screen.getByRole("button", { name: /new conversation/i }));

    await waitFor(() => expect(new URLSearchParams(window.location.search).get("thread")).toBe("thr_maint_url"));
    // Other query parameters survive the rewrite.
    expect(new URLSearchParams(window.location.search).get("section")).toBe("console");
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
});


it("shows named project starters in an active desktop conversation without sending or changing thread", async () => {
  render(<StaffConsoleDesktop roles={ROLES} chatFirst />);
  fireEvent.click(screen.getByRole("button", { name: "Choose staff" }));
  openMaintenance();
  await screen.findByText("All hosts healthy.");
  for (const project of ["UpstreamDrift", "AffineDrift"]) {
    fireEvent.click(screen.getByRole("button", { name: `Start ${project}` }));
    expect((screen.getByRole("textbox", { name: /staff conversation input/i }) as HTMLTextAreaElement).value).toContain(`Triage ${project}`);
    expect(screen.getByRole("heading", { name: MAINT_THREAD.title })).toBeInTheDocument();
    expect(api.postThreadMessage).not.toHaveBeenCalled();
  }
  const fresh = { ...MAINT_THREAD, id: "thr_fresh", title: "Fresh conversation" };
  api.createThread.mockResolvedValue(fresh);
  fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
  await screen.findByRole("heading", { name: fresh.title });
  expect(screen.getByRole("button", { name: "Start UpstreamDrift" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Start AffineDrift" })).toBeInTheDocument();
});
