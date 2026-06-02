// @vitest-environment jsdom
/**
 * Behaviour tests for pages/AssistantSidebar — the chat assistant sidebar and
 * the floating Dashboard Help button, extracted from the legacy App.tsx
 * (decomposition #836, pass 9).
 *
 * Covers: collapsed/expanded rendering and aria-hidden, the help toggle, the
 * settings panel toggle, sending a message (POST /api/assistant/chat) and the
 * assistant reply, the error path, and save-history persistence/clearing.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AssistantSidebar,
  DashboardHelp,
} from "../AssistantSidebar";
import { ASST_LS } from "../../lib/assistantStorage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
});
beforeEach(() => {
  localStorage.clear();
  vi.spyOn(console, "error").mockImplementation(() => {});
});

function mockChat(opts: { reply?: string; ok?: boolean } = {}) {
  const fn = vi.fn(() =>
    Promise.resolve({
      ok: opts.ok !== false,
      status: opts.ok === false ? 500 : 200,
      json: () => Promise.resolve({ response: opts.reply ?? "the answer" }),
    } as Response),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("DashboardHelp", () => {
  it("shows the help panel after clicking the ? button and hides it on Close", () => {
    render(<DashboardHelp currentTab="fleet" />);
    fireEvent.click(screen.getByTitle("Dashboard help"));
    expect(screen.getByText("Dashboard Help")).toBeInTheDocument();
    expect(screen.getByText("Current tab: fleet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close assessment dialog" }));
    expect(screen.queryByText("Dashboard Help")).toBeNull();
  });
});

describe("AssistantSidebar", () => {
  it("is aria-hidden and renders no chat controls when closed", () => {
    mockChat();
    render(<AssistantSidebar currentTab="fleet" open={false} onToggle={() => {}} />);
    const region = screen.getByRole("complementary", { hidden: true });
    expect(region).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByText("💬 Chat")).toBeNull();
  });

  it("renders the chat header and empty placeholder when open", () => {
    mockChat();
    render(<AssistantSidebar currentTab="fleet" open onToggle={() => {}} />);
    expect(screen.getByText("💬 Chat")).toBeInTheDocument();
    expect(
      screen.getByText("Ask anything about the dashboard…"),
    ).toBeInTheDocument();
  });

  it("invokes onToggle when the close button is clicked", () => {
    mockChat();
    const onToggle = vi.fn();
    render(<AssistantSidebar currentTab="fleet" open onToggle={onToggle} />);
    fireEvent.click(screen.getByRole("button", { name: "Close assistant" }));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("opens the settings panel via the gear button", () => {
    mockChat();
    render(<AssistantSidebar currentTab="fleet" open onToggle={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Assistant settings" }));
    expect(screen.getByText("Settings")).toBeInTheDocument();
    expect(screen.getByText("Open by default")).toBeInTheDocument();
  });

  it("sends a message and appends the assistant reply", async () => {
    const fn = mockChat({ reply: "hi there" });
    render(<AssistantSidebar currentTab="fleet" open onToggle={() => {}} />);
    fireEvent.change(
      screen.getByPlaceholderText("Ask a question… (Enter to send)"),
      { target: { value: "hello" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("hi there")).toBeInTheDocument();
    expect(screen.getByText("hello")).toBeInTheDocument();
    const [, init] = fn.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(body.prompt).toBe("hello");
    expect(body.context.current_tab).toBe("fleet");
  });

  it("appends an error bubble when the chat request fails", async () => {
    mockChat({ ok: false });
    render(<AssistantSidebar currentTab="fleet" open onToggle={() => {}} />);
    fireEvent.change(
      screen.getByPlaceholderText("Ask a question… (Enter to send)"),
      { target: { value: "boom" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText(/^Error: /)).toBeInTheDocument();
  });

  it("persists the transcript when save-history is enabled", async () => {
    mockChat({ reply: "saved reply" });
    render(<AssistantSidebar currentTab="fleet" open onToggle={() => {}} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Save chat history" }));
    fireEvent.change(
      screen.getByPlaceholderText("Ask a question… (Enter to send)"),
      { target: { value: "remember me" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("saved reply");
    await waitFor(() => {
      const stored = localStorage.getItem(ASST_LS.transcript);
      expect(stored).toContain("remember me");
    });
  });

  it("clears persisted history when save-history is toggled off", async () => {
    mockChat();
    render(<AssistantSidebar currentTab="fleet" open onToggle={() => {}} />);
    const toggle = screen.getByRole("checkbox", { name: "Save chat history" });
    fireEvent.click(toggle); // on
    fireEvent.click(toggle); // off
    await waitFor(() =>
      expect(localStorage.getItem(ASST_LS.transcript)).toBeNull(),
    );
  });
});
