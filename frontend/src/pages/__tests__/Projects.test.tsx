// @vitest-environment jsdom
/**
 * Projects tab (issue #1199): cards from GET /api/projects, missing/malformed
 * charters, decisions needed, last steward run link, and the "Run steward now"
 * POST carrying the CSRF sentinel header.
 */
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectsPage } from "../ProjectsPage";
import type { ProjectOverview, ProjectsResponse } from "../Projects";

afterEach(cleanup);

const ALPHA: ProjectOverview = {
  repo: "Alpha",
  charter_present: true,
  features: [
    {
      id: "F1",
      feature: "Parser",
      status: "shipped",
      tracking: "#1",
      notes: "",
    },
    {
      id: "F2",
      feature: "Router",
      status: "in-progress",
      tracking: "#1199",
      notes: "",
    },
    { id: "F3", feature: "Page", status: "planned", tracking: "-", notes: "" },
    { id: "F4", feature: "Bots", status: "parked", tracking: "#4", notes: "" },
  ],
  progress: {
    planned: 1,
    in_progress: 1,
    shipped: 1,
    parked: 1,
    percent_shipped: 25,
  },
  status_present: true,
  decisions_needed: ["Adopt the charter contract fleet-wide?"],
  last_steward_run: {
    id: "steward-run-alpha-01",
    status: "succeeded",
    created_at: "2026-09-22T10:00:00Z",
    ended_at: "2026-09-22T10:05:00Z",
    machine: "desk",
  },
};

const BETA: ProjectOverview = {
  repo: "Beta",
  charter_present: false,
  features: [],
  progress: {
    planned: 0,
    in_progress: 0,
    shipped: 0,
    parked: 0,
    percent_shipped: 0,
  },
  status_present: false,
  decisions_needed: [],
  last_steward_run: null,
};

const GAMMA: ProjectOverview = {
  ...BETA,
  repo: "Gamma",
  charter_present: true,
  error: "charter invalid: Feature F3: invalid status 'someday'",
};

const RESPONSE: ProjectsResponse = {
  projects: [ALPHA, BETA, GAMMA],
  count: 3,
  cache_ttl_seconds: 600,
};

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  } as Response;
}

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("ProjectsPage", () => {
  it("renders one card per repo with progress, decisions and steward run", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(RESPONSE)));
    global.fetch = fetchMock as unknown as typeof fetch;
    render(<ProjectsPage />);

    await waitFor(() =>
      expect(screen.getByTestId("project-card-Alpha")).toBeInTheDocument(),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/projects",
      expect.objectContaining({ method: "GET" }),
    );

    const alpha = within(screen.getByTestId("project-card-Alpha"));
    expect(alpha.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "25",
    );
    expect(alpha.getByText("25% shipped")).toBeInTheDocument();
    expect(
      alpha.getByText("Adopt the charter contract fleet-wide?"),
    ).toBeInTheDocument();
    expect(alpha.getByText("succeeded")).toBeInTheDocument();
    expect(alpha.getByRole("link", { name: /run steward-/ })).toHaveAttribute(
      "href",
      "/api/staff/runs/steward-run-alpha-01",
    );

    const beta = within(screen.getByTestId("project-card-Beta"));
    expect(beta.queryByRole("progressbar")).toBeNull();
    expect(beta.getByText("docs/project/CHARTER.md")).toBeInTheDocument();
    expect(
      beta.getByText("No steward run recorded on this node."),
    ).toBeInTheDocument();

    const gamma = within(screen.getByTestId("project-card-Gamma"));
    expect(gamma.getByRole("alert")).toHaveTextContent(
      "invalid status 'someday'",
    );
  });

  it("posts the steward dispatch with the CSRF header and reports the run", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/projects")
        return Promise.resolve(jsonResponse(RESPONSE));
      return Promise.resolve(
        jsonResponse({ run: { id: "run-0001-xyz", status: "queued" } }),
      );
    });
    global.fetch = fetchMock as unknown as typeof fetch;
    render(<ProjectsPage />);
    await waitFor(() =>
      expect(screen.getByTestId("project-card-Beta")).toBeInTheDocument(),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Run steward now for Beta" }),
    );

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Steward run submitted (run run-0001, queued).",
      ),
    );
    const [url, init] = fetchMock.mock.calls[1] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("/api/staff/project-steward/run");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-Requested-With"]).toBe(
      "XMLHttpRequest",
    );
    expect(JSON.parse(String(init.body))).toEqual({
      repo: "Beta",
      prompt: "Scheduled steward pass",
      machine: "auto",
    });
  });

  it("shows the dispatch error on the card when the POST is rejected", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/projects")
        return Promise.resolve(jsonResponse(RESPONSE));
      return Promise.resolve(
        jsonResponse({ detail: "unknown role project-steward" }, 404),
      );
    });
    global.fetch = fetchMock as unknown as typeof fetch;
    render(<ProjectsPage />);
    await waitFor(() =>
      expect(screen.getByTestId("project-card-Alpha")).toBeInTheDocument(),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Run steward now for Alpha" }),
    );

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Steward run failed",
      ),
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "unknown role project-steward",
    );
  });

  it("surfaces a load error without throwing", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve(jsonResponse({ detail: "boom" }, 502)),
    ) as unknown as typeof fetch;
    render(<ProjectsPage />);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Could not load projects",
      ),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("502");
  });

  it("exposes parked plan identity and its owner link in feature details", async () => {
    const url =
      "https://github.com/D-sorganization/Alpha/blob/main/docs/development/planning/DV-24.md";
    const project: ProjectOverview = {
      ...ALPHA,
      features: [
        {
          id: "DV-24",
          feature: "Physical validation",
          status: "parked",
          tracking: "-",
          notes: `Awaiting resources. [Owner plan](${url})`,
        },
      ],
      progress: {
        planned: 0,
        in_progress: 0,
        shipped: 0,
        parked: 1,
        percent_shipped: 0,
      },
      decisions_needed: ["DV-24: approve resources and experimental access?"],
    };
    global.fetch = vi.fn(() =>
      Promise.resolve(
        jsonResponse({ ...RESPONSE, projects: [project], count: 1 }),
      ),
    ) as unknown as typeof fetch;
    render(<ProjectsPage />);
    const card = within(await screen.findByTestId("project-card-Alpha"));
    fireEvent.click(card.getByText("Features and plans (1)"));
    expect(card.getByText("DV-24")).toBeVisible();
    expect(card.getByText("Physical validation")).toBeVisible();
    expect(card.getByText("parked", { exact: true })).toBeVisible();
    expect(card.getByRole("link", { name: "Owner plan" })).toHaveAttribute(
      "href",
      url,
    );
    expect(
      card.getByText("DV-24: approve resources and experimental access?"),
    ).toBeVisible();
  });

  it("renders untrusted feature notes without executable links or HTML", async () => {
    const project: ProjectOverview = {
      ...ALPHA,
      features: [
        {
          id: "DV-unsafe",
          feature: "Review pending",
          status: "parked",
          tracking: "-",
          notes:
            '[Run](javascript:alert%281%29) [Data](data:text/html,unsafe) <img src="x" onerror="alert(1)"> <script>alert(1)</script>',
        },
      ],
    };
    global.fetch = vi.fn(() =>
      Promise.resolve(
        jsonResponse({ ...RESPONSE, projects: [project], count: 1 }),
      ),
    ) as unknown as typeof fetch;
    render(<ProjectsPage />);
    const cardElement = await screen.findByTestId("project-card-Alpha");
    const card = within(cardElement);
    fireEvent.click(card.getByText("Features and plans (1)"));
    expect(card.getByText("DV-unsafe")).toBeVisible();
    expect(card.queryByRole("link", { name: "Run" })).toBeNull();
    expect(card.queryByRole("link", { name: "Data" })).toBeNull();
    expect(cardElement.querySelector("img, script, [onerror]")).toBeNull();
  });
});
