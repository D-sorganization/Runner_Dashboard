// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { SecurityPanel } from "../SecurityPanel";
import { useSecurity } from "../useSecurity";
import { formatAlert, formatSeverityCounts, noteRequired, severityTone } from "../securityApi";

const FINDING_ID = "0123456789abcdef0123456789abcdef";
const XSS_TITLE = '<script>window.__pwned = 1</script><img src=x onerror="window.__pwned = 2">';
const XSS_PATCH = '--- a/x\n+++ b/x\n+<img src=x onerror="window.__pwned = 3"><script>window.__pwned = 4</script>';

const CONTRACTS = {
  contracts: [
    {
      repo: "Tools",
      present: true,
      valid: false,
      problems: ["threat_model is missing"],
      dockerfile: ".oss-scanner/Dockerfile",
      threat_model: null,
      threat_model_present: false,
      disabled: false,
    },
    { repo: "Runner_Dashboard", present: true, valid: true, problems: [] },
    { repo: "Gasification_Model", present: false, valid: false, problems: [] },
  ],
  summary: { repos: 3, enrolled: 2, valid: 1, invalid: 1, errors: 0 },
};

const POSTURE = {
  repos: [
    {
      repo: "Runner_Dashboard",
      contract: CONTRACTS.contracts[1],
      operator: { enabled: true, full_scan_days: 7, priority: 1 },
      open_findings: { high: 2, low: 1 },
      github_alerts: {
        dependabot: { status: "ok", open: 3, by_severity: { critical: 1, medium: 2 }, capped: false },
        code_scanning: { status: "unavailable", reason: "HTTP 403" },
        secret_scanning: { status: "error", reason: "HTTP 502" },
      },
    },
  ],
  operator: { scan_hosts: [], real_scans_possible: false, errors: [] },
};

function finding(overrides: Record<string, unknown> = {}) {
  return {
    id: FINDING_ID,
    repo: "Runner_Dashboard",
    title: "SQL injection in report query",
    cwe: "CWE-89",
    severity: "high",
    confidence: "medium",
    path: "backend/reports.py",
    symbol: "build_query",
    root_cause: "User input is concatenated into SQL.",
    repro: "curl 'http://localhost/api?x=1 OR 1=1'",
    patch: "--- a/backend/reports.py\n+++ b/backend/reports.py",
    state: "new",
    last_seen: "2026-10-22T00:00:00Z",
    occurrences: 2,
    ...overrides,
  };
}

type Route = { status: number; body: unknown };
type Handler = (url: string, init?: RequestInit) => Route;

function mockFetch(routes: Record<string, Route | Handler>) {
  const fn = vi.fn((url: string, init?: RequestInit) => {
    const path = url.split("?")[0];
    const hit = routes[`${init?.method ?? "GET"} ${path}`] ?? routes[path];
    const res = typeof hit === "function" ? hit(url, init) : hit;
    return Promise.resolve({
      ok: !!res && res.status < 300,
      status: res?.status ?? 500,
      headers: new Headers(),
      json: () => Promise.resolve(res?.body ?? {}),
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function fullRoutes(extra: Record<string, Route | Handler> = {}) {
  return {
    "/api/v1/security/contracts": { status: 200, body: CONTRACTS },
    "/api/v1/security/posture": { status: 200, body: POSTURE },
    "/api/v1/security/findings": { status: 200, body: { findings: [finding()], count: 1 } },
    [`/api/v1/security/findings/${FINDING_ID}`]: { status: 200, body: { finding: finding(), audit: [] } },
    ...extra,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete (window as unknown as { __pwned?: number }).__pwned;
});

describe("security api helpers", () => {
  it("formats counts, alerts and tones", () => {
    expect(formatSeverityCounts({ low: 1, critical: 2 })).toBe("critical 2 · low 1");
    expect(formatSeverityCounts({})).toBe("none");
    expect(formatAlert({ status: "ok", open: 100, by_severity: {}, capped: true })).toBe("100+");
    expect(formatAlert({ status: "unavailable", reason: "HTTP 404" })).toBe("unavailable");
    expect(severityTone("critical")).toBe("danger");
    expect(severityTone("info")).toBe("neutral");
    expect(noteRequired("false_positive")).toBe(true);
    expect(noteRequired("accepted_risk")).toBe(true);
    expect(noteRequired("confirmed")).toBe(false);
  });
});

describe("security hook", () => {
  it("treats 404 as disabled and 403 as forbidden per section", async () => {
    mockFetch({
      "/api/v1/security/contracts": { status: 404, body: {} },
      "/api/v1/security/posture": { status: 403, body: { detail: "missing scope" } },
      "/api/v1/security/findings": { status: 403, body: {} },
    });
    const { result } = renderHook(() => useSecurity());
    await waitFor(() => expect(result.current.contracts.kind).toBe("disabled"));
    await waitFor(() => expect(result.current.posture.kind).toBe("forbidden"));
    expect(result.current.findings.kind).toBe("forbidden");
  });

  it("rejects malformed responses", async () => {
    mockFetch({
      "/api/v1/security/contracts": { status: 200, body: { contracts: "nope" } },
      "/api/v1/security/posture": { status: 200, body: {} },
      "/api/v1/security/findings": { status: 200, body: { findings: [{ id: 1 }] } },
    });
    const { result } = renderHook(() => useSecurity());
    await waitFor(() => expect(result.current.contracts.kind).toBe("error"));
    await waitFor(() => expect(result.current.posture.kind).toBe("error"));
    await waitFor(() => expect(result.current.findings.kind).toBe("error"));
  });

  it("refuses a suppressing triage without a note before calling the API", async () => {
    const fetchMock = mockFetch(fullRoutes());
    const { result } = renderHook(() => useSecurity());
    await waitFor(() => expect(result.current.findings.kind).toBe("ready"));
    const ok = await result.current.submitTriage(FINDING_ID, "false_positive", "   ");
    expect(ok).toBe(false);
    await waitFor(() => expect(result.current.triage.error).toMatch(/note is required/i));
    expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "POST")).toBe(false);
  });
});

describe("security panel", () => {
  it("renders contracts, posture and findings", async () => {
    mockFetch(fullRoutes());
    render(<SecurityPanel />);
    const contracts = await screen.findByTestId("security-contracts-table");
    expect(within(contracts).getByText("threat_model is missing")).toBeInTheDocument();
    expect(within(contracts).getByText("invalid")).toBeInTheDocument();
    expect(within(contracts).getByText("not enrolled")).toBeInTheDocument();

    const posture = await screen.findByTestId("security-posture-table");
    expect(within(posture).getByText("high 2 · low 1")).toBeInTheDocument();
    expect(within(posture).getByText("unavailable")).toBeInTheDocument();
    expect(within(posture).getByText("error")).toBeInTheDocument();
    expect(screen.getByTestId("security-no-real-scans")).toBeInTheDocument();

    const list = await screen.findByTestId("security-findings-table");
    expect(within(list).getByText("SQL injection in report query")).toBeInTheDocument();
  });

  it("falls back to contracts only with a security.read notice on 403", async () => {
    mockFetch({
      "/api/v1/security/contracts": { status: 200, body: CONTRACTS },
      "/api/v1/security/posture": { status: 403, body: {} },
      "/api/v1/security/findings": { status: 403, body: {} },
    });
    render(<SecurityPanel />);
    expect(await screen.findByTestId("security-contracts-table")).toBeInTheDocument();
    expect(await screen.findByTestId("security-posture-forbidden")).toHaveTextContent(/requires security\.read/i);
    expect(await screen.findByTestId("security-findings-forbidden")).toHaveTextContent(/requires security\.read/i);
  });

  it("shows disabled sections on 404", async () => {
    mockFetch({
      "/api/v1/security/contracts": { status: 404, body: {} },
      "/api/v1/security/posture": { status: 404, body: {} },
      "/api/v1/security/findings": { status: 404, body: {} },
    });
    render(<SecurityPanel />);
    expect(await screen.findByTestId("security-contracts-disabled")).toBeInTheDocument();
    expect(await screen.findByTestId("security-posture-disabled")).toBeInTheDocument();
  });

  it("passes filters to the findings query", async () => {
    const fetchMock = mockFetch(fullRoutes());
    render(<SecurityPanel />);
    await screen.findByTestId("security-findings-table");
    fireEvent.change(screen.getByLabelText("Filter findings by severity"), { target: { value: "critical" } });
    fireEvent.change(screen.getByLabelText("Filter findings by state"), { target: { value: "confirmed" } });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url]) => String(url).includes("severity=critical") && String(url).includes("state=confirmed"),
        ),
      ).toBe(true),
    );
  });

  it("opens the detail drawer with root cause, patch and audit trail, and triages", async () => {
    let state = "new";
    const audit: unknown[] = [];
    const fetchMock = mockFetch(
      fullRoutes({
        [`/api/v1/security/findings/${FINDING_ID}`]: () => ({
          status: 200,
          body: { finding: finding({ state }), audit },
        }),
        [`POST /api/v1/security/findings/${FINDING_ID}/triage`]: (_url, init) => {
          const body = JSON.parse(String(init?.body)) as { state: string; note: string };
          audit.push({ from_state: state, to_state: body.state, actor: "admin", note: body.note, ts: "2026-10-23T00:00:00Z" });
          state = body.state;
          return { status: 200, body: { finding: finding({ state }) } };
        },
      }),
    );
    render(<SecurityPanel />);
    fireEvent.click(await screen.findByRole("button", { name: /open finding sql injection/i }));
    const drawer = await screen.findByTestId("security-finding-detail");
    expect(within(drawer).getByTestId("security-finding-root-cause")).toHaveTextContent(
      "User input is concatenated into SQL.",
    );
    expect(within(drawer).getByTestId("security-finding-patch").tagName).toBe("PRE");
    expect(within(drawer).getByText(/no triage history/i)).toBeInTheDocument();

    const submit = within(drawer).getByRole("button", { name: /apply triage/i });
    fireEvent.change(within(drawer).getByLabelText("Triage state"), { target: { value: "accepted_risk" } });
    expect(within(drawer).getByText(/required for this state/i)).toBeInTheDocument();
    expect(submit).toBeDisabled();

    fireEvent.change(within(drawer).getByLabelText("Triage note"), { target: { value: "internal-only tool" } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    const auditList = await screen.findByTestId("security-finding-audit");
    expect(auditList).toHaveTextContent("admin: new → accepted risk — internal-only tool");
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(post).toBeDefined();
    const headers = new Headers((post![1] as RequestInit).headers);
    expect(headers.get("X-Requested-With")).toBe("XMLHttpRequest");
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("surfaces a triage 403 as a scope message", async () => {
    mockFetch(
      fullRoutes({
        [`POST /api/v1/security/findings/${FINDING_ID}/triage`]: { status: 403, body: {} },
      }),
    );
    render(<SecurityPanel />);
    fireEvent.click(await screen.findByRole("button", { name: /open finding/i }));
    const drawer = await screen.findByTestId("security-finding-detail");
    fireEvent.change(within(drawer).getByLabelText("Triage state"), { target: { value: "confirmed" } });
    fireEvent.click(within(drawer).getByRole("button", { name: /apply triage/i }));
    expect(await screen.findByTestId("security-triage-error")).toHaveTextContent(/security\.triage/);
  });

  it("renders script and onerror payloads in titles and patches inertly as text", async () => {
    const hostile = finding({
      title: XSS_TITLE,
      root_cause: XSS_TITLE,
      repro: XSS_PATCH,
      patch: XSS_PATCH,
      path: "a/<b>.py",
    });
    mockFetch(
      fullRoutes({
        "/api/v1/security/findings": { status: 200, body: { findings: [hostile], count: 1 } },
        [`/api/v1/security/findings/${FINDING_ID}`]: {
          status: 200,
          body: {
            finding: hostile,
            audit: [{ from_state: "new", to_state: "confirmed", actor: "a", note: XSS_TITLE, ts: "t" }],
          },
        },
      }),
    );
    const { container } = render(<SecurityPanel />);
    const list = await screen.findByTestId("security-findings-table");
    expect(within(list).getByText(XSS_TITLE, { exact: false })).toBeInTheDocument();

    fireEvent.click(within(list).getByRole("button", { name: /open finding/i }));
    const drawer = await screen.findByTestId("security-finding-detail");
    await within(drawer).findByTestId("security-finding-patch");

    expect(within(drawer).getByTestId("security-finding-patch").textContent).toBe(XSS_PATCH);
    expect(within(drawer).getByTestId("security-finding-repro").textContent).toBe(XSS_PATCH);
    expect(within(drawer).getByRole("heading", { name: XSS_TITLE })).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });
});
