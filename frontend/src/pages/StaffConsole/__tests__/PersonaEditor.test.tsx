// @vitest-environment jsdom
/**
 * PersonaEditor.test.tsx — Unit tests for mobile personal display name and avatar editor (#3499).
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { PersonaEditor } from "../PersonaEditor";
import type { StaffRoleItem } from "../types";
import type { PresentationPrefsResponse } from "../personaGalleryUtils";

const mockApiRequest = vi.fn();
const mockFetchWithBrowserCsrf = vi.fn();

vi.mock("../../../lib/api", () => ({
  apiRequest: (...args: unknown[]) => mockApiRequest(...args),
  ApiClientError: class ApiClientError extends Error {
    constructor(public status: number, public detail: string, public url: string) {
      super(`API ${status} — ${detail} (${url})`);
    }
  },
}));

vi.mock("../../../lib/browserCsrf", () => ({
  fetchWithBrowserCsrf: (...args: unknown[]) => mockFetchWithBrowserCsrf(...args),
}));

afterEach(cleanup);

const MOCK_ROLES: StaffRoleItem[] = [
  { name: "barb", title: "Barb", summary: "Fleet Orchestrator", group: "leadership", valid: true },
  { name: "architect", title: "Chief Architect", summary: "System design", group: "specialists", valid: true },
];

const MOCK_PREFS: PresentationPrefsResponse = {
  operator_id: "test-op",
  display_label: "Test Operator",
  role_order: ["barb", "architect"],
  avatar_overrides: { architect: "/api/v1/staff/avatars/personal/test-op/architect?v=abc" },
  revision: 1,
  updated_at: "2026-11-01T00:00:00Z",
};

describe("PersonaEditor (#3499)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.URL.createObjectURL = vi.fn(() => "blob:http://localhost/test-preview");
    global.URL.revokeObjectURL = vi.fn();
  });

  it("displays canonical role ID alongside display name so duplicate names remain identifiable", () => {
    render(<PersonaEditor roles={MOCK_ROLES} selectedRoleName="barb" prefs={MOCK_PREFS} onPrefsSaved={vi.fn()} />);
    expect(screen.getByTestId("persona-editor-canonical-id")).toHaveTextContent("barb");
    expect(screen.getByRole("option", { name: "Barb (barb)" })).toBeInTheDocument();
  });

  it("validates bounded plain text/Unicode display name and saves successfully", async () => {
    const onPrefsSaved = vi.fn();
    mockApiRequest.mockResolvedValueOnce({ ...MOCK_PREFS, display_label: "Lead 🚀", revision: 2 });
    render(<PersonaEditor roles={MOCK_ROLES} selectedRoleName="barb" prefs={MOCK_PREFS} onPrefsSaved={onPrefsSaved} />);
    fireEvent.change(screen.getByTestId("persona-editor-name-input"), { target: { value: "Lead 🚀" } });
    fireEvent.click(screen.getByTestId("persona-editor-save-name-btn"));
    await waitFor(() => { expect(mockApiRequest).toHaveBeenCalledWith("/api/v1/staff/presentation-prefs", expect.objectContaining({ method: "PUT" })); });
    expect(screen.getByTestId("persona-editor-status")).toHaveTextContent(/display name saved successfully/i);
    expect(onPrefsSaved).toHaveBeenCalled();
  });

  it("reverts display name on cancel and resets to default on reset", async () => {
    const onPrefsSaved = vi.fn();
    mockApiRequest.mockResolvedValueOnce({ ...MOCK_PREFS, display_label: "", revision: 2 });
    render(<PersonaEditor roles={MOCK_ROLES} selectedRoleName="barb" prefs={MOCK_PREFS} onPrefsSaved={onPrefsSaved} />);
    const nameInput = screen.getByTestId("persona-editor-name-input");
    fireEvent.change(nameInput, { target: { value: "Temp" } });
    fireEvent.click(screen.getByTestId("persona-editor-cancel-name-btn"));
    expect(nameInput).toHaveValue("Test Operator");
    fireEvent.click(screen.getByTestId("persona-editor-reset-name-btn"));
    await waitFor(() => { expect(mockApiRequest).toHaveBeenCalled(); });
    expect(nameInput).toHaveValue("");
  });

  it("rejects display name exceeding 80 characters or containing control characters", () => {
    render(<PersonaEditor roles={MOCK_ROLES} selectedRoleName="barb" prefs={MOCK_PREFS} onPrefsSaved={vi.fn()} />);
    const nameInput = screen.getByTestId("persona-editor-name-input");
    fireEvent.change(nameInput, { target: { value: "x".repeat(81) } });
    expect(screen.getByTestId("persona-editor-name-error")).toHaveTextContent(/cannot exceed 80 characters/i);
    fireEvent.change(nameInput, { target: { value: "bad\x01name" } });
    expect(screen.getByTestId("persona-editor-name-error")).toHaveTextContent(/plain text/i);
  });

  it("handles image file selection, preview, and cancel preview", () => {
    render(<PersonaEditor roles={MOCK_ROLES} selectedRoleName="barb" prefs={MOCK_PREFS} onPrefsSaved={vi.fn()} />);
    fireEvent.change(screen.getByTestId("persona-editor-file-input"), { target: { files: [new File(["data"], "avatar.png", { type: "image/png" })] } });
    expect(screen.getByTestId("persona-editor-upload-btn")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("persona-editor-cancel-preview-btn"));
    expect(screen.queryByTestId("persona-editor-upload-btn")).not.toBeInTheDocument();
  });

  it("retains recoverable form state on oversized (>1MB) and invalid image formats", () => {
    render(<PersonaEditor roles={MOCK_ROLES} selectedRoleName="barb" prefs={MOCK_PREFS} onPrefsSaved={vi.fn()} />);
    const fileInput = screen.getByTestId("persona-editor-file-input");
    fireEvent.change(fileInput, { target: { files: [new File([new ArrayBuffer(1024 * 1024 + 10)], "big.png", { type: "image/png" })] } });
    expect(screen.getByTestId("persona-editor-avatar-error")).toHaveTextContent(/exceeds 1mb limit/i);
    fireEvent.change(fileInput, { target: { files: [new File(["<svg></svg>"], "icon.svg", { type: "image/svg+xml" })] } });
    expect(screen.getByTestId("persona-editor-avatar-error")).toHaveTextContent(/must be png or webp/i);
  });

  it("uploads custom avatar successfully and notifies parent", async () => {
    const onPrefsSaved = vi.fn();
    mockFetchWithBrowserCsrf.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ ok: true, operator_id: "test-op", role_id: "barb", avatar_url: "/avatar.webp" }),
    });
    render(<PersonaEditor roles={MOCK_ROLES} selectedRoleName="barb" prefs={MOCK_PREFS} onPrefsSaved={onPrefsSaved} />);
    fireEvent.change(screen.getByTestId("persona-editor-file-input"), { target: { files: [new File(["x"], "a.png", { type: "image/png" })] } });
    fireEvent.click(screen.getByTestId("persona-editor-upload-btn"));
    await waitFor(() => {
      expect(mockFetchWithBrowserCsrf).toHaveBeenCalledWith("/api/v1/staff/avatars/custom/barb?expected_revision=1", expect.anything());
    });
    expect(onPrefsSaved).toHaveBeenCalledWith(expect.objectContaining({ avatar_overrides: expect.objectContaining({ barb: "/avatar.webp" }) }));
  });

  it("resets custom avatar to default and notifies parent", async () => {
    const onPrefsSaved = vi.fn();
    mockApiRequest.mockResolvedValueOnce({ ok: true, role_id: "architect", reset: true });
    render(<PersonaEditor roles={MOCK_ROLES} selectedRoleName="architect" prefs={MOCK_PREFS} onPrefsSaved={onPrefsSaved} />);
    fireEvent.click(screen.getByTestId("persona-editor-reset-avatar-btn"));
    await waitFor(() => {
      expect(mockApiRequest).toHaveBeenCalledWith("/api/v1/staff/avatars/custom/architect/reset?expected_revision=1", expect.anything());
    });
    expect(onPrefsSaved).toHaveBeenCalled();
  });

  it("changing selected role while upload is pending cannot update the wrong role", async () => {
    let resolveUpload: (val: unknown) => void = () => {};
    mockFetchWithBrowserCsrf.mockReturnValueOnce(new Promise((resolve) => { resolveUpload = resolve; }));
    const onPrefsSaved = vi.fn();
    render(<PersonaEditor roles={MOCK_ROLES} selectedRoleName="barb" prefs={MOCK_PREFS} onPrefsSaved={onPrefsSaved} />);
    fireEvent.change(screen.getByTestId("persona-editor-file-input"), { target: { files: [new File(["x"], "b.png", { type: "image/png" })] } });
    fireEvent.click(screen.getByTestId("persona-editor-upload-btn"));
    fireEvent.change(screen.getByTestId("persona-editor-role-select"), { target: { value: "architect" } });
    resolveUpload({ ok: true, json: async () => ({ ok: true, operator_id: "test-op", role_id: "barb", avatar_url: "/barb.webp" }) });
    await waitFor(() => {
      expect(onPrefsSaved).toHaveBeenCalledWith(expect.objectContaining({
        avatar_overrides: expect.objectContaining({ barb: "/barb.webp", architect: MOCK_PREFS.avatar_overrides.architect }),
      }));
    });
  });

  it("handles 409 stale revision and 401/403 visibly without false success", async () => {
    mockFetchWithBrowserCsrf.mockResolvedValueOnce({ ok: false, status: 409, json: async () => ({ detail: "stale" }) });
    render(<PersonaEditor roles={MOCK_ROLES} selectedRoleName="barb" prefs={MOCK_PREFS} onPrefsSaved={vi.fn()} />);
    fireEvent.change(screen.getByTestId("persona-editor-file-input"), { target: { files: [new File(["x"], "a.png", { type: "image/png" })] } });
    fireEvent.click(screen.getByTestId("persona-editor-upload-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("persona-editor-avatar-error")).toHaveTextContent(/stale revision/i);
    });
    expect(screen.getByTestId("persona-editor-upload-btn")).toBeInTheDocument();
  });
});
