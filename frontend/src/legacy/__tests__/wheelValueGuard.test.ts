import { afterEach, describe, expect, it } from "vitest";
import { installWheelValueGuard } from "../wheelValueGuard";

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
