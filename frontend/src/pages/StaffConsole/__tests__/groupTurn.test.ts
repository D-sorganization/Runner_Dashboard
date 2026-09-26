/**
 * groupTurn.test.ts — parsing a Board group turn (SC-D7, #1342).
 */
import { describe, expect, it } from "vitest";
import { consensusMarkdown, groupIdOf, isGroupThread, parseGroupTurn, proposalPrefill } from "../groupTurn";
import type { ThreadInfo } from "../threadTypes";
import { PARTIAL_FAILURE } from "./groupTurnFixtures";

const THREAD: ThreadInfo = { id: "t1", title: "Board", kind: "direct", participants: [], status: "active" };

describe("isGroupThread / groupIdOf", () => {
  it("mirrors the backend rule: kind group, or meta.group / meta.is_group", () => {
    expect(isGroupThread({ ...THREAD, kind: "group" })).toBe(true);
    expect(isGroupThread({ ...THREAD, meta: { group: "board" } })).toBe(true);
    expect(isGroupThread({ ...THREAD, meta: { is_group: true } })).toBe(true);
    expect(isGroupThread(THREAD)).toBe(false);
    expect(isGroupThread(null)).toBe(false);
  });

  it("defaults the group id to board, like the backend", () => {
    expect(groupIdOf({ ...THREAD, kind: "group" })).toBe("board");
    expect(groupIdOf({ ...THREAD, meta: { group: "council" } })).toBe("council");
  });
});

describe("parseGroupTurn", () => {
  it("reads quorum, cost and every seat, including the one that did not answer", () => {
    const turn = parseGroupTurn(PARTIAL_FAILURE);
    expect(turn).not.toBeNull();
    expect(turn?.quorum).toBe("2/3 seats answered (Alpha, Bravo; Charlie: no response)");
    expect(turn?.costUsd).toBeCloseTo(0.0123);
    expect(turn?.seats.map((s) => [s.seat, s.answered])).toEqual([
      ["alpha", true],
      ["bravo", true],
      ["charlie", false],
    ]);
    expect(turn?.seats[2]).toMatchObject({ status: "timeout", errorDetail: "seat timed out after 30s" });
  });

  it("is null for a pending placeholder, a non-group message, or missing seat replies", () => {
    expect(parseGroupTurn({ ...PARTIAL_FAILURE, delivery: "pending" })).toBeNull();
    expect(parseGroupTurn({ ...PARTIAL_FAILURE, meta: {} })).toBeNull();
    expect(parseGroupTurn({ ...PARTIAL_FAILURE, meta: { is_group_turn: true } })).toBeNull();
  });

  it("drops malformed seat entries instead of inventing fields", () => {
    const turn = parseGroupTurn({
      ...PARTIAL_FAILURE,
      meta: { is_group_turn: true, seat_replies: { alpha: "nope", bravo: { status: "ok", text: "x" } } },
    });
    expect(turn?.seats.map((s) => s.seat)).toEqual(["bravo"]);
    expect(turn?.costUsd).toBeNull();
    expect(turn?.quorum).toBe("");
  });

  it("treats any status other than ok as no response", () => {
    const turn = parseGroupTurn({
      ...PARTIAL_FAILURE,
      meta: { is_group_turn: true, seat_replies: { delta: { seat_name: "delta", status: "error", text: "" } } },
    });
    expect(turn?.seats[0]).toMatchObject({ answered: false, status: "error" });
  });
});

describe("consensusMarkdown", () => {
  it("removes the seat-reply block that the card renders separately", () => {
    const md = consensusMarkdown(PARTIAL_FAILURE.body_md);
    expect(md).toContain("**Quorum:**");
    expect(md).not.toContain("<details>");
    expect(md).not.toContain("Ship it.");
  });
});

describe("proposalPrefill", () => {
  it("carries only what the seats said; the user chooses repos, urgency and cost", () => {
    const turn = parseGroupTurn(PARTIAL_FAILURE);
    if (!turn) throw new Error("fixture must parse");
    const prefill = proposalPrefill(turn);
    expect(prefill.evidence).toContain("2/3 seats answered");
    expect(prefill.evidence).toContain("- alpha: Ship it.");
    expect(prefill.evidence).toContain("- charlie: no response (timeout: seat timed out after 30s)");
    expect(Object.keys(prefill)).toEqual(["evidence"]);
  });
});
