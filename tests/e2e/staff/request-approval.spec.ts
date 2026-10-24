import { test, expect } from "./fixtures";
import { dispatchRequesterHeaders } from "./identity";

test("a recorded work request can be reviewed and denied from its conversation", async ({ page, request }) => {
  const backend = process.env.STAFF_E2E_BACKEND_URL
    ?? `http://127.0.0.1:${process.env.STAFF_E2E_BACKEND_PORT ?? "5092"}`;
  const response = await request.post(`${backend}/api/v1/staff/requests`, {
    headers: { ...dispatchRequesterHeaders, "X-Requested-With": "XMLHttpRequest" },
    data: {
      kind: "staff.dispatch",
      role: "research-scout",
      target: { repo: "AffineDrift" },
      prompt: "Read-only project audit fixture. Wait for operator approval.",
    },
  });
  expect(response.status(), await response.text()).toBe(202);
  const recorded = await response.json();
  await page.goto(`/staff?thread=${recorded.thread_id}`);
  const thread = page.getByRole("region", { name: /^Conversation thread with/ });
  await expect(thread.getByText("staff.dispatch", { exact: true })).toBeVisible();
  await expect(thread.getByRole("button", { name: "Approve", exact: true })).toBeEnabled();
  await thread.getByRole("button", { name: "Deny", exact: true }).click();
  await expect(thread.getByText(/^✓ Denied by principal:e2e-operator/)).toBeVisible();
  await page.reload();
  await expect(thread.getByText(/^✓ Denied by principal:e2e-operator/)).toBeVisible();
  const detail = await request.get(`${backend}/api/v1/staff/proposals/${recorded.proposal_id}`, {
    headers: dispatchRequesterHeaders,
  });
  expect((await detail.json()).state).toBe("denied");
});
