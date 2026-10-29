// @vitest-environment jsdom
/** RosterRow renders a persona avatar image when present, falling back to the letter circle (#2680). */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RosterRow } from "../RosterRow";
import type { StaffRoleItem } from "../types";

afterEach(cleanup);

const BASE_ROLE: StaffRoleItem = {
  name: "barb",
  title: "Executive Secretary",
  summary: "Secretary & attention gate.",
};

describe("RosterRow avatar", () => {
  it("renders an <img> using the role's avatar URL when present", () => {
    render(
      <RosterRow
        role={{ ...BASE_ROLE, avatar: "/api/v1/staff/avatars/barb.webp" }}
        isSelected={false}
        isPinned={false}
        onSelect={() => {}}
      />,
    );
    const img = screen.getByTestId("roster-row-barb").querySelector("img.roster-avatar--image");
    expect(img).toHaveAttribute("src", "/api/v1/staff/avatars/barb.webp");
  });

  it("falls back to the letter circle when no avatar is present", () => {
    render(<RosterRow role={BASE_ROLE} isSelected={false} isPinned={false} onSelect={() => {}} />);
    const row = screen.getByTestId("roster-row-barb");
    expect(row.querySelector("img")).toBeNull();
    expect(row.querySelector(".roster-avatar")).toHaveTextContent("E");
  });

  it("falls back to the letter circle when the avatar image fails to load", () => {
    render(
      <RosterRow
        role={{ ...BASE_ROLE, avatar: "/api/v1/staff/avatars/missing.webp" }}
        isSelected={false}
        isPinned={false}
        onSelect={() => {}}
      />,
    );
    const row = screen.getByTestId("roster-row-barb");
    const img = row.querySelector("img.roster-avatar--image");
    expect(img).not.toBeNull();
    fireEvent.error(img as HTMLImageElement);
    expect(row.querySelector("img")).toBeNull();
    expect(row.querySelector(".roster-avatar")).toHaveTextContent("E");
  });
});
