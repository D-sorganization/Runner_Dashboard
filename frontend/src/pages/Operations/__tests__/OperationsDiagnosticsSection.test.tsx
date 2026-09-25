// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { OperationsDiagnosticsSection } from "../OperationsDiagnosticsSection";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const DIAGNOSTICS_SUMMARY = {
  dashboard_pid: 12345,
  dashboard_memory_mb: 156,
  dashboard_port: 8000,
  git_commit: "abc1234",
  is_drifted: false,
  source_commit: "abc1234",
  remote_commit: "abc1234",
  wsl_available: true,
  wsl_status: "running",
};

const GIT_DRIFT = {
  is_drifted: false,
  source_commit: "abc1234",
  remote_commit: "abc1234",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockDiagnosticsFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((url, init) => {
    const urlStr = String(url);
    if (urlStr === "/api/diagnostics/summary") {
      return Promise.resolve(jsonResponse(DIAGNOSTICS_SUMMARY));
    }
    if (urlStr === "/api/deployment/git-drift") {
      return Promise.resolve(jsonResponse(GIT_DRIFT));
    }
    if (urlStr === "/api/diagnostics/restart-service" && init?.method === "POST") {
      return Promise.resolve(jsonResponse({ success: true, output: "Service restarted" }));
    }
    if (urlStr === "/api/launchers/generate" && init?.method === "POST") {
      return Promise.resolve(
        jsonResponse({ message: "Generated 3 launcher scripts", launchers: ["run.bat"] }),
      );
    }
    return Promise.reject(new Error("unexpected url " + urlStr));
  });
}

describe("OperationsDiagnosticsSection", () => {
  it("renders section with id=diagnostics and displays system overview", async () => {
    mockDiagnosticsFetch();

    render(<OperationsDiagnosticsSection />);

    expect(screen.getByRole("heading", { name: /Diagnostics/i })).toBeInTheDocument();
    expect(await screen.findByText("156 MB")).toBeInTheDocument();
    expect(screen.getByText("PID 12345")).toBeInTheDocument();
    expect(screen.getByText("Port 8000")).toBeInTheDocument();
    expect(screen.getByText("WSL: running")).toBeInTheDocument();
  });

  it("handles service restart with confirmation step", async () => {
    const fetchMock = mockDiagnosticsFetch();

    render(<OperationsDiagnosticsSection />);

    const restartBtn = await screen.findByRole("button", { name: /Restart Service/i });
    fireEvent.click(restartBtn);

    const confirmBtn = await screen.findByRole("button", { name: /Confirm Restart/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/diagnostics/restart-service",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(await screen.findByText(/Service restarted/i)).toBeInTheDocument();
  });

  it("handles Windows launcher generation", async () => {
    const fetchMock = mockDiagnosticsFetch();

    render(<OperationsDiagnosticsSection />);

    const generateBtn = await screen.findByRole("button", { name: /Generate Launchers/i });
    fireEvent.click(generateBtn);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/launchers/generate",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(await screen.findByText(/Generated 3 launcher scripts/i)).toBeInTheDocument();
  });

  it("shows an error banner with a Retry button when fetch fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Diagnostics error"));

    render(<OperationsDiagnosticsSection />);

    expect(await screen.findByText(/Failed to load diagnostics: Diagnostics error/i)).toBeInTheDocument();
    const retryBtn = screen.getByRole("button", { name: /Retry/i });
    expect(retryBtn).toBeInTheDocument();
  });
});
