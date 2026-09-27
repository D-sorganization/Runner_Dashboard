/**
 * panelTurn.test.ts — Unit tests for pure panel turn helpers (SC-D/Issue #1635).
 */
import { describe, expect, it } from "vitest";
import {
  groupByRound,
  isPanelThread,
  panelMeta,
  validatePanelForm,
  type PanelFormState,
} from "../panelTurn";
import type { ThreadInfo, ThreadMessage } from "../threadTypes";

describe("validatePanelForm", () => {
  const validForm: PanelFormState = {
    topic: "Should we rewrite the frontend in Elm?",
    mode: "debate",
    rounds: 3,
    experts: [
      { name: "Architect", perspective: "System reliability", provider: "claude" },
      { name: "Skeptic", perspective: "Risk and migration cost", provider: "claude" },
      { name: "Operator", perspective: "Monitoring and operations", provider: "claude" },
    ],
  };

  it("accepts a valid form", () => {
    expect(validatePanelForm(validForm)).toEqual([]);
  });

  it("rejects 2 experts (minimum 3 required)", () => {
    const form: PanelFormState = {
      ...validForm,
      experts: validForm.experts.slice(0, 2),
    };
    const errors = validatePanelForm(form);
    expect(errors.some((e) => e.toLowerCase().includes("3 to 4") || e.toLowerCase().includes("3"))).toBe(true);
  });

  it("rejects 5 experts (maximum 4 allowed)", () => {
    const form: PanelFormState = {
      ...validForm,
      experts: [
        ...validForm.experts,
        { name: "Theorist", perspective: "Theory", provider: "claude" },
        { name: "Reviewer", perspective: "Review", provider: "claude" },
      ],
    };
    const errors = validatePanelForm(form);
    expect(errors.some((e) => e.toLowerCase().includes("3 to 4") || e.toLowerCase().includes("4"))).toBe(true);
  });

  it("rejects duplicate expert names (case-insensitive)", () => {
    const form: PanelFormState = {
      ...validForm,
      experts: [
        { name: "Architect", perspective: "System", provider: "claude" },
        { name: "architect", perspective: "Duplicate", provider: "claude" },
        { name: "Operator", perspective: "Operations", provider: "claude" },
      ],
    };
    const errors = validatePanelForm(form);
    expect(errors.some((e) => e.toLowerCase().includes("unique"))).toBe(true);
  });

  it("rejects empty topic", () => {
    const form: PanelFormState = {
      ...validForm,
      topic: "   ",
    };
    const errors = validatePanelForm(form);
    expect(errors.some((e) => e.toLowerCase().includes("topic"))).toBe(true);
  });

  it("rejects rounds = 0 and rounds = 7 (allowed range 1..6)", () => {
    const form0: PanelFormState = { ...validForm, rounds: 0 };
    const errors0 = validatePanelForm(form0);
    expect(errors0.some((e) => e.toLowerCase().includes("1 and 6"))).toBe(true);

    const form7: PanelFormState = { ...validForm, rounds: 7 };
    const errors7 = validatePanelForm(form7);
    expect(errors7.some((e) => e.toLowerCase().includes("1 and 6"))).toBe(true);
  });

  it("rejects empty expert names", () => {
    const form: PanelFormState = {
      ...validForm,
      experts: [
        { name: "", perspective: "System", provider: "claude" },
        { name: "Skeptic", perspective: "Risk", provider: "claude" },
        { name: "Operator", perspective: "Ops", provider: "claude" },
      ],
    };
    const errors = validatePanelForm(form);
    expect(errors.some((e) => e.toLowerCase().includes("name"))).toBe(true);
  });

  it("rejects reserved 'Moderator' as an expert name", () => {
    const form: PanelFormState = {
      ...validForm,
      experts: [
        { name: "Moderator", perspective: "System", provider: "claude" },
        { name: "Skeptic", perspective: "Risk", provider: "claude" },
        { name: "Operator", perspective: "Ops", provider: "claude" },
      ],
    };
    const errors = validatePanelForm(form);
    expect(errors.some((e) => e.toLowerCase().includes("moderator"))).toBe(true);
  });
});

describe("panelMeta", () => {
  it("returns null on a non-panel message", () => {
    const nonPanelMsg: ThreadMessage = {
      id: "m1",
      thread_id: "t1",
      author: "user",
      author_kind: "user",
      kind: "text",
      body_md: "What do you think?",
      delivery: "complete",
    };
    expect(panelMeta(nonPanelMsg)).toBeNull();

    const topicMsg: ThreadMessage = {
      ...nonPanelMsg,
      id: "m2",
      meta: { panel_topic: true },
    };
    expect(panelMeta(topicMsg)).toBeNull();

    const synthesisMsg: ThreadMessage = {
      ...nonPanelMsg,
      id: "m3",
      meta: { is_panel_synthesis: true },
    };
    expect(panelMeta(synthesisMsg)).toBeNull();
  });

  it("extracts panel turn metadata when is_panel_turn is true", () => {
    const turnMsg: ThreadMessage = {
      id: "m4",
      thread_id: "t1",
      author: "Architect",
      author_kind: "staff",
      kind: "text",
      body_md: "I support this proposal.\nSTANCE: agree\nPOSITION: Adopt it incrementally.",
      delivery: "complete",
      meta: {
        is_panel_turn: true,
        panel_round: 1,
        panel_expert: "Architect",
        panel_status: "ok",
        panel_stance: "agree",
        panel_position: "Adopt it incrementally.",
      },
    };

    expect(panelMeta(turnMsg)).toEqual({
      round: 1,
      expert: "Architect",
      status: "ok",
      stance: "agree",
      position: "Adopt it incrementally.",
    });
  });
});

describe("groupByRound", () => {
  it("groups turns by round number in ascending order and leaves non-panel messages outside", () => {
    const topicMsg: ThreadMessage = {
      id: "msg-topic",
      thread_id: "t1",
      author: "user",
      author_kind: "user",
      kind: "text",
      body_md: "The topic",
      delivery: "complete",
      meta: { panel_topic: true },
    };

    const r2Turn: ThreadMessage = {
      id: "msg-r2-1",
      thread_id: "t1",
      author: "Architect",
      author_kind: "staff",
      kind: "text",
      body_md: "Round 2 response",
      delivery: "complete",
      meta: { is_panel_turn: true, panel_round: 2, panel_expert: "Architect", panel_status: "ok" },
    };

    const r1TurnA: ThreadMessage = {
      id: "msg-r1-1",
      thread_id: "t1",
      author: "Architect",
      author_kind: "staff",
      kind: "text",
      body_md: "Round 1 Architect",
      delivery: "complete",
      meta: { is_panel_turn: true, panel_round: 1, panel_expert: "Architect", panel_status: "ok" },
    };

    const r1TurnB: ThreadMessage = {
      id: "msg-r1-2",
      thread_id: "t1",
      author: "Skeptic",
      author_kind: "staff",
      kind: "text",
      body_md: "Round 1 Skeptic",
      delivery: "complete",
      meta: { is_panel_turn: true, panel_round: 1, panel_expert: "Skeptic", panel_status: "ok" },
    };

    const synthesisMsg: ThreadMessage = {
      id: "msg-syn",
      thread_id: "t1",
      author: "Moderator",
      author_kind: "staff",
      kind: "text",
      body_md: "Synthesis conclusion",
      delivery: "complete",
      meta: { is_panel_synthesis: true },
    };

    // Passed out of order to verify sorting by round
    const messages = [topicMsg, r2Turn, r1TurnA, r1TurnB, synthesisMsg];

    const rounds = groupByRound(messages);
    expect(rounds).toHaveLength(2);

    expect(rounds[0].round).toBe(1);
    expect(rounds[0].messages.map((m) => m.id)).toEqual(["msg-r1-1", "msg-r1-2"]);

    expect(rounds[1].round).toBe(2);
    expect(rounds[1].messages.map((m) => m.id)).toEqual(["msg-r2-1"]);

    // Neither topic nor synthesis should be included in round messages
    const allRoundMessageIds = rounds.flatMap((r) => r.messages.map((m) => m.id));
    expect(allRoundMessageIds).not.toContain("msg-topic");
    expect(allRoundMessageIds).not.toContain("msg-syn");
  });
});

describe("isPanelThread", () => {
  it("identifies panel threads by kind or meta", () => {
    const threadKindPanel: ThreadInfo = {
      id: "p1",
      title: "Panel",
      kind: "panel",
      participants: [],
      status: "active",
    };
    expect(isPanelThread(threadKindPanel)).toBe(true);

    const threadMetaPanel: ThreadInfo = {
      id: "p2",
      title: "Panel via meta",
      kind: "direct",
      participants: [],
      status: "active",
      meta: { is_panel: true },
    };
    expect(isPanelThread(threadMetaPanel)).toBe(true);

    const normalThread: ThreadInfo = {
      id: "d1",
      title: "Direct",
      kind: "direct",
      participants: [],
      status: "active",
    };
    expect(isPanelThread(normalThread)).toBe(false);
    expect(isPanelThread(null)).toBe(false);
    expect(isPanelThread(undefined)).toBe(false);
  });
});
