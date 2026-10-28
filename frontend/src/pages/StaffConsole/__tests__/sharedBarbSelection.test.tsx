import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BrowserRouter, useLocation, useNavigate } from "react-router-dom";
import { StaffPage } from "../../Staff/StaffPage";
import { BarbSidebar } from "../../../shell/BarbSidebar";
import { queryClient } from "../../../hooks/usePollingQueries";

const threads = [
  {
    id: "a",
    title: "Barb A",
    kind: "auto",
    participants: ["barb"],
    status: "active",
  },
  {
    id: "b",
    title: "Barb B",
    kind: "auto",
    participants: ["barb"],
    status: "active",
  },
  {
    id: "specialist",
    title: "Specialist",
    kind: "direct",
    participants: ["maintenance"],
    status: "active",
  },
];
const api = vi.hoisted(() => ({
  fetchThread: vi.fn(),
  fetchThreads: vi.fn(),
  createThread: vi.fn(),
  fetchThreadMessages: vi.fn(),
  postThreadMessage: vi.fn(),
  fetchRoster: vi.fn(),
}));
vi.mock("../../Staff/staffApi", async (original) => ({
  ...(await original<typeof import("../../Staff/staffApi")>()),
  ...api,
}));

function Surfaces() {
  const location = useLocation();
  const navigate = useNavigate();
  const select = (id: string, role: string) =>
    navigate(`/staff?thread=${id}&role=${role}`);
  return (
    <>
      <StaffPage
        routeThreadId={new URLSearchParams(location.search).get("thread")}
        onThreadSelected={select}
      />
      <BarbSidebar
        open
        onClose={() => {}}
        onNavigate={() => {}}
        onThreadSelected={select}
      />
    </>
  );
}

beforeEach(() => {
  window.history.replaceState({}, "", "/staff?thread=a&role=barb");
  const roles = ["barb", "maintenance"].map((name) => ({
    name,
    title: name,
    summary: name,
    group: "leadership",
    valid: true,
    providers: [],
  }));
  api.fetchRoster.mockResolvedValue({ roles });
  api.fetchThreads.mockResolvedValue({ threads });
  api.fetchThread.mockImplementation(async (id: string) => ({
    thread: threads.find((thread) => thread.id === id),
    messages: [],
  }));
  api.fetchThreadMessages.mockResolvedValue({ messages: [] });
  api.createThread.mockResolvedValue(threads[1]);
  api.postThreadMessage.mockResolvedValue({});
});
afterEach(() => {
  cleanup();
  queryClient.clear();
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/");
});

it("sidebar selection updates the mounted central console and both composers send to it", async () => {
  render(
    <BrowserRouter>
      <Surfaces />
    </BrowserRouter>,
  );
  const consoleRegion = within(
    screen.getByRole("region", { name: "Staff conversation" }),
  );
  const sidebar = within(screen.getByTestId("barb-sidebar"));
  await consoleRegion.findByRole("heading", { name: "Barb A" });
  await sidebar.findByRole("button", { name: "New chat" });
  fireEvent.click(sidebar.getByRole("button", { name: "New chat" }));
  await consoleRegion.findByRole("heading", { name: "Barb B" });
  for (const surface of [consoleRegion, sidebar]) {
    fireEvent.change(
      surface.getByRole("textbox", { name: /staff conversation input/i }),
      { target: { value: "hello" } },
    );
    fireEvent.click(surface.getByRole("button", { name: /^send/i }));
  }
  await waitFor(() => expect(api.postThreadMessage).toHaveBeenCalledTimes(2));
  expect(api.postThreadMessage.mock.calls.map((call) => call[0])).toEqual([
    "b",
    "b",
  ]);
});

it("deep links restore both surfaces and a central specialist selection preserves the sidebar", async () => {
  window.history.replaceState({}, "", "/staff?thread=b&role=barb");
  render(
    <BrowserRouter>
      <Surfaces />
    </BrowserRouter>,
  );
  const central = within(
    screen.getByRole("region", { name: "Staff conversation" }),
  );
  await central.findByRole("heading", { name: "Barb B" });
  fireEvent.click(central.getByRole("button", { name: "Choose staff" }));
  fireEvent.click(await screen.findByTestId("roster-row-maintenance"));
  await central.findByRole("heading", { name: "Specialist" });
  const sidebar = within(screen.getByTestId("barb-sidebar"));
  fireEvent.change(
    sidebar.getByRole("textbox", { name: /staff conversation input/i }),
    { target: { value: "still Barb" } },
  );
  await waitFor(() =>
    expect(sidebar.getByRole("button", { name: /^send/i })).toBeEnabled(),
  );
  fireEvent.click(sidebar.getByRole("button", { name: /^send/i }));
  await waitFor(() => expect(api.postThreadMessage).toHaveBeenCalled());
  expect(api.postThreadMessage.mock.calls[0][0]).toBe("b");
});

it("central New conversation refreshes the sidebar's authorized list for the new Barb thread", async () => {
  api.fetchThreads.mockResolvedValueOnce({ threads: [threads[0]] });
  render(
    <BrowserRouter>
      <Surfaces />
    </BrowserRouter>,
  );
  const central = within(
    screen.getByRole("region", { name: "Staff conversation" }),
  );
  const sidebar = within(screen.getByTestId("barb-sidebar"));
  await central.findByRole("heading", { name: "Barb A" });
  await sidebar.findByRole("button", { name: "New chat" });
  fireEvent.click(central.getByRole("button", { name: "New conversation" }));
  await central.findByRole("heading", { name: "Barb B" });
  await waitFor(() =>
    expect(new URLSearchParams(window.location.search).get("thread")).toBe("b"),
  );
  fireEvent.change(
    sidebar.getByRole("textbox", { name: /staff conversation input/i }),
    { target: { value: "new mission" } },
  );
  await waitFor(() =>
    expect(sidebar.getByRole("button", { name: /^send/i })).toBeEnabled(),
  );
  fireEvent.click(sidebar.getByRole("button", { name: /^send/i }));
  await waitFor(() => expect(api.postThreadMessage).toHaveBeenCalled());
  expect(api.postThreadMessage.mock.calls[0][0]).toBe("b");
});
