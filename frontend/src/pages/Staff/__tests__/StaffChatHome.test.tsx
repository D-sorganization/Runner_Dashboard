// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StaffPage } from "../StaffPage";

vi.mock("../../../hooks/useStaffQueries", () => ({
  useResolvedQueryClient: () => ({}),
  useStaffRoster: () => ({
    data: { roles: [] },
    isLoading: false,
    refetch: vi.fn(),
  }),
  invalidateStaffQueries: vi.fn(),
}));
vi.mock("../Board", () => ({ Board: () => <div>Board activity</div> }));
vi.mock("../InboxPanel", () => ({
  InboxPanel: () => <div>Pending approvals</div>,
}));
vi.mock("../Roster", () => ({ Roster: () => <div>Staff directory</div> }));
vi.mock("../../StaffConsole/Desktop", () => ({
  StaffConsoleDesktop: ({ chatFirst }: { chatFirst?: boolean }) => (
    <div>
      {chatFirst ? "Focused Barb conversation" : "Three-column console"}
    </div>
  ),
}));
afterEach(cleanup);

it("starts with the conversation and leaves operational panels available on demand", () => {
  render(<StaffPage />);
  const activity = screen
    .getByText("Activity and approvals")
    .closest("details");
  expect(activity).not.toHaveAttribute("open");
  expect(screen.getByText("Focused Barb conversation")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Roster" }));
  expect(activity).toHaveAttribute("open");
  expect(screen.getByText("Staff directory")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Console" }));
  expect(activity).not.toHaveAttribute("open");
});
