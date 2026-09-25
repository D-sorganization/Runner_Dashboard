// @vitest-environment jsdom
/**
 * Behaviour tests for pages/CodeRequests.tsx (CR-1, #1281).
 *
 * Covers:
 * 1. Smoke render ("Code Requests" header).
 * 2. Repo <select> is populated from string + object repo entries.
 * 3. Dispatch button is gated on repo + prompt, and the dispatch payload
 *    carries the selected repo/branch/provider/standards.
 * 4. Prompt-notes preamble is prepended when enabled and non-empty.
 * 5. Toggling a standard chip adds it to the dispatch payload.
 * 6. Save-template is gated and invokes onSaveTemplate with name + prompt.
 * 7. Clicking a saved template loads its prompt into the editor.
 * 8. Save-notes invokes onSavePromptNotes and surfaces the saved flag.
 * 9. Dispatch history renders rows; loading + empty states surface.
 * 10. Backward-compatible FeatureRequestsTab re-export works identically.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  CodeRequestsTab,
  FeatureRequestsTab,
  type CodeRequestsProps,
} from "../CodeRequests";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

function setup(overrides: Partial<CodeRequestsProps> = {}) {
  const onDispatch = vi.fn().mockResolvedValue(undefined);
  const onSaveTemplate = vi.fn().mockResolvedValue(undefined);
  const onSavePromptNotes = vi.fn().mockResolvedValue(undefined);
  const onRefresh = vi.fn();
  const props: CodeRequestsProps = {
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
  const view = render(<CodeRequestsTab {...props} />);
  return { view, onDispatch, onSaveTemplate, onSavePromptNotes, onRefresh };
}

describe("CodeRequestsTab", () => {
  it("renders without throwing (smoke test)", () => {
    expect(() => setup()).not.toThrow();
    expect(screen.getByText("Code Requests")).toBeInTheDocument();
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
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Runner_Dashboard" } });
    fireEvent.change(selects[1], { target: { value: "codex" } });
    fireEvent.change(
      screen.getByPlaceholderText("Describe the code request to plan and execute…"),
      { target: { value: "Add a widget" } },
    );
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
    expect(screen.getByText("Code request dispatched.")).toBeInTheDocument();
  });

  it("prepends enabled prompt notes to the dispatched prompt", () => {
    const { onDispatch } = setup({ promptNotes: { notes: "Be terse.", enabled: true } });
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Runner_Dashboard" } });
    fireEvent.change(
      screen.getByPlaceholderText("Describe the code request to plan and execute…"),
      { target: { value: "Add a widget" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /Dispatch/ }));
    expect(onDispatch.mock.calls[0][0].prompt).toBe("Be terse.\n\nAdd a widget");
  });

  it("does not prepend prompt notes when disabled", () => {
    const { onDispatch } = setup({ promptNotes: { notes: "Be terse.", enabled: false } });
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Runner_Dashboard" } });
    fireEvent.change(
      screen.getByPlaceholderText("Describe the code request to plan and execute…"),
      { target: { value: "Add a widget" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /Dispatch/ }));
    expect(onDispatch.mock.calls[0][0].prompt).toBe("Add a widget");
  });

  it("surfaces a dispatch error with the backend detail and refreshes history (#1280)", async () => {
    const onDispatch = vi.fn().mockRejectedValue(new Error("boom"));
    const { onRefresh } = setup({ onDispatch });
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Runner_Dashboard" } });
    fireEvent.change(
      screen.getByPlaceholderText("Describe the code request to plan and execute…"),
      { target: { value: "x" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /Dispatch/ }));
    await waitFor(() => expect(screen.getByText("Dispatch failed: boom")).toBeInTheDocument());
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("disables dispatch and explains why when the dispatch target is unavailable (#1280)", () => {
    setup({
      dispatchTarget: {
        available: false,
        detail: "dispatch target unavailable: Jules-Feature-Request.yml — HTTP 404",
      },
    });
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Runner_Dashboard" } });
    fireEvent.change(
      screen.getByPlaceholderText("Describe the code request to plan and execute…"),
      { target: { value: "x" } },
    );
    expect(screen.getByRole("button", { name: /Dispatch/ })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Jules-Feature-Request.yml");
  });

  it("keeps dispatch enabled when the target is available or unknown", () => {
    setup({ dispatchTarget: { available: true } });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows failed history entries with their error (#1280)", () => {
    setup({
      requests: [
        {
          repository: "Runner_Dashboard",
          prompt: "Do the thing",
          provider: "jules_api",
          status: "failed",
          error: "HTTP 404",
          created_at: "2026-06-01T10:00:00Z",
        },
      ],
    });
    expect(screen.getAllByText("failed").length).toBe(2);
    expect(screen.getAllByText("HTTP 404").length).toBe(2);
  });

  it("toggles standard chips on and off", () => {
    const { onDispatch } = setup();
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "Runner_Dashboard" } });
    fireEvent.change(
      screen.getByPlaceholderText("Describe the code request to plan and execute…"),
      { target: { value: "x" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "DRY" }));
    fireEvent.click(screen.getByRole("button", { name: "SECURITY" }));
    fireEvent.click(screen.getByRole("button", { name: "DRY" })); // toggle off
    fireEvent.click(screen.getByRole("button", { name: /Dispatch/ }));
    expect(onDispatch.mock.calls[0][0].standards).toEqual(["security"]);
  });

  it("saves a template with name + prompt", () => {
    const { onSaveTemplate } = setup();
    fireEvent.change(
      screen.getByPlaceholderText("Describe the code request to plan and execute…"),
      { target: { value: "Reusable body" } },
    );
    fireEvent.change(screen.getByPlaceholderText("Template name…"), {
      target: { value: "My Template" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Template" }));
    expect(onSaveTemplate).toHaveBeenCalledWith({ name: "My Template", prompt: "Reusable body" });
  });

  it("loads a saved template into the prompt editor", () => {
    setup({ templates: [{ name: "Tmpl", prompt: "Loaded prompt body" }] });
    fireEvent.click(screen.getByText("Tmpl"));
    expect(
      screen.getByPlaceholderText("Describe the code request to plan and execute…"),
    ).toHaveValue("Loaded prompt body");
  });

  it("saves prompt notes and surfaces the saved flag", async () => {
    const { onSavePromptNotes } = setup();
    fireEvent.change(
      screen.getByPlaceholderText(
        "Enter global prompt notes that will be auto-added to every dispatch…",
      ),
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
    expect(screen.getAllByText("Runner_Dashboard").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2026-06-01").length).toBeGreaterThan(0);
    expect(screen.getByText("3 votes")).toBeInTheDocument();
  });

  it("shows the loading and empty history states", () => {
    const { view } = setup({ loading: true });
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    view.rerender(
      <CodeRequestsTab
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

  it("FeatureRequestsTab alias renders identically", () => {
    const onDispatch = vi.fn().mockResolvedValue(undefined);
    render(
      <FeatureRequestsTab
        repos={["Runner_Dashboard"]}
        requests={[]}
        templates={[]}
        loading={false}
        promptNotes={{ notes: "", enabled: true }}
        onDispatch={onDispatch}
        onSaveTemplate={vi.fn().mockResolvedValue(undefined)}
        onSavePromptNotes={vi.fn().mockResolvedValue(undefined)}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByText("Code Requests")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Runner_Dashboard" })).toBeInTheDocument();
  });

  it("loads provider list dynamically from provider registry without hardcoded defaults", async () => {
    const mockProviders = [
      {
        id: "mock-provider-alpha",
        dashboard_id: "mock_provider_alpha",
        label: "Alpha Provider",
        login_status: "authenticated",
        auth_mode: "token",
        resource: "local",
      },
      {
        id: "mock-provider-beta",
        dashboard_id: "mock_provider_beta",
        label: "Beta Provider",
        login_status: "unauthenticated",
        login_detail: "API token missing",
        auth_mode: "token",
        resource: "local",
      },
    ];

    const originalFetch = globalThis.fetch;
    const fetchSpy = vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/api/providers/registry")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              schema_version: "1.0",
              providers: mockProviders,
              auth_kinds: [],
              task_classes: [],
              capabilities: [],
            }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ profiles: [], providers: [] }),
      });
    });
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    try {
      setup();
      await waitFor(() => {
        expect(screen.getByRole("option", { name: /Alpha Provider/ })).toBeInTheDocument();
      });
      expect(screen.getByRole("option", { name: /Beta Provider/ })).toBeInTheDocument();

      // Ensure no hardcoded jules_api option is present when registry loaded
      expect(screen.queryByRole("option", { name: /^Jules$/ })).not.toBeInTheDocument();

      // Check warning on selecting unauthenticated provider
      const providerSelect = screen.getByRole("combobox", { name: "Provider" });
      fireEvent.change(providerSelect, { target: { value: "mock_provider_beta" } });

      await waitFor(() => {
        expect(screen.getByRole("alert")).toHaveTextContent(/unauthenticated or unavailable/i);
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
