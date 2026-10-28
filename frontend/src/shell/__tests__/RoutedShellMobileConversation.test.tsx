// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";

const mocks = vi.hoisted(() => ({
  breakpoint: vi.fn(() => "md"),
  createThread: vi.fn(),
  fetchRoster: vi.fn(),
  fetchThread: vi.fn(),
  fetchThreadMessages: vi.fn(),
  fetchThreads: vi.fn(),
}));

vi.mock("../../hooks/useBreakpoint", () => ({ useBreakpoint: mocks.breakpoint }));
vi.mock("../../hooks/useSession", () => ({
  useSession: () => ({ loading: false, loggedIn: false, refresh: vi.fn() }),
}));
vi.mock("../../pages/Staff/staffApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../pages/Staff/staffApi")>()),
  ...mocks,
}));
vi.mock("../MobileShell", () => ({
  MobileShell: ({ tabContent }: { tabContent?: Record<string, React.ReactNode> }) => (
    <main>{tabContent?.staff}</main>
  ),
}));
vi.mock("../../primitives/Toaster", () => ({
  useToast: () => ({ showToast: vi.fn() }),
}));

import { RoutedShell } from "../RoutedShell";

const oldThread = {
  id: "th-old",
  title: "Old conversation",
  kind: "auto",
  participants: ["user", "barb"],
  status: "active",
};
const newThread = {
  id: "th-created",
  title: "New conversation",
  kind: "auto",
  participants: ["user", "barb"],
  status: "active",
};
const roles = [{
  name: "barb",
  title: "Barb",
  summary: "Fleet concierge",
  group: "leadership",
  valid: true,
  dispatchable: false,
  active_runs: 0,
  caller_unread_count: 0,
}];

function LocationProbe() {
  return <output data-testid="route-search">{useLocation().search}</output>;
}

function renderStaffRoute() {
  return render(
    <BrowserRouter>
      <LocationProbe />
      <Routes><Route path="/staff" element={<RoutedShell />} /></Routes>
    </BrowserRouter>,
  );
}

afterEach(cleanup);

describe("mobile Staff new-conversation route integration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/staff?section=console");
    mocks.breakpoint.mockReturnValue("md");
    mocks.fetchRoster.mockResolvedValue({ roles });
    mocks.fetchThreads.mockResolvedValue({ threads: [oldThread] });
    mocks.fetchThread.mockImplementation(async (id: string) => ({
      thread: id === newThread.id ? newThread : oldThread,
      messages: [],
    }));
    mocks.fetchThreadMessages.mockResolvedValue({ messages: [] });
    mocks.createThread.mockResolvedValue(newThread);
  });

  it("updates the shell URL to the created thread and restores it after reload", async () => {
    renderStaffRoute();
    await userEvent.click(await screen.findByTestId("staff-mobile-tab-roster"));
    await userEvent.click(await screen.findByTestId("staff-mobile-role-barb"));
    await userEvent.click(await screen.findByRole("button", { name: "New conversation" }));
    await waitFor(() => expect(window.location.search).toContain("thread=th-created"));

    expect(window.location.search).toContain("role=barb");
    expect(window.location.search).toContain("section=console");
    expect(screen.getByRole("heading", { name: "New conversation" })).toBeInTheDocument();

    cleanup();
    mocks.fetchThreadMessages.mockClear();
    renderStaffRoute();

    expect(await screen.findByRole("heading", { name: "Conversation with barb" })).toBeInTheDocument();
    await waitFor(() => expect(mocks.fetchThreadMessages).toHaveBeenCalledWith("th-created", expect.any(AbortSignal)));
  });
});
