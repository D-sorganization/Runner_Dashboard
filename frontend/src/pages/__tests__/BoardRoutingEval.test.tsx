/**
 * Unit tests for Board component routing evaluation display (SC-C7, Issue #1340).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Board } from "../Staff/Board";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

function wrap(ui: React.ReactElement) {
  return <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>;
}

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

const BASE_BOARD = {
  machine: "test-node",
  generated_at: "2026-09-25T12:00:00Z",
  running: [],
  queued: [],
  recent: [],
  spend_today_usd: { total: 0.0 },
  providers: { claude: true },
  liveness: [],
  liveness_alerts: [],
};

describe("Board routing evaluation display", () => {
  beforeEach(() => {
    queryClient.clear();
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders routing eval badge when routing_eval is present on board", async () => {
    const boardWithEval = {
      ...BASE_BOARD,
      routing_eval: {
        accuracy: 0.98,
        passed: 59,
        total: 60,
        evaluated_at: "2026-09-25T12:00:00Z",
      },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/staff/board")) {
          return jsonResponse(200, boardWithEval);
        }
        return jsonResponse(404, { detail: "not found" });
      }),
    );

    render(wrap(<Board />));
    await waitFor(() => expect(screen.getByTestId("board-routing-eval")).toBeInTheDocument());
    expect(screen.getByTestId("board-routing-eval")).toHaveTextContent("routing 98%");
  });

  it("hides routing eval badge when routing_eval is not present", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/staff/board")) {
          return jsonResponse(200, BASE_BOARD);
        }
        return jsonResponse(404, { detail: "not found" });
      }),
    );

    render(wrap(<Board />));
    await waitFor(() => expect(screen.getByText("Board")).toBeInTheDocument());
    expect(screen.queryByTestId("board-routing-eval")).not.toBeInTheDocument();
  });
});
