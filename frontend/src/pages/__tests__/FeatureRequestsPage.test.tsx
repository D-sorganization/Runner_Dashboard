// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FeatureRequestsPage } from "../FeatureRequestsPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

function mockFeatureRequestFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.endsWith("/api/repos")) {
      return Promise.resolve(jsonResponse({ repos: ["Runner_Dashboard"] }));
    }
    if (url.endsWith("/api/feature-requests")) {
      return Promise.resolve(
        jsonResponse({
          requests: [
            {
              repository: "Runner_Dashboard",
              prompt: "Ship native routing",
              provider: "codex",
              created_at: "2026-06-15T10:00:00Z",
            },
          ],
        }),
      );
    }
    if (url.endsWith("/api/feature-requests/templates")) {
      return Promise.resolve(
        jsonResponse({
          templates: [{ name: "Bug fix", prompt: "Fix the bug" }],
          promptNotes: { notes: "Use TDD.", enabled: true },
        }),
      );
    }
    if (url.endsWith("/api/feature-requests/dispatch")) {
      return Promise.resolve(jsonResponse({ ok: true }));
    }
    if (url.endsWith("/api/prompt-templates")) {
      return Promise.resolve(jsonResponse({ ok: true }));
    }
    if (url.endsWith("/api/settings/prompt-notes")) {
      return Promise.resolve(jsonResponse({ ok: true }));
    }
    return Promise.reject(new Error("unexpected fetch " + url));
  });
}

describe("FeatureRequestsPage", () => {
  it("owns repos, feature requests, templates, and prompt-notes fetches", async () => {
    const fetchMock = mockFeatureRequestFetch();

    render(<FeatureRequestsPage />);

    expect(
      await screen.findByRole("option", { name: "Runner_Dashboard" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Bug fix")).toBeInTheDocument();
    expect(screen.getAllByText("Runner_Dashboard").length).toBeGreaterThan(0);
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

  it("submits dispatch payloads through the native page owner", async () => {
    const fetchMock = mockFeatureRequestFetch();

    render(<FeatureRequestsPage />);

    const selects = await screen.findAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Runner_Dashboard" } });
    fireEvent.change(selects[1], { target: { value: "codex" } });
    fireEvent.change(
      screen.getByPlaceholderText("Describe the feature to implement…"),
      { target: { value: "Add native route" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /Dispatch/ }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/feature-requests/dispatch",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining("Add native route"),
        }),
      ),
    );
  });

  it("submits templates and prompt-note updates through the native page owner", async () => {
    const fetchMock = mockFeatureRequestFetch();

    render(<FeatureRequestsPage />);

    await screen.findByRole("option", { name: "Runner_Dashboard" });
    fireEvent.change(
      screen.getByPlaceholderText("Describe the feature to implement…"),
      { target: { value: "Reusable body" } },
    );
    fireEvent.change(screen.getByPlaceholderText("Template name…"), {
      target: { value: "Reusable" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Template" }));
    fireEvent.change(
      screen.getByPlaceholderText(
        "Enter global prompt notes that will be auto-added to every dispatch…",
      ),
      { target: { value: "Prefer DbC." } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save Notes" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/prompt-templates",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ name: "Reusable", prompt: "Reusable body" }),
        }),
      ),
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/settings/prompt-notes",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ notes: "Prefer DbC.", enabled: true }),
        }),
      ),
    );
  });
});
