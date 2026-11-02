// @vitest-environment jsdom
/**
 * PersonaGallery.test.tsx — Unit tests for dedicated mobile Persona gallery (#3502).
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { PersonaGallery } from "../PersonaGallery";
import { orderRoles, type PresentationPrefsResponse } from "../personaGalleryUtils";
import type { StaffRoleItem } from "../types";

// Mock apiRequest used by fetchPresentationPrefs / savePresentationPrefs
const mockApiRequest = vi.fn();
vi.mock("../../../lib/api", () => ({
  apiRequest: (...args: unknown[]) => mockApiRequest(...args),
}));

afterEach(cleanup);

const MOCK_ROLES: StaffRoleItem[] = [
  { name: "barb", title: "Barb", summary: "Fleet Orchestrator", group: "leadership", valid: true, caller_unread_count: 1 },
  { name: "architect", title: "Chief Architect", summary: "System design & ADR review", group: "specialists", valid: true },
  { name: "maintenance", title: "Fleet Maintenance", summary: "Disk hygiene and health", group: "operations", valid: true },
];

const MOCK_PREFS: PresentationPrefsResponse = {
  operator_id: "test-op",
  display_label: "Test User",
  role_order: ["architect", "barb", "maintenance"],
  avatar_overrides: {},
  revision: 1,
  updated_at: "2026-11-01T00:00:00Z",
};

describe("PersonaGallery (#3502)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApiRequest.mockResolvedValue(MOCK_PREFS);
  });

  describe("orderRoles utility", () => {
    it("orders roles by role_order and handles unknown/removed roles safely", () => {
      const ordered = orderRoles(MOCK_ROLES, ["unknown-role", "maintenance", "deleted-role"]);
      expect(ordered.map((r) => r.name)).toEqual(["maintenance", "barb", "architect"]);
    });
  });

  describe("Rendering & Card Content", () => {
    it("renders card content and selects role outside edit mode", async () => {
      const onSelectRole = vi.fn();
      render(<PersonaGallery roles={MOCK_ROLES} onSelectRole={onSelectRole} initialPrefs={MOCK_PREFS} />);
      expect(screen.getByText("Chief Architect")).toBeInTheDocument();
      expect(screen.getByText("architect")).toBeInTheDocument();
      expect(screen.getByText("System design & ADR review")).toBeInTheDocument();
      expect(screen.getByText("1")).toBeInTheDocument();
      fireEvent.click(screen.getByTestId("persona-card-architect"));
      expect(onSelectRole).toHaveBeenCalledWith("architect");
    });
  });

  describe("Edit mode, Keyboard & Touch reordering", () => {
    it("edit mode prevents opening chat, enables keyboard move buttons and cancel rollback", async () => {
      const onSelectRole = vi.fn();
      render(<PersonaGallery roles={MOCK_ROLES} onSelectRole={onSelectRole} initialPrefs={MOCK_PREFS} />);
      fireEvent.click(screen.getByTestId("persona-gallery-edit-btn"));
      expect(screen.getByRole("status")).toHaveTextContent(/entered edit mode/i);

      fireEvent.click(screen.getByTestId("persona-card-architect"));
      expect(onSelectRole).not.toHaveBeenCalled();

      expect(screen.getByTestId("move-up-architect")).toBeDisabled();
      const moveDownBtn = screen.getByTestId("move-down-architect");
      expect(moveDownBtn).not.toBeDisabled();
      fireEvent.click(moveDownBtn);
      expect(screen.getByRole("status")).toHaveTextContent(/moved chief architect to position 2 of 3/i);

      fireEvent.click(screen.getByTestId("persona-gallery-cancel-btn"));
      expect(screen.getByRole("status")).toHaveTextContent(/reordering cancelled/i);
    });

    it("touch drag handle and arrow keys reorder personas", async () => {
      render(<PersonaGallery roles={MOCK_ROLES} onSelectRole={vi.fn()} initialPrefs={MOCK_PREFS} />);
      fireEvent.click(screen.getByTestId("persona-gallery-edit-btn"));

      const dragHandle = screen.getByTestId("drag-handle-architect");
      fireEvent.touchStart(dragHandle, { touches: [{ clientY: 100 }] });
      fireEvent.touchMove(dragHandle, { touches: [{ clientY: 160 }] });
      fireEvent.touchEnd(dragHandle);
      expect(screen.getByRole("status")).toHaveTextContent(/moved chief architect to position 2 of 3/i);

      // Architect is now at index 1; ArrowDown moves it to position 3
      fireEvent.keyDown(dragHandle, { key: "ArrowDown" });
      expect(screen.getByRole("status")).toHaveTextContent(/moved chief architect to position 3 of 3/i);
    });
  });

  describe("Filtering & Persistence", () => {
    it("filtered view does not corrupt complete order", async () => {
      render(<PersonaGallery roles={MOCK_ROLES} onSelectRole={vi.fn()} initialPrefs={MOCK_PREFS} />);
      fireEvent.click(screen.getByTestId("persona-gallery-edit-btn"));
      const searchInput = screen.getByRole("searchbox", { name: /filter personas/i });
      fireEvent.change(searchInput, { target: { value: "maint" } });
      expect(screen.queryByTestId("persona-card-architect")).not.toBeInTheDocument();
      expect(screen.getByTestId("persona-card-maintenance")).toBeInTheDocument();
      fireEvent.change(searchInput, { target: { value: "" } });
      expect(screen.getByTestId("persona-card-architect")).toBeInTheDocument();
    });

    it("saves updated order and handles 409 conflict with retained state", async () => {
      const onPrefsSaved = vi.fn();
      mockApiRequest.mockResolvedValueOnce({ ...MOCK_PREFS, revision: 2 });
      render(<PersonaGallery roles={MOCK_ROLES} onSelectRole={vi.fn()} initialPrefs={MOCK_PREFS} onPrefsSaved={onPrefsSaved} />);

      fireEvent.click(screen.getByTestId("persona-gallery-edit-btn"));
      fireEvent.click(screen.getByTestId("move-down-architect"));
      fireEvent.click(screen.getByTestId("persona-gallery-save-btn"));

      await waitFor(() => {
        expect(mockApiRequest).toHaveBeenCalledWith("/api/v1/staff/presentation-prefs", {
          method: "PUT",
          body: { expected_revision: 1, role_order: ["barb", "architect", "maintenance"], display_label: "Test User", avatar_overrides: {} },
          signal: undefined,
        });
      });
      expect(screen.getByRole("status")).toHaveTextContent(/saved successfully/i);

      // 409 Conflict test
      mockApiRequest.mockRejectedValueOnce(new Error("409 Conflict"));
      fireEvent.click(screen.getByTestId("persona-gallery-edit-btn"));
      fireEvent.click(screen.getByTestId("persona-gallery-save-btn"));
      await waitFor(() => {
        expect(screen.getByRole("alert")).toHaveTextContent(/conflict: preferences were updated elsewhere/i);
      });
      expect(screen.getByTestId("persona-gallery-save-btn")).toBeInTheDocument();
    });
  });
});
