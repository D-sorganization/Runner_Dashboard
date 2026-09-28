// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { StaffPage } from "../StaffPage";
import { Board } from "../Board";
import { queryClient } from "../../../hooks/usePollingQueries";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const ROSTER = {
  machine: "DeskComputer",
  active_runs: 0,
  providers: {},
  roles: [{ name: "night-watch", title: "Night Watch", summary: "Sweeps red main.", providers: [] }],
};

const BOARD = {
  machine: "DeskComputer",
  generated_at: "2026-09-27T12:00:00Z",
  running: [],
  queued: [],
  recent: [],
  spend_today_usd: 12.5,
  providers: { claude: true, codex: true },
};

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
}

describe("StaffPage & Board (Workstream D)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    cleanup();
    queryClient.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (/\/staff\/roster$/.test(url)) return jsonResponse(200, ROSTER);
        if (/\/staff\/board$/.test(url)) return jsonResponse(200, BOARD);
        if (/\/staff\/inbox$/.test(url)) return jsonResponse(200, { items: [], count: 0, counts: { total: 0 } });
        if (/\/staff\/quota$/.test(url)) return jsonResponse(200, { providers: [] });
        return jsonResponse(404, { detail: "Not Found" });
      }),
    );
  });

  afterEach(() => {
    cleanup();
    queryClient.clear();
    vi.unstubAllGlobals();
  });

  it("renders compact underline section tab bar with Console active", async () => {
    render(<StaffPage />);

    const consoleTab = screen.getByRole("tab", { name: "Console" });
    expect(consoleTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("staff-console-desktop")).toBeInTheDocument();

    const tabsContainer = screen.getByRole("tablist", { name: /staff sections/i });
    expect(tabsContainer).toBeInTheDocument();
  });

  it("renders Board as compact status strip with live dot and spend today", async () => {
    render(<Board />);

    await waitFor(() => {
      expect(screen.getByTestId("board-spend")).toHaveTextContent("$12.50");
    });

    const liveDot = screen.getByTestId("board-live-dot");
    expect(liveDot).toBeInTheDocument();

    const boardSection = screen.getByRole("region", { name: /staff board/i });
    // Must not have glass-card class
    expect(boardSection.className).not.toContain("glass-card");
    expect(boardSection.className).toContain("staff-board");
  });

  it("satisfies CSS contracts for StaffPage tabs and Board", () => {
    const cssPath = path.join(__dirname, "../StaffPage.css");
    const css = fs.readFileSync(cssPath, "utf8");

    // Check compact underline styling (13px, 500, accent underline)
    expect(css).toMatch(/13px/);
    expect(css).toMatch(/500/);
    expect(css).not.toMatch(/\.glass-card:hover/);
  });
});
