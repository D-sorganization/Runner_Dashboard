// @vitest-environment jsdom
/**
 * PanelThread.test.tsx — Thread rendering tests for expert panel conversations (SC-D/Issue #1635).
 */
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PanelResult } from "../panelApi";
import { Thread } from "../Thread";
import type { ThreadInfo, ThreadMessage } from "../threadTypes";

const panelApiMock = vi.hoisted(() => ({
  fetchPanel: vi.fn(),
}));

vi.mock("../panelApi", async (importOriginal) => {
  const original = await importOriginal<typeof import("../panelApi")>();
  return {
    ...original,
    fetchPanel: panelApiMock.fetchPanel,
  };
});

const mockPanelThread: ThreadInfo = {
  id: "thread-panel-abc",
  title: "Panel on Microservices Architecture",
  kind: "panel",
  participants: ["Architect", "Skeptic", "Moderator"],
  status: "active",
};

const mockPanelResult: PanelResult = {
  thread_id: "thread-panel-abc",
  title: "Panel on Microservices Architecture",
  status: "complete",
  mode: "debate",
  rounds: 3,
  rounds_used: 2,
  consensus: true,
  experts: [
    { name: "Architect", perspective: "Architecture" },
    { name: "Skeptic", perspective: "Risk" },
  ],
  turns: [],
  synthesis: "Moderator synthesis: consensus was reached.",
};

const mockMessages: ThreadMessage[] = [
  {
    id: "m-topic",
    thread_id: "thread-panel-abc",
    author: "user",
    author_kind: "user",
    kind: "text",
    body_md: "Should we adopt microservices?",
    delivery: "complete",
    meta: { panel_topic: true },
  },
  {
    id: "m-r1-1",
    thread_id: "thread-panel-abc",
    author: "Architect",
    author_kind: "staff",
    kind: "text",
    body_md: "Microservices improve developer velocity.",
    delivery: "complete",
    meta: {
      is_panel_turn: true,
      panel_round: 1,
      panel_expert: "Architect",
      panel_status: "ok",
      panel_stance: "agree",
      panel_position: "Strongly in favor of decoupling services.",
    },
  },
  {
    id: "m-r1-2",
    thread_id: "thread-panel-abc",
    author: "Skeptic",
    author_kind: "staff",
    kind: "text",
    body_md: "Operational overhead is too high for small teams.",
    delivery: "complete",
    meta: {
      is_panel_turn: true,
      panel_round: 1,
      panel_expert: "Skeptic",
      panel_status: "ok",
      panel_stance: "partly",
      panel_position: "Only with mature platform automation.",
    },
  },
  {
    id: "m-r2-1",
    thread_id: "thread-panel-abc",
    author: "Architect",
    author_kind: "staff",
    kind: "text",
    body_md: "We can provide automated templates.",
    delivery: "complete",
    meta: {
      is_panel_turn: true,
      panel_round: 2,
      panel_expert: "Architect",
      panel_status: "ok",
      panel_stance: "agree",
      panel_position: "Decouple with self-service templates.",
    },
  },
  {
    id: "m-r2-2",
    thread_id: "thread-panel-abc",
    author: "Skeptic",
    author_kind: "staff",
    kind: "text",
    body_md: "Skeptic timed out.",
    delivery: "failed",
    meta: {
      is_panel_turn: true,
      panel_round: 2,
      panel_expert: "Skeptic",
      panel_status: "timeout",
      panel_stance: null,
      panel_position: null,
    },
  },
  {
    id: "m-synth",
    thread_id: "thread-panel-abc",
    author: "Moderator",
    author_kind: "staff",
    kind: "text",
    body_md: "Moderator synthesis: consensus was reached.",
    delivery: "complete",
    meta: {
      is_panel_synthesis: true,
      panel_expert: "Moderator",
    },
  },
];

describe("Panel thread rendering", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    panelApiMock.fetchPanel.mockResolvedValue(mockPanelResult);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders round headers, stance chips, error status, and consensus card", async () => {
    render(
      <Thread
        thread={mockPanelThread}
        messages={mockMessages}
        onSendMessage={vi.fn().mockResolvedValue({ ok: true })}
      />,
    );

    // Round headers
    expect(screen.getByText("Round 1")).toBeInTheDocument();
    expect(screen.getByText("Round 2")).toBeInTheDocument();

    // Stance chips
    expect(screen.getAllByTestId("stance-chip-agree").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByTestId("stance-chip-partly")).toBeInTheDocument();

    // Timeout status chip
    expect(screen.getByTestId("status-chip-timeout")).toBeInTheDocument();

    // One-line positions
    expect(screen.getByText("Strongly in favor of decoupling services.")).toBeInTheDocument();
    expect(screen.getByText("Only with mature platform automation.")).toBeInTheDocument();

    // Consensus card
    await waitFor(() => {
      expect(screen.getByRole("article", { name: /panel consensus/i })).toBeInTheDocument();
    });

    expect(screen.getByText("Consensus reached")).toBeInTheDocument();
    expect(screen.getByText("2 of 3 rounds used")).toBeInTheDocument();
    expect(screen.getByText("Moderator synthesis: consensus was reached.")).toBeInTheDocument();

    // Composer disabled in panel thread
    expect(
      screen.getByText("Panels run on their own; start a new panel to ask again."),
    ).toBeInTheDocument();
    const sendButton = screen.getByRole("button", { name: /send message/i });
    expect(sendButton).toBeDisabled();
  });
});
