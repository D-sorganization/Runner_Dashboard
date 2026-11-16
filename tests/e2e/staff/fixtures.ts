/**
 * Shared fixtures for the Staff Console e2e suite (#1341).
 *
 * `principal` picks the fixture identity; its bearer is added to `/api/**`
 * requests only, so third-party requests (fonts) never carry it.
 */

import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, rm, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { test as base, expect, type Page } from "@playwright/test";
import { operatorHeaders, requesterHeaders, viewerHeaders } from "./identity";
import { NETWORK_CHANGED, isNetworkChanged, networkChangedMarker, retryAllowed } from "./networkChangedRetry";

type Principal = "operator" | "viewer" | "requester";

const HEADERS: Record<Principal, Record<string, string>> = {
  operator: operatorHeaders,
  viewer: viewerHeaders,
  requester: requesterHeaders,
};

export const test = base.extend<{ principal: Principal; networkChangedGate: void }>({
  principal: ["operator", { option: true }],
  // Every test: a CI retry only follows a Chromium network-change abort (#3861).
  networkChangedGate: [
    async ({ context }, use, testInfo) => {
      const marker = networkChangedMarker(testInfo.project.outputDir, testInfo.testId);
      if (!retryAllowed(testInfo.retry, existsSync(marker))) {
        throw new Error(`retry ${testInfo.retry} refused: only a ${NETWORK_CHANGED} abort earns a retry (#3861)`);
      }
      if (testInfo.retry > 0) {
        await rm(marker, { force: true });
        testInfo.annotations.push({ type: "network-changed-retry", description: `previous attempt saw ${NETWORK_CHANGED}` });
      }
      let networkChanged = false;
      context.on("requestfailed", (request) => {
        if (isNetworkChanged(request.failure()?.errorText)) networkChanged = true;
      });
      await use();
      if (networkChanged && testInfo.status !== testInfo.expectedStatus) {
        await mkdir(dirname(marker), { recursive: true });
        await writeFile(marker, `${testInfo.title}\n`, "utf-8");
      }
    },
    { auto: true },
  ],
  page: async ({ page, principal }, use) => {
    await page.route("**/api/**", (route) =>
      route.continue({ headers: { ...route.request().headers(), ...HEADERS[principal] } }),
    );
    await use(page);
  },
});

export { expect };

const THREAD_DETAIL = /\/api\/v1\/staff\/threads\/[^/]+$/;

export function staffE2eBackendUrl(): string {
  return process.env.STAFF_E2E_BACKEND_URL ?? `http://127.0.0.1:${process.env.STAFF_E2E_BACKEND_PORT ?? "5092"}`;
}

/**
 * Open the Staff Console on the direct thread to `roleTitle`.
 *
 * Threads are reused per role. Post: the composer is ready and the thread's
 * history has been fetched, so counts taken now include earlier tests' messages.
 */
export async function openThread(page: Page, roleTitle: string): Promise<string> {
  await page.goto("/staff");
  const title = roleTitle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const roleButton = page.getByRole("button", { name: new RegExp(`^${title}, Status:`) });
  try {
    await roleButton.waitFor({ state: "visible", timeout: 3_000 });
  } catch {
    await page.getByRole("button", { name: "Choose staff" }).click();
  }
  await expect(roleButton).toBeVisible({ timeout: 20_000 });
  const history = page.waitForResponse(
    (r) => r.request().method() === "GET" && THREAD_DETAIL.test(new URL(r.url()).pathname),
  );
  await roleButton.click();
  const histResp = await history;
  const parts = new URL(histResp.url()).pathname.split("/").filter(Boolean);
  const threadId = parts[parts.length - 1] ?? "";
  if (!threadId) {
    throw new Error("could not resolve staff thread id from history response");
  }
  await expect(page.getByRole("textbox", { name: "Staff conversation input" })).toBeVisible();
  return threadId;
}

/** POST a chat message with optional meta (e.g. confirm_actions for E-18). */
export async function postThreadMessageApi(
  page: Page,
  threadId: string,
  text: string,
  meta: Record<string, unknown> | undefined,
  headers: Record<string, string>,
): Promise<void> {
  const response = await page.request.post(
    `${staffE2eBackendUrl()}/api/v1/staff/threads/${encodeURIComponent(threadId)}/messages`,
    {
      headers: {
        ...headers,
        "X-Requested-With": "XMLHttpRequest",
        "Idempotency-Key": randomUUID(),
      },
      data: { body: text, meta: meta ?? {} },
    },
  );
  expect(response.status()).toBe(202);
}

/** Type and send one message. */
export async function send(page: Page, text: string): Promise<void> {
  await page.getByRole("textbox", { name: "Staff conversation input" }).fill(text);
  await page.getByRole("button", { name: "Send message" }).click();
}

/** The conversation log, the region every reply lands in. */
export function conversation(page: Page) {
  return page.getByRole("log", { name: "Conversation messages" });
}
