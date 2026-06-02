// @vitest-environment jsdom
/**
 * Behaviour tests for pages/FeatureRequests.tsx — extracted from the legacy
 * App.tsx monolith (decomposition #836, pass 7).
 *
 * Covers:
 * 1. Smoke render.
 * 2. Repo <select> is populated from string + object repo entries.
 * 3. Dispatch button is gated on repo + prompt, and the dispatch payload
 *    carries the selected repo/branch/provider/standards.
 * 4. Prompt-notes preamble is prepended when enabled and non-empty.
 * 5. Toggling a standard chip adds it to the dispatch payload.
 * 6. Save-template is gated and invokes onSaveTemplate with name + prompt.
 * 7. Clicking a saved template loads its prompt into the editor.
 * 8. Save-notes invokes onSavePromptNotes and surfaces the saved flag.
 * 9. Dispatch history renders rows; loading + empty states surface.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  FeatureRequestsTab,
  type FeatureRequestsProps,
} from "../FeatureRequests";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

function setup(overrides: Partial<FeatureRequestsProps> = {}) {
  const onDispatch = vi.fn().mockResolvedValue(undefined);
  const onSaveTemplate = vi.fn().mockResolvedValue(undefined);
  const onSavePromptNotes = vi.fn().mockResolvedValue(undefined);
  const onRefresh = vi.fn();
  const props: FeatureRequestsProps = {
    repos: ["Runner_Dashboard", { name: "Maxwell-Daemon" }],
    requests: [],
    templates: [],
    loading: false,
    promptNotes: { notes: "", enabled: true },
    onDispatch,
    onSaveTemplate,
    onSavePromptNotes,
    onRefresh,
    ...overrides,
  };
  const view = render(<FeatureRequestsTab {...props} />);
  return { view, onDispatch, onSaveTemplate, onSavePromptNotes, onRefresh };
}

describe("FeatureRequestsTab", () => {
  it("renders without throwing (smoke test)", () => {
    expect(() => setup()).not.toThrow();
    expect(screen.getByText("Feature Requests")).toBeInTheDocument();
  });

  it("populates the repo select from string and object entries", () => {
    setup();
    expect(screen.getByRole("option", { name: "Runner_Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Maxwell-Daemon" })).toBeInTheDocument();
  });

  it("gates the Dispatch button on repo + prompt", () => {
    setup();
    const dispatchBtn = screen.getByRole("button", { name: /Dispatch/ });
    expect(dispatchBtn).toBeDisabled();
  });

  it("dispatches with the selected repo, branch, provider, and standards", async () => {
    const { onDispatch, onRefresh } = setup();
    // Repository select is the first combobox; provider is the second.
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Runner_Dashboard" } });
    fireEvent.change(selects[1], { target: { value: "codex" } });
    fireEvent.change(screen.getByPlaceholderText("Describe the feature to implement…"), {
      target: { value: "Add a widget" },
    });
    // Turn off prompt-notes injection (notes empty anyway) to keep prompt clean.
    fireEvent.click(screen.getByRole("button", { name: "TDD" }));
    fireEvent.click(screen.getByRole("button", { name: /Dispatch/ }));
    expect(onDispatch).toHaveBeenCalledTimes(1);
    const payload = onDispatch.mock.calls[0][0];
    expect(payload.repository).toBe("Runner_Dashboard");
    expect(payload.branch).toBe("main");
    expect(payload.provider).toBe("codex");
    expect(payload.prompt).toBe("Add a widget");
    expect(payload.standards).toEqual(["tdd"]);
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(screen.getByText("Feature request dispatched.")).toBeInTheDocument();
  });

  it("prepends enabled prompt notes to the dispatched prompt", () => {
    const { onDispatch } = setup({ promptNotes: { notes: "Be terse.", enabled: true } });
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Runner_Dashboard" } });
    fireEvent.change(screen.getByPlaceholderText("Describe the feature to implement…"), {
      target: { value: "Add a widget" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Dispatch/ }));
    expect(onDispatch.mock.calls[0][0].prompt).toBe("Be terse.\n\nAdd a widget");
  });

  it("does not prepend prompt notes when disabled", () => {
    const { onDispatch } = setup({ promptNotes: { notes: "Be terse.", enabled: false } });
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Runner_Dashboard" } });
    fireEvent.change(screen.getByPlaceholderText("Describe the feature to implement…"), {
      target: { value: "Add a widget" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Dispatch/ }));
    expect(onDispatch.mock.calls[0][0].prompt).toBe("Add a widget");
  });

  it("surfaces a dispatch error", async () => {
    const onDispatch = vi.fn().mockRejectedValue(new Error("boom"));
    setup({ onDispatch });
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Runner_Dashboard" } });
    fireEvent.change(screen.getByPlaceholderText("Describe the feature to implement…"), {
      target: { value: "x" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Dispatch/ }));
    await waitFor(() => expect(screen.getByText("Dispatch failed.")).toBeInTheDocument());
  });

  it("toggles standard chips on and off", () => {
    const { onDispatch } = setup();
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Runner_Dashboard" } });
    fireEvent.change(screen.getByPlaceholderText("Describe the feature to implement…"), {
      target: { value: "x" },
    });
    fireEvent.click(screen.getByRole("button", { name: "DRY" }));
    fireEvent.click(screen.getByRole("button", { name: "SECURITY" }));
    fireEvent.click(screen.getByRole("button", { name: "DRY" })); // toggle off
    fireEvent.click(screen.getByRole("button", { name: /Dispatch/ }));
    expect(onDispatch.mock.calls[0][0].standards).toEqual(["security"]);
  });

  it("saves a template with name + prompt", () => {
    const { onSaveTemplate } = setup();
    fireEvent.change(screen.getByPlaceholderText("Describe the feature to implement…"), {
      target: { value: "Reusable body" },
    });
    fireEvent.change(screen.getByPlaceholderText("Template name…"), {
      target: { value: "My Template" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Template" }));
    expect(onSaveTemplate).toHaveBeenCalledWith({ name: "My Template", prompt: "Reusable body" });
  });

  it("loads a saved template into the prompt editor", () => {
    setup({ templates: [{ name: "Tmpl", prompt: "Loaded prompt body" }] });
    fireEvent.click(screen.getByText("Tmpl"));
    expect(screen.getByPlaceholderText("Describe the feature to implement…")).toHaveValue(
      "Loaded prompt body",
    );
  });

  it("saves prompt notes and surfaces the saved flag", async () => {
    const { onSavePromptNotes } = setup();
    fireEvent.change(
      screen.getByPlaceholderText("Enter global prompt notes that will be auto-added to every dispatch…"),
      { target: { value: "New notes" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save Notes" }));
    expect(onSavePromptNotes).toHaveBeenCalledWith({ notes: "New notes", enabled: true });
    await waitFor(() => expect(screen.getByText("✓ Saved")).toBeInTheDocument());
  });

  it("renders dispatch history rows", () => {
    setup({
      requests: [
        {
          repository: "Runner_Dashboard",
          prompt: "Do the thing",
          provider: "jules_api",
          standards: ["tdd"],
          created_at: "2026-06-01T10:00:00Z",
          votes: 3,
        },
      ],
    });
    // Repository appears in both desktop + mobile renders.
    expect(screen.getAllByText("Runner_Dashboard").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2026-06-01").length).toBeGreaterThan(0);
    expect(screen.getByText("3 votes")).toBeInTheDocument();
  });

  it("shows the loading and empty history states", () => {
    const { view } = setup({ loading: true });
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    view.rerender(
      <FeatureRequestsTab
        repos={[]}
        requests={[]}
        templates={[]}
        loading={false}
        promptNotes={{ notes: "", enabled: true }}
        onDispatch={vi.fn().mockResolvedValue(undefined)}
        onSaveTemplate={vi.fn().mockResolvedValue(undefined)}
        onSavePromptNotes={vi.fn().mockResolvedValue(undefined)}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByText("No dispatched requests yet.")).toBeInTheDocument();
    expect(screen.getByText("No saved templates.")).toBeInTheDocument();
  });
});
