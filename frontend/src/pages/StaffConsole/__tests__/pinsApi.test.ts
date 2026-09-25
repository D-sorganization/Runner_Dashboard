import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchPins, pinRole, unpinRole } from "../pinsApi";

describe("pinsApi", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("fetches pins from server and stores in localStorage", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ pins: ["barb", "orchestrator"] }),
      })
    );

    const pins = await fetchPins();
    expect(pins).toEqual(["barb", "orchestrator"]);
    expect(localStorage.getItem("staff_console_pinned_roles")).toBe(
      JSON.stringify(["barb", "orchestrator"])
    );
  });

  it("falls back to localStorage if fetch fails", async () => {
    localStorage.setItem(
      "staff_console_pinned_roles",
      JSON.stringify(["librarian"])
    );
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Network Error")));

    const pins = await fetchPins();
    expect(pins).toEqual(["librarian"]);
  });

  it("pins a role and updates server and localStorage", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ pins: ["cartographer"] }),
      })
    );

    const pins = await pinRole("cartographer");
    expect(pins).toEqual(["cartographer"]);
    expect(localStorage.getItem("staff_console_pinned_roles")).toBe(
      JSON.stringify(["cartographer"])
    );
  });

  it("unpins a role and updates server and localStorage", async () => {
    localStorage.setItem(
      "staff_console_pinned_roles",
      JSON.stringify(["barb", "cartographer"])
    );

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ pins: ["barb"] }),
      })
    );

    const pins = await unpinRole("cartographer");
    expect(pins).toEqual(["barb"]);
    expect(localStorage.getItem("staff_console_pinned_roles")).toBe(
      JSON.stringify(["barb"])
    );
  });
});
