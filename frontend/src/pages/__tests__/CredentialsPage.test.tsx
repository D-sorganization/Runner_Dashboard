// @vitest-environment jsdom
/**
 * Behaviour tests for pages/CredentialsPage.tsx — extracted from the legacy
 * App.tsx monolith (decomposition #836, pass 6).
 *
 * Covers:
 * 1. Smoke render.
 * 2. Summary stat row reflects the summary payload.
 * 3. Renders a card per probe with status label + detail/setup hint.
 * 4. Re-probe button invokes onRefresh and reflects loading.
 * 5. "Set API key" / "Replace API key" wording + onSetKey on desktop.
 * 6. Error banner renders the error string.
 * 7. Mobile gates behind a lock screen until unlocked.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CredentialsTab, type CredentialProbe } from "../CredentialsPage";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

const PROBES: CredentialProbe[] = [
  {
    id: "anthropic",
    label: "Anthropic",
    status: "ready",
    detail: "Key present",
    usable: true,
    key_provider: "anthropic",
    docs_url: "https://docs.example/anthropic",
  },
  {
    id: "openai",
    label: "OpenAI",
    status: "missing_key",
    setup_hint: "Add an API key to enable",
    usable: false,
    key_provider: "openai",
  },
  {
    id: "github",
    name: "GitHub",
    status: "not_installed",
  },
];

describe("CredentialsTab", () => {
  it("renders without throwing (smoke test)", () => {
    expect(() =>
      render(<CredentialsTab probes={[]} summary={{}} loading={false} onRefresh={() => {}} />),
    ).not.toThrow();
  });

  it("reflects the summary in the stat row", () => {
    render(
      <CredentialsTab
        probes={PROBES}
        summary={{ ready: 1, not_ready: 2, total: 3 }}
        loading={false}
        onRefresh={() => {}}
      />,
    );
    expect(screen.getAllByText("Ready").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Not ready")).toBeInTheDocument();
    expect(screen.getByText("Total")).toBeInTheDocument();
  });

  it("renders a card per probe with status + hints", () => {
    render(<CredentialsTab probes={PROBES} summary={{}} loading={false} onRefresh={() => {}} />);
    expect(screen.getByText("Anthropic")).toBeInTheDocument();
    expect(screen.getByText("Key present")).toBeInTheDocument();
    expect(screen.getByText("Add an API key to enable")).toBeInTheDocument();
    expect(screen.getByText("Not installed")).toBeInTheDocument();
    expect(screen.getByText("Missing key")).toBeInTheDocument();
  });

  it("re-probe button invokes onRefresh and shows loading text", () => {
    const onRefresh = vi.fn();
    const { rerender } = render(
      <CredentialsTab probes={PROBES} summary={{}} loading={false} onRefresh={onRefresh} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /re-probe runner status/i }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    rerender(<CredentialsTab probes={PROBES} summary={{}} loading={true} onRefresh={onRefresh} />);
    expect(screen.getByText("Probing...")).toBeInTheDocument();
  });

  it("invokes onSetKey with the probe on desktop", () => {
    const onSetKey = vi.fn();
    render(
      <CredentialsTab probes={PROBES} summary={{}} loading={false} onRefresh={() => {}} onSetKey={onSetKey} />,
    );
    fireEvent.click(screen.getByText("Replace API key"));
    expect(onSetKey).toHaveBeenCalledWith(expect.objectContaining({ id: "anthropic" }));
    expect(screen.getByText("Set API key")).toBeInTheDocument();
  });

  it("falls back to the raw status string for unknown statuses", () => {
    render(
      <CredentialsTab
        probes={[{ id: "weird", label: "Weird", status: "some_unmapped_state" }]}
        summary={{}}
        loading={false}
        onRefresh={() => {}}
      />,
    );
    expect(screen.getByText("some_unmapped_state")).toBeInTheDocument();
  });

  it("renders auth_failed and not_authed status labels", () => {
    render(
      <CredentialsTab
        probes={[
          { id: "a", label: "A", status: "auth_failed" },
          { id: "b", label: "B", status: "not_authed" },
          { id: "c", label: "C", status: "missing_env" },
        ]}
        summary={{}}
        loading={false}
        onRefresh={() => {}}
      />,
    );
    expect(screen.getByText("Auth failed")).toBeInTheDocument();
    expect(screen.getByText("Not authenticated")).toBeInTheDocument();
    expect(screen.getByText("Missing key")).toBeInTheDocument();
  });

  it("renders an error banner", () => {
    render(
      <CredentialsTab probes={[]} summary={{}} loading={false} onRefresh={() => {}} error="probe blew up" />,
    );
    expect(screen.getByText("probe blew up")).toBeInTheDocument();
  });

  it("gates behind a lock screen on mobile", () => {
    render(
      <CredentialsTab probes={PROBES} summary={{}} loading={false} onRefresh={() => {}} mobile />,
    );
    expect(screen.getByText("Credentials locked")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show credentials" })).toBeInTheDocument();
    // probe cards are not rendered while locked
    expect(screen.queryByText("Anthropic")).not.toBeInTheDocument();
  });

  it("reports a friendly message when WebAuthn is unavailable on mobile", async () => {
    // No window.PublicKeyCredential / navigator.credentials.get → graceful lock.
    vi.stubGlobal("PublicKeyCredential", undefined);
    render(
      <CredentialsTab probes={PROBES} summary={{}} loading={false} onRefresh={() => {}} mobile />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show credentials" }));
    await waitFor(() =>
      expect(screen.getByText("This browser does not expose WebAuthn credentials.")).toBeInTheDocument(),
    );
  });

  it("unlocks on mobile after a successful WebAuthn assertion", async () => {
    vi.stubGlobal("PublicKeyCredential", function () {});
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({ challenge: "AAAA", allow_credentials: [] }))));
    const getMock = vi.fn(() => Promise.resolve({ id: "c", type: "public-key", rawId: new ArrayBuffer(0), response: {} }));
    vi.stubGlobal("navigator", { credentials: { get: getMock } });
    const onRefresh = vi.fn();
    render(
      <CredentialsTab probes={PROBES} summary={{}} loading={false} onRefresh={onRefresh} mobile />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show credentials" }));
    await waitFor(() => expect(screen.getByText("Anthropic")).toBeInTheDocument());
    expect(getMock).toHaveBeenCalled();
    expect(onRefresh).toHaveBeenCalled();
  });

  it("re-locks on mobile when the tab loses visibility", async () => {
    vi.stubGlobal("PublicKeyCredential", function () {});
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({ challenge: "AAAA", allow_credentials: [] }))));
    vi.stubGlobal("navigator", {
      credentials: { get: () => Promise.resolve({ id: "c", type: "public-key", rawId: new ArrayBuffer(0), response: {} }) },
    });
    render(
      <CredentialsTab probes={PROBES} summary={{}} loading={false} onRefresh={() => {}} mobile />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show credentials" }));
    await waitFor(() => expect(screen.getByText("Anthropic")).toBeInTheDocument());
    // Simulate the tab being hidden — the visibilitychange handler re-locks.
    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    fireEvent(document, new Event("visibilitychange"));
    await waitFor(() => expect(screen.getByText("Credentials locked")).toBeInTheDocument());
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
  });

  it("re-locks when visibility is lost during the unlock completion tick", async () => {
    vi.stubGlobal("PublicKeyCredential", function () {});
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({ challenge: "AAAA", allow_credentials: [] }))));
    vi.stubGlobal("navigator", {
      credentials: { get: () => Promise.resolve({ id: "c", type: "public-key", rawId: new ArrayBuffer(0), response: {} }) },
    });
    const onRefresh = vi.fn(() => {
      Object.defineProperty(document, "hidden", { value: true, configurable: true });
      fireEvent(document, new Event("visibilitychange"));
    });
    render(
      <CredentialsTab probes={PROBES} summary={{}} loading={false} onRefresh={onRefresh} mobile />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show credentials" }));
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText("Credentials locked")).toBeInTheDocument());
    expect(screen.queryByText("Anthropic")).not.toBeInTheDocument();
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
  });

  it("reports a failed WebAuthn assertion on mobile", async () => {
    vi.stubGlobal("PublicKeyCredential", function () {});
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({ challenge: "AAAA", allow_credentials: [] }))));
    vi.stubGlobal("navigator", {
      credentials: { get: () => Promise.reject(new Error("user cancelled")) },
    });
    render(
      <CredentialsTab probes={PROBES} summary={{}} loading={false} onRefresh={() => {}} mobile />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show credentials" }));
    await waitFor(() => expect(screen.getByText("user cancelled")).toBeInTheDocument());
    expect(screen.getByText("Credentials locked")).toBeInTheDocument();
  });

  it("serialises a full WebAuthn assertion response on unlock", async () => {
    vi.stubGlobal("PublicKeyCredential", function () {});
    const completeBody = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (String(url).includes("assert/complete")) {
          completeBody(init?.body);
          return Promise.resolve(jsonResponse({ ok: true }));
        }
        return Promise.resolve(jsonResponse({ challenge: "AAAA", allow_credentials: [{ id: "BBBB", type: "public-key" }] }));
      }),
    );
    // Credential carries a populated assertion response → exercises the
    // Array.from(...) serialisation branches in credentialToPayload.
    vi.stubGlobal("navigator", {
      credentials: {
        get: () =>
          Promise.resolve({
            id: "cred-1",
            type: "public-key",
            rawId: new Uint8Array([1, 2, 3]).buffer,
            response: {
              authenticatorData: new Uint8Array([4, 5]).buffer,
              clientDataJSON: new Uint8Array([6, 7]).buffer,
              signature: new Uint8Array([8, 9]).buffer,
              userHandle: new Uint8Array([10]).buffer,
            },
          }),
      },
    });
    render(<CredentialsTab probes={PROBES} summary={{}} loading={false} onRefresh={() => {}} mobile />);
    fireEvent.click(screen.getByRole("button", { name: "Show credentials" }));
    await waitFor(() => expect(completeBody).toHaveBeenCalled());
    const sent = JSON.parse(completeBody.mock.calls[0][0]);
    expect(sent.credential.id).toBe("cred-1");
    expect(sent.credential.rawId).toEqual([1, 2, 3]);
    expect(sent.credential.response.signature).toEqual([8, 9]);
  });

  it("shows a confirm sheet before mutating a key on mobile", async () => {
    vi.stubGlobal("PublicKeyCredential", function () {});
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({ challenge: "AAAA", allow_credentials: [] }))));
    vi.stubGlobal("navigator", {
      credentials: { get: () => Promise.resolve({ id: "c", type: "public-key", rawId: new ArrayBuffer(0), response: {} }) },
    });
    const onSetKey = vi.fn();
    render(
      <CredentialsTab probes={PROBES} summary={{}} loading={false} onRefresh={() => {}} onSetKey={onSetKey} mobile />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show credentials" }));
    await waitFor(() => expect(screen.getByText("Replace API key")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Replace API key"));
    // Confirm sheet appears; onSetKey only fires after Confirm.
    expect(screen.getByText("Confirm sensitive operation")).toBeInTheDocument();
    expect(onSetKey).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Confirm"));
    expect(onSetKey).toHaveBeenCalledWith(expect.objectContaining({ id: "anthropic" }));
  });

  it("dismisses the mobile confirm sheet via Cancel without mutating", async () => {
    vi.stubGlobal("PublicKeyCredential", function () {});
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({ challenge: "AAAA", allow_credentials: [] }))));
    vi.stubGlobal("navigator", {
      credentials: { get: () => Promise.resolve({ id: "c", type: "public-key", rawId: new ArrayBuffer(0), response: {} }) },
    });
    const onSetKey = vi.fn();
    render(
      <CredentialsTab probes={PROBES} summary={{}} loading={false} onRefresh={() => {}} onSetKey={onSetKey} mobile />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show credentials" }));
    await waitFor(() => expect(screen.getByText("Replace API key")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Replace API key"));
    fireEvent.click(screen.getByText("Cancel"));
    expect(screen.queryByText("Confirm sensitive operation")).not.toBeInTheDocument();
    expect(onSetKey).not.toHaveBeenCalled();
  });

  it("falls back to key_provider in the confirm sheet copy", async () => {
    vi.stubGlobal("PublicKeyCredential", function () {});
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({ challenge: "AAAA", allow_credentials: [] }))));
    vi.stubGlobal("navigator", {
      credentials: { get: () => Promise.resolve({ id: "c", type: "public-key", rawId: new ArrayBuffer(0), response: {} }) },
    });
    render(
      <CredentialsTab
        probes={[{ id: "p", status: "missing_key", usable: false, key_provider: "perplexity" }]}
        summary={{}}
        loading={false}
        onRefresh={() => {}}
        onSetKey={() => {}}
        mobile
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show credentials" }));
    await waitFor(() => expect(screen.getByText("Set API key")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Set API key"));
    expect(screen.getByText(/perplexity/)).toBeInTheDocument();
  });
});
