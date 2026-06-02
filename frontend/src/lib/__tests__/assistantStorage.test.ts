/**
 * Unit tests for lib/assistantStorage — the chat-assistant localStorage helpers
 * extracted from the legacy App.tsx (decomposition #836, pass 9).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ASST_HISTORY_TTL_MS,
  ASST_LS,
  clearAssistantTranscriptHistory,
  lsGet,
  lsLoadTranscript,
  lsSet,
} from "../assistantStorage";

beforeEach(() => {
  localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("lsGet / lsSet", () => {
  it("round-trips a JSON value", () => {
    lsSet("k", { a: 1, b: ["x"] });
    expect(lsGet("k", null)).toEqual({ a: 1, b: ["x"] });
  });

  it("returns the fallback when the key is absent", () => {
    expect(lsGet("missing", "fallback")).toBe("fallback");
  });

  it("returns the fallback when the stored value is unparseable", () => {
    localStorage.setItem("bad", "{not json");
    expect(lsGet("bad", 42)).toBe(42);
  });

  it("swallows setItem quota errors without throwing", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceeded");
    });
    expect(() => lsSet("k", "v")).not.toThrow();
  });
});

describe("lsLoadTranscript", () => {
  it("returns the saved transcript when within the TTL", () => {
    const msgs = [{ role: "user", content: "hi", id: 1 }];
    localStorage.setItem(ASST_LS.transcript, JSON.stringify(msgs));
    localStorage.setItem(ASST_LS.transcriptTimestamp, String(Date.now()));
    expect(lsLoadTranscript()).toEqual(msgs);
  });

  it("discards (and clears) an expired transcript", () => {
    localStorage.setItem(
      ASST_LS.transcript,
      JSON.stringify([{ role: "user", content: "old", id: 1 }]),
    );
    localStorage.setItem(
      ASST_LS.transcriptTimestamp,
      String(Date.now() - ASST_HISTORY_TTL_MS - 1000),
    );
    expect(lsLoadTranscript()).toEqual([]);
    expect(localStorage.getItem(ASST_LS.transcript)).toBeNull();
    expect(localStorage.getItem(ASST_LS.transcriptTimestamp)).toBeNull();
  });

  it("returns [] when no timestamp is present", () => {
    localStorage.setItem(
      ASST_LS.transcript,
      JSON.stringify([{ role: "user", content: "x", id: 1 }]),
    );
    expect(lsLoadTranscript()).toEqual([]);
  });
});

describe("clearAssistantTranscriptHistory", () => {
  it("removes the transcript and its timestamp", () => {
    localStorage.setItem(ASST_LS.transcript, "[]");
    localStorage.setItem(ASST_LS.transcriptTimestamp, "123");
    clearAssistantTranscriptHistory();
    expect(localStorage.getItem(ASST_LS.transcript)).toBeNull();
    expect(localStorage.getItem(ASST_LS.transcriptTimestamp)).toBeNull();
  });

  it("does not throw when removeItem fails", () => {
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("boom");
    });
    expect(() => clearAssistantTranscriptHistory()).not.toThrow();
  });
});

describe("ASST_LS registry", () => {
  it("namespaces every key under assistant:", () => {
    for (const v of Object.values(ASST_LS)) {
      expect(v.startsWith("assistant:")).toBe(true);
    }
  });
});
