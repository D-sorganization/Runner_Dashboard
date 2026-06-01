// @vitest-environment jsdom
/**
 * Tests for the desktop-shell layout flag (issue #802, part of #796).
 *
 * Contract (precedence, highest wins):
 *   1. localStorage `dashboard.layout` === "legacy"  → false (escape hatch)
 *   2. localStorage `dashboard.layout` === "modern"  → true  (opt-in)
 *   3. env VITE_DESKTOP_SHELL === "0"/"off"/"legacy" → false
 *   4. env VITE_DESKTOP_SHELL === "1"/"on"/"modern"  → true
 *   5. default                                       → true  (modern is default)
 *
 * The modern desktop shell is the DEFAULT, but it stays fully reversible: an
 * operator can pin the legacy shell via localStorage or a build-time env var.
 */
import { afterEach, describe, expect, it } from "vitest";
import { resolveDesktopShellLayout } from "../layoutFlag";

afterEach(() => {
  window.localStorage.clear();
});

describe("resolveDesktopShellLayout", () => {
  it("defaults to the modern desktop shell when nothing is set", () => {
    expect(resolveDesktopShellLayout({})).toBe(true);
  });

  it("honours env opt-out", () => {
    expect(resolveDesktopShellLayout({ env: "0" })).toBe(false);
    expect(resolveDesktopShellLayout({ env: "off" })).toBe(false);
    expect(resolveDesktopShellLayout({ env: "legacy" })).toBe(false);
  });

  it("honours env opt-in", () => {
    expect(resolveDesktopShellLayout({ env: "1" })).toBe(true);
    expect(resolveDesktopShellLayout({ env: "modern" })).toBe(true);
  });

  it("localStorage legacy overrides an env opt-in (reversible escape hatch)", () => {
    window.localStorage.setItem("dashboard.layout", "legacy");
    expect(resolveDesktopShellLayout({ env: "modern" })).toBe(false);
  });

  it("localStorage modern overrides an env opt-out", () => {
    window.localStorage.setItem("dashboard.layout", "modern");
    expect(resolveDesktopShellLayout({ env: "0" })).toBe(true);
  });

  it("ignores unrelated localStorage values and falls back to env/default", () => {
    window.localStorage.setItem("dashboard.layout", "banana");
    expect(resolveDesktopShellLayout({ env: "0" })).toBe(false);
    expect(resolveDesktopShellLayout({})).toBe(true);
  });
});
