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
  emptyProbes = false,
  setKeyOk = true,
  clearKeyOk = true,
  beginOk = true,
  completeOk = true,
}: {
  credentialsOk?: boolean;
  emptyProbes?: boolean;
  setKeyOk?: boolean;
  clearKeyOk?: boolean;
  beginOk?: boolean;
  completeOk?: boolean;
} = {}) {
  const fetchMock = vi.fn((url: string, _options?: RequestInit) => {
    if (url.includes("/api/credentials") && !url.includes("set-key") && !url.includes("clear-key")) {
      return Promise.resolve({
        ok: credentialsOk,
        status: credentialsOk ? 200 : 500,
        json: () =>
          Promise.resolve(
            emptyProbes
              ? { probes: [], summary: { total: 0, ready: 0, not_ready: 0 } }
              : MOCK_CREDENTIALS,
          ),
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
    if (url.includes("/api/auth/webauthn/assert/begin")) {
      return Promise.resolve({
        ok: beginOk,
        status: beginOk ? 200 : 404,
        json: () =>
          Promise.resolve(
            beginOk
              ? {
                  challenge: "dGVzdC1jaGFsbGVuZ2U",
                  challenge_signature: "sig-1",
                  allow_credentials: [{ id: "Y3JlZC0x", type: "public-key" }],
                  timeout_ms: 60000,
                }
              : { detail: "No active WebAuthn credential registered for this user" },
          ),
      } as Response);
    }
    if (url.includes("/api/auth/webauthn/assert/complete")) {
      return Promise.resolve({
        ok: completeOk,
        status: completeOk ? 200 : 401,
        json: () =>
          Promise.resolve(
            completeOk ? { ok: true } : { detail: "Invalid WebAuthn assertion signature" },
          ),
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

function stubWebAuthnAvailable() {
  Object.defineProperty(window, "PublicKeyCredential", {
    configurable: true,
    value: {
      isUserVerifyingPlatformAuthenticatorAvailable: vi.fn(() => Promise.resolve(true)),
    },
    writable: true,
  });
  Object.defineProperty(navigator, "credentials", {
    configurable: true,
    value: {
      get: vi.fn(() =>
        Promise.resolve({
          id: "cred-1",
          type: "public-key",
        }),
      ),
    },
    writable: true,
  });
}

function stubWebAuthnUnavailable() {
  Object.defineProperty(window, "PublicKeyCredential", {
    configurable: true,
    value: undefined,
    writable: true,
  });
}

async function unlockCredentials() {
  const unlockBtn = screen.getByRole("button", { name: /unlock credentials with biometrics/i });
  await act(async () => {
    fireEvent.click(unlockBtn);
  });
  await waitFor(() => {
    expect(screen.queryByRole("button", { name: /unlock credentials with biometrics/i })).not.toBeInTheDocument();
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CredentialsMobile", () => {
  beforeEach(() => {
    stubMatchMedia();
    vi.spyOn(console, "error").mockImplementation(() => {});
    stubWebAuthnAvailable();
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
      screen.getByRole("button", { name: /unlock credentials with biometrics/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: /credentials locked/i }),
    ).toBeInTheDocument();
  });

  it("stays locked when WebAuthn is unavailable or verification fails", async () => {
    stubWebAuthnUnavailable();
    setupFetch();
    render(<CredentialsMobile />);

    const unlockBtn = screen.getByRole("button", { name: /unlock credentials with biometrics/i });
    await act(async () => {
      fireEvent.click(unlockBtn);
    });

    expect(
      screen.getByRole("button", { name: /unlock credentials with biometrics/i }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Claude Code CLI")).not.toBeInTheDocument();

    stubWebAuthnAvailable();
    setupFetch({ completeOk: false });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /unlock credentials with biometrics/i }));
    });
    expect(
      screen.getByRole("button", { name: /unlock credentials with biometrics/i }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Claude Code CLI")).not.toBeInTheDocument();
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

  it("BottomSheet is read-only (no API key mutation actions)", async () => {
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

    expect(screen.queryByRole("button", { name: /set api key/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /clear key/i })).not.toBeInTheDocument();
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
        screen.getByRole("button", { name: /unlock credentials with biometrics/i }),
      ).toBeInTheDocument();
    });
  });

  it("shows empty state when no probes are returned", async () => {
    setupFetch({ emptyProbes: true });

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
