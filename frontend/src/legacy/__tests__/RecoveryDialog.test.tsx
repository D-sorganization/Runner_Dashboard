import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RecoveryDialog } from "../RecoveryDialog";

function setPlatform(platform: string) {
  Object.defineProperty(window.navigator, "platform", {
    configurable: true,
    value: platform,
  });
}

describe("RecoveryDialog", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders as a modal dialog with an assertive backend outage announcement", () => {
    setPlatform("Win32");

    render(<RecoveryDialog onClose={vi.fn()} />);

    const dialog = screen.getByRole("dialog", { name: "Backend Not Responding" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-labelledby", "recovery-dialog-title");
    expect(dialog).toHaveAttribute("aria-describedby", "recovery-dialog-description");
    expect(screen.getByText(/The dashboard backend is not responding/).closest("[aria-live]")).toHaveAttribute(
      "aria-live",
      "assertive",
    );
    expect(screen.getByRole("button", { name: "Start Now" })).toHaveFocus();
  });

  it("closes when Escape is pressed", () => {
    setPlatform("Win32");
    const onClose = vi.fn();

    render(<RecoveryDialog onClose={onClose} />);

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps Tab focus cycling inside the dialog", () => {
    setPlatform("Win32");

    render(<RecoveryDialog onClose={vi.fn()} />);

    const dialog = screen.getByRole("dialog");
    const startNow = screen.getByRole("button", { name: "Start Now" });
    const refresh = screen.getByRole("button", { name: "Refresh" });

    refresh.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(startNow).toHaveFocus();

    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(refresh).toHaveFocus();
  });

  it("shows inline protocol handler guidance instead of using alert", () => {
    setPlatform("Win32");
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});

    render(<RecoveryDialog onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Start Now" }));

    expect(alertSpy).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Protocol handler requires HTTPS context");

    alertSpy.mockRestore();
  });
});
