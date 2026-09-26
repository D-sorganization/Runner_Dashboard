/**
 * Staff Console POSTs send a JSON object, not a JSON string (#1341).
 *
 * `apiRequest` serialises `body` itself. These three helpers serialised it
 * first, so the backend received `"{\"role\":...}"` and rejected every
 * new thread, message and approval with 422. The Staff Console e2e suite
 * found it; component tests mock these helpers and could not.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createThread, decideActionProposal, postThreadMessage } from "../staffApi";

describe("staffApi request bodies", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function sentBody(): unknown {
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    return JSON.parse(init.body as string);
  }

  it("createThread sends the thread fields as an object", async () => {
    await createThread({ role: "e2e-analyst", kind: "direct", title: "T" });
    expect(sentBody()).toEqual({ role: "e2e-analyst", kind: "direct", title: "T" });
  });

  it("postThreadMessage sends the message as an object", async () => {
    await postThreadMessage("th_1", { body: "hello" });
    expect(sentBody()).toEqual({ body: "hello" });
  });

  it("decideActionProposal sends the decision as an object", async () => {
    await decideActionProposal("prop_1", "approved", "ok");
    expect(sentBody()).toEqual({ decision: "approved", reason: "ok", execute: true });
  });
});
