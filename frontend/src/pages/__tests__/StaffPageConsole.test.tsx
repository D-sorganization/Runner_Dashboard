// @vitest-environment jsdom
/**
 * StaffPage opens on the three-pane Staff Console (#1446); the hub sections
 * (Roster, Runs, Assign, Holds) stay reachable as tabs.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { queryClient } from "../../hooks/usePollingQueries";
import { StaffPage } from "../Staff";

const ROSTER = {
  machine: "hub-a",
  active_runs: 0,
  providers: {},
  roles: [{ name: "night-watch", title: "Night Watch", summary: "Sweeps red main.", providers: [] }],
};

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
}

afterEach(() => {
  cleanup();
  queryClient.clear();
  vi.unstubAllGlobals();
});

describe("StaffPage console", () => {
  it("opens on the chat-first Staff Console with the roster available on demand", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        /\/staff\/roster$/.test(url) ? jsonResponse(200, ROSTER) : jsonResponse(404, { detail: "Not Found" }),
      ),
    );
    render(<StaffPage />);

    expect(screen.getByRole("tab", { name: "Console" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("staff-console-desktop")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Staff conversation" })).toBeInTheDocument();
    expect(screen.queryByTestId("roster-row-night-watch")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Choose staff" }));
    await waitFor(() => expect(screen.getByTestId("roster-row-night-watch")).toBeInTheDocument());
    expect(screen.queryByTestId("role-card-night-watch")).not.toBeInTheDocument();
    for (const tab of ["Roster", "Runs", "Assign", "Holds"]) {
      expect(screen.getByRole("tab", { name: tab })).toBeInTheDocument();
    }
  });

  it("opens the thread named by ?thread= so its proposal can be decided (#1712)", async () => {
    window.history.pushState({}, "", "/staff?thread=th_barb_prop");
    const thread = { id: "th_barb_prop", title: "Barb proposal thread", kind: "direct", participants: ["barb"], status: "active" };
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (/\/staff\/roster$/.test(url)) return jsonResponse(200, ROSTER);
        if (/\/staff\/threads\/th_barb_prop$/.test(url)) return jsonResponse(200, { thread, messages: [] });
        return jsonResponse(404, { detail: "Not Found" });
      }),
    );
    try {
      render(<StaffPage />);
      await waitFor(() =>
        expect(screen.getByRole("heading", { name: "Barb proposal thread" })).toBeInTheDocument(),
      );
    } finally {
      window.history.pushState({}, "", "/");
    }
  });
});
