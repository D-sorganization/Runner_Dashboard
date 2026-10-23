// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LibraryPage } from "../Library/LibraryPage";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const stats = { works_active: 2, files_present: 3, passages: 40, rejections: 1, last_ingest: { finished_at: "2026-10-22T12:00:00Z", files_indexed: 2 } };
const srcFixture = { id: "docs", label: "Engineering Docs", root_path: "/home/user/docs", shared: false, include_globs: [], exclude_globs: [], created_at: "2026-10-22T10:00:00Z", last_ingest_at: "2026-10-22T12:00:00Z", status: "active", file_count: 42 };

function mockFetch(handler: (url: string, opts?: RequestInit) => unknown) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, opts?: RequestInit) => {
    if (url.includes("/stats")) return { ok: true, json: async () => stats };
    if (url.includes("/submissions")) return { ok: true, json: async () => ({ submissions: [{ id: 7, status: "pending", created_at: "2026-10-22T11:00:00Z", payload: { title: "Biomechanics Primer", requesting_ref: "UpstreamDrift#9" } }] }) };
    const res = await handler(url, opts);
    return res ? { ok: true, json: async () => res } : { ok: false, status: 404 };
  }));
}

describe("LibraryPage", () => {
  it("renders sources, toggles sharing, and removes source with disk warning", async () => {
    const user = userEvent.setup();
    let patched = false;
    let deleted = false;
    mockFetch((url, opts) => {
      if (url.endsWith("/sources") && !opts?.method) return { sources: [srcFixture], is_hub: false, current_node: "node1", nodes: [] };
      if (url.includes("/sources/docs") && opts?.method === "PATCH") { patched = true; return { ...srcFixture, shared: true }; }
      if (url.includes("/sources/docs") && opts?.method === "DELETE") { deleted = true; return { ok: true }; }
    });
    render(<LibraryPage />);
    await waitFor(() => expect(screen.getByText("Engineering Docs")).toBeInTheDocument());
    expect(screen.getByText("Biomechanics Primer")).toBeInTheDocument();
    expect(screen.getByText(/42 files/)).toBeInTheDocument();
    const share = screen.getByLabelText(/share/i);
    expect(share).not.toBeChecked();
    await user.click(share);
    expect(patched).toBe(true);

    await user.click(screen.getByRole("button", { name: /remove/i }));
    expect(screen.getByText(/files will not be touched on disk/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /confirm remove/i }));
    expect(deleted).toBe(true);
  });

  it("browses directory picker and adds a source", async () => {
    const user = userEvent.setup();
    let created: Record<string, unknown> | null = null;
    mockFetch((url, opts) => {
      if (url.endsWith("/sources") && opts?.method === "POST") { created = JSON.parse(opts.body as string) as Record<string, unknown>; return { ...srcFixture, ...created }; }
      if (url.endsWith("/sources")) return { sources: [], is_hub: false, current_node: "n1", nodes: [] };
      if (url.includes("/browse")) {
        const inBooks = url.includes("books");
        return {
          current_path: inBooks ? "/home/user/books" : "/home/user",
          parent_path: inBooks ? "/home/user" : null,
          roots: ["/home/user"],
          entries: inBooks ? [{ name: "science", path: "/home/user/books/science", child_dirs: 1, file_count: 5 }] : [{ name: "books", path: "/home/user/books", child_dirs: 1, file_count: 10 }],
        };
      }
    });
    render(<LibraryPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: /add folder/i })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /add folder/i }));
    await waitFor(() => expect(screen.getByText("books")).toBeInTheDocument());
    await user.click(screen.getByText("books"));
    await waitFor(() => expect(screen.getByText("science")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /select this folder/i }));
    expect(screen.getByLabelText(/source label/i)).toHaveValue("books");
    await user.click(screen.getByRole("button", { name: /add source/i }));
    expect(created).toEqual(expect.objectContaining({ label: "books", root_path: "/home/user/books" }));
  });

  it("displays hub view with remote node links and read-only message", async () => {
    mockFetch((url) => url.endsWith("/sources") ? { sources: [srcFixture], is_hub: true, current_node: "hub", nodes: [{ name: "thinkpad", url: "http://100.64.0.3:8321" }] } : null);
    render(<LibraryPage />);
    await waitFor(() => expect(screen.getByText("Engineering Docs")).toBeInTheDocument());
    expect(screen.getByText(/hub view: sources are read-only/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /thinkpad/i })).toHaveAttribute("href", "http://100.64.0.3:8321/library");
    expect(screen.queryByRole("button", { name: /add folder/i })).not.toBeInTheDocument();
  });
});
