// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RemediationPage } from "../RemediationPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  localStorage.clear();
});

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

const FAILED_RUN = {
  id: 4242,
  conclusion: "failure",
  name: "CI Standard",
  repository: { name: "alpha" },
  head_branch: "fix/thing",
  created_at: "2026-06-01T12:34:56Z",
  html_url: "https://github.com/D-sorganization/alpha/actions/runs/4242",
};

function endpointPayload(url: string): unknown {
  switch (url) {
    case "/api/agent-remediation/config":
      return {
        policy: {
          default_provider: "jules_api",
          max_same_failure_attempts: 4,
          workflow_type_rules: {},
        },
        providers: {
          jules_api: { label: "Jules API", notes: "cloud" },
          codex_cli: { label: "Codex CLI", notes: "local" },
        },
        availability: {
          jules_api: { available: true, status: "ready" },
        },
      };
    case "/api/agent-remediation/workflows":
      return { workflows: [] };
    case "/api/agent-remediation/history":
      return { history: [] };
    case "/api/runs/enriched?per_page=50":
      return { runs: [FAILED_RUN] };
    case "/api/runs?per_page=30":
      return { runs: [] };
    default:
      throw new Error("unexpected GET " + url);
  }
}

function mockRemediationFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url === "/api/agent-remediation/config" && init?.method === "PUT") {
      expect(JSON.parse(String(init.body))).toMatchObject({
        policy: { max_same_failure_attempts: 7 },
      });
      return Promise.resolve(
        jsonResponse({
          policy: { default_provider: "jules_api", max_same_failure_attempts: 7 },
          providers: {},
          availability: {},
        }),
      );
    }
    if (url === "/api/agent-remediation/plan") {
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init.body))).toMatchObject({
        repository: "alpha",
        workflow_name: "CI Standard",
        branch: "fix/thing",
        run_id: 4242,
        provider_override: "jules_api",
      });
      return Promise.resolve(
        jsonResponse({
          decision: {
            accepted: true,
            provider_id: "jules_api",
            prompt_preview: "fix alpha",
          },
        }),
      );
    }
    if (url === "/api/agent-remediation/dispatch") {
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init.body))).toMatchObject({
        repository: "alpha",
        run_id: 4242,
        provider: "jules_api",
      });
      return Promise.resolve(
        jsonResponse({ provider: "jules_api", workflow: "remediation.yml" }),
      );
    }
    return Promise.resolve(jsonResponse(endpointPayload(url)));
  });
}

describe("RemediationPage", () => {
  it("loads remediation controls and failed runs for the native route", async () => {
    const fetchMock = mockRemediationFetch();

    render(<RemediationPage />);

    expect(await screen.findByText("Manual Dispatch")).toBeInTheDocument();
    expect(
      screen.getByText(/alpha · CI Standard · fix\/thing #4242/),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agent-remediation/config",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it("saves policy through the native route", async () => {
    const fetchMock = mockRemediationFetch();

    render(<RemediationPage />);

    await screen.findByText("Manual Dispatch");
    fireEvent.click(screen.getByText("Loop guard"));
    const input = screen.getByDisplayValue("4");
    fireEvent.change(input, { target: { value: "7" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/agent-remediation/config",
        expect.objectContaining({ method: "PUT" }),
      ),
    );
  });

  it("previews and dispatches remediation through native handlers", async () => {
    const fetchMock = mockRemediationFetch();

    render(<RemediationPage />);

    await screen.findByText("Manual Dispatch");
    await screen.findByText(/alpha · CI Standard · fix\/thing #4242/);
    fireEvent.click(screen.getByText("Preview"));
    expect(await screen.findByText("fix alpha")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Dispatch"));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/agent-remediation/dispatch",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    await waitFor(() =>
      expect(
        screen.getAllByText("Dispatched jules_api through remediation.yml.")
          .length,
      ).toBeGreaterThan(0),
    );
  });
});
