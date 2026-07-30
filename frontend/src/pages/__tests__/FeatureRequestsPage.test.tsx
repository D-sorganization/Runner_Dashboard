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
import { FeatureRequestsPage } from "../FeatureRequestsPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function mockFeatureRequestFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url === "/api/repos") {
      return Promise.resolve(
        jsonResponse({ repos: ["Runner_Dashboard", { name: "Tools" }] }),
      );
    }
    if (url === "/api/feature-requests") {
      return Promise.resolve(
        jsonResponse({
          requests: [
            {
              repository: "Runner_Dashboard",
              prompt: "Add native page",
              provider: "codex",
              created_at: "2026-06-15T00:00:00Z",
              votes: 2,
            },
          ],
        }),
      );
    }
    if (url === "/api/feature-requests/templates") {
      if (init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toMatchObject({
          name: "Reusable",
          prompt: "Make it native",
        });
        return Promise.resolve(jsonResponse({ status: "saved" }));
      }
      return Promise.resolve(
        jsonResponse({
          templates: [{ name: "Starter", prompt: "Template body" }],
          promptNotes: { notes: "Use TDD.", enabled: true },
        }),
      );
    }
    if (url === "/api/feature-requests/dispatch") {
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init.body))).toMatchObject({
        repository: "Runner_Dashboard",
        branch: "main",
        provider: "codex",
        prompt: "Use TDD.\n\nMake it native",
      });
      return Promise.resolve(jsonResponse({ status: "queued" }));
    }
    if (url === "/api/settings/prompt-notes") {
      expect(init?.method).toBe("PUT");
      expect(JSON.parse(String(init.body))).toMatchObject({
        notes: "Use TDD. Keep it small.",
        enabled: true,
      });
      return Promise.resolve(jsonResponse({ status: "saved" }));
    }
    return Promise.reject(new Error("unexpected fetch " + url));
  });
}

describe("FeatureRequestsPage", () => {
  it("loads repos, requests, templates, and prompt notes for the native route", async () => {
    const fetchMock = mockFeatureRequestFetch();

    render(<FeatureRequestsPage />);

    await waitFor(() =>
      expect(screen.getAllByText("Runner_Dashboard").length).toBeGreaterThan(0),
    );
    expect(screen.getByRole("option", { name: "Tools" })).toBeInTheDocument();
    expect(screen.getByText("Starter")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByPlaceholderText(
          "Enter global prompt notes that will be auto-added to every dispatch…",
        ),
      ).toHaveValue("Use TDD."),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/repos",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/feature-requests",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/feature-requests/templates",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it("dispatches feature requests through the native page", async () => {
    const fetchMock = mockFeatureRequestFetch();

    render(<FeatureRequestsPage />);
    await screen.findByText("Starter");

    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Runner_Dashboard" } });
    fireEvent.change(selects[1], { target: { value: "codex" } });
    fireEvent.change(
      screen.getByPlaceholderText("Describe the feature to implement…"),
      { target: { value: "Make it native" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /Dispatch/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/feature-requests/dispatch",
        expect.anything(),
      ),
    );
  });

  it("saves templates and prompt notes through canonical native endpoints", async () => {
    const fetchMock = mockFeatureRequestFetch();

    render(<FeatureRequestsPage />);
    await screen.findByText("Starter");

    fireEvent.change(
      screen.getByPlaceholderText("Describe the feature to implement…"),
      { target: { value: "Make it native" } },
    );
    fireEvent.change(screen.getByPlaceholderText("Template name…"), {
      target: { value: "Reusable" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Template" }));

    fireEvent.change(
      screen.getByPlaceholderText(
        "Enter global prompt notes that will be auto-added to every dispatch…",
      ),
      { target: { value: "Use TDD. Keep it small." } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save Notes" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/feature-requests/templates",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/settings/prompt-notes",
        expect.objectContaining({ method: "PUT" }),
      ),
    );
  });
});
