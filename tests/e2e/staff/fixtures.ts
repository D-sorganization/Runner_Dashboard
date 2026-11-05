/**
 * Shared fixtures for the Staff Console e2e suite (#1341).
 *
 * `principal` picks the fixture identity; its bearer is added to `/api/**`
 * requests only, so third-party requests (fonts) never carry it.
 */

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

/**
 * Open the Staff Console on the direct thread to `roleTitle`.
 *
 * Threads are reused per role. Post: the composer is ready and the thread's
 * history has been fetched, so counts taken now include earlier tests' messages.
 */
export async function openThread(page: Page, roleTitle: string): Promise<void> {
  await page.goto("/staff");
  const title = roleTitle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const roleButton = page.getByRole("button", { name: new RegExp(`^${title}, Status:`) });
  if (!(await roleButton.isVisible())) {
    await page.getByRole("button", { name: "Choose staff" }).click();
  }
  await expect(roleButton).toBeVisible();
  const history = page.waitForResponse(
    (r) => r.request().method() === "GET" && THREAD_DETAIL.test(new URL(r.url()).pathname),
  );
  await roleButton.click();
  await history;
  await expect(page.getByRole("textbox", { name: "Staff conversation input" })).toBeVisible();
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
