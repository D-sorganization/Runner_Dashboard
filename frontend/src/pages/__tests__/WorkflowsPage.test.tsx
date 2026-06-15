// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkflowsPage } from "../WorkflowsPage";
import { WorkflowsTab, type Workflow } from "../Workflows";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
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

    fireEvent.click(await screen.findByRole("button", { name: "Run" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm dispatch" }));
    fireEvent.click(screen.getByRole("button", { name: "Dispatch now" }));

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
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });
});
