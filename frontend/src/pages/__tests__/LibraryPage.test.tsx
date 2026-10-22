// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LibraryPage } from "../Library/LibraryPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("LibraryPage", () => {
  it("renders ingest stats and pending submissions", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url.includes("/stats")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              works_active: 2,
              files_present: 3,
              passages: 40,
              rejections: 1,
              last_ingest: { finished_at: "2026-10-22T12:00:00Z", files_indexed: 2 },
            }),
          });
        }
        if (url.includes("/submissions")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              submissions: [
                {
                  id: 7,
                  status: "pending",
                  created_at: "2026-10-22T11:00:00Z",
                  payload: { title: "Biomechanics Primer", requesting_ref: "UpstreamDrift#9" },
                },
              ],
            }),
          });
        }
        return Promise.resolve({ ok: false, status: 404 });
      }),
    );
    render(<LibraryPage />);
    await waitFor(() => expect(screen.getByText(/Active works: 2/)).toBeInTheDocument());
    expect(screen.getByText("Biomechanics Primer")).toBeInTheDocument();
    expect(screen.getByText(/Review queue \(1 pending\)/)).toBeInTheDocument();
  });
});
