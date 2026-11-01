/**
 * Staff Console end-to-end flows against the hermetic fake backend (#1341).
 *
 * The backend is the real FastAPI app; only the provider CLIs are fakes
 * (`tests/e2e/fakes/bin`). A `[[e2e:<scenario>]]` directive in a message picks
 * the fake's behaviour. Threads are reused per role, so every test tags its
 * message with a nonce and asserts on that nonce or on counts relative to the
 * state it found.
 */

import { randomUUID } from "node:crypto";
import type { Browser, Locator, Page } from "@playwright/test";
import { conversation, expect, openThread, send, test } from "./fixtures";
import { operatorHeaders, requesterHeaders, viewerHeaders } from "./identity";

const COMPOSER = "Staff conversation input";
// The console banner, not an error card from an earlier turn.
const SEND_ERROR = /^staff-console-error-/;

function nonce(): string {
  return randomUUID().slice(0, 8);
}

/** The fake provider's reply to `text`. */
function reply(text: string): string {
  return `Fake reply: ${text}`;
}

const BARB = "Ask Barb (auto-route)";

/** Ask Barb for a dispatch and return the ActionCard her reply proposes. */
async function proposeDispatch(page: Page, scenario = "dispatch"): Promise<Locator> {
  const cards = conversation(page).locator(".staff-action-card");
  const before = await cards.count();
  await send(page, `dispatch ${nonce()} [[e2e:${scenario}]]`);
  await expect(cards).toHaveCount(before + 1);
  return cards.nth(before);
}

/**
 * Have a caller who can chat but not approve ask Barb for a dispatch; return the proposal id.
 * An approver's own request would run at once (#1786), so cards to act on come from here.
 */
async function proposeAsRequester(browser: Browser): Promise<string> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.route("**/api/**", (route) =>
    route.continue({ headers: { ...route.request().headers(), ...requesterHeaders } }),
  );
  await openThread(page, BARB);
  const id = await (await proposeDispatch(page)).getAttribute("data-proposal-id");
  await context.close();
  if (!id) throw new Error("proposal card has no id");
  return id;
}

/** The operator's own request for a dispatch runs at once; return the run card it starts (#1547, #1786). */
async function requestAndFollowRun(page: Page, scenario: string): Promise<Locator> {
  await openThread(page, BARB);
  const runCards = conversation(page).locator(".staff-run-card");
  const before = await runCards.count();
  await proposeDispatch(page, scenario);
  await expect(runCards).toHaveCount(before + 1);
  return runCards.nth(before);
}

/** Occurrences of `needle` in the conversation log's visible text. */
async function occurrences(page: Page, needle: string): Promise<number> {
  const text = await conversation(page).innerText();
  return text.split(needle).length - 1;
}

test.describe("chat", () => {
  test("the persistent Barb panel follows the selected conversation across tab navigation", async ({ page, request }) => {
    await page.goto("/staff");
    const backendPort = process.env.STAFF_E2E_BACKEND_PORT ?? "5092";
    const backendUrl = process.env.STAFF_E2E_BACKEND_URL ?? `http://127.0.0.1:${backendPort}`;
    const createThread = async (role: string, kind: "auto" | "direct", title: string) => {
      const response = await request.post(`${backendUrl}/api/v1/staff/threads`, {
        headers: { ...operatorHeaders, "X-Requested-With": "XMLHttpRequest" },
        data: { role, kind, title },
      });
      if (!response.ok()) throw new Error(`Thread creation failed: ${response.status()}`);
      return response.json() as Promise<{ id: string }>;
    };
    const selected = await createThread("barb", "auto", "Selected operator conversation");
    await createThread("barb", "auto", "Newer operator conversation");

    await page.goto(`/staff?role=barb&thread=${encodeURIComponent(selected.id)}`);
    await expect(page.getByRole("region", { name: "Conversation thread with Selected operator conversation" })).toBeVisible();
    await page.getByRole("navigation", { name: /dashboard sections/i }).getByRole("button", { name: /^Context/i }).click();
    await expect(page).toHaveURL(new RegExp(`thread=${selected.id}`));

    await page.getByTestId("ask-barb-toggle").click();
    const panel = page.getByRole("complementary", { name: "Ask Barb" });
    await expect(panel.getByRole("region", { name: "Conversation thread with Selected operator conversation" })).toBeVisible();
    await expect(panel.getByRole("region", { name: "Conversation thread with Newer operator conversation" })).toHaveCount(0);

    const specialist = await createThread("reviewer", "direct", "Reviewer conversation");
    await page.goto(`/staff?role=reviewer&thread=${encodeURIComponent(specialist.id)}`);
    await expect(page.getByRole("region", { name: "Conversation thread with Reviewer conversation" })).toBeVisible();
    await page.getByTestId("ask-barb-toggle").click();
    const resumedBarb = page.getByRole("complementary", { name: "Ask Barb" });
    await expect(resumedBarb.getByRole("region", { name: "Conversation thread with Reviewer conversation" })).toHaveCount(0);
    await expect(resumedBarb.getByRole("region", { name: "Conversation thread with Newer operator conversation" })).toBeVisible();
  });

  test("chat-first home shows the composer and can reveal staff choices", async ({ page }) => {
    await page.goto("/staff");
    await expect(page.getByRole("textbox", { name: COMPOSER })).toBeVisible();

    const chooseStaff = page.getByRole("button", { name: "Choose staff" });
    await expect(chooseStaff).toBeVisible();
    await chooseStaff.click();
    await expect(page.getByRole("button", { name: /^E2E Analyst, Status:/ })).toBeVisible();
  });

  test("project starters keep the named repository context and wait for explicit send", async ({ page }) => {
    let messagePosts = 0;
    page.on("request", (request) => {
      if (request.method() === "POST" && /\/threads\/[^/]+\/messages$/.test(new URL(request.url()).pathname)) {
        messagePosts += 1;
      }
    });

    for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      await page.goto("/staff");
      const composer = page.getByRole("textbox", { name: COMPOSER });
      await expect(composer).toBeVisible();

      await page.getByRole("button", { name: "Start UpstreamDrift" }).click();
      await expect(composer).toHaveValue(/open issues and pull requests in the UpstreamDrift repository/);
      await expect(composer).toHaveValue(/wait for my approval before dispatching/);

      await page.getByRole("button", { name: "Start AffineDrift" }).click();
      await expect(composer).toHaveValue(/open issues and pull requests in the AffineDrift repository/);
      await expect(composer).toHaveValue(/wait for my approval before dispatching/);
    }

    expect(messagePosts).toBe(0);
  });

  test("sending from the mobile chat-first home reveals Barb's conversation", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/staff");
    await expect(page.getByTestId("staff-mobile-home")).toBeVisible();

    const text = `mobile home ${nonce()}`;
    await send(page, text);

    await expect(page.getByTestId("staff-mobile-thread")).toBeVisible();
    await expect(page.getByRole("log", { name: "Conversation messages" }).getByText(text, { exact: true })).toBeVisible();
    await expect(page.getByRole("textbox", { name: COMPOSER })).toBeVisible();
    await expect(page).toHaveURL(/\/staff[?]role=rd-barb&thread=[^&]+/);

    await page.reload();
    await expect(page.getByTestId("staff-mobile-thread")).toBeVisible();
    await expect(page.getByRole("log", { name: "Conversation messages" }).getByText(text, { exact: true })).toBeVisible();
  });

  test("a direct reply is shown exactly once", async ({ page }) => {
    const text = `hello ${nonce()}`;
    await openThread(page, "E2E Analyst");
    await send(page, text);

    await expect(conversation(page).getByText(reply(text))).toBeVisible();
    expect(await occurrences(page, reply(text))).toBe(1);
    await expect(page.getByRole("textbox", { name: COMPOSER })).toHaveValue("");
  });

  test("an unavailable provider falls back to the default chain", async ({ page }) => {
    // E2E Offline names codex only; the fake PATH has no codex, so claude answers.
    const text = `offline ${nonce()}`;
    await openThread(page, "E2E Offline");
    await send(page, text);

    await expect(conversation(page).getByText(reply(text))).toBeVisible();
  });

  test("Barb's handoff reply posts a handoff card that opens the analyst's thread (#1548)", async ({ page }) => {
    await openThread(page, "Ask Barb (auto-route)");
    const cards = conversation(page).locator(".staff-handoff-card");
    const before = await cards.count();
    const tag = nonce();
    await send(page, `analyse the queue ${tag} [[e2e:handoff]]`);

    await expect(cards).toHaveCount(before + 1);
    const card = cards.last();
    await expect(card).toContainText("handing it over");
    await card.getByRole("button", { name: "Continue with E2e-analyst" }).click();

    // The analyst's thread opens, seeded with Barb's brief quoting the request.
    const brief = conversation(page).getByText("Hand-off from Barb").last();
    await expect(brief).toBeVisible();
    await expect(conversation(page).getByText(tag)).toBeVisible();
    await expect(conversation(page).locator(".staff-handoff-card")).toHaveCount(0);
  });

  test("the reply arrives after the event stream drops and reconnects", async ({ page }) => {
    let dropped = false;
    await page.route("**/api/v1/staff/threads/*/stream**", (route) => {
      if (dropped) return route.fallback();
      dropped = true;
      return route.abort("connectionreset");
    });
    const text = `resume ${nonce()}`;
    await openThread(page, "E2E Analyst");
    await send(page, text);

    await expect(conversation(page).getByText(reply(text))).toBeVisible({ timeout: 20_000 });
    expect(dropped).toBe(true);
  });
});

test.describe("provider failures", () => {
  test("a provider crash ends in an error card, not a pending reply", async ({ page }) => {
    await openThread(page, "E2E Analyst");
    const alerts = conversation(page).getByRole("alert");
    const before = await alerts.count();
    await send(page, `crash ${nonce()} [[e2e:crash]]`);

    await expect(alerts).toHaveCount(before + 1);
  });

  test("an expired login ends in an authentication error card", async ({ page }) => {
    await openThread(page, "E2E Analyst");
    const alerts = conversation(page).getByRole("alert").filter({ hasText: "Authentication Expired" });
    const before = await alerts.count();
    await send(page, `auth ${nonce()} [[e2e:auth]]`);

    const latest = alerts.nth(before);
    await expect(latest).toBeVisible({ timeout: 30_000 });
    await expect(latest).toContainText("claude auth login");
    await expect(latest).not.toContainText("systemctl");
  });
});

test.describe("send failures keep the draft", () => {
  test("a backend 500 shows an error and keeps the draft", async ({ page }) => {
    await page.route("**/api/v1/staff/threads/*/messages", (route) =>
      route.request().method() === "POST"
        ? route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"boom"}' })
        : route.fallback(),
    );
    const text = `server error ${nonce()}`;
    await openThread(page, "E2E Analyst");
    await send(page, text);

    const sendAlert = page.getByRole("alert").filter({ hasText: /Failed to send message/ });
      await expect(sendAlert).toBeVisible();
      await expect(sendAlert).toHaveCount(1);
      await expect(page.getByTestId(SEND_ERROR)).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: COMPOSER })).toHaveValue(text);
    expect(await occurrences(page, reply(text))).toBe(0);
  });

  test.describe("as a viewer", () => {
    test.use({ principal: "viewer" });

    test("a send without write permission is refused and keeps the draft", async ({ page }) => {
      const text = `viewer ${nonce()}`;
      await openThread(page, "E2E Analyst");
      await send(page, text);

      const sendAlert = page.getByRole("alert").filter({ hasText: /Failed to send message/ });
      await expect(sendAlert).toBeVisible();
      await expect(sendAlert).toHaveCount(1);
      await expect(page.getByTestId(SEND_ERROR)).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
      await expect(page.getByRole("textbox", { name: COMPOSER })).toHaveValue(text);
    });
  });
});

test.describe("Ask Barb drawer keyboard access", () => {
  test("returns focus to the launcher after focus moves during lazy drawer loading", async ({ page }) => {
    await page.route("**/src/shell/BarbSidebar.tsx*", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 250));
      await route.continue();
    });
    await page.goto("/staff");
    const trigger = page.getByRole("button", { name: "Ask Barb" });
    await trigger.focus();
    await page.keyboard.press("Enter");
    await page.getByRole("main").focus();

    const drawer = page.getByTestId("barb-sidebar");
    await expect(drawer).toBeVisible();
    await expect.poll(() => drawer.evaluate((element) => element.contains(document.activeElement))).toBe(true);
    await page.keyboard.press("Escape");
    await expect(drawer).toHaveAttribute("aria-hidden", "true");
    await expect(trigger).toBeFocused();
  });

  test("Escape restores toggle focus at desktop, tablet, and mobile widths", async ({ page }) => {
    for (const viewport of [
      { width: 1440, height: 900 },
      { width: 960, height: 900 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto("/staff");
      const trigger = page.getByRole("button", { name: "Ask Barb" });
      await trigger.focus();
      await page.keyboard.press("Enter");

      const drawer = page.getByTestId("barb-sidebar");
      const close = drawer.getByRole("button", { name: "Close Ask Barb" });
      await expect(drawer).toBeVisible();
      await expect.poll(() => drawer.evaluate((element) => element.contains(document.activeElement))).toBe(true);

      if (viewport.width === 390) {
        await expect(drawer).toHaveAttribute("aria-modal", "true");
        await close.focus();
        const focusable = drawer
          .locator('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')
          .filter({ visible: true });
        const first = focusable.first();
        const last = focusable.last();
        await first.focus();
        await page.keyboard.press("Shift+Tab");
        await expect(last).toBeFocused();
        await page.keyboard.press("Tab");
        await expect(first).toBeFocused();
      }

      await page.keyboard.press("Escape");
      await expect(drawer).toHaveAttribute("aria-hidden", "true");
      await expect(trigger).toBeFocused();
    }
  });
});

test.describe("proposals", () => {
  test("a proposed action appears as an action card, live and after reload", async ({ page }) => {
    await openThread(page, BARB);
    const card = await proposeDispatch(page);
    await expect(card).toContainText("staff.dispatch");
    await expect(card).toContainText("The analyst can look at the queue.");
    const id = await card.getAttribute("data-proposal-id");

    await openThread(page, BARB);
    await expect(page.locator(`[data-proposal-id="${id}"]`)).toContainText("staff.dispatch");
  });

  test("the operator's own request runs at once, without an Approve tap (#1786)", async ({ page }) => {
    await openThread(page, BARB);
    const card = await proposeDispatch(page);

    await expect(card).toContainText(/Executed by .*e2e-operator/);
    await expect(card.getByRole("button", { name: /^Approve$/ })).toHaveCount(0);
  });

  test("approving a dispatch proposal executes it", async ({ page, browser }) => {
    const id = await proposeAsRequester(browser);
    await openThread(page, BARB);
    const card = page.locator(`[data-proposal-id="${id}"]`);

    await card.getByRole("button", { name: "Approve" }).click();

    await expect(card).toContainText(/Executed by .*e2e-operator/);
  });

  test("denying a proposal marks the card denied", async ({ page, browser }) => {
    const id = await proposeAsRequester(browser);
    await openThread(page, BARB);
    const card = page.locator(`[data-proposal-id="${id}"]`);

    await card.getByRole("button", { name: "Deny" }).click();

    await expect(card).toContainText(/Denied by .*e2e-operator/);
  });

  test.describe("as a viewer", () => {
    test.use({ principal: "viewer" });

    test("a viewer sees Approve disabled with the reason, and a forced approval is still refused (#1807)", async ({ page, browser }) => {
      const id = await proposeAsRequester(browser);

      await openThread(page, BARB);
      const card = page.locator(`[data-proposal-id="${id}"]`);

      await expect(card.getByTestId("approval-denial-reason")).toBeVisible();
      await expect(card.getByRole("button", { name: /^Approve$/ })).toBeDisabled();
      await expect(card.getByRole("button", { name: /^Deny$/ })).toBeDisabled();

      // The preview is advisory: bypassing the UI is still refused server-side and changes nothing.
      const forced = await page.request.post(`/api/v1/staff/proposals/${id}/decide`, {
        headers: { ...viewerHeaders, "X-Requested-With": "XMLHttpRequest" },
        data: { decision: "approved", execute: true, params_hash: "0".repeat(64) },
      });
      expect(forced.status()).toBe(403);
      await page.reload();
      await expect(page.locator(`[data-proposal-id="${id}"]`).getByRole("button", { name: /^Approve$/ })).toBeVisible();
    });
  });
});

test.describe("runs", () => {
  test("an approved dispatch shows a run card that follows the run to its result", async ({ page }) => {
    const run = await requestAndFollowRun(page, "dispatch");

    await expect(run).toContainText("completed", { timeout: 30_000 });
    await expect(run).toContainText("fake run finished");
  });

  test("a run that needs input takes an answer and the continuation completes", async ({ page }) => {
    const run = await requestAndFollowRun(page, "dispatch-ask");
    const runCards = conversation(page).locator(".staff-run-card");
    await expect(run).toContainText("Which repository should I look at?", { timeout: 30_000 });
    const before = await runCards.count();

    await run.getByRole("textbox", { name: "Answer" }).fill("Runner_Dashboard");
    await run.getByRole("button", { name: "Send answer" }).click();

    await expect(run).toContainText(/Answered by .*e2e-operator/);
    await expect(runCards).toHaveCount(before + 1);
    await expect(runCards.nth(before)).toContainText("fake run finished for Runner_Dashboard", { timeout: 30_000 });
  });

  test("a running run can be cancelled from its card", async ({ page }) => {
    const run = await requestAndFollowRun(page, "dispatch-hang");
    await expect(run).toContainText("running", { timeout: 30_000 });

    await run.getByRole("button", { name: "Cancel run" }).click();

    await expect(run).toContainText("cancelled", { timeout: 30_000 });
  });
});

test.describe("approved dashboard navigation", () => {
  async function openBarbSidebar(page: Page, threadId?: string): Promise<Locator> {
    await page.goto(threadId ? `/staff?role=barb&thread=${threadId}` : "/staff");
    const askBarb = page.getByRole("button", { name: "Ask Barb" });
    if (!(await page.getByTestId("barb-sidebar").isVisible())) await askBarb.click();
    const drawer = page.getByTestId("barb-sidebar");
    await expect(drawer).toBeVisible();
    await expect(drawer.getByRole("textbox", { name: COMPOSER })).toBeVisible();
    return drawer;
  }

  async function proposeFromSidebar(drawer: Locator, scenario: string): Promise<string> {
    const cards = drawer.locator(".staff-action-card");
    const before = await cards.count();
    await drawer.getByRole("textbox", { name: COMPOSER }).fill(`open Context [[e2e:${scenario}]]`);
    await drawer.getByRole("button", { name: "Send message" }).click();
    await expect(cards).toHaveCount(before + 1);
    const id = await cards.last().getAttribute("data-proposal-id");
    if (!id) throw new Error("navigation proposal has no id");
    return id;
  }

  test("approved navigation changes the route, preserves Barb's conversation on desktop and mobile, and rejects unknown sections", async ({ page, browser }) => {
    const requesterContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const requester = await requesterContext.newPage();
    await requester.route("**/api/**", (route) =>
      route.continue({ headers: { ...route.request().headers(), ...requesterHeaders } }),
    );
    await openThread(requester, BARB);
    const priorThreadId = new URL(requester.url()).searchParams.get("thread");
    requester.route("**/api/v1/staff/threads", async (route) => {
      if (route.request().method() === "POST") {
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      await route.continue({
        headers: { ...route.request().headers(), ...requesterHeaders },
      });
    });
    const createdThread = requester.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/api/v1/staff/threads"),
    );
    await requester.getByRole("button", { name: "New conversation" }).click();
    const createdThreadResponse = await createdThread;
    expect(createdThreadResponse.ok()).toBeTruthy();
    const createdThreadId = (await createdThreadResponse.json()).id as string;
    await expect(requester).toHaveURL(new RegExp(`thread=${createdThreadId}`));
    expect(createdThreadId).not.toBe(priorThreadId);
    const threadId = new URL(requester.url()).searchParams.get("thread");
    if (!threadId) throw new Error("new navigation conversation has no thread id");
    const requesterDrawer = await openBarbSidebar(requester, threadId);

    const desktopProposalId = await proposeFromSidebar(requesterDrawer, "navigate");
    const desktopDrawer = await openBarbSidebar(page, threadId);
    const desktopCard = desktopDrawer.locator(`[data-proposal-id="${desktopProposalId}"]`);
    await expect(desktopCard.getByRole("button", { name: /^Approve$/ })).toBeEnabled();
    await desktopCard.getByRole("button", { name: /^Approve$/ }).click();
    await expect(page).toHaveURL(/\/context(?:[?]|$)/);
    await expect(desktopDrawer).toContainText("open Context");
    await expect(desktopDrawer).toContainText("Action Executed");

    await desktopDrawer.getByRole("button", { name: "Open Projects" }).click();
    await expect(page).toHaveURL(/\/projects(?:[?]|$)/);

    const replayContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const replayPage = await replayContext.newPage();
    await replayPage.route("**/api/**", (route) =>
      route.continue({ headers: { ...route.request().headers(), ...requesterHeaders } }),
    );
    await replayPage.goto(`/projects?role=barb&thread=${encodeURIComponent(threadId)}`);
    await replayPage.getByRole("button", { name: "Ask Barb" }).click();
    await expect(replayPage.getByTestId("barb-sidebar")).toBeVisible();
    await expect(replayPage).toHaveURL(/\/projects(?:[?]|$)/);
    await replayContext.close();

    const restoredContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const restoredPage = await restoredContext.newPage();
    await restoredPage.route("**/api/**", (route) =>
      route.continue({ headers: { ...route.request().headers(), ...requesterHeaders } }),
    );
    await restoredPage.goto("/projects");
    await restoredPage.getByRole("button", { name: "Ask Barb" }).click();
    const restoredDrawer = restoredPage.getByTestId("barb-sidebar");
    await expect(restoredDrawer).toBeVisible();
    await expect(restoredDrawer).toContainText("open Context");
    await expect(restoredDrawer).toContainText("Action Executed");
    await expect(restoredPage).toHaveURL(/\/projects(?:[?]|$)/);
    await restoredContext.close();

    if (!(await desktopDrawer.isVisible())) await page.getByRole("button", { name: "Ask Barb" }).click();
    const mobileProposalId = await proposeFromSidebar(requesterDrawer, "navigate");
    await page.setViewportSize({ width: 390, height: 844 });
    const mobileDrawer = page.getByTestId("barb-sidebar");
    if (!(await mobileDrawer.isVisible())) await page.getByRole("button", { name: "Ask Barb" }).click();
    await expect(mobileDrawer).toBeVisible();
    const mobileCard = mobileDrawer.locator(`[data-proposal-id="${mobileProposalId}"]`);
    await expect(mobileCard.getByRole("button", { name: /^Approve$/ })).toBeEnabled();
    await mobileCard.getByRole("button", { name: /^Approve$/ }).click();
    await expect(page).toHaveURL(/\/context(?:[?]|$)/);
    await expect(mobileDrawer).toHaveAttribute("aria-hidden", "true");
    await page.getByRole("button", { name: "Ask Barb" }).click();
    const reopenedDrawer = page.getByTestId("barb-sidebar");
    await expect(reopenedDrawer).toBeVisible();
    await expect(reopenedDrawer).toContainText("open Context");
    await expect(reopenedDrawer).toContainText("Action Executed");

    const routeBeforeInvalidAction = page.url();
    const invalidProposalId = await proposeFromSidebar(requesterDrawer, "navigate-invalid");
    const invalidCard = reopenedDrawer.locator(`[data-proposal-id="${invalidProposalId}"]`);
    await expect(invalidCard.getByRole("button", { name: /^Approve$/ })).toBeEnabled();
    await invalidCard.getByRole("button", { name: /^Approve$/ }).click();
    await expect(page).toHaveURL(routeBeforeInvalidAction);
    await expect(reopenedDrawer).toContainText("Action Failed", { timeout: 30_000 });
    await expect(invalidCard).toContainText("Action failed: tab_id must be a registered dashboard section. You can retry approval.");
    await expect(invalidCard.getByRole("button", { name: /^Approve$/ })).toBeEnabled();
    await expect(reopenedDrawer).toContainText("registered dashboard section");
    const retryResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith(`/proposals/${invalidProposalId}/decide`),
    );
    await invalidCard.getByRole("button", { name: /^Approve$/ }).click();
    expect((await retryResponse).status()).toBe(200);
    await expect(invalidCard.getByRole("button", { name: /^Approve$/ })).toBeEnabled();
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByRole("button", { name: "Staff Console", exact: true }).click();
    expect(new URL(page.url()).pathname).toBe("/");
    expect(new URL(page.url()).searchParams.get("thread")).toBe(threadId);
    await requesterContext.close();
  });
});


test.describe("persistent Barb conversation selection", () => {
  test("keeps a new sidebar conversation selected through navigation and mobile remount", async ({ page, request }) => {
    const backendPort = process.env.STAFF_E2E_BACKEND_PORT ?? "5092";
    const backendUrl = process.env.STAFF_E2E_BACKEND_URL ?? `http://127.0.0.1:${backendPort}`;
    const created = await request.post(`${backendUrl}/api/v1/staff/threads`, {
      headers: { ...operatorHeaders, "X-Requested-With": "XMLHttpRequest" },
      data: { role: "barb", kind: "auto", title: "Original Barb conversation A" },
    });
    expect(created.ok()).toBeTruthy();
    const originalThreadId = (await created.json()).id as string;

    await page.goto(`/staff?role=barb&thread=${encodeURIComponent(originalThreadId)}`);
    await page.getByTestId("ask-barb-toggle").click();
    const sidebar = page.getByTestId("barb-sidebar");
    await expect(sidebar.getByRole("region", {
      name: "Conversation thread with Original Barb conversation A",
    })).toBeVisible();

    const createdThreadResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/api/v1/staff/threads"),
    );
    await sidebar.getByRole("button", { name: "New chat" }).click();
    const createdThreadBody = await (await createdThreadResponse).json();
    const newThreadId = createdThreadBody.id as string;
    expect(newThreadId).not.toBe(originalThreadId);
    await expect(page).toHaveURL(new RegExp(`thread=${newThreadId}`));
    await expect(sidebar.getByRole("region", {
      name: "Conversation thread with Conversation with Barb",
    })).toBeVisible();

    await sidebar.getByRole("button", { name: "Open Projects" }).click();
    await expect(page).toHaveURL(new RegExp(`/projects[?][^#]*thread=${newThreadId}`));
    await sidebar.getByRole("button", { name: "Close Ask Barb" }).click();
    await page.getByTestId("ask-barb-toggle").click();
    await expect(sidebar.getByRole("region", {
      name: "Conversation thread with Conversation with Barb",
    })).toBeVisible();

    await page.setViewportSize({ width: 390, height: 844 });
    const askBarb = page.getByTestId("mobile-ask-barb");
    const mobileSidebar = page.getByTestId("barb-sidebar");
    await expect(askBarb).toBeVisible();
    if (await mobileSidebar.isVisible()) {
      await mobileSidebar.getByRole("button", { name: "Close Ask Barb" }).click();
    }
    await askBarb.click();
    await expect(mobileSidebar).toBeVisible();
    await expect(mobileSidebar.getByRole("region", {
      name: "Conversation thread with Conversation with Barb",
    })).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/projects[?][^#]*thread=${newThreadId}`));
  });
});
