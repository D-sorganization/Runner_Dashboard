// @vitest-environment jsdom
/**
 * Tests for the codebase Q&A assistant (issue #838).
 *
 * TDD: covers the repo picker forwarding repo/repo_root through
 * POST /api/maxwell/chat, codebase quick-chips being gated on a repo selection,
 * streaming responses rendering, and the graceful dead-end-free error string.
 */
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { CodebaseChat } from "../CodebaseChat";

afterEach(cleanup);

/** Build a fetch mock: repos for /api/repos, a streamed text body for /chat. */
function makeFetch(opts?: { chatText?: string; chatOk?: boolean }) {
  const chatText = opts?.chatText ?? "Queue handling lives in backend/routers/queue.py.";
  const chatOk = opts?.chatOk ?? true;
  const calls: { url: string; body?: unknown }[] = [];
  const impl = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (url === "/api/repos") {
      return new Response(JSON.stringify([{ name: "Runner_Dashboard" }, { name: "Maxwell-Daemon" }]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    // /api/maxwell/chat — streamed text/plain body.
    return new Response(chatText, {
      status: chatOk ? 200 : 500,
      headers: { "Content-Type": "text/plain" },
    });
  }) as unknown as typeof fetch;
  return { impl, calls };
}

describe("CodebaseChat", () => {
  it("disables chips and composer until a repo is selected", () => {
    const { impl } = makeFetch();
    render(<CodebaseChat fetchImpl={impl} />);
    expect(screen.getByRole("button", { name: /send question/i })).toBeDisabled();
    // A codebase quick-chip is present but disabled before repo selection.
    const chip = screen.getByRole("button", { name: /what does \/api\/queue do\?/i });
    expect(chip).toBeDisabled();
  });

  it("forwards repo and repo_root through /api/maxwell/chat", async () => {
    const { impl, calls } = makeFetch();
    render(<CodebaseChat fetchImpl={impl} />);

    fireEvent.change(screen.getByLabelText(/^Repository$/i), {
      target: { value: "Runner_Dashboard" },
    });
    fireEvent.change(screen.getByLabelText(/local repository path/i), {
      target: { value: "/home/runner/Runner_Dashboard" },
    });

    const chip = screen.getByRole("button", { name: /what does \/api\/queue do\?/i });
    await waitFor(() => expect(chip).not.toBeDisabled());
    fireEvent.click(chip);

    await waitFor(() => {
      const chat = calls.find((c) => c.url === "/api/maxwell/chat");
      expect(chat).toBeTruthy();
      expect((chat!.body as Record<string, unknown>).repo).toBe("Runner_Dashboard");
      expect((chat!.body as Record<string, unknown>).repo_root).toBe(
        "/home/runner/Runner_Dashboard",
      );
    });
  });

  it("renders the streamed assistant response", async () => {
    const { impl } = makeFetch({ chatText: "It lives in queue.py." });
    render(<CodebaseChat fetchImpl={impl} />);
    fireEvent.change(screen.getByLabelText(/^Repository$/i), {
      target: { value: "Runner_Dashboard" },
    });
    fireEvent.click(screen.getByRole("button", { name: /how does runner autoscaling work\?/i }));
    expect(await screen.findByText(/It lives in queue\.py\./)).toBeInTheDocument();
  });

  it("shows an actionable error (not a raw HTTP code) when the chat call fails", async () => {
    const { impl } = makeFetch({ chatOk: false });
    render(<CodebaseChat fetchImpl={impl} />);
    fireEvent.change(screen.getByLabelText(/^Repository$/i), {
      target: { value: "Runner_Dashboard" },
    });
    fireEvent.click(screen.getByRole("button", { name: /where is the job queue handled\?/i }));
    expect(await screen.findByText(/Maxwell-Daemon must be running/i)).toBeInTheDocument();
  });
});
