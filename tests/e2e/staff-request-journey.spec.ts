import { test, expect, type Page, type Request } from "@playwright/test";

test.use({ serviceWorkers: "block" });

// ── Shared Fixtures (DRY) ───────────────────────────────────────────────────

const FIXTURES = {
  failedRun: {
    id: 12345,
    name: "CI Workflow",
    workflow_name: "CI Workflow",
    head_branch: "feature/remediate-test",
    conclusion: "failure",
    repository: { name: "Runner_Dashboard", full_name: "D-sorganization/Runner_Dashboard" },
    failure_reason: "Unit tests failed with code 1",
    log_excerpt: "AssertionError: Expected 200 but received 500 in test_pipeline",
    created_at: "2026-09-26T00:00:00Z",
    html_url: "https://github.com/D-sorganization/Runner_Dashboard/actions/runs/12345",
  },
  remediationConfig: {
    policy: {
      default_provider: "claude_code_cli",
      max_same_failure_attempts: 3,
      workflow_type_rules: {},
    },
    providers: {
      claude_code_cli: { label: "Claude Code", notes: "cli" },
    },
    availability: {
      claude_code_cli: { available: true, status: "ready" },
    },
  },
  // The remediation preview must accept the run before "Fix this failed run" is enabled.
  remediationPlan: {
    decision: {
      accepted: true,
      provider_id: "claude_code_cli",
      prompt_preview: "Fix failed run 12345",
    },
  },
  // An idle StaffBoardResponse: the Staff page groups board.running/queued by machine.
  board: {
    generated_at: "2026-09-26T00:00:00Z",
    machine: "e2e-node",
    providers: {},
    queued: [],
    recent: [],
    running: [],
    spend_today_usd: {},
  },
  roster: {
    roles: [
      {
        name: "barb",
        title: "Barb",
        summary: "Fleet Orchestrator",
        dispatchable: true,
        providers: ["claude_code_cli"],
      },
    ],
    providers: {
      claude_code_cli: true,
    },
  },
  requestSuccessResponse: {
    state: "executed" as const,
    kind: "ci.remediate",
    action: "remediate_run",
    risk: "low",
    run_id: "run-9876",
    thread_id: "thread-remediate-123",
  },
  requestFailureResponse: {
    detail: "Upstream rate limit exceeded for request dispatch",
  },
  threadDetail: {
    thread: {
      id: "thread-remediate-123",
      title: "Remediation for run 12345",
      kind: "direct",
      participants: ["barb"],
      status: "active",
    },
    messages: [
      {
        id: "msg-user-1",
        thread_id: "thread-remediate-123",
        author: "dashboard",
        author_kind: "user",
        kind: "text",
        body_md: "**Request** `ci.remediate`\n\nFix failed run #12345",
        created_at: "2026-09-26T00:01:00Z",
      },
      {
        id: "msg-run-card-1",
        thread_id: "thread-remediate-123",
        author: "system",
        author_kind: "system",
        kind: "run_card",
        run_id: "run-9876",
        meta: {
          run: {
            id: "run-9876",
            status: "running",
            run_number: 9876,
          },
          run_id: "run-9876",
          action: "remediate_run",
        },
        body_md: "**Staff Run Started**: `run-9876`",
        created_at: "2026-09-26T00:01:01Z",
      },
    ],
  },
};

/** Stub common backend endpoints for the Remediation page and Staff Console. */
async function setupApiMocks(
  page: Page,
  options?: { onRequestSubmit?: (req: Request, body: Record<string, unknown>) => void; requestFail?: boolean },
) {
  await page.route("**/api/agent-remediation/config", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(FIXTURES.remediationConfig),
    }),
  );

  await page.route("**/api/agent-remediation/workflows", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ workflows: [] }),
    }),
  );

  await page.route("**/api/agent-remediation/history", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ history: [] }),
    }),
  );

  await page.route("**/api/agent-remediation/plan", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(FIXTURES.remediationPlan),
    }),
  );

  await page.route("**/api/runs/enriched?per_page=50", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ runs: [FIXTURES.failedRun] }),
    }),
  );

  await page.route("**/api/runs?per_page=30", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ runs: [] }),
    }),
  );

  await page.route("**/api/v1/staff/roster", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(FIXTURES.roster),
    }),
  );

  await page.route("**/api/v1/staff/board", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ machine: "local", running: [], queued: [], recent: [] }),
    }),
  );

  await page.route("**/api/v1/staff/inbox**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ unread_count: 0, items: [] }),
    }),
  );

  await page.route("**/api/client-errors**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok" }),
    }),
  );

  await page.route("**/api/v1/staff/runs**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ runs: [] }),
    }),
  );

  await page.route("**/api/providers/registry", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ providers: {}, active: null }),
    }),
  );

  await page.route("**/api/usage/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" }),
  );

  await page.route("**/api/metrics/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" }),
  );

  await page.route("**/api/v1/staff/threads", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ threads: [FIXTURES.threadDetail.thread] }),
    }),
  );

  await page.route("**/api/v1/staff/threads/thread-remediate-123/stream**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: "",
    }),
  );

  await page.route("**/api/v1/staff/threads/thread-remediate-123", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(FIXTURES.threadDetail),
    }),
  );

  await page.route("**/api/v1/staff/threads/thread-remediate-123/messages**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(FIXTURES.threadDetail),
    }),
  );

  await page.route("**/api/v1/staff/requests", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      options?.onRequestSubmit?.(route.request(), body);
      if (options?.requestFail) {
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify(FIXTURES.requestFailureResponse),
        });
      } else {
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify(FIXTURES.requestSuccessResponse),
        });
      }
    } else {
      await route.fallback();
    }
  });
}

/**
 * The first failed run is implicitly selected, so its "Fix this failed run"
 * button stays disabled until the remediation preview accepts it: preview, then fix.
 */
async function previewThenFix(page: Page) {
  await page.getByRole("button", { name: "Preview", exact: true }).first().click();
  const fixButton = page.getByRole("button", { name: "Fix this failed run" });
  await expect(fixButton).toBeEnabled();
  await fixButton.click();
}

test.describe("SC-G5-7: Staff request journey from remediation context button (#1504)", () => {
  test("context button opens prefilled request form, submit sends request and renders run card in named thread", async ({
    page,
  }) => {
    let capturedBody: Record<string, unknown> | null = null;
    await setupApiMocks(page, {
      onRequestSubmit: (_req, body) => {
        capturedBody = body;
      },
    });

    // 1. Open the Remediation page with a failed workflow run listed
    await page.goto("/work/remediation");
    await expect(page.getByText(/Runner_Dashboard · CI Workflow/)).toBeVisible();

    // 2. Click its "Fix this failed run" context button
    await previewThenFix(page);

    // 3. The request form opens prefilled (repository, the run, and a prompt that mentions the failure)
    await expect(page.getByLabel("Repo")).toHaveValue("Runner_Dashboard");
    await expect(page.getByLabel("Run ID")).toHaveValue("12345");
    const promptInput = page.getByLabel("Prompt");
    await expect(promptInput).toHaveValue(/12345/);
    await expect(promptInput).toHaveValue(/Unit tests failed with code 1|AssertionError/);

    // 4. Approve or submit it. The browser sends POST /api/v1/staff/requests with expected kind and target
    const dispatchBtn = page.getByRole("button", { name: "Dispatch" });
    await expect(dispatchBtn).toBeEnabled();
    await dispatchBtn.click();

    await expect.poll(() => capturedBody).not.toBeNull();
    expect(capturedBody).toMatchObject({
      kind: "ci.remediate",
      target: {
        repo: "Runner_Dashboard",
        run_id: 12345,
      },
    });
    expect(capturedBody.prompt).toContain("12345");

    // 5. A run card for the resulting run appears in the Staff thread the response names
    const runCard = page.locator('[data-run-id="run-9876"]').or(page.locator(".staff-run-card"));
    await expect(runCard.first()).toBeVisible({ timeout: 10_000 });
    await expect(runCard.first()).toContainText(/9876/);
  });

  test("failure path: request API failure shows error visibly and preserves prefilled/edited input", async ({
    page,
  }) => {
    let capturedBody: Record<string, unknown> | null = null;
    await setupApiMocks(page, {
      requestFail: true,
      onRequestSubmit: (_req, body) => {
        capturedBody = body;
      },
    });

    // 1. Open Remediation page and click Fix this failed run
    await page.goto("/work/remediation");
    await previewThenFix(page);

    // 2. Form opens prefilled
    await expect(page.getByLabel("Repo")).toHaveValue("Runner_Dashboard");
    await expect(page.getByLabel("Run ID")).toHaveValue("12345");

    // Edit prompt slightly to verify user's edited input is kept
    const promptInput = page.getByLabel("Prompt");
    await promptInput.fill("Custom operator instruction: Please fix this immediately");

    // 3. Submit request -> fails with 500
    const dispatchBtn = page.getByRole("button", { name: "Dispatch" });
    await dispatchBtn.click();

    await expect.poll(() => capturedBody).not.toBeNull();
    expect(capturedBody).toMatchObject({
      kind: "ci.remediate",
      target: {
        repo: "Runner_Dashboard",
        run_id: 12345,
      },
    });

    // 4. Error is shown visibly (role="alert" or console error banner)
    const alert = page.getByRole("alert").filter({ hasText: /rate limit/i });
    await expect(alert).toBeVisible();

    // 5. User's prefilled and edited inputs are kept
    await expect(page.getByLabel("Repo")).toHaveValue("Runner_Dashboard");
    await expect(page.getByLabel("Run ID")).toHaveValue("12345");
    await expect(page.getByLabel("Prompt")).toHaveValue("Custom operator instruction: Please fix this immediately");
  });
});
