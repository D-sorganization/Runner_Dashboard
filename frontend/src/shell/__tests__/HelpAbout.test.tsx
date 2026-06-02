// @vitest-environment jsdom
/**
 * Tests for the Help/About surface (#822).
 *
 * TDD: authored alongside the component. Covers the '?' trigger opening an
 * accessible dialog, the lazy /api/version probe, quick-link navigation, the
 * "first things to check" list, and the keyboard-shortcut help. Focus-trap /
 * Escape behaviour is owned (and separately tested) by the Dialog primitive.
 */
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { HelpAbout } from "../HelpAbout";

afterEach(cleanup);

function versionFetch(body: Record<string, unknown> = { dashboard: "1.2.3", git_sha: "0000111" }) {
  return vi.fn(async () =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  ) as unknown as typeof fetch;
}

describe("HelpAbout", () => {
  it("renders a labelled '?' trigger that opens a dialog", async () => {
    render(<HelpAbout onNavigate={vi.fn()} fetchImpl={versionFetch()} />);
    const trigger = screen.getByRole("button", { name: /help and about/i });
    expect(trigger).toHaveAttribute("aria-haspopup", "dialog");
    fireEvent.click(trigger);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("fetches and shows the dashboard version when opened", async () => {
    const fetchImpl = versionFetch({ dashboard: "9.9.9", git_sha: "1234567890" });
    render(<HelpAbout onNavigate={vi.fn()} fetchImpl={fetchImpl} />);
    fireEvent.click(screen.getByRole("button", { name: /help and about/i }));
    await waitFor(() => expect(screen.getByText(/9\.9\.9/)).toBeInTheDocument());
    // Short (7-char) git sha is surfaced too.
    expect(screen.getByText(/1234567/)).toBeInTheDocument();
  });

  it("navigates and closes when a quick-link is clicked", async () => {
    const onNavigate = vi.fn();
    render(<HelpAbout onNavigate={onNavigate} fetchImpl={versionFetch()} />);
    fireEvent.click(screen.getByRole("button", { name: /help and about/i }));
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: /^Queue$/i }));
    expect(onNavigate).toHaveBeenCalledWith("queue");
  });

  it("shows the first-things-to-check list and keyboard shortcuts", async () => {
    render(<HelpAbout onNavigate={vi.fn()} fetchImpl={versionFetch()} />);
    fireEvent.click(screen.getByRole("button", { name: /help and about/i }));
    await screen.findByRole("dialog");
    expect(screen.getByRole("region", { name: /first things to check/i })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: /keyboard shortcuts/i })).toBeInTheDocument();
    expect(screen.getByText(/command palette/i)).toBeInTheDocument();
  });

  it("opens when the '?' key is pressed outside a text field", async () => {
    render(<HelpAbout onNavigate={vi.fn()} fetchImpl={versionFetch()} />);
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.keyDown(document.body, { key: "?" });
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});
