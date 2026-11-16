// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import demoBoard from "../fixtures/demoBoard.json";
import { KanbanPage } from "../KanbanPage";

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("KanbanPage", () => {
  it("renders fixture board when the kanban API is missing (404)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("not found", { status: 404 }));

    render(<KanbanPage />);

    await waitFor(() => expect(screen.getByTestId("kanban-fixture-banner")).toBeInTheDocument());
    expect(screen.getByTestId("kanban-column-armed")).toBeInTheDocument();
    expect(screen.getAllByRole("link").length).toBeGreaterThan(0);
    expect(demoBoard.items.length).toBeGreaterThan(0);
  });

  it("shows unknown and stale badges on matching fixture cards", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("not found", { status: 404 }));

    render(<KanbanPage />);

    await waitFor(() => expect(screen.getByText("Stale review")).toBeInTheDocument());
    expect(screen.getAllByText("Unknown").length).toBeGreaterThan(0);
  });

  it("uses live API payload when present", async () => {
    const body = {
      ...demoBoard,
      fixture: false,
      items: demoBoard.items.slice(0, 2),
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    render(<KanbanPage />);

    await waitFor(() => expect(screen.queryByTestId("kanban-fixture-banner")).not.toBeInTheDocument());
    expect(screen.getByText(/2 cards/)).toBeInTheDocument();
  });
});
