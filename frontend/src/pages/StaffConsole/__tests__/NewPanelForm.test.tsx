// @vitest-environment jsdom
/**
 * NewPanelForm.test.tsx — Unit tests for NewPanelForm component (SC-D/Issue #1635).
 */
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NewPanelForm } from "../NewPanelForm";
import type { PanelCostEstimate, PanelPresetsResponse } from "../panelApi";
import { PanelCostRequired } from "../panelApi";
import type { ThreadInfo } from "../threadTypes";

const mockPresets: PanelPresetsResponse = {
  presets: [
    {
      id: "engineering-review",
      title: "Engineering design review",
      experts: [
        { name: "Architect", perspective: "System architecture and maintainability." },
        { name: "Skeptic", perspective: "Failure modes and hidden assumptions." },
        { name: "Operator", perspective: "Day-to-day operation and cost." },
      ],
    },
  ],
  providers: ["claude", "codex", "gemini"],
};

const mockThread: ThreadInfo = {
  id: "thread-panel-1",
  title: "Panel on Microservices",
  kind: "panel",
  participants: ["Architect", "Skeptic", "Operator", "Moderator"],
  status: "active",
};

const mockEstimate: PanelCostEstimate = {
  group_id: "panel",
  total_cost_usd: 1.5,
  threshold_usd: 1.0,
  cost_per_seat: {
    Architect: 0.45,
    Skeptic: 0.45,
    Operator: 0.45,
    Moderator: 0.15,
  },
  exceeds_threshold: true,
  warning: "Estimated cost $1.50 exceeds $1.00 threshold.",
};

const panelApiMock = vi.hoisted(() => ({
  fetchPanelPresets: vi.fn(),
  createPanel: vi.fn(),
  fetchPanel: vi.fn(),
}));

vi.mock("../panelApi", async (importOriginal) => {
  const original = await importOriginal<typeof import("../panelApi")>();
  return {
    ...original,
    fetchPanelPresets: panelApiMock.fetchPanelPresets,
    createPanel: panelApiMock.createPanel,
    fetchPanel: panelApiMock.fetchPanel,
  };
});

describe("NewPanelForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    panelApiMock.fetchPanelPresets.mockResolvedValue(mockPresets);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("disables submit while validation errors exist and enables when valid", async () => {
    render(<NewPanelForm onCreated={vi.fn()} />);

    await waitFor(() => {
      expect(panelApiMock.fetchPanelPresets).toHaveBeenCalled();
    });

    const submitBtn = screen.getByRole("button", { name: /start panel/i });
    // Initially empty topic -> validation error -> submit disabled
    expect(submitBtn).toBeDisabled();
    expect(screen.getByText(/topic must not be blank/i)).toBeInTheDocument();

    // Fill in topic
    const topicInput = screen.getByLabelText(/topic/i);
    fireEvent.change(topicInput, { target: { value: "Should we migrate to microfrontends?" } });

    // Ensure preset is selected or experts are populated
    const presetSelect = screen.getByLabelText(/preset/i);
    fireEvent.change(presetSelect, { target: { value: "engineering-review" } });

    await waitFor(() => {
      expect(submitBtn).not.toBeDisabled();
    });

    // Invalidate by setting rounds to 0
    const roundsInput = screen.getByLabelText(/rounds/i);
    fireEvent.change(roundsInput, { target: { value: "0" } });

    expect(screen.getByText(/rounds must be between 1 and 6/i)).toBeInTheDocument();
    expect(submitBtn).toBeDisabled();
  });

  it("fills the expert rows when a preset is chosen", async () => {
    render(<NewPanelForm onCreated={vi.fn()} />);

    await waitFor(() => {
      expect(panelApiMock.fetchPanelPresets).toHaveBeenCalled();
    });

    const presetSelect = screen.getByLabelText(/preset/i);
    fireEvent.change(presetSelect, { target: { value: "engineering-review" } });

    const nameInputs = screen.getAllByLabelText(/expert name/i);
    expect(nameInputs).toHaveLength(3);
    expect(nameInputs[0]).toHaveValue("Architect");
    expect(nameInputs[1]).toHaveValue("Skeptic");
    expect(nameInputs[2]).toHaveValue("Operator");

    const perspectiveInputs = screen.getAllByLabelText(/expert perspective/i);
    expect(perspectiveInputs[0]).toHaveValue("System architecture and maintainability.");
  });

  it("shows the estimate on PanelCostRequired and Confirm resubmits with confirm_cost true", async () => {
    const onCreated = vi.fn();
    panelApiMock.createPanel
      .mockRejectedValueOnce(new PanelCostRequired("Estimated cost exceeds threshold", mockEstimate))
      .mockResolvedValueOnce({ thread: mockThread, estimate: mockEstimate });

    render(<NewPanelForm onCreated={onCreated} />);

    await waitFor(() => {
      expect(panelApiMock.fetchPanelPresets).toHaveBeenCalled();
    });

    // Setup valid form
    fireEvent.change(screen.getByLabelText(/topic/i), { target: { value: "Evaluate Rust vs Go" } });
    fireEvent.change(screen.getByLabelText(/preset/i), { target: { value: "engineering-review" } });

    const submitBtn = screen.getByRole("button", { name: /start panel/i });
    fireEvent.click(submitBtn);

    // Cost confirmation modal/banner appears
    await waitFor(() => {
      expect(screen.getByRole("alert", { name: /cost confirmation/i })).toBeInTheDocument();
    });

    expect(screen.getByText("$1.50")).toBeInTheDocument();
    expect(screen.getByText(/Architect: \$0.45/i)).toBeInTheDocument();

    const confirmBtn = screen.getByRole("button", { name: /confirm/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(panelApiMock.createPanel).toHaveBeenCalledTimes(2);
    });

    const secondCallArg = panelApiMock.createPanel.mock.calls[1][0];
    expect(secondCallArg.confirm_cost).toBe(true);
    expect(onCreated).toHaveBeenCalledWith(mockThread);
  });

  it("shows err.message and still confirms when the 400 carries no estimate", async () => {
    const onCreated = vi.fn();
    panelApiMock.createPanel
      .mockRejectedValueOnce(new PanelCostRequired("Cost exceeds limit; please confirm to proceed", null))
      .mockResolvedValueOnce({ thread: mockThread, estimate: mockEstimate });

    render(<NewPanelForm onCreated={onCreated} />);

    await waitFor(() => {
      expect(panelApiMock.fetchPanelPresets).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByLabelText(/topic/i), { target: { value: "Evaluate Rust vs Go" } });
    fireEvent.change(screen.getByLabelText(/preset/i), { target: { value: "engineering-review" } });

    fireEvent.click(screen.getByRole("button", { name: /start panel/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert", { name: /cost confirmation/i })).toBeInTheDocument();
    });

    expect(screen.getByText("Cost exceeds limit; please confirm to proceed")).toBeInTheDocument();

    const confirmBtn = screen.getByRole("button", { name: /confirm/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(panelApiMock.createPanel).toHaveBeenCalledTimes(2);
    });

    expect(panelApiMock.createPanel.mock.calls[1][0].confirm_cost).toBe(true);
    expect(onCreated).toHaveBeenCalledWith(mockThread);
  });

  it("shows the running-panels message on a 429 error", async () => {
    panelApiMock.createPanel.mockRejectedValue(new Error("2 panels are already running"));

    render(<NewPanelForm onCreated={vi.fn()} />);

    await waitFor(() => {
      expect(panelApiMock.fetchPanelPresets).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByLabelText(/topic/i), { target: { value: "Evaluate Rust vs Go" } });
    fireEvent.change(screen.getByLabelText(/preset/i), { target: { value: "engineering-review" } });

    fireEvent.click(screen.getByRole("button", { name: /start panel/i }));

    await waitFor(() => {
      expect(screen.getByText(/2 panels are already running/i)).toBeInTheDocument();
    });
  });
});
