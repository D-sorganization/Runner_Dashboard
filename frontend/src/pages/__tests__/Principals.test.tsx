// @vitest-environment jsdom
/**
 * Behaviour tests for pages/Principals.tsx — extracted from the legacy
 * App.tsx monolith (decomposition #836).
 *
 * Covers:
 * 1. Smoke render.
 * 2. Renders the principals table from the API.
 * 3. Renders the service tokens table (and empty state).
 * 4. Edit Quota opens the modal and PATCHes on save.
 * 5. Revoke confirms then DELETEs the token.
 * 6. Error banner when a load fails (orthogonality — no crash).
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
import { PrincipalsTab } from "../Principals";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const PRINCIPALS = {
  principals: [
    {
      id: "maxwell-daemon",
      type: "bot",
      roles: ["dispatcher"],
      quotas: { max_runners: 4, agent_spend_usd_day: 10, local_app_slots: 2 },
    },
    {
      id: "dieterolson",
      type: "user",
      roles: ["admin"],
      quotas: { max_runners: 8, agent_spend_usd_day: 50, local_app_slots: 4 },
    },
  ],
};

const TOKENS = {
  tokens: [
    {
      hash: "0123456789abcdef0123456789abcdef", // pragma: allowlist secret — dummy test fixture
      principal_id: "maxwell-daemon",
      name: "ci-token",
      created_at: "2026-05-01T00:00:00Z",
    },
  ],
};

const EMPTY_TOKENS = { tokens: [] };

function mockFetchByUrl(opts: {
  principals?: object;
  tokens?: object;
  failPrincipals?: boolean;
}) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      // Mutations succeed by default.
      if (init && init.method && init.method !== "GET") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ token: "newly-minted-token" }),
        } as Response);
      }
      if (url.includes("/api/admin/tokens")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(opts.tokens ?? TOKENS),
        } as Response);
      }
      // principals
      return Promise.resolve({
        ok: !opts.failPrincipals,
        status: opts.failPrincipals ? 500 : 200,
        json: () => Promise.resolve(opts.principals ?? PRINCIPALS),
      } as Response);
    }),
  );
}

describe("PrincipalsTab", () => {
  it("renders without throwing (smoke)", () => {
    mockFetchByUrl({});
    expect(() => render(<PrincipalsTab />)).not.toThrow();
  });

  it("renders the principals table from the API", async () => {
    mockFetchByUrl({ principals: PRINCIPALS, tokens: TOKENS });
    render(<PrincipalsTab />);
    // "dieterolson" is unique to the principal table (not a token principal_id).
    await waitFor(() =>
      expect(screen.getByText("dieterolson")).toBeInTheDocument(),
    );
    // "maxwell-daemon" appears in both the principal table and as a token's
    // principal_id, so assert it is present at least once.
    expect(screen.getAllByText("maxwell-daemon").length).toBeGreaterThan(0);
    expect(screen.getByText("Registered Principals")).toBeInTheDocument();
  });

  it("renders the empty tokens state", async () => {
    mockFetchByUrl({ principals: PRINCIPALS, tokens: EMPTY_TOKENS });
    render(<PrincipalsTab />);
    await waitFor(() =>
      expect(
        screen.getByText(/no active service tokens found/i),
      ).toBeInTheDocument(),
    );
  });

  it("opens the Edit Quota modal and PATCHes on save", async () => {
    mockFetchByUrl({ principals: PRINCIPALS, tokens: TOKENS });
    render(<PrincipalsTab />);
    await waitFor(() =>
      expect(screen.getByText("dieterolson")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getAllByRole("button", { name: /edit quota/i })[0]);
    expect(screen.getByText(/Edit Quota: maxwell-daemon/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /save quota settings/i }));
    await waitFor(() => {
      const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
      const patched = fetchMock.mock.calls.some(
        (c: unknown[]) =>
          typeof c[0] === "string" &&
          (c[0] as string).includes("/quota") &&
          (c[1] as RequestInit | undefined)?.method === "PATCH",
      );
      expect(patched).toBe(true);
    });
  });

  it("confirms then DELETEs on revoke", async () => {
    mockFetchByUrl({ principals: PRINCIPALS, tokens: TOKENS });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<PrincipalsTab />);
    await waitFor(() =>
      expect(screen.getByText("ci-token")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /revoke/i }));
    expect(confirmSpy).toHaveBeenCalled();
    await waitFor(() => {
      const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
      const deleted = fetchMock.mock.calls.some(
        (c: unknown[]) =>
          typeof c[0] === "string" &&
          (c[0] as string).includes("/api/admin/tokens/") &&
          (c[1] as RequestInit | undefined)?.method === "DELETE",
      );
      expect(deleted).toBe(true);
    });
  });

  it("shows an error banner when principals fail to load", async () => {
    mockFetchByUrl({ failPrincipals: true });
    render(<PrincipalsTab />);
    await waitFor(() =>
      expect(screen.getByText(/failed to load principals/i)).toBeInTheDocument(),
    );
  });
});
