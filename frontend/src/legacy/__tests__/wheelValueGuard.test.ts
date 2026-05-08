import { afterEach, describe, expect, it } from "vitest";
import { installWheelValueGuard } from "../wheelValueGuard";

// WheelEvent polyfill for jsdom environment
class WheelEventPolyfill extends Event {
  deltaX: number;
  deltaY: number;
  deltaZ: number;

  constructor(type: string, options?: WheelEventInit) {
    super(type, options);
    this.deltaX = options?.deltaX ?? 0;
    this.deltaY = options?.deltaY ?? 0;
    this.deltaZ = options?.deltaZ ?? 0;
  }
}
const WheelEvent = (globalThis as any).WheelEvent ?? WheelEventPolyfill;

describe("installWheelValueGuard", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("blurs a focused number input when the mouse wheel is used", () => {
    document.body.innerHTML = '<input id="number-field" type="number" value="3" />';
    const cleanup = installWheelValueGuard(document);
    const input = document.getElementById("number-field") as HTMLInputElement;

    input.focus();
    input.dispatchEvent(new WheelEvent("wheel", { bubbles: true }));

    expect(document.activeElement).not.toBe(input);
    cleanup();
  });

  it("leaves focused text inputs alone", () => {
    document.body.innerHTML = '<input id="text-field" type="text" value="runner" />';
    const cleanup = installWheelValueGuard(document);
    const input = document.getElementById("text-field") as HTMLInputElement;

    input.focus();
    input.dispatchEvent(new WheelEvent("wheel", { bubbles: true }));

    expect(document.activeElement).toBe(input);
    cleanup();
  });

  it("blurs a focused select when the mouse wheel is used", () => {
    document.body.innerHTML = `
      <select id="repo-select">
        <option value="one">One</option>
        <option value="two">Two</option>
      </select>
    `;
    const cleanup = installWheelValueGuard(document);
    const select = document.getElementById("repo-select") as HTMLSelectElement;

    select.focus();
    select.dispatchEvent(new WheelEvent("wheel", { bubbles: true }));

    expect(document.activeElement).not.toBe(select);
    cleanup();
  });
});
