// @vitest-environment jsdom
/**
 * Holds.test.tsx — the Holds tab labels guardrail holds as standing rules,
 * distinct from schedule holds that actually block (#1726).
 */
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { Holds } from "../Holds";
import * as staffApi from "../staffApi";
import type { Hold } from "../staffApi";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function hold(overrides: Partial<Hold> = {}): Hold {
  return {
    id: "h1",
    text: "no bulk stale-queue cancel",
    set_on: "2026-09-22",
    lifted_when: "",
    applies_to: ["barb"],
    active: true,
    kind: "schedule",
    ...overrides,
  } as Hold;
}

describe("Holds tab guardrail vs schedule labeling (#1726)", () => {
  it("labels a guardrail hold 'Standing rule' and a schedule hold 'Hold'", async () => {
    vi.spyOn(staffApi, "fetchHolds").mockResolvedValue({
      holds: [
        hold({ id: "g1", text: "no direct force-push", kind: "guardrail" }),
        hold({ id: "s1", text: "freeze deploys", kind: "schedule" }),
      ],
      path: "",
    });

    render(<Holds roles={["barb"]} />);

    await waitFor(() => expect(screen.getByTestId("hold-g1")).toBeInTheDocument());

    expect(screen.getByTestId("hold-kind-g1")).toHaveTextContent("Standing rule");
    expect(screen.getByTestId("hold-kind-s1")).toHaveTextContent("Hold");
  });

  it("a newly added hold defaults to schedule kind (created via the Holds tab blocks scheduling)", async () => {
    vi.spyOn(staffApi, "fetchHolds").mockResolvedValue({ holds: [], path: "" });

    render(<Holds roles={["barb"]} />);

    await waitFor(() => expect(screen.getByText(/No holds\./)).toBeInTheDocument());
    screen.getByRole("button", { name: "Add hold" }).click();

    await waitFor(() => {
      const badges = screen.getAllByText("Hold");
      expect(badges.length).toBeGreaterThan(0);
    });
    expect(screen.queryByText("Standing rule")).not.toBeInTheDocument();
  });
});
