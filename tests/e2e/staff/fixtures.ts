/**
 * Shared fixtures for the Staff Console e2e suite (#1341).
 *
 * `principal` picks the fixture identity; its bearer is added to `/api/**`
 * requests only, so third-party requests (fonts) never carry it.
 */

import { test as base, expect, type Page } from "@playwright/test";
import { operatorHeaders, viewerHeaders } from "./identity";

type Principal = "operator" | "viewer";

const HEADERS: Record<Principal, Record<string, string>> = { operator: operatorHeaders, viewer: viewerHeaders };

export const test = base.extend<{ principal: Principal }>({
  principal: ["operator", { option: true }],
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
  const history = page.waitForResponse(
    (r) => r.request().method() === "GET" && THREAD_DETAIL.test(new URL(r.url()).pathname),
  );
  await page.getByRole("button", { name: new RegExp(`^${title}, Status:`) }).click();
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
