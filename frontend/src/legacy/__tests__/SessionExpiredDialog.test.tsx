import "@testing-library/jest-dom/vitest";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionExpiredDialog } from "../SessionExpiredDialog";
import {
  emitSessionExpired,
  shouldIgnoreUnauthorizedResponse,
  subscribeSessionExpired,
  tryRefreshSession,
} from "../sessionExpired";

function SessionExpiredHarness() {
  const [open, setOpen] = React.useState(false);

  React.useEffect(() => subscribeSessionExpired(() => setOpen(true)), []);

  return <SessionExpiredDialog open={open} onClose={() => setOpen(false)} />;
}

describe("SessionExpiredDialog", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders as an accessible modal dialog and focuses the re-authenticate action", () => {
    render(<SessionExpiredDialog open onClose={vi.fn()} />);

    const dialog = screen.getByRole("dialog", { name: "Session Expired" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("aria-labelledby", "session-expired-dialog-title");
    expect(dialog).toHaveAttribute("aria-describedby", "session-expired-dialog-description");
    expect(screen.getByRole("button", { name: "Re-authenticate" })).toHaveFocus();
  });

  it("closes when Escape is pressed", () => {
    const onClose = vi.fn();
    render(<SessionExpiredDialog open onClose={onClose} />);

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("mounts exactly once after repeated session-expired events", () => {
    render(<SessionExpiredHarness />);

    act(() => {
      for (let i = 0; i < 10; i += 1) {
        emitSessionExpired();
      }
    });

    expect(screen.getAllByRole("dialog", { name: "Session Expired" })).toHaveLength(1);
  });

  it("does not classify auth probes or health checks as session expiry", () => {
    expect(shouldIgnoreUnauthorizedResponse("/api/auth/me")).toBe(true);
    expect(shouldIgnoreUnauthorizedResponse("/api/health")).toBe(true);
    expect(shouldIgnoreUnauthorizedResponse("/api/auth/refresh")).toBe(true);
    expect(shouldIgnoreUnauthorizedResponse("/api/runners")).toBe(false);
  });

  it("attempts a silent refresh before the dialog path is needed", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));

    await expect(tryRefreshSession(fetchMock as unknown as typeof window.fetch)).resolves.toBe(true);

    expect(fetchMock).toHaveBeenCalledWith("/api/auth/refresh", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
    });
  });
});
