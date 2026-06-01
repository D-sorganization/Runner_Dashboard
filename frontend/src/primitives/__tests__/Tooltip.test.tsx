// @vitest-environment jsdom
/**
 * Behaviour tests for the Tooltip primitive (issue #801, part of #796).
 *
 * Contract:
 *  - shows on hover (mouseenter) after a small delay;
 *  - shows on keyboard focus (focusin) — accessibility parity with hover;
 *  - hides on mouseleave / blur / Escape;
 *  - associates the tooltip with its trigger via aria-describedby;
 *  - renders the trigger's existing children/handlers untouched (composition).
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import {
  cleanup,
  render,
  screen,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Tooltip } from "../Tooltip";

afterEach(cleanup);
beforeEach(() => {
  vi.useRealTimers();
});

describe("Tooltip", () => {
  it("renders its trigger child", () => {
    render(
      <Tooltip content="Helpful hint">
        <button type="button">Do thing</button>
      </Tooltip>,
    );
    expect(screen.getByRole("button", { name: "Do thing" })).toBeInTheDocument();
  });

  it("is hidden until interaction", () => {
    render(
      <Tooltip content="Helpful hint">
        <button type="button">Do thing</button>
      </Tooltip>,
    );
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("shows on hover after the delay and hides on mouseleave", async () => {
    render(
      <Tooltip content="Helpful hint" delayMs={100}>
        <button type="button">Do thing</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole("button", { name: "Do thing" });
    fireEvent.mouseEnter(trigger);
    // Appears after the delay.
    await waitFor(() => expect(screen.getByRole("tooltip")).toHaveTextContent("Helpful hint"));
    fireEvent.mouseLeave(trigger);
    await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());
  });

  it("shows on focus and hides on blur (keyboard parity)", async () => {
    render(
      <Tooltip content="Helpful hint" delayMs={0}>
        <button type="button">Do thing</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole("button", { name: "Do thing" });
    fireEvent.focus(trigger);
    await waitFor(() => expect(screen.getByRole("tooltip")).toBeInTheDocument());
    fireEvent.blur(trigger);
    await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());
  });

  it("hides on Escape", async () => {
    render(
      <Tooltip content="Helpful hint" delayMs={0}>
        <button type="button">Do thing</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole("button", { name: "Do thing" });
    fireEvent.focus(trigger);
    await waitFor(() => expect(screen.getByRole("tooltip")).toBeInTheDocument());
    fireEvent.keyDown(trigger, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());
  });

  it("associates the tooltip with the trigger via aria-describedby", async () => {
    render(
      <Tooltip content="Helpful hint" delayMs={0}>
        <button type="button">Do thing</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole("button", { name: "Do thing" });
    fireEvent.focus(trigger);
    const tip = await screen.findByRole("tooltip");
    expect(trigger).toHaveAttribute("aria-describedby", tip.id);
  });

  it("preserves the child's own event handlers", async () => {
    const onFocus = vi.fn();
    const onMouseEnter = vi.fn();
    render(
      <Tooltip content="Helpful hint" delayMs={0}>
        <button type="button" onFocus={onFocus} onMouseEnter={onMouseEnter}>
          Do thing
        </button>
      </Tooltip>,
    );
    const trigger = screen.getByRole("button", { name: "Do thing" });
    fireEvent.focus(trigger);
    fireEvent.mouseEnter(trigger);
    expect(onFocus).toHaveBeenCalledTimes(1);
    expect(onMouseEnter).toHaveBeenCalledTimes(1);
  });

  it("does not render an empty tooltip when content is blank", () => {
    render(
      <Tooltip content="" delayMs={0}>
        <button type="button">Do thing</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole("button", { name: "Do thing" });
    fireEvent.focus(trigger);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
