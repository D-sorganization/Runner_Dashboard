// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueueTab } from "../index";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const bulkQueue = {
  in_progress: [],
  queued: [
    {
      id: 1,
      name: "CI",
      repository: { name: "org/repo" },
      head_branch: "main",
      created_at: new Date().toISOString(),
    },
    {
      id: 2,
      name: "CI",
      repository: { name: "org/repo" },
      head_branch: "main",
      created_at: new Date().toISOString(),
    },
  ],
  total: 2,
};

describe("Queue bulk cancel — useSubmit (#4276)", () => {
  function armBulkCancel() {
    fireEvent.click(screen.getByRole("button", { name: /Cancel all 'CI'/i }));
  }

  it("double-click sends one cancel-workflow POST", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ cancelled_count: 2 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<QueueTab queue={bulkQueue} loading={false} />);
    armBulkCancel();
    const confirm = await screen.findByRole("button", { name: /Click again to confirm/i });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => {
      const posts = fetchMock.mock.calls.filter((c) => String(c[0]).includes("cancel-workflow"));
      expect(posts).toHaveLength(1);
    });
  });

  it("HTTP 500 renders failure alert", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ detail: "nope" }), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<QueueTab queue={bulkQueue} loading={false} />);
    armBulkCancel();
    fireEvent.click(await screen.findByRole("button", { name: /Click again to confirm/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/HTTP 500|nope/i);
  });

  it("retry reuses Idempotency-Key", async () => {
    let fail = true;
    const keys: string[] = [];
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      const headers = init?.headers as Record<string, string>;
      if (headers?.["Idempotency-Key"]) keys.push(headers["Idempotency-Key"]);
      if (fail) {
        fail = false;
        return Promise.resolve(
          new Response(JSON.stringify({ detail: "nope" }), { status: 500 }),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ cancelled_count: 2 }), { status: 200 }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<QueueTab queue={bulkQueue} loading={false} />);
    armBulkCancel();
    const confirm = await screen.findByRole("button", { name: /Click again to confirm/i });
    fireEvent.click(confirm);
    await screen.findByRole("alert");
    const retry = await screen.findByRole("button", { name: /Click again to confirm/i });
    fireEvent.click(retry);
    await waitFor(() => expect(keys.length).toBe(2));
    expect(keys[0]).toBe(keys[1]);
  });
});
