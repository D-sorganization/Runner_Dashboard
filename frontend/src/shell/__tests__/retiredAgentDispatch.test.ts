/**
 * AgentDispatch is retired (#1499, SC-G5-3; superseded by the Staff Console
 * composer and Advanced dispatch form).
 *
 * Contract:
 *  - it has no nav entry in navRegistry;
 *  - every old address (/work/agent-dispatch, /agent-dispatch, /t/agent-dispatch)
 *    redirects to the Staff Console ("/") instead of 404.
 */
import { describe, expect, it } from "vitest";
import { navItemById } from "../navRegistry";
import { getTabRedirect } from "../routing";

describe("retired AgentDispatch (#1499)", () => {
  it("has no nav entry in navRegistry", () => {
    expect(navItemById("agent-dispatch")).toBeUndefined();
  });

  it.each([
    "/work/agent-dispatch",
    "/agent-dispatch",
    "/t/agent-dispatch",
    "/work/agent-dispatch/",
    "/agent-dispatch/",
  ])("redirects %s to the Staff Console", (path) => {
    expect(getTabRedirect(path)).toEqual({ to: "/", label: "Staff Console" });
  });
});
