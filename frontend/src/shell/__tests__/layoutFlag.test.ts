// @vitest-environment jsdom
/**
 * The Classic layout is retired (#1345). A browser that pinned it with
 * `localStorage["dashboard.layout"] = "legacy"` gets the modern shell and one
 * notice: the stored preference is removed the first time it is seen.
 */
import { afterEach, describe, expect, it } from "vitest";
import { LAYOUT_STORAGE_KEY, retireLegacyLayoutPreference } from "../layoutFlag";

afterEach(() => {
  window.localStorage.clear();
});

describe("retireLegacyLayoutPreference", () => {
  it("reports and removes a stored Classic layout preference once", () => {
    window.localStorage.setItem(LAYOUT_STORAGE_KEY, "legacy");

    expect(retireLegacyLayoutPreference()).toBe(true);
    expect(window.localStorage.getItem(LAYOUT_STORAGE_KEY)).toBeNull();
    expect(retireLegacyLayoutPreference()).toBe(false);
  });

  it("removes any other stored layout value without a notice", () => {
    window.localStorage.setItem(LAYOUT_STORAGE_KEY, "modern");

    expect(retireLegacyLayoutPreference()).toBe(false);
    expect(window.localStorage.getItem(LAYOUT_STORAGE_KEY)).toBeNull();
  });

  it("does nothing when nothing is stored", () => {
    expect(retireLegacyLayoutPreference()).toBe(false);
  });

  it("never throws when storage is unavailable", () => {
    const storage = {
      getItem: () => {
        throw new Error("blocked");
      },
      removeItem: () => {
        throw new Error("blocked");
      },
    };
    expect(retireLegacyLayoutPreference(storage)).toBe(false);
  });
});
