// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RemediationPage } from "../RemediationPage";
import type { RemediationTabProps } from "../RemediationTab";

vi.mock("../RemediationTab", () => ({
  RemediationTab: (props: RemediationTabProps) => (
    <section>
      <span data-testid="provider">{props.provider}</span>
      <span data-testid="run-count">{props.runs?.length ?? 0}</span>
      <button onClick={props.onRefresh}>Refresh</button>
      <button
        onClick={() =>
          props.onSaveConfig({
            default_provider: "jules_api",
            max_same_failure_attempts: 4,
          })
        }
      >
        Save config
      </button>
      <button onClick={() => props.onPreview(props.runs?.[0])}>Preview</button>
      <button onClick={() => props.onDispatch(props.runs?.[0])}>Dispatch</button>
    </section>
  ),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function remediationRun() {
  return {
    id: 42,
    conclusion: "failure",
    name: "Frontend Tests",
    head_branch: "main",
    repository: { name: "Runner_Dashboard" },
  };
}

function mockRemediationFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url === "/api/agent-remediation/config") {
      if (init?.method === "PUT") {
        expect(JSON.parse(String(init.body))).toMatchObject({
          policy: {
            default_provider: "jules_api",
            max_same_failure_attempts: 4,
          },
        });
        return Promise.resolve(
          jsonResponse({ policy: { default_provider: "jules_api" } }),
        );
      }
      return Promise.resolve(
        jsonResponse({
          policy: {
            default_provider: "codex_cli",
            max_same_failure_attempts: 3,
          },
        }),
      );
    }
    if (url === "/api/agent-remediation/workflows") {
      return Promise.resolve(jsonResponse({ workflows: ["ci.yml"] }));
    }
    if (url === "/api/agent-remediation/history") {
      return Promise.resolve(jsonResponse({ history: [{ id: "hist-1" }] }));
    }
    if (url === "/api/runs/enriched?per_page=50") {
      return Promise.resolve(jsonResponse({ workflow_runs: [remediationRun()] }));
    }
    if (url === "/api/agent-remediation/plan") {
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init.body))).toMatchObject({
        repository: "Runner_Dashboard",
        workflow_name: "Frontend Tests",
        branch: "main",
        run_id: 42,
        provider_override: "codex_cli",
        protected_branch: true,
      });
      return Promise.resolve(
        jsonResponse({ decision: { accepted: true, provider_id: "codex_cli" } }),
      );
    }
    if (url === "/api/agent-remediation/dispatch") {
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init.body))).toMatchObject({
        repository: "Runner_Dashboard",
        workflow_name: "Frontend Tests",
        branch: "main",
        run_id: 42,
        provider: "codex_cli",
      });
      return Promise.resolve(
        jsonResponse({ provider: "codex_cli", workflow: "ci.yml" }),
      );
    }
    return Promise.reject(new Error("unexpected fetch " + url));
  });
}

describe("RemediationPage", () => {
  it("loads remediation controls and failed runs for the native route", async () => {
    const fetchMock = mockRemediationFetch();

    render(<RemediationPage />);

    expect(await screen.findByTestId("provider")).toHaveTextContent("codex_cli");
    expect(screen.getByTestId("run-count")).toHaveTextContent("1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agent-remediation/config",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agent-remediation/workflows",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/runs/enriched?per_page=50",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it("saves remediation policy through the native page owner", async () => {
    const fetchMock = mockRemediationFetch();

    render(<RemediationPage />);
    await screen.findByTestId("provider");
    fireEvent.click(screen.getByRole("button", { name: "Save config" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/agent-remediation/config",
        expect.objectContaining({ method: "PUT" }),
      ),
    );
    expect(await screen.findByTestId("provider")).toHaveTextContent("jules_api");
  });

  it("previews and dispatches remediation through canonical endpoints", async () => {
    const fetchMock = mockRemediationFetch();

    render(<RemediationPage />);
    await screen.findByTestId("provider");

    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    fireEvent.click(screen.getByRole("button", { name: "Dispatch" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/agent-remediation/plan",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/agent-remediation/dispatch",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });
});
