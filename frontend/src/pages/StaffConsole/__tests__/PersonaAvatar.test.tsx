// @vitest-environment jsdom
/** Shared persona avatar: consistent role-to-image mapping across surfaces (#3501). */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { PersonaAvatar } from "../PersonaAvatar";

afterEach(cleanup);

describe("PersonaAvatar", () => {
  it("renders an <img> using the role's avatar URL when present", () => {
    const { container } = render(
      <PersonaAvatar roleName="board-secretary" initial="B" avatarUrl="/api/v1/staff/avatars/board-secretary.webp" variant="roster" />,
    );
    const img = container.querySelector("img.roster-avatar--image");
    expect(img).toHaveAttribute("src", "/api/v1/staff/avatars/board-secretary.webp");
    expect(img).toHaveAttribute("alt", "");
    expect(img).toHaveAttribute("aria-hidden", "true");
  });

  it("falls back to the letter circle when no avatar is present", () => {
    const { container } = render(<PersonaAvatar roleName="board-secretary" initial="B" variant="roster" />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".roster-avatar")).toHaveTextContent("B");
  });

  it("falls back to the letter circle when the avatar image fails to load", () => {
    const { container } = render(
      <PersonaAvatar roleName="board-secretary" initial="B" avatarUrl="/api/v1/staff/avatars/missing.webp" variant="roster" />,
    );
    const img = container.querySelector("img.roster-avatar--image");
    expect(img).not.toBeNull();
    fireEvent.error(img as HTMLImageElement);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".roster-avatar")).toHaveTextContent("B");
  });

  it("uses the thread variant's class names", () => {
    const { container } = render(
      <PersonaAvatar roleName="board-secretary" initial="B" avatarUrl="/api/v1/staff/avatars/board-secretary.webp" variant="thread" />,
    );
    expect(container.querySelector("img.thread-avatar--image")).not.toBeNull();
  });

  it("never shows a photo for the auto-route (rd-barb) persona, even when an avatar URL is set", () => {
    const { container } = render(
      <PersonaAvatar
        roleName="barb"
        initial="B"
        avatarUrl="/api/v1/staff/avatars/barb.webp"
        isAutoRoute
        variant="roster"
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".roster-avatar")).toHaveTextContent("★");
  });

  it("maps the same role name to the same tint across both variants", () => {
    const roster = render(<PersonaAvatar roleName="marge" initial="M" variant="roster" />);
    const rosterBg = (roster.container.querySelector(".roster-avatar") as HTMLElement).style.backgroundColor;
    roster.unmount();
    const thread = render(<PersonaAvatar roleName="marge" initial="M" variant="thread" />);
    const threadBg = (thread.container.querySelector(".thread-avatar") as HTMLElement).style.backgroundColor;
    thread.unmount();
    expect(rosterBg).toBe(threadBg);
  });
});
