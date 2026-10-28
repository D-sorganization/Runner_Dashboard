// @vitest-environment jsdom
/**
 * Tests for TabErrorBoundary (D1 / issue #720).
 *
 * Covers:
 * 1. Renders children when no error.
 * 2. Shows fallback with tab name when child throws.
 * 3. "Reload tab" button remounts the child (resets error state).
 * 4. role="alert" is present on the fallback.
 * 5. aria-live="assertive" on the fallback.
 * 6. onReset callback is called when boundary resets.
 */

import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TabErrorBoundary } from "../TabErrorBoundary";

// Silence React error boundary console noise
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

function Bomb({ shouldThrow }: { shouldThrow: boolean }): React.ReactElement {
  if (shouldThrow) throw new Error("Tab explosion");
  return <div data-testid="ok">Content loaded</div>;
}

describe("TabErrorBoundary", () => {
  it("renders children when no error occurs", () => {
    render(
      <TabErrorBoundary tabName="Fleet">
        <Bomb shouldThrow={false} />
      </TabErrorBoundary>,
    );
    expect(screen.getByTestId("ok")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("renders fallback with tab name when child throws", () => {
    render(
      <TabErrorBoundary tabName="Maxwell">
        <Bomb shouldThrow={true} />
      </TabErrorBoundary>,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toBeTruthy();
    expect(alert.textContent).toContain("Maxwell");
  });

  it("shows the error message in the fallback", () => {
    render(
      <TabErrorBoundary tabName="Fleet">
        <Bomb shouldThrow={true} />
      </TabErrorBoundary>,
    );
    expect(screen.getByRole("alert").textContent).toContain("Tab explosion");
  });

  it('has aria-live="assertive" on fallback', () => {
    render(
      <TabErrorBoundary tabName="Fleet">
        <Bomb shouldThrow={true} />
      </TabErrorBoundary>,
    );
    const alert = screen.getByRole("alert");
    expect(alert.getAttribute("aria-live")).toBe("assertive");
  });

  it('"Reload tab" button resets the error state', () => {
    let shouldThrow = true;

    function ToggleBomb(): React.ReactElement {
      if (shouldThrow) throw new Error("boom");
      return <div data-testid="recovered">Recovered</div>;
    }

    const { rerender } = render(
      <TabErrorBoundary tabName="Queue">
        <ToggleBomb />
      </TabErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeTruthy();

    shouldThrow = false;
    fireEvent.click(screen.getByRole("button", { name: /reload tab/i }));
    rerender(
      <TabErrorBoundary tabName="Queue">
        <ToggleBomb />
      </TabErrorBoundary>,
    );
    expect(screen.getByTestId("recovered")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("calls onReset callback when boundary resets", () => {
    const onReset = vi.fn();
    let shouldThrow = true;

    function ToggleBomb(): React.ReactElement {
      if (shouldThrow) throw new Error("boom");
      return <div data-testid="ok">ok</div>;
    }

    const { rerender } = render(
      <TabErrorBoundary tabName="Remediation" onReset={onReset}>
        <ToggleBomb />
      </TabErrorBoundary>,
    );

    shouldThrow = false;
    fireEvent.click(screen.getByRole("button", { name: /reload tab/i }));
    rerender(
      <TabErrorBoundary tabName="Remediation" onReset={onReset}>
        <ToggleBomb />
      </TabErrorBoundary>,
    );
    expect(onReset).toHaveBeenCalledOnce();
  });

  it("renders Copy details button and report issue link with stack and build SHA", () => {
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    });

    render(
      <TabErrorBoundary tabName="Overview" buildSha="git-sha-1234">
        <Bomb shouldThrow={true} />
      </TabErrorBoundary>,
    );

    // Verify copy button
    const copyBtn = screen.getByRole("button", { name: /copy details/i });
    expect(copyBtn).toBeTruthy();
    fireEvent.click(copyBtn);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      expect.stringContaining("Tab: Overview"),
    );
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      expect.stringContaining("git-sha-1234"),
    );

    // Verify report issue link
    const reportLink = screen.getByRole("link", { name: /report issue/i });
    expect(reportLink).toBeTruthy();
    const href = reportLink.getAttribute("href") || "";
    expect(href).toContain(
      "github.com/D-sorganization/Runner_Dashboard/issues/new",
    );
    expect(href).toContain("git-sha-1234");
    expect(href).toContain("Overview");
  });

  it("resets the error boundary when resetKey changes (navigation simulation)", () => {
    let shouldThrow = true;

    function ConditionalBomb(): React.ReactElement {
      if (shouldThrow) throw new Error("Crashed on Tab A");
      return <div data-testid="tab-b-content">Tab B loaded</div>;
    }

    const { rerender } = render(
      <TabErrorBoundary tabName="Overview" resetKey="overview">
        <ConditionalBomb />
      </TabErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeTruthy();

    // Navigate to Queue tab
    shouldThrow = false;
    rerender(
      <TabErrorBoundary tabName="Queue" resetKey="queue">
        <ConditionalBomb />
      </TabErrorBoundary>,
    );

    // Boundary automatically resets on resetKey change
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByTestId("tab-b-content")).toBeTruthy();
  });

  it("reports caught errors to reportErrorFn with page, message, stack, build_sha", () => {
    const reportFn = vi.fn().mockResolvedValue(undefined);

    render(
      <TabErrorBoundary
        tabName="Staff"
        buildSha="build-999"
        reportErrorFn={reportFn}
      >
        <Bomb shouldThrow={true} />
      </TabErrorBoundary>,
    );

    expect(reportFn).toHaveBeenCalledWith(
      expect.objectContaining({
        page: "Staff",
        message: "Tab explosion",
        build_sha: "build-999",
      }),
    );
  });

  describe("reportClientError", () => {
    it("sends POST /api/client-errors with JSON body", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(
          new Response('{"status":"recorded"}', { status: 200 }),
        );
      vi.stubGlobal("fetch", fetchMock);

      const { reportClientError } = await import("../TabErrorBoundary");
      await reportClientError({
        page: "Events",
        message: "Something crashed",
        build_sha: "sha-test",
      });

      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/client-errors"),
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
        }),
      );
    });

    it("does not throw when fetch rejects", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockRejectedValue(new Error("Network error")),
      );
      const { reportClientError } = await import("../TabErrorBoundary");
      await expect(
        reportClientError({
          page: "Events",
          message: "Network drop",
        }),
      ).resolves.not.toThrow();
    });

    it("rate limits and drops reports when exceeding the limit", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(
          new Response('{"status":"recorded"}', { status: 200 }),
        );
      vi.stubGlobal("fetch", fetchMock);
      const { reportClientError } = await import("../TabErrorBoundary");

      // Call 15 times
      for (let i = 0; i < 15; i++) {
        await reportClientError({
          page: "SpamPage",
          message: `Crash ${i}`,
        });
      }

      // Should be clamped to max 10
      expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(10);
    });
  });
});
