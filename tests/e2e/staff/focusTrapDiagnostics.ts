import {
  expect,
  type Locator,
  type Page,
  type Request,
  type Response,
  type TestInfo,
} from "@playwright/test";

export const SIDEBAR_FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

type RequestEntry = {
  atMs: number;
  method: string;
  route: string;
  state: "pending" | "complete" | "failed";
  status?: number;
  durationMs?: number;
};

function safeMethod(method: string): string {
  const value = method.toUpperCase();
  return ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"].includes(
    value,
  )
    ? value
    : "OTHER";
}

function safeRoute(rawUrl: string): string {
  let path: string;
  try {
    path = new URL(rawUrl).pathname;
  } catch {
    return "other";
  }
  if (path === "/api/v1/staff/threads") return "staff.threads";
  if (/^\/api\/v1\/staff\/threads\/[^/]+\/stream$/.test(path))
    return "staff.thread_stream";
  if (/^\/api\/v1\/staff\/threads\/[^/]+(?:\/messages)?$/.test(path))
    return "staff.thread";
  if (/^\/api\/v1\/staff\/proposals\/[^/]+\/decide$/.test(path))
    return "staff.proposal_decision";
  if (/^\/api\/v1\/staff\/proposals\/[^/]+$/.test(path))
    return "staff.proposal";
  return "other";
}

function safe<T>(callback: (value: T) => void) {
  return (value: T) => {
    try {
      callback(value);
    } catch {
      /* diagnostics never mask assertions */
    }
  };
}

export function isBarbThreadList(value: Request | Response): boolean {
  const request = "request" in value ? value.request() : value;
  const url = new URL(request.url());
  return (
    request.method() === "GET" &&
    url.pathname === "/api/v1/staff/threads" &&
    url.searchParams.get("role") === "barb"
  );
}

/** Page events only; direct APIRequestContext calls are not included. */
export class FailureRequestSummary {
  private readonly started = performance.now();
  private readonly rows: RequestEntry[] = [];
  private readonly pending = new Map<Request, RequestEntry>();
  private readonly cleanup: Array<() => void> = [];
  private dropped = 0;

  constructor(private readonly limit = 60) {}

  watch(page: Page): void {
    const begin = safe((request: Request) => {
      if (!new URL(request.url()).pathname.startsWith("/api/")) return;
      if (this.rows.length >= this.limit) {
        this.dropped += 1;
        return;
      }
      const row: RequestEntry = {
        atMs: Math.round(performance.now() - this.started),
        method: safeMethod(request.method()),
        route: safeRoute(request.url()),
        state: "pending",
      };
      this.rows.push(row);
      this.pending.set(request, row);
    });
    const complete = safe((response: Response) =>
      this.finish(response.request(), "complete", response.status()),
    );
    const fail = safe((request: Request) => this.finish(request, "failed"));
    const remove = () => {
      try {
        page.off("request", begin);
      } catch {
        /* best effort */
      }
      try {
        page.off("response", complete);
      } catch {
        /* best effort */
      }
      try {
        page.off("requestfailed", fail);
      } catch {
        /* best effort */
      }
    };
    try {
      page.on("request", begin);
      page.on("response", complete);
      page.on("requestfailed", fail);
      this.cleanup.push(remove);
    } catch {
      remove();
    }
  }

  private finish(
    request: Request,
    state: RequestEntry["state"],
    status?: number,
  ): void {
    const row = this.pending.get(request);
    if (!row) return;
    row.state = state;
    if (Number.isInteger(status) && status! >= 100 && status! <= 599)
      row.status = status;
    row.durationMs = Math.max(
      0,
      Math.round(performance.now() - this.started) - row.atMs,
    );
    this.pending.delete(request);
  }

  snapshot() {
    return {
      dropped: this.dropped,
      requests: this.rows.map((row) => ({ ...row })),
    };
  }

  async attach(testInfo: TestInfo): Promise<void> {
    try {
      await testInfo.attach("staff-request-summary.json", {
        body: JSON.stringify(this.snapshot()),
        contentType: "application/json",
      });
    } catch {
      /* keep the original test result */
    }
  }

  dispose(): void {
    for (const remove of this.cleanup.splice(0)) {
      try {
        remove();
      } catch {
        /* best effort */
      }
    }
  }
}

export async function attachSidebarFocusState(
  testInfo: TestInfo,
  drawer: Locator,
  name: string,
): Promise<void> {
  let state: unknown;
  try {
    state = await drawer.evaluate((panel, selector) => {
      const flags = (node: Element) => {
        const style = getComputedStyle(node);
        return {
          hidden: (node as HTMLElement).hidden,
          ariaHidden: node.getAttribute("aria-hidden") === "true",
          inert: (node as HTMLElement).inert || node.hasAttribute("inert"),
          computedVisible:
            style.display !== "none" && style.visibility === "visible",
        };
      };
      const describe = (element: Element | null) => {
        if (!element) return null;
        const ancestors = [];
        for (let node = element.parentElement; node; node = node.parentElement)
          ancestors.push(flags(node));
        const own = flags(element);
        const label = element.getAttribute("aria-label") ?? "";
        const kind = element.getAttribute("data-focus-diagnostic");
        const disabled =
          "disabled" in element && (element as HTMLButtonElement).disabled;
        return {
          tag: ["BUTTON", "INPUT", "TEXTAREA", "A"].includes(element.tagName)
            ? element.tagName.toLowerCase()
            : "other",
          label: ["Ask Barb", "Close Ask Barb", "Send message"].includes(label)
            ? label
            : label.startsWith("Voice input")
              ? "Voice input"
              : null,
          diagnosticKind: [
            "css-visibility",
            "ancestor-aria-hidden",
            "inert",
          ].includes(kind ?? "")
            ? kind
            : null,
          candidate: element.matches(selector),
          active: element === document.activeElement,
          eligible:
            element.matches(selector) &&
            !disabled &&
            [own, ...ancestors].every(
              (item) =>
                !item.hidden &&
                !item.ariaHidden &&
                !item.inert &&
                item.computedVisible,
            ),
          disabled,
          ...own,
          ancestors,
        };
      };
      return {
        activeElement: describe(document.activeElement),
        candidates: Array.from(
          panel.querySelectorAll<HTMLElement>(selector),
          (element, index) => ({ index, ...(describe(element) ?? {}) }),
        ),
      };
    }, SIDEBAR_FOCUSABLE);
  } catch {
    state = { diagnosticAvailable: false };
  }
  try {
    await testInfo.attach(name, {
      body: JSON.stringify(state),
      contentType: "application/json",
    });
  } catch {
    /* preserve the original assertion */
  }
}

export async function expectFocusWithState(
  testInfo: TestInfo,
  target: Locator,
  drawer: Locator,
  name: string,
): Promise<void> {
  try {
    await expect(target).toBeFocused();
  } catch (error) {
    await attachSidebarFocusState(testInfo, drawer, name);
    throw error;
  }
}
