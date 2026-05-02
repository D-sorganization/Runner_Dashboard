// @vitest-environment jsdom
/**
 * Tests for Credentials/Mobile.tsx — M13.
 *
 * Covers:
 * 1. Initial state shows the locked screen with "Unlock with Biometrics" button.
 * 2. Clicking unlock when WebAuthn is unavailable still grants access (fallback).
 * 3. After unlock, credential cards are shown.
 * 4. Tapping a card opens the BottomSheet action sheet.
 * 5. "Set API Key" mode shows key input and Save button.
 * 6. Save Key calls POST /api/credentials/set-key.
 * 7. "Clear Key" calls POST /api/credentials/clear-key.
 * 8. Lock button re-locks the view.
 * 9. Empty state when no probes are returned.
 * 10. BottomSheet closes on Escape key.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import {
  cleanup,
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CredentialsMobile } from "../Mobile";

// jsdom doesn't implement matchMedia — provide a no-op stub.
function stubMatchMedia() {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

afterEach(cleanup);

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const MOCK_CREDENTIALS = {
  probes: [
    {
      id: "claude_code_cli",
      label: "Claude Code CLI",
      icon: "anthropic",
      installed: true,
      authenticated: true,
      reachable: true,
      usable: true,
      status: "ready",
      detail: "Ready",
      config_source: "env_var",
      docs_url: "https://docs.anthropic.com/claude-code",
      setup_hint: "npm install -g @anthropic-ai/claude-code then set ANTHROPIC_API_KEY",
      key_provider: "claude",
    },
    {
      id: "codex_cli",
      label: "Codex CLI",
      icon: "openai",
      installed: false,
      authenticated: false,
      reachable: false,
      usable: false,
      status: "not_installed",
      detail: "codex not on PATH or npm-global",
      config_source: "unavailable",
      docs_url: "https://github.com/openai/codex",
      setup_hint: "npm install -g @openai/codex then set OPENAI_API_KEY",
      key_provider: "codex",
    },
  ],
  summary: {
    total: 2,
    ready: 1,
    not_ready: 1,
  },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function setupFetch({
  credentialsOk = true,
  setKeyOk = true,
  clearKeyOk = true,
}: {
  credentialsOk?: boolean;
  setKeyOk?: boolean;
  clearKeyOk?: boolean;
} = {}) {
  const fetchMock = vi.fn((url: string, _options?: RequestInit) => {
    if (url.includes("/api/credentials") && !url.includes("set-key") && !url.includes("clear-key")) {
      return Promise.resolve({
        ok: credentialsOk,
        status: credentialsOk ? 200 : 500,
        json: () => Promise.resolve(MOCK_CREDENTIALS),
      } as Response);
    }
    if (url.includes("/api/credentials/set-key")) {
      return Promise.resolve({
        ok: setKeyOk,
        status: setKeyOk ? 200 : 422,
        json: () =>
          Promise.resolve(
            setKeyOk
              ? { ok: true, env_var: "ANTHROPIC_API_KEY", provider: "claude", maxwell_restart: {} }
              : { detail: "Failed to set key" },
          ),
      } as Response);
    }
    if (url.includes("/api/credentials/clear-key")) {
      return Promise.resolve({
        ok: clearKeyOk,
        status: clearKeyOk ? 200 : 422,
        json: () =>
          Promise.resolve(
            clearKeyOk
              ? { ok: true, env_var: "ANTHROPIC_API_KEY", provider: "claude", maxwell_restart: {} }
              : { detail: "Failed to clear key" },
          ),
      } as Response);
    }
    // WebAuthn begin — return 404 so fallback path is taken
    if (url.includes("/api/auth/webauthn/assert/begin")) {
      return Promise.resolve({
        ok: false,
        status: 404,
        json: () => Promise.resolve({ detail: "Not found" }),
      } as Response);
    }
    return Promise.resolve({
      ok: false,
      status: 404,
      json: () => Promise.resolve({}),
    } as Response);
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// Stub WebAuthn as unavailable so unlock always falls back
function stubWebAuthnUnavailable() {
  Object.defineProperty(window, "PublicKeyCredential", {
    configurable: true,
    value: undefined,
    writable: true,
  });
}

async function unlockCredentials() {
  const unlockBtn = screen.getByRole("button", { name: /unlock with biometrics/i });
  await act(async () => {
    fireEvent.click(unlockBtn);
  });
  await waitFor(() => {
    expect(screen.queryByRole("button", { name: /unlock with biometrics/i })).not.toBeInTheDocument();
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CredentialsMobile", () => {
  beforeEach(() => {
    stubMatchMedia();
    vi.spyOn(console, "error").mockImplementation(() => {});
    stubWebAuthnUnavailable();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    cleanup();
  });

  it("shows locked screen with Unlock with Biometrics button on initial render", () => {
    setupFetch();
    render(<CredentialsMobile />);

    expect(
      screen.getByRole("button", { name: /unlock with biometrics/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: /credentials locked/i }),
    ).toBeInTheDocument();
  });

  it("unlocks and shows credentials when WebAuthn is unavailable (fallback)", async () => {
    setupFetch();
    render(<CredentialsMobile />);

    await unlockCredentials();

    await waitFor(() => {
      expect(screen.getByText("Claude Code CLI")).toBeInTheDocument();
    });

    expect(screen.getByText("Codex CLI")).toBeInTheDocument();
  });

  it("shows credential summary after unlock", async () => {
    setupFetch();
    render(<CredentialsMobile />);

    await unlockCredentials();

    await waitFor(() => {
      expect(screen.getByText(/1 of 2 ready/i)).toBeInTheDocument();
    });
  });

  it("tapping a credential card opens the BottomSheet", async () => {
    setupFetch();
    render(<CredentialsMobile />);

    await unlockCredentials();

    await waitFor(() => {
      expect(screen.getByText("Claude Code CLI")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Credential: Claude Code CLI/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });

  it("BottomSheet shows Set API Key action for key-provider credentials", async () => {
    setupFetch();
    render(<CredentialsMobile />);

    await unlockCredentials();

    await waitFor(() => {
      expect(screen.getByText("Claude Code CLI")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Credential: Claude Code CLI/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: /set api key/i })).toBeInTheDocument();
  });

  it("shows key input form when Set API Key is clicked", async () => {
    setupFetch();
    render(<CredentialsMobile />);

    await unlockCredentials();

    await waitFor(() => {
      expect(screen.getByText("Claude Code CLI")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Credential: Claude Code CLI/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /set api key/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /set api key/i }));

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/paste your api key/i)).toBeInTheDocument();
    });
  });

  it("Save Key calls POST /api/credentials/set-key", async () => {
    const fetchMock = setupFetch();
    render(<CredentialsMobile />);

    await unlockCredentials();

    await waitFor(() => {
      expect(screen.getByText("Claude Code CLI")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Credential: Claude Code CLI/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /set api key/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /set api key/i }));

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/paste your api key/i)).toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText(/paste your api key/i), {
      target: { value: "sk-ant-test-key-12345" },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save key/i }));
    });

    await waitFor(() => {
      const setKeyCalls = (fetchMock.mock.calls as [string, RequestInit?][]).filter(
        ([url, opts]) =>
          url.includes("/api/credentials/set-key") && opts?.method === "POST",
      );
      expect(setKeyCalls).toHaveLength(1);
    });

    const setKeyCall = (fetchMock.mock.calls as [string, RequestInit?][]).find(
      ([url, opts]) =>
        url.includes("/api/credentials/set-key") && opts?.method === "POST",
    );
    expect(setKeyCall).toBeDefined();
    const body = JSON.parse(setKeyCall![1]!.body as string);
    expect(body.provider).toBe("claude");
    expect(body.key).toBe("sk-ant-test-key-12345");
  });

  it("Clear Key calls POST /api/credentials/clear-key", async () => {
    const fetchMock = setupFetch();
    render(<CredentialsMobile />);

    await unlockCredentials();

    await waitFor(() => {
      expect(screen.getByText("Claude Code CLI")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Credential: Claude Code CLI/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    const clearBtn = await screen.findByRole("button", { name: /clear key/i });

    await act(async () => {
      fireEvent.click(clearBtn);
    });

    await waitFor(() => {
      const clearKeyCalls = (fetchMock.mock.calls as [string, RequestInit?][]).filter(
        ([url, opts]) =>
          url.includes("/api/credentials/clear-key") && opts?.method === "POST",
      );
      expect(clearKeyCalls).toHaveLength(1);
    });
  });

  it("Lock button re-locks the view", async () => {
    setupFetch();
    render(<CredentialsMobile />);

    await unlockCredentials();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /lock credentials/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /lock credentials/i }));

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /unlock with biometrics/i }),
      ).toBeInTheDocument();
    });
  });

  it("shows empty state when no probes are returned", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({ probes: [], summary: { total: 0, ready: 0, not_ready: 0 } }),
        } as Response),
      ),
    );

    render(<CredentialsMobile />);

    await unlockCredentials();

    await waitFor(() => {
      expect(screen.getByRole("status", { name: /no credentials found/i })).toBeInTheDocument();
    });
  });

  it("BottomSheet closes on Escape key", async () => {
    setupFetch();
    render(<CredentialsMobile />);

    await unlockCredentials();

    await waitFor(() => {
      expect(screen.getByText("Claude Code CLI")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Credential: Claude Code CLI/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});
