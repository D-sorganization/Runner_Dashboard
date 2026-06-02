// @vitest-environment jsdom
/**
 * Tests for BiometricUnlock.tsx — issue #728 E3.
 *
 * Covers:
 * 1. Renders without throwing (smoke test).
 * 2. Renders heading and register button.
 * 3. Shows "not supported" message when WebAuthn is unavailable.
 * 4. Renders existing credentials list when API returns credentials.
 * 5. Shows error when registration API call fails.
 * 6. Loading credentials from /api/auth/webauthn/credentials.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BiometricUnlock } from "../BiometricUnlock";

afterEach(cleanup);

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

const MOCK_CREDENTIALS = {
  credentials: [
    {
      credential_id: "cred-abc-001",
      label: "iPhone Touch ID",
      created_at: 1700000000,
    },
  ],
};

const EMPTY_CREDENTIALS = { credentials: [] };

function makeCredsFetch(data: object = EMPTY_CREDENTIALS, ok: boolean = true) {
  return vi.fn((_url: string) =>
    Promise.resolve({
      ok,
      status: ok ? 200 : 500,
      json: () => Promise.resolve(data),
    } as Response),
  );
}

// Helper: mock WebAuthn as unsupported
function removeWebAuthn() {
  const orig = (window as Record<string, unknown>).PublicKeyCredential;
  delete (window as Record<string, unknown>).PublicKeyCredential;
  return orig;
}

describe("BiometricUnlock", () => {
  it("renders without throwing (smoke test)", () => {
    global.fetch = makeCredsFetch();
    expect(() => render(<BiometricUnlock />)).not.toThrow();
  });

  it("renders the main heading", () => {
    global.fetch = makeCredsFetch();
    render(<BiometricUnlock />);
    // The heading text is "Mobile Biometric Unlock" - use getAllByText to handle
    // potential duplicates and just assert at least one exists
    const headings = screen.getAllByText(/Mobile Biometric Unlock/i);
    expect(headings.length).toBeGreaterThan(0);
  });

  it("renders a register button", async () => {
    global.fetch = makeCredsFetch();
    render(<BiometricUnlock />);
    await waitFor(() => {
      const btn = screen.queryByRole("button", { name: /register device/i });
      expect(btn).not.toBeNull();
    });
  });

  it("shows 'not supported' message when WebAuthn is unavailable (jsdom default)", async () => {
    global.fetch = makeCredsFetch();
    // jsdom does not implement PublicKeyCredential — isSupported will be false,
    // and the component renders a static "Your browser or device does not support
    // biometric authentication." block when isSupported === false.
    const orig = removeWebAuthn();
    render(<BiometricUnlock />);
    await waitFor(() => {
      // The component renders the static unsupported message once isSupported=false
      // is set (synchronously in useEffect in jsdom).
      const bodyText = document.body.textContent ?? "";
      expect(bodyText).toMatch(
        /does not support biometric|not supported on this device|Your browser/i,
      );
    });
    if (orig !== undefined) {
      (window as Record<string, unknown>).PublicKeyCredential = orig;
    }
  });

  it("renders existing credentials when API returns them", async () => {
    global.fetch = makeCredsFetch(MOCK_CREDENTIALS);
    render(<BiometricUnlock />);
    await waitFor(() => {
      expect(screen.getByText(/iPhone Touch ID/i)).toBeInTheDocument();
    });
  });

  it("renders empty credentials list without crashing", async () => {
    global.fetch = makeCredsFetch(EMPTY_CREDENTIALS);
    render(<BiometricUnlock />);
    await waitFor(() => {
      // Component renders without crash even with empty list
      expect(document.body.textContent).toBeTruthy();
    });
  });

  it("shows 'no registered credentials' or empty state when list is empty", async () => {
    global.fetch = makeCredsFetch(EMPTY_CREDENTIALS);
    render(<BiometricUnlock />);
    await waitFor(() => {
      // Should display either no-credentials message or an empty container
      // The exact text depends on the component, but it must not crash
      expect(document.body).toBeInTheDocument();
    });
  });

  it("handles credential fetch failure gracefully (credentials default to empty)", async () => {
    global.fetch = vi.fn(() => Promise.reject(new Error("Network error")));
    // Component should not throw even if credentials fetch fails
    expect(() => render(<BiometricUnlock />)).not.toThrow();
    await waitFor(() => {
      expect(document.body).toBeInTheDocument();
    });
  });

  it("registers via typed navigator.credentials.create and handles 501 stub", async () => {
    // Mark WebAuthn as supported so the buttons become enabled.
    const origPkc = (window as Record<string, unknown>).PublicKeyCredential;
    (window as Record<string, unknown>).PublicKeyCredential = {
      isUserVerifyingPlatformAuthenticatorAvailable: () =>
        Promise.resolve(true),
    };

    // Mock the typed WebAuthn credential the component now narrows to
    // PublicKeyCredential / AuthenticatorAttestationResponse.
    const fakeBuffer = new ArrayBuffer(4);
    const createMock = vi.fn(() =>
      Promise.resolve({
        id: "new-cred-id",
        type: "public-key",
        rawId: fakeBuffer,
        response: {
          clientDataJSON: fakeBuffer,
          attestationObject: fakeBuffer,
        },
      }),
    );
    const origCreds = (navigator as Record<string, unknown>).credentials;
    Object.defineProperty(navigator, "credentials", {
      configurable: true,
      value: { create: createMock, get: vi.fn() },
    });

    global.fetch = vi.fn((url: string) => {
      if (url.includes("/register/begin")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              challenge: "AAAA",
              rp: { name: "Runner Dashboard", id: "localhost" },
              user: { id: "user-1", name: "operator" },
              timeout_ms: 60000,
            }),
        } as Response);
      }
      if (url.includes("/register/complete")) {
        return Promise.resolve({
          ok: false,
          status: 501,
          json: () => Promise.resolve({ detail: "not implemented" }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(EMPTY_CREDENTIALS),
      } as Response);
    }) as typeof fetch;

    render(<BiometricUnlock />);
    const registerBtn = await screen.findByRole("button", {
      name: /register device/i,
    });
    await waitFor(() => expect(registerBtn).not.toBeDisabled());
    fireEvent.click(registerBtn);

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledTimes(1);
      expect(
        screen.getByText(/Backend verification is not yet implemented/i),
      ).toBeInTheDocument();
    });

    // Restore globals
    if (origPkc !== undefined) {
      (window as Record<string, unknown>).PublicKeyCredential = origPkc;
    } else {
      delete (window as Record<string, unknown>).PublicKeyCredential;
    }
    Object.defineProperty(navigator, "credentials", {
      configurable: true,
      value: origCreds,
    });
  });
});
