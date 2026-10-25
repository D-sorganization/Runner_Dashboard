// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkflowsPage } from "../WorkflowsPage";
import { WorkflowsTab, type Workflow } from "../Workflows";

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
  sessionStorage.clear();
});

const WORKFLOWS: Workflow[] = [
  {
    id: "ci.yml",
    name: "CI",
    repository: "Runner_Dashboard",
    triggers: ["manual", "push_pr"],
    html_url: "https://example.test/ci",
    latest_run: { conclusion: "success" },
    recent_runs: [{ id: 1, conclusion: "success", created_at: "2026-06-15" }],
  },
];

const runWorkflowButton = () =>
  screen.findByRole("button", { name: /Run workflow CI/i });

describe("WorkflowsPage", () => {
  it("loads workflow data for the native routed page", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ workflows: WORKFLOWS }), { status: 200 }),
    );

    render(<WorkflowsPage />);

    await waitFor(() =>
      expect(screen.getAllByText("Runner_Dashboard").length).toBeGreaterThan(0),
    );
    expect(screen.getByText("CI")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/workflows/list",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it("explains a warming catalogue and retries until workflows arrive", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ workflows: [], total: 0, status: "warming" }),
          { status: 200 },
        ),
      )
      .mockResolvedValue(
        new Response(JSON.stringify({ workflows: WORKFLOWS }), { status: 200 }),
      );

    render(<WorkflowsPage warmingRetryMs={300} />);

    expect(
      await screen.findByText(/Gathering workflows from GitHub/),
    ).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(await runWorkflowButton()).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("posts workflow dispatches through the native routed page", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      (input, init) => {
        const url = String(input);
        if (url === "/api/workflows/list") {
          return Promise.resolve(
            new Response(JSON.stringify({ workflows: WORKFLOWS }), {
              status: 200,
            }),
          );
        }
        if (url === "/api/workflows/dispatch") {
          expect(init?.method).toBe("POST");
          expect(JSON.parse(String(init?.body))).toMatchObject({
            repository: "Runner_Dashboard",
            workflow_id: "ci.yml",
            ref: "main",
          });
          return Promise.resolve(
            new Response(JSON.stringify({ status: "queued" }), {
              status: 200,
            }),
          );
        }
        return Promise.reject(new Error("unexpected fetch " + url));
      },
    );

    render(<WorkflowsPage />);

    fireEvent.click(await runWorkflowButton());
    fireEvent.click(screen.getByRole("button", { name: "Confirm dispatch" }));
    fireEvent.click(screen.getByRole("button", { name: /Dispatch workflow now/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/workflows/dispatch",
        expect.anything(),
      ),
    );
  });

  it("posts workflow dispatches after the workflow list finishes loading", async () => {
    let resolveList!: (response: Response) => void;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      (input, init) => {
        const url = String(input);
        if (url === "/api/workflows/list") {
          return new Promise<Response>((resolve) => {
            resolveList = resolve;
          });
        }
        if (url === "/api/workflows/dispatch") {
          expect(init?.method).toBe("POST");
          return Promise.resolve(
            new Response(JSON.stringify({ status: "queued" }), {
              status: 200,
            }),
          );
        }
        return Promise.reject(new Error("unexpected fetch " + url));
      },
    );

    render(<WorkflowsPage />);

    expect(
      screen.queryByRole("button", { name: /Run workflow/i }),
    ).not.toBeInTheDocument();

    await act(async () => {
      resolveList(
        new Response(JSON.stringify({ workflows: WORKFLOWS }), { status: 200 }),
      );
    });

    fireEvent.click(await runWorkflowButton());
    fireEvent.click(screen.getByRole("button", { name: "Confirm dispatch" }));
    fireEvent.click(screen.getByRole("button", { name: /Dispatch workflow now/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/workflows/dispatch",
        expect.anything(),
      ),
    );
  });

  it("keeps WorkflowsTab prop-driven for legacy fallback callers", () => {
    const onDispatch = vi.fn();
    const onRefresh = vi.fn();

    render(
      <WorkflowsTab
        workflows={WORKFLOWS}
        loading={false}
        onDispatch={onDispatch}
        onRefresh={onRefresh}
      />,
    );

    expect(screen.getAllByText("Runner_Dashboard").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /Refresh workflows/i }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });
});
