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
    expect(dialog).toHaveAttribute("aria-describedby", "recovery-dialog-description");
    expect(dialog.getAttribute("aria-labelledby")).toBeTruthy();
    expect(screen.getByText(/The dashboard backend is not responding/).closest("[aria-live]")).toHaveAttribute(
      "aria-live",
      "assertive",
    );
    expect(screen.getByRole("button", { name: "Start Now" })).toHaveAttribute(
      "data-touch-primitive",
      "TouchButton",
    );
    expect(screen.getByRole("button", { name: "Refresh" })).toHaveAttribute(
      "data-touch-primitive",
      "TouchButton",
    );
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

  it("shows actionable diagnostic guidance without misleading HTTPS verbiage when Start Now is clicked", () => {
    setPlatform("Win32");
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});

    render(
      <RecoveryDialog
        onClose={vi.fn()}
        healthUrl="https://example.ts.net/health"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Start Now" }));

    expect(alertSpy).not.toHaveBeenCalled();
    const alert = screen.getByRole("alert");
    // The misleading "Make sure you're using HTTPS" guidance must be gone:
    expect(alert.textContent ?? "").not.toMatch(/make sure you're using https/i);
    expect(alert.textContent ?? "").not.toMatch(/protocol handler requires https context/i);
    // The replacement is actionable: tells the user what to do and which URL
    // is being probed.
    expect(alert).toHaveTextContent(/sudo systemctl restart runner-dashboard\.service/);
    expect(alert).toHaveTextContent("https://example.ts.net/health");

    alertSpy.mockRestore();
  });

  it("always shows the terminal restart command, including on desktop platforms", () => {
    setPlatform("Win32");

    render(<RecoveryDialog onClose={vi.fn()} />);

    // The restart command must be visible up-front — clicking Start Now is
    // best-effort and frequently does nothing (handler not registered).
    expect(
      screen.getByText(/sudo systemctl restart runner-dashboard\.service/),
    ).toBeInTheDocument();
  });

  it("surfaces the health-probe URL so operators can curl it from outside the page", () => {
    setPlatform("Win32");

    render(
      <RecoveryDialog
        onClose={vi.fn()}
        healthUrl="https://example.ts.net/health"
      />,
    );

    expect(screen.getByText("https://example.ts.net/health")).toBeInTheDocument();
  });
});
