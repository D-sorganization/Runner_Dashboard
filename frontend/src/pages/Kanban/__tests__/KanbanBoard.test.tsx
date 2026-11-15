// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import demoBoard from "../fixtures/demoBoard.json";
import { KanbanBoard } from "../KanbanBoard";
import type { KanbanGithubBoardResponse } from "../types";

afterEach(() => cleanup());

describe("KanbanBoard", () => {
  it("places cards in precedence columns", () => {
    const board = demoBoard as KanbanGithubBoardResponse;
    render(<KanbanBoard board={board} />);

    const armed = board.items.find((row) => row.card.column === "armed");
    expect(armed).toBeDefined();
    expect(screen.getByTestId(`kanban-card-${armed!.issue_number}`)).toBeInTheDocument();
    const armedColumn = screen.getByTestId("kanban-column-armed");
    expect(armedColumn).toContainElement(screen.getByTestId(`kanban-card-${armed!.issue_number}`));
  });
});
