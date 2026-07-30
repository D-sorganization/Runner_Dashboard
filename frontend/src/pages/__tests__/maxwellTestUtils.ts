import { vi } from "vitest";
import type { MaxwellStatus } from "../MaxwellPage";

export function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(""),
    body: null,
  } as unknown as Response;
}

export function stubMaxwellFetch(
  opts: { tasks?: unknown[]; contract?: string; chatReply?: string } = {},
): void {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (String(url).includes("/api/maxwell/tasks")) {
        return Promise.resolve(jsonResponse({ tasks: opts.tasks || [] }));
      }
      if (String(url).includes("/api/maxwell/version")) {
        return Promise.resolve(jsonResponse({ contract: opts.contract || "" }));
      }
      if (String(url).includes("/api/maxwell/chat")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          body: null,
          text: () => Promise.resolve(opts.chatReply ?? "pong"),
          json: () => Promise.resolve({}),
        } as unknown as Response);
      }
      return Promise.resolve(jsonResponse({}));
    }),
  );
}

export const RUNNING: MaxwellStatus = {
  status: "running",
  http_reachable: true,
  binary_found: true,
  binary_path: "/usr/bin/maxwell",
};

export const STOPPED: MaxwellStatus = {
  status: "stopped",
  http_reachable: false,
  binary_found: false,
};
