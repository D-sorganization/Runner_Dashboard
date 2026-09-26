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
import type { Page } from "@playwright/test";
import { conversation, expect, openThread, send, test } from "./fixtures";

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

/** Occurrences of `needle` in the conversation log's visible text. */
async function occurrences(page: Page, needle: string): Promise<number> {
  const text = await conversation(page).innerText();
  return text.split(needle).length - 1;
}

test.describe("chat", () => {
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

  test("Barb's handoff reply is shown", async ({ page }) => {
    await openThread(page, "Ask Barb (auto-route)");
    const before = await occurrences(page, "handing it over");
    await send(page, `analyse the queue ${nonce()} [[e2e:handoff]]`);

    await expect.poll(() => occurrences(page, "handing it over")).toBe(before + 1);
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

    await expect(alerts).toHaveCount(before + 1);
    await expect(alerts.last()).toContainText("claude auth login");
    await expect(alerts.last()).not.toContainText("systemctl --user start ollama");
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

    await expect(page.getByTestId(SEND_ERROR)).toBeVisible();
    await expect(page.getByRole("textbox", { name: COMPOSER })).toHaveValue(text);
    expect(await occurrences(page, reply(text))).toBe(0);
  });

  test.describe("as a viewer", () => {
    test.use({ principal: "viewer" });

    test("a send without write permission is refused and keeps the draft", async ({ page }) => {
      const text = `viewer ${nonce()}`;
      await openThread(page, "E2E Analyst");
      await send(page, text);

      await expect(page.getByTestId(SEND_ERROR)).toBeVisible();
      await expect(page.getByRole("textbox", { name: COMPOSER })).toHaveValue(text);
    });
  });
});
