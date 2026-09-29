/**
 * Playwright mobile viewport specs for runner-dashboard (EPIC #186 — M16).
 *
 * These tests run against the mobile Playwright projects defined in
 * playwright.config.ts (chromium-iphone-12, chromium-pixel-5,
 * chromium-epic-compact-375, chromium-epic-standard-412). The config derives
 * those projects from tests/frontend/mobile/viewport_profiles.json, so this
 * suite is always in sync with the viewport definitions.
 *
 * Tests verify the MobileShell renders correctly at 375 px and 412 px widths,
 * bottom navigation is reachable and touch-target-compliant, and the
 * 3-tap dispatch flow reaches the Remediation tab.
 *
 * Run locally (desktop projects are skipped by Playwright project filtering):
 *   ./start-dashboard.sh
 *   npm run test:e2e -- --project="chromium-epic-compact-375"
 */

import { test, expect, type Locator } from "@playwright/test";

// ---------------------------------------------------------------------------
// Mobile shell — bottom navigation
// ---------------------------------------------------------------------------

test.describe("Mobile shell bottom navigation", () => {
  test.skip(({ isMobile }) => !isMobile, "Mobile viewport only");

  test("renders bottom tab bar at mobile viewport", async ({ page }) => {
    await page.goto("/");
    // MobileShell bottom nav should be visible at mobile widths
    const nav = page.locator('[role="tablist"]');
    await expect(nav).toBeVisible();
  });

  test("Fleet tab is default active tab", async ({ page }) => {
    await page.goto("/");
    const fleetTab = page.locator('[role="tab"][aria-selected="true"]');
    await expect(fleetTab).toContainText(/fleet/i);
  });

  test("bottom nav tabs are at least 44px tall (touch target)", async ({
    page,
  }) => {
    await page.goto("/");
    const tabs = page.locator('[role="tab"]');
    const count = await tabs.count();
    for (let i = 0; i < count; i++) {
      const box = await tabs.nth(i).boundingBox();
      if (box) {
        expect(box.height).toBeGreaterThanOrEqual(44);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// Mobile 3-tap dispatch flow
// ---------------------------------------------------------------------------

test.describe("Mobile 3-tap dispatch flow", () => {
  test.skip(({ isMobile }) => !isMobile, "Mobile viewport only");

  test("Remediation tab is reachable via bottom nav", async ({ page }) => {
    await page.goto("/");
    const remediationTab = page.locator('[role="tab"]', {
      hasText: /remediation/i,
    });
    await remediationTab.click();
    // Should show remediation content without crashing
    await expect(page.locator("body")).not.toBeEmpty();
  });
});

// ---------------------------------------------------------------------------
// Mobile accessibility
// ---------------------------------------------------------------------------

test.describe("Mobile accessibility", () => {
  test.skip(({ isMobile }) => !isMobile, "Mobile viewport only");

  test("all bottom nav tabs have aria-label or accessible text", async ({
    page,
  }) => {
    await page.goto("/");
    const tabs = page.locator('[role="tab"]');
    const count = await tabs.count();
    for (let i = 0; i < count; i++) {
      const tab = tabs.nth(i);
      const label = await tab.getAttribute("aria-label");
      const text = await tab.textContent();
      expect(label || text?.trim()).toBeTruthy();
    }
  });
});

// ---------------------------------------------------------------------------
// Mobile Staff Console (SC-D8)
// ---------------------------------------------------------------------------

/** Mock the Staff Hub endpoints the mobile console reads (Barb thread + one proposal). */
async function mockStaffApi(page: import("@playwright/test").Page): Promise<void> {
  const thread = {
    id: "thread-barb-auto",
    title: "Conversation with Barb",
    kind: "auto",
    participants: ["barb"],
    status: "active",
  };
  const pendingProposalMessage = {
    id: "msg-prop-1",
    thread_id: "thread-barb-auto",
    author: "barb",
    author_kind: "staff",
    kind: "action_proposal",
    body_md: "Trim stale worktrees",
    meta: {
      proposal: {
        id: "prop-123",
        thread_id: "thread-barb-auto",
        action_name: "maintenance.trim_worktrees",
        description: "Trim stale worktrees",
        risk_level: "medium",
        status: "proposed",
      },
    },
    created_at: new Date().toISOString(),
  };

  await page.route("**/api/v1/staff/threads/*/stream**", async (route) => {
    await route.fulfill({ status: 204, body: "" });
  });

  await page.route("**/api/v1/staff/roster", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        roles: [
          {
            name: "barb",
            title: "Barb",
            summary: "Fleet Orchestrator",
            group: "leadership",
            valid: true,
          },
        ],
      }),
    });
  });

  await page.route("**/api/v1/staff/threads?*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ threads: [thread] }),
    });
  });

  await page.route("**/api/v1/staff/threads/thread-barb-auto", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ thread, messages: [pendingProposalMessage] }),
    });
  });

  await page.route("**/api/v1/staff/threads/thread-barb-auto/messages", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ id: "msg-user-1", body_md: "Please run disk hygiene" }),
      });
    } else {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          messages: [
            {
              id: "msg-1",
              thread_id: "thread-barb-auto",
              author: "barb",
              author_kind: "staff",
              kind: "text",
              body_md: "Hello! I am Barb.",
              created_at: new Date().toISOString(),
            },
            pendingProposalMessage,
          ],
        }),
      });
    }
  });

  await page.route("**/api/v1/staff/inbox", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        count: 1,
        counts: {
          total: 1,
          approvals: 1,
          needs_input: 0,
          escalations: 0,
          project_decisions: 0,
          board_proposals: 0,
          auth_sign_ins: 0,
        },
        sources: { approval: { status: "ok", count: 1 } },
        generated_at: new Date().toISOString(),
        items: [
          {
            id: "approval-1",
            source: "approval",
            title: "Approve disk hygiene",
            summary: "Review Barb's proposed maintenance action",
            severity: "medium",
            created_at: new Date().toISOString(),
            link: "/staff?thread=thread-barb-auto",
          },
        ],
      }),
    });
  });

  await page.route("**/api/v1/staff/runs?*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ count: 0, runs: [] }),
    });
  });

  await page.route("**/api/v1/staff/proposals/prop-123/decide", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, status: "approved" }),
    });
  });
}

/** The hit-tested center belongs to the control instead of an overlapping layer. */
async function ownsItsCentre(locator: Locator): Promise<boolean> {
  return locator.evaluate((el) => {
    const box = el.getBoundingClientRect();
    const top = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
    return el.contains(top);
  });
}

test.describe("Mobile Staff Console (SC-D8)", () => {
  test.use({ serviceWorkers: "block" });

  test.skip(({ isMobile }) => !isMobile, "Mobile viewport only");

  test("open Barb, send message, approve an action, and open from push deep link", async ({
    page,
  }) => {
    // 1. Mock staff endpoints
    await mockStaffApi(page);

    // 2. Open mobile dashboard and navigate to Staff Console
    await page.goto("/");
    const staffTab = page.locator('[role="tab"]', { hasText: /staff/i });
    if (await staffTab.isVisible()) {
      await staffTab.click();
    }

    // Verify roster view with Ask Barb
    const askBarb = page.locator('[data-testid="staff-mobile-ask-barb"]');
    await expect(askBarb).toBeVisible();

    // 3. Open Barb (transitions full-screen to thread view)
    await askBarb.click();
    const threadView = page.locator('[data-testid="staff-mobile-thread"]');
    await expect(threadView).toBeVisible();
    await expect(page.locator('[data-testid="staff-mobile-back-btn"]')).toBeVisible();

    // 4. Send a message in the safe-area composer
    const composer = page.locator('[data-testid="staff-mobile-composer-container"] textarea');
    await expect(composer).toBeVisible();
    await composer.fill("Please run disk hygiene");
    const sendBtn = page.getByRole("button", { name: "Send message" });
    await sendBtn.click();

    // 5. Approve an action proposal
    const approveBtn = page.locator('button:has-text("Approve")').first();
    await expect(approveBtn).toBeVisible();
    await approveBtn.click();

    // 6. Open from push deep link directly to thread
    await page.goto("/staff?thread=thread-barb-auto");
    await expect(page.locator('[data-testid="staff-mobile-thread"]')).toBeVisible();
    await expect(page).toHaveURL(/thread=thread-barb-auto/);
    await expect(page.getByRole("log", { name: "Conversation messages" })).toContainText("Trim stale worktrees");
  });

  test("keyboard walkthrough: open Barb, focus follows the view, back returns to search (SC-D9)", async ({
    page,
  }) => {
    await mockStaffApi(page);
    await page.goto("/staff");

    const askBarb = page.getByTestId("staff-mobile-ask-barb");
    await expect(askBarb).toBeVisible();
    await askBarb.focus();
    await page.keyboard.press("Enter");

    // Opening a thread moves focus to its heading, not the composer.
    await expect(page.getByTestId("staff-mobile-thread")).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toBeFocused();

    // The message log is reachable by keyboard and is a polite live region.
    const log = page.getByRole("log", { name: "Conversation messages" });
    await expect(log).toHaveAttribute("aria-live", "polite");
    await log.focus();
    await expect(log).toBeFocused();

    // Back returns focus to the roster search box.
    await page.getByTestId("staff-mobile-back-btn").focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("searchbox", { name: "Search staff roles" })).toBeFocused();
  });
});

test.describe("Staff Console bottom-nav regression (#1805)", () => {
  // Its "New version" toast can sit over the tabs; this test is about the shell nav.
  test.use({ serviceWorkers: "block" });

  test("Console, Inbox, Runs and the composer are not covered by the shell nav (#1805)", async ({
    page,
  }) => {
    await mockStaffApi(page);

    for (const width of [320, 390, 430]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/staff");
      const shellContent = page.locator(".mobile-shell__content");
      await expect(shellContent).toBeVisible();
      await expect(shellContent.getByTestId("staff-mobile-root")).toBeVisible();

      for (const view of ["inbox", "runs", "roster"]) {
        const tab = page.getByTestId(`staff-mobile-tab-${view}`);
        expect(await ownsItsCentre(tab), `${view} tab at ${width}px`).toBe(true);
        await tab.click();
        await expect(tab).toHaveAttribute("aria-selected", "true");
        if (view === "inbox") {
          const inbox = shellContent.getByTestId("staff-mobile-inbox-view");
          await expect(inbox.getByText("Waiting on You")).toBeVisible();
          await expect(inbox.getByTestId("inbox-kind-counts")).toContainText("Approvals 1");
          const openInbox = inbox.getByTestId("inbox-open-btn");
          expect(await ownsItsCentre(openInbox), `Inbox open at ${width}px`).toBe(true);
          await openInbox.click();
          const inboxDrawer = inbox.getByRole("dialog", { name: "Waiting on you inbox" });
          await expect(inboxDrawer).toBeVisible();
          const approvalFilter = inboxDrawer.getByTestId("filter-tab-approval");
          await expect(approvalFilter).toBeVisible();
          expect(await ownsItsCentre(approvalFilter), `approval filter at ${width}px`).toBe(true);
          await approvalFilter.click();
          await expect(approvalFilter).toHaveAttribute("aria-selected", "true");
          await inbox.getByTestId("inbox-close-btn").click();
        } else if (view === "runs") {
          await expect(shellContent.getByTestId("staff-mobile-runs-view")).toBeVisible();
        }
      }

      const askBarb = page.getByTestId("staff-mobile-ask-barb");
      await askBarb.focus();
      await page.keyboard.press("Enter");
      const thread = page.getByTestId("staff-mobile-thread");
      await expect(thread).toBeVisible();
      await expect(thread.getByRole("heading", { level: 1 })).toBeFocused();
      await expect(page).toHaveURL(/thread=thread-barb-auto/);

      const composer = page.getByTestId("staff-mobile-composer-container").locator("textarea");
      await composer.fill("Viewport hit-target check");
      const send = page.getByRole("button", { name: "Send message" });
      await expect(send).toBeVisible();
      expect(await ownsItsCentre(send), `send button at ${width}px`).toBe(true);
      const sentMessage = page.waitForResponse(
        (response) => response.url().endsWith("/messages") && response.request().method() === "POST",
      );
      await send.click();
      expect((await sentMessage).ok(), `send request at ${width}px`).toBe(true);

      const approve = thread.getByRole("button", { name: "Approve" }).first();
      await expect(approve).toBeVisible();
      expect(await ownsItsCentre(approve), `approval button at ${width}px`).toBe(true);
      const approvalDecision = page.waitForResponse(
        (response) => response.url().endsWith("/proposals/prop-123/decide") && response.request().method() === "POST",
      );
      await approve.click();
      expect((await approvalDecision).ok(), `approval request at ${width}px`).toBe(true);

      // The address retains the selected conversation when the phone page is reopened.
      const threadUrl = page.url();
      await page.goto(threadUrl);
      await expect(page.getByTestId("staff-mobile-thread")).toBeVisible();
      await expect(page.getByRole("heading", { level: 1 })).toContainText(/Conversation with Barb/i);

      await page.getByTestId("staff-mobile-back-btn").focus();
      await page.keyboard.press("Enter");
      await expect(page.getByRole("searchbox", { name: "Search staff roles" })).toBeFocused();
    }
  });

  // CSS viewport widths stress layouts near 200% reflow; they do not apply
  // browser chrome zoom or alter DPR.
  for (const width of [160, 195, 215]) {
    test(`narrow shell tabs stay clear of the fixed navigation at ${width}px`, async ({ page }) => {
      await mockStaffApi(page);
      await page.setViewportSize({ width, height: 422 });
      await page.goto("/staff");

      const shellContent = page.locator(".mobile-shell__content");
      const shellNav = page.locator(".mobile-shell__nav");
      const staffRoot = shellContent.getByTestId("staff-mobile-root");
      await expect(staffRoot).toBeVisible();
      const reservedNav = await shellNav.boundingBox();
      const staffBounds = await staffRoot.boundingBox();
      expect(reservedNav, "fixed shell navigation is present").not.toBeNull();
      expect(staffBounds, "Staff Console has layout geometry").not.toBeNull();
      expect(staffBounds!.y + staffBounds!.height, "Staff Console fits above shell navigation")
        .toBeLessThanOrEqual(reservedNav!.y);

      for (const view of ["roster", "inbox", "runs"]) {
        const tab = page.getByTestId(`staff-mobile-tab-${view}`);
        expect(await ownsItsCentre(tab), `${view} hit target at ${width}px`).toBe(true);
        await tab.click();
        await expect(tab).toHaveAttribute("aria-selected", "true");
        expect(await ownsItsCentre(tab), `${view} click target at ${width}px`).toBe(true);
      }
    });
  }

  for (const width of [160, 195, 215, 320, 390, 430]) {
    test(`conversation heading and controls fit at ${width}px`, async ({ page }) => {
      await mockStaffApi(page);
      await page.setViewportSize({ width, height: 422 });
      await page.goto("/staff?thread=thread-barb-auto");
      const thread = page.getByTestId("staff-mobile-thread");
      await expect(thread).toBeVisible();
      const headerTargetsFit = await thread.locator(".staff-mobile__header").evaluate((header) => {
        const heading = header.querySelector("h1");
        const targets = [
          header.querySelector('[data-testid="staff-mobile-back-btn"]'),
          header.querySelector('[data-testid="staff-mobile-new-conversation-btn"]'),
          header.querySelector('[data-testid="staff-mobile-details-btn"]'),
        ];
        if (!heading || targets.some((target) => !target)) return { fits: false, boxes: [] };
        const headingBox = heading.getBoundingClientRect();
        const headerBox = header.getBoundingClientRect();
        const boxes = targets.map((target) => target!.getBoundingClientRect());
        const headingVisible = headingBox.width > 0 && headingBox.height >= 16 &&
          headingBox.left >= headerBox.left && headingBox.right <= headerBox.right &&
          headingBox.top >= headerBox.top && headingBox.bottom <= headerBox.bottom;
        const headingClear = boxes.every((box) =>
          headingBox.right <= box.left || box.right <= headingBox.left ||
          headingBox.bottom <= box.top || box.bottom <= headingBox.top,
        );
        const targetsFit = boxes.every((box, index) =>
          box.width >= 44 && box.height >= 44 &&
          boxes.slice(index + 1).every((other) =>
            box.right <= other.left || other.right <= box.left ||
            box.bottom <= other.top || other.bottom <= box.top,
          ),
        );
        return {
          fits: headingVisible && headingClear && targetsFit,
          heading: { x: headingBox.x, y: headingBox.y, width: headingBox.width, height: headingBox.height },
          boxes: boxes.map(({ x, y, width, height }) => ({ x, y, width, height })),
        };
      });
      expect(
        headerTargetsFit.fits,
        `heading and 44px controls stay within the header at ${width}px: ${JSON.stringify(headerTargetsFit)}`,
      ).toBe(true);
      for (const testId of [
        "staff-mobile-back-btn",
        "staff-mobile-new-conversation-btn",
        "staff-mobile-details-btn",
      ]) {
        const target = thread.getByTestId(testId);
        expect(await ownsItsCentre(target), `${testId} center at ${width}px`).toBe(true);
      }

      const approve = thread.getByRole("button", { name: "Approve" }).first();
      await expect(approve).toBeVisible();
      await approve.evaluate((el) => el.scrollIntoView({ block: "center" }));
      expect(await ownsItsCentre(approve), `approval center at ${width}px`).toBe(true);
      const approvalDecision = page.waitForResponse(
        (response) => response.url().endsWith("/proposals/prop-123/decide") && response.request().method() === "POST",
      );
      await approve.click();
      expect((await approvalDecision).ok()).toBe(true);
    });
  }

  test("keyboard-height resize keeps focused composer controls reachable", async ({ page }) => {
    await mockStaffApi(page);
    await page.setViewportSize({ width: 320, height: 844 });
    await page.goto("/staff");
    await page.getByTestId("staff-mobile-ask-barb").click();
    await expect(page.getByTestId("staff-mobile-thread")).toBeVisible();
    const composer = page.getByTestId("staff-mobile-composer-container").locator("textarea");
    await composer.fill("Keyboard resize check");
    const send = page.getByRole("button", { name: "Send message" });
    await page.setViewportSize({ width: 320, height: 844 });
    await expect(composer).toBeFocused();
    await expect(send).toBeVisible();
    const sentMessage = page.waitForResponse(
      (response) => response.url().endsWith("/messages") && response.request().method() === "POST",
    );
    await send.click();
    expect((await sentMessage).ok()).toBe(true);

    await composer.focus();
    await page.setViewportSize({ width: 320, height: 422 });
    await expect(composer).toBeFocused();
    await expect(send).toBeVisible();
    expect(await ownsItsCentre(send), "send at reduced keyboard viewport height").toBe(true);

    await page.setViewportSize({ width: 320, height: 844 });
    await expect(composer).toBeFocused();
    await expect(page.getByRole("heading", { level: 1 })).toContainText(/Conversation with Barb/i);
  });
});
