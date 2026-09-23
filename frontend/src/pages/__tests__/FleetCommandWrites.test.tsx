// @vitest-environment jsdom
/**
 * Write-path tests for the Fleet Command tab (#1243).
 *
 * 1. Directives: the PUT carries the `version` the GET returned; a 409 (someone
 *    else saved first) shows a reload prompt instead of overwriting their list.
 * 2. Messages: the operator session registers presence on the board before its
 *    first send (RM drops messages from unregistered senders), and a 409
 *    "register presence first" answer is shown as a clear error.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FleetCommandPage } from "../FleetCommand";
import { operatorSession } from "../FleetCommand/fleetApi";

afterEach(cleanup);
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const DIRECTIVE = {
  id: "d1",
  text: "Finish the coordination API first",
  repo: "*",
  priority: 1,
  expires: null,
};

type Reply = { status: number; body: unknown } | undefined;
type Handler = (url: string, opts?: RequestInit) => Reply;

function stubFetch(handler: Handler) {
  const fetchMock = vi.fn((url: string, opts?: RequestInit) => {
    const reply = handler(url, opts) ?? {
      status: 404,
      body: { detail: "Not Found" },
    };
    return Promise.resolve({
      ok: reply.status >= 200 && reply.status < 300,
      status: reply.status,
      json: () => Promise.resolve(reply.body),
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function calls(fetchMock: ReturnType<typeof vi.fn>, method: string) {
  return fetchMock.mock.calls
    .filter(([, o]) => (o as RequestInit | undefined)?.method === method)
    .map(([url, o]) => ({
      url: String(url),
      body: JSON.parse(String((o as RequestInit).body)),
    }));
}

describe("FleetCommandPage — directive versions", () => {
  it("sends the loaded version with the PUT", async () => {
    const fetchMock = stubFetch((url, opts) => {
      if (url !== "/api/priorities/directives") return undefined;
      if (opts?.method === "PUT")
        return {
          status: 200,
          body: { directives: [DIRECTIVE], version: "v2" },
        };
      return { status: 200, body: { directives: [DIRECTIVE], version: "v1" } };
    });
    render(<FleetCommandPage />);
    await waitFor(() => expect(screen.getByTestId("directive-0")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox", { name: "Directive 1 priority" }), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls(fetchMock, "PUT")).toHaveLength(1));
    const body = calls(fetchMock, "PUT")[0].body;
    expect(body.version).toBe("v1");
    expect(body.directives[0]).not.toHaveProperty("set_by");
  });

  it("shows a reload prompt on 409 and reloads the list", async () => {
    let gets = 0;
    const fetchMock = stubFetch((url, opts) => {
      if (url !== "/api/priorities/directives") return undefined;
      if (opts?.method === "PUT")
        return {
          status: 409,
          body: { detail: "directives changed since version v1" },
        };
      gets += 1;
      const text = gets === 1 ? DIRECTIVE.text : "Someone else's directive";
      return {
        status: 200,
        body: { directives: [{ ...DIRECTIVE, text }], version: `v${gets}` },
      };
    });
    render(<FleetCommandPage />);
    await waitFor(() => expect(screen.getByTestId("directive-0")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox", { name: "Directive 1 priority" }), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByTestId("directives-conflict")).toBeInTheDocument());
    expect(screen.getByTestId("directives-conflict")).toHaveTextContent("changed");
    fireEvent.click(screen.getByRole("button", { name: "Reload directives" }));
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "Directive 1 text" })).toHaveValue("Someone else's directive"),
    );
    expect(screen.queryByTestId("directives-conflict")).not.toBeInTheDocument();
    expect(calls(fetchMock, "PUT")).toHaveLength(1);
  });
});

describe("FleetCommandPage — operator presence before messages", () => {
  function fillAndSend() {
    fireEvent.click(screen.getByRole("tab", { name: "Messages" }));
    fireEvent.change(screen.getByLabelText("Repo"), {
      target: { value: "Runner_Dashboard" },
    });
    fireEvent.change(screen.getByLabelText("Issue"), {
      target: { value: "1243" },
    });
    fireEvent.change(screen.getByLabelText("To"), {
      target: { value: "codex-7" },
    });
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Please rebase" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
  }

  it("registers presence once, then sends as the operator session", async () => {
    const session = operatorSession();
    expect(session).toMatch(/^operator-\d{8}$/);
    const fetchMock = stubFetch((url, opts) =>
      opts?.method === "POST" && url.startsWith("/api/coordination/") ? { status: 200, body: { ok: true } } : undefined,
    );
    render(<FleetCommandPage />);
    fillAndSend();
    await waitFor(() => expect(calls(fetchMock, "POST")).toHaveLength(2));
    const [presence, message] = calls(fetchMock, "POST");
    expect(presence).toEqual({
      url: "/api/coordination/presence",
      body: {
        agent: "user",
        session,
        repo: "Runner_Dashboard",
        issue: 1243,
        branch: "main",
        ttl_hours: 2,
      },
    });
    expect(message).toEqual({
      url: "/api/coordination/messages",
      body: {
        session,
        repo: "Runner_Dashboard",
        to: "codex-7",
        text: "Please rebase",
      },
    });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Sent to codex-7."));

    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Again" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(calls(fetchMock, "POST")).toHaveLength(3));
    expect(calls(fetchMock, "POST")[2].url).toBe("/api/coordination/messages");
  });

  it("requires an issue before sending", () => {
    stubFetch(() => undefined);
    render(<FleetCommandPage />);
    fireEvent.click(screen.getByRole("tab", { name: "Messages" }));
    fireEvent.change(screen.getByLabelText("Repo"), {
      target: { value: "Runner_Dashboard" },
    });
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "hi" },
    });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("explains a 409 from the message endpoint", async () => {
    stubFetch((url, opts) => {
      if (opts?.method !== "POST") return undefined;
      if (url === "/api/coordination/presence") return { status: 200, body: { ok: true } };
      if (url === "/api/coordination/messages")
        return {
          status: 409,
          body: {
            detail: {
              error: "sender session has no presence; register presence first",
            },
          },
        };
      return undefined;
    });
    render(<FleetCommandPage />);
    fillAndSend();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("register presence first"));
    expect(screen.getByRole("alert")).toHaveTextContent("not registered on the board");
  });
});
