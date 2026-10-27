// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, afterEach } from "vitest";
import { vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueueTab } from "../index";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("cancelRun HTTP status handling (#3028)", () => {
  const cancelQueue = {
    in_progress: [
      {
        id: 7,
        name: "Build Workflow",
        repository: { name: "org/repo" },
        head_branch: "main",
        html_url: "https://github.com/org/repo/actions/runs/7",
        run_started_at: new Date(Date.now() - 300000).toISOString(),
        runner_name: "ubuntu-latest",
      },
    ],
    queued: [],
    total: 1,
  };

  async function confirmCancel(): Promise<void> {
    fireEvent.click(screen.getAllByText("Cancel")[0]);
    fireEvent.click(await screen.findByText("Click again to confirm"));
  }

  it("test_cancelRun_marks_failed_on_http_error", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ error: { message: "forbidden" } }), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onRefresh = vi.fn();
    render(
      <QueueTab queue={cancelQueue} loading={false} onRefresh={onRefresh} />,
    );

    await confirmCancel();

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(/cancelled/i)).not.toBeInTheDocument();
    expect(onRefresh).not.toHaveBeenCalled();
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(url).toContain(
      `/api/runs/${encodeURIComponent("org/repo")}/cancel/7`,
    );
  });

  it("marks the run cancelled when the backend accepts the cancel", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<QueueTab queue={cancelQueue} loading={false} />);

    await confirmCancel();

    expect((await screen.findAllByText(/cancelled/i)).length).toBeGreaterThan(
      0,
    );
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(url).toContain(encodeURIComponent("org/repo"));
  });
});
