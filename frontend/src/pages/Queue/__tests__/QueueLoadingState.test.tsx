// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueueTab } from "../index";

// #1742: while the first /api/queue load is pending the page must not claim
// the fleet is idle or the queue is empty.
describe("QueueTab first-load state", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("does not report idle or empty before the first payload arrives", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));

    render(<QueueTab />);

    expect(screen.getByText(/Loading queue/)).toBeInTheDocument();
    expect(screen.queryByText("idle")).not.toBeInTheDocument();
    expect(screen.queryByText("empty")).not.toBeInTheDocument();
    expect(screen.queryByText(/No runs currently in progress/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Queue is empty/)).not.toBeInTheDocument();
    expect(screen.getAllByText("loading").length).toBeGreaterThanOrEqual(2);
  });

  it("says the counts are unknown when the first load fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("", { status: 504 }))));

    render(<QueueTab />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/Could not load the queue \(HTTP 504\)/);
    expect(screen.queryByText("idle")).not.toBeInTheDocument();
    expect(screen.queryByText(/Queue is empty/)).not.toBeInTheDocument();
    expect(screen.getAllByText("unknown").length).toBeGreaterThanOrEqual(2);
  });

  it("still reports an empty queue once a payload has arrived", () => {
    render(<QueueTab queue={{ in_progress: [], queued: [], total: 0 }} loading={false} />);

    expect(screen.getByText("idle")).toBeInTheDocument();
    expect(screen.getByText(/Queue is empty/)).toBeInTheDocument();
  });
});
