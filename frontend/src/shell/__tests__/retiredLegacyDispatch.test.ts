/**
 * QuickDispatch and Jules remediation dispatch are retired (#1503, SC-G5-6;
 * superseded by the Staff Console and Staff Requests API).
 *
 * Contract:
 *  - QuickDispatch popover and remediationJules helper are retired;
 *  - Old addresses (/work/quick-dispatch, /quick-dispatch, /t/quick-dispatch)
 *    redirect to the Staff Console ("/") instead of 404.
 */
import { describe, expect, it } from "vitest";
import { getTabRedirect } from "../routing";

describe("retired QuickDispatch (#1503)", () => {
  it.each([
    "/work/quick-dispatch",
    "/quick-dispatch",
    "/t/quick-dispatch",
    "/work/quick-dispatch/",
    "/quick-dispatch/",
  ])("redirects %s to the Staff Console", (path) => {
    expect(getTabRedirect(path)).toEqual({ to: "/", label: "Staff Console" });
  });
});
