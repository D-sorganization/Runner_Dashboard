// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { OperationsWslDiskCard } from "../OperationsWslDiskCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const MOCK_DATA = {
  distributions: [
    {
      name: "Ubuntu",
      path: "C:\\WSL\\Ubuntu\\ext4.vhdx",
      vhdx_bytes: 196497276928, // 183.0 GiB
      sparse: true,
      is_current: true,
      fs_used_bytes: 48318382080, // 45.0 GiB
      fs_total_bytes: 268435456000,
      slack_bytes: 148178894848, // 138.0 GiB
      findings: ["fstrim_timer_inactive"],
    },
    {
      name: "docker-desktop",
      path: "C:\\WSL\\docker\\ext4.vhdx",
      vhdx_bytes: 21474836480, // 20.0 GiB
      sparse: false,
      is_current: false,
      fs_used_bytes: null,
      fs_total_bytes: null,
      slack_bytes: null,
      findings: ["not_sparse", "custom_finding_code"],
    },
    {
      name: "Debian",
      path: "C:\\WSL\\Debian\\ext4.vhdx",
      vhdx_bytes: 10737418240, // 10.0 GiB
      sparse: null,
      is_current: false,
      fs_used_bytes: null,
      fs_total_bytes: null,
      slack_bytes: null,
      findings: [],
    },
  ],
  fstrim: {
    timer_active: true,
    last_trigger: "2026-09-27 10:00:00 UTC",
    last_result: "success",
  },
  current_distro: "Ubuntu",
  generated_at: "2026-09-27T10:00:00Z",
};

const EMPTY_DATA = {
  distributions: [],
  fstrim: {
    timer_active: null,
    last_trigger: null,
    last_result: null,
  },
  current_distro: null,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockWslDiskFetch(data: unknown = MOCK_DATA) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((url) => {
    if (String(url) === "/api/diagnostics/wsl-disk") {
      return Promise.resolve(jsonResponse(data));
    }
    return Promise.reject(new Error("unexpected url " + url));
  });
}

describe("OperationsWslDiskCard (#1332)", () => {
  it("renders loading state initially on mount", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));
    render(<OperationsWslDiskCard />);

    expect(
      screen.getByRole("heading", { name: "WSL disk" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Checking WSL disks…/i)).toBeInTheDocument();
  });

  it("renders rows and '(this node)' for the current distro", async () => {
    mockWslDiskFetch();
    render(<OperationsWslDiskCard />);

    expect(
      screen.getByRole("heading", { name: "WSL disk" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Ubuntu (this node)")).toBeInTheDocument();
    expect(screen.getByText("docker-desktop")).toBeInTheDocument();
    expect(screen.getByText("Debian")).toBeInTheDocument();

    // VHDX sizes
    expect(screen.getByText("183.0 GiB")).toBeInTheDocument();
    expect(screen.getByText("20.0 GiB")).toBeInTheDocument();
    expect(screen.getByText("10.0 GiB")).toBeInTheDocument();

    // Used inside WSL and slack for current distro
    expect(screen.getByText("45.0 GiB")).toBeInTheDocument();
    expect(screen.getByText("138.0 GiB")).toBeInTheDocument();

    // Sparse values
    expect(screen.getByText("Yes")).toBeInTheDocument();
    expect(screen.getByText("No")).toBeInTheDocument();
    expect(screen.getByText("Unknown")).toBeInTheDocument();

    // fstrim status line
    expect(
      screen.getByText(
        /fstrim timer: active · last run 2026-09-27 10:00:00 UTC \(success\)/i,
      ),
    ).toBeInTheDocument();
  });

  it("shows '—' for used and slack on non-current distros", async () => {
    mockWslDiskFetch();
    const { container } = render(<OperationsWslDiskCard />);

    await screen.findByText("Ubuntu (this node)");

    const rows = container.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(3);

    // Row 0 is Ubuntu (current) -> has numbers
    expect(rows[0]).toHaveTextContent("183.0 GiB");
    expect(rows[0]).toHaveTextContent("45.0 GiB");
    expect(rows[0]).toHaveTextContent("138.0 GiB");

    // Row 1 is docker-desktop (non-current) -> has "—" for used and slack
    expect(rows[1]).toHaveTextContent("docker-desktop");
    expect(rows[1]).toHaveTextContent("20.0 GiB");
    const cellsRow1 = rows[1].querySelectorAll("td");
    expect(cellsRow1[2]).toHaveTextContent("—");
    expect(cellsRow1[3]).toHaveTextContent("—");

    // Row 2 is Debian (non-current) -> has "—" for used and slack
    expect(rows[2]).toHaveTextContent("Debian");
    const cellsRow2 = rows[2].querySelectorAll("td");
    expect(cellsRow2[2]).toHaveTextContent("—");
    expect(cellsRow2[3]).toHaveTextContent("—");
  });

  it("shows findings sentences and runbook note without buttons", async () => {
    mockWslDiskFetch();
    render(<OperationsWslDiskCard />);

    // not_sparse finding
    expect(
      await screen.findByText(
        "docker-desktop: VHDX is not sparse, so space freed inside WSL is not returned to Windows.",
      ),
    ).toBeInTheDocument();

    // fstrim_timer_inactive finding
    expect(
      screen.getByText("fstrim.timer is not active on this node."),
    ).toBeInTheDocument();

    // raw unknown finding code
    expect(screen.getByText("custom_finding_code")).toBeInTheDocument();

    // runbook note as plain text (not a link)
    const runbookText = screen.getByText(
      /docs\/runbooks\/wsl-vhdx-compaction\.md/i,
    );
    expect(runbookText).toBeInTheDocument();
    expect(runbookText.closest("a")).toBeNull();

    // In data state, queryAllByRole('button') has length 0
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("shows empty state when distributions is empty", async () => {
    mockWslDiskFetch(EMPTY_DATA);
    render(<OperationsWslDiskCard />);

    expect(
      screen.getByRole("heading", { name: "WSL disk" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        "No WSL distributions found (not a Windows host?)",
      ),
    ).toBeInTheDocument();

    // In empty state, no buttons
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("shows error state and refetches on Retry, with Retry as the only button", async () => {
    let callCount = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((url) => {
      if (String(url) === "/api/diagnostics/wsl-disk") {
        callCount++;
        if (callCount === 1) {
          return Promise.reject(new Error("Connection refused"));
        }
        return Promise.resolve(jsonResponse(MOCK_DATA));
      }
      return Promise.reject(new Error("unexpected url " + url));
    });

    render(<OperationsWslDiskCard />);

    expect(
      await screen.findByText(
        /Failed to load WSL disk status: Connection refused/i,
      ),
    ).toBeInTheDocument();

    // In error state, getAllByRole("button") has length 1 (only Retry)
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveTextContent("Retry");

    // Click Retry to refetch
    fireEvent.click(buttons[0]);

    // Data should now load
    expect(await screen.findByText("Ubuntu (this node)")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // In data state, queryAllByRole("button") has length 0
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("has accessible section and table structure", async () => {
    mockWslDiskFetch();
    const { container } = render(<OperationsWslDiskCard />);

    await screen.findByText("Ubuntu (this node)");

    const section = container.querySelector("section");
    expect(section).not.toBeNull();
    const headingId = section?.getAttribute("aria-labelledby");
    expect(headingId).toBeTruthy();

    const heading = container.querySelector(`#${headingId}`);
    expect(heading).toHaveTextContent("WSL disk");

    const table = screen.getByRole("table");
    expect(table).toBeInTheDocument();
    expect(table).toHaveAttribute("aria-label", "WSL distributions");
  });
});
