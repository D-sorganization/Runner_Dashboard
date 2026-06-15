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
import { AssessmentsPage } from "../AssessmentsPage";
import { AssessmentsTab, type AssessmentScore } from "../Assessments";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const SCORES: AssessmentScore[] = [
  {
    repo: "Runner_Dashboard",
    score: 0.92,
    provider: "codex",
    date: "2026-06-15T00:00:00Z",
    summary: "native tab route",
  },
];

describe("AssessmentsPage", () => {
  it("loads repos and scores for the native routed page", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url === "/api/repos") {
        return Promise.resolve(
          new Response(JSON.stringify({ repos: [{ name: "Runner_Dashboard" }] }), {
            status: 200,
          }),
        );
      }
      if (url === "/api/assessments/scores") {
        return Promise.resolve(
          new Response(JSON.stringify({ scores: SCORES }), { status: 200 }),
        );
      }
      return Promise.reject(new Error("unexpected fetch " + url));
    });

    render(<AssessmentsPage />);

    await waitFor(() =>
      expect(screen.getAllByText("native tab route").length).toBeGreaterThan(0),
    );
    expect(screen.getAllByText("92%").length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/repos",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/assessments/scores",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it("posts assessment dispatches through the native routed page", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      (input, init) => {
        const url = String(input);
        if (url === "/api/repos") {
          return Promise.resolve(
            new Response(JSON.stringify({ repos: [{ name: "Runner_Dashboard" }] }), {
              status: 200,
            }),
          );
        }
        if (url === "/api/assessments/scores") {
          return Promise.resolve(
            new Response(JSON.stringify({ scores: SCORES }), { status: 200 }),
          );
        }
        if (url === "/api/assessments/dispatch") {
          expect(init?.method).toBe("POST");
          expect(JSON.parse(String(init?.body))).toMatchObject({
            repository: "Runner_Dashboard",
            provider: "jules_api",
          });
          return Promise.resolve(
            new Response(JSON.stringify({ status: "queued" }), { status: 200 }),
          );
        }
        return Promise.reject(new Error("unexpected fetch " + url));
      },
    );

    render(<AssessmentsPage />);
    await waitFor(() =>
      expect(screen.getAllByText("native tab route").length).toBeGreaterThan(0),
    );

    const selects = document.querySelectorAll("select");
    fireEvent.change(selects[0], { target: { value: "Runner_Dashboard" } });
    fireEvent.click(screen.getByRole("button", { name: /Run Assessment/i }));
    fireEvent.click(screen.getByRole("button", { name: /Confirm/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/assessments/dispatch",
        expect.anything(),
      ),
    );
  });

  it("keeps AssessmentsTab prop-driven for legacy fallback callers", () => {
    const onDispatch = vi.fn();
    const onRefresh = vi.fn();

    render(
      <AssessmentsTab
        repos={[{ name: "Runner_Dashboard" }]}
        scores={SCORES}
        loading={false}
        onDispatch={onDispatch}
        onRefresh={onRefresh}
      />,
    );

    expect(screen.getAllByText("native tab route").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /Refresh/i }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });
});
