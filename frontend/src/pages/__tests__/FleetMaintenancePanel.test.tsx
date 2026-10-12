// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FleetMaintenancePanel } from "../FleetMaintenancePanel";
import { maintenanceStatus } from "../maintenanceStatus";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function ok(): Response {
  return new Response(JSON.stringify({ started: true }), { status: 200 });
}

function lastBody(): unknown {
  const init = fetchMock.mock.calls[0][1] as RequestInit;
  return JSON.parse(init.body as string);
}

describe("maintenanceStatus", () => {
  it("maps the scheduler state", () => {
    expect(maintenanceStatus(undefined).label).toBe("Normal operation");
    expect(maintenanceStatus({ active: true }).label).toBe("Draining...");
    expect(maintenanceStatus({ active: true, drained: true }).label).toContain("Drained");
    expect(maintenanceStatus({ active: true, rebooting: true }).label).toBe("Rebooting host");
  });
});

describe("FleetMaintenancePanel", () => {
  it("sends nothing until the drain is confirmed", async () => {
    fetchMock.mockResolvedValue(ok());
    const onChanged = vi.fn();
    render(<FleetMaintenancePanel onChanged={onChanged} />);
    fireEvent.click(screen.getByText("Drain"));
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Confirm drain"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/fleet/maintenance");
    expect(lastBody()).toEqual({ action: "drain", reboot: false });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("cancel aborts without a request", () => {
    render(<FleetMaintenancePanel />);
    fireEvent.click(screen.getByText("Drain"));
    fireEvent.click(screen.getByText("Cancel"));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText("Drain")).toBeInTheDocument();
  });

  it("uses a distinct, explicit confirm when rebooting", async () => {
    fetchMock.mockResolvedValue(ok());
    render(<FleetMaintenancePanel />);
    fireEvent.click(screen.getByLabelText(/Reboot host after drain/));
    fireEvent.click(screen.getByText("Drain"));
    expect(screen.queryByText("Confirm drain")).toBeNull();
    expect(screen.getByRole("alertdialog")).toHaveTextContent("REBOOT");
    fireEvent.click(screen.getByText("Confirm drain and REBOOT"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(lastBody()).toEqual({ action: "drain", reboot: true });
  });

  it("resume confirms then posts resume with no reboot field", async () => {
    fetchMock.mockResolvedValue(ok());
    render(<FleetMaintenancePanel />);
    fireEvent.click(screen.getByText("Resume"));
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Confirm resume"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(lastBody()).toEqual({ action: "resume" });
  });

  it("surfaces a server error detail", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "not installed" }), { status: 503 }));
    render(<FleetMaintenancePanel />);
    fireEvent.click(screen.getByText("Drain"));
    fireEvent.click(screen.getByText("Confirm drain"));
    expect(await screen.findByRole("alert")).toHaveTextContent("not installed");
  });

  it("shows the live maintenance state", () => {
    render(<FleetMaintenancePanel maintenance={{ active: true, drained: true }} />);
    expect(screen.getByText(/Drained/)).toBeInTheDocument();
  });
});
