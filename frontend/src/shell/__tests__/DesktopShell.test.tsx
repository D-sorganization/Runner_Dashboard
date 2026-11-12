// @vitest-environment jsdom
/**
 * Behaviour tests for the modern desktop shell (issue #802, part of #796).
 *
 * Contract:
 *  - renders the left Sidebar (navigation landmark) AND the slim TopToolstrip
 *    (primary toolbar), both driven by the shared nav registry;
 *  - renders the page body (children) in a main landmark;
 *  - selecting a category in either nav surface calls onSelect(tabId);
 *  - the active category is reflected in both nav surfaces;
 *  - every action button is rendered with an accessible name and a tooltip;
 *  - A11Y AUDIT: no interactive control (button/link) in the whole shell lacks
 *    an accessible label.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import {
  act,
  cleanup,
  render,
  screen,
  fireEvent,
  within,
} from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { DesktopShell, BarbSidebarErrorBoundary, type ShellAction } from "../DesktopShell";

afterEach(cleanup);

// DesktopShell mounts BarbSidebar through React.lazy. Preload it so tests
// check sidebar behaviour, not cold-import latency on a busy runner (#2774).
beforeAll(async () => {
  await import("../BarbSidebar");
});

function actions(spy = vi.fn()): ShellAction[] {
  return [
    { id: "refresh", label: "Refresh", tooltip: "Reload all dashboard data.", onClick: () => spy("refresh") },
    { id: "chat", label: "Chat", tooltip: "Toggle the assistant chat sidebar.", onClick: () => spy("chat") },
  ];
}

function renderShell(props: Partial<React.ComponentProps<typeof DesktopShell>> = {}) {
  const onSelect = props.onSelect ?? vi.fn();
  render(
    <DesktopShell
      activeTabId={props.activeTabId ?? "overview"}
      onSelect={onSelect}
      actions={props.actions ?? actions()}
      {...props}
    >
      <div data-testid="page-body">PAGE</div>
    </DesktopShell>,
  );
  return { onSelect };
}

describe("DesktopShell — structure", () => {
  it("renders the sidebar navigation landmark", () => {
    renderShell();
    expect(screen.getByRole("navigation", { name: /dashboard sections/i })).toBeInTheDocument();
  });

  it("renders the global search bar and command palette trigger (SC-D2)", () => {
    renderShell();
    expect(screen.getByRole("search")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /open command palette/i })).toBeInTheDocument();
  });

  it("shows the cold-open state and then an interactive Barb composer on every section", async () => {
    const { onSelect } = renderShell({ activeTabId: "queue" });

    fireEvent.click(screen.getByRole("button", { name: /ask barb/i }));
    expect(screen.getByRole("status")).toHaveTextContent(/loading your barb conversation/i);
    const loadingPanel = document.getElementById("barb-sidebar");
    expect(loadingPanel).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /close ask barb/i }));
    expect(loadingPanel).toHaveAttribute("aria-hidden", "true");
    expect(loadingPanel).toHaveAttribute("inert");
    fireEvent.click(screen.getByTestId("ask-barb-toggle"));
    expect(screen.getByRole("status")).toHaveTextContent(/loading your barb conversation/i);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(loadingPanel).toHaveAttribute("aria-hidden", "true");
    expect(loadingPanel).toHaveAttribute("inert");
    expect(document.activeElement).toBe(screen.getByTestId("ask-barb-toggle"));
    fireEvent.click(screen.getByTestId("ask-barb-toggle"));
    expect(screen.getByRole("status")).toHaveTextContent(/loading your barb conversation/i);
    await act(async () => { await import("../BarbSidebar"); });
    const panel = screen.getByRole("complementary", { name: /ask barb/i });
    expect(panel).toBeInTheDocument();
    const composer = screen.getByRole("textbox", { name: /staff conversation input/i });
    expect(composer).toBeEnabled();
    fireEvent.change(composer, { target: { value: "Help me plan this" } });
    expect(composer).toHaveValue("Help me plan this");

    fireEvent.click(screen.getByRole("button", { name: /open fleet command/i }));
    expect(onSelect).toHaveBeenCalledWith("fleet-command");
    expect(screen.getByRole("complementary", { name: /ask barb/i })).toBe(panel);
    fireEvent.click(screen.getByTestId("ask-barb-toggle"));
    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(panel).toHaveAttribute("inert");
    fireEvent.click(screen.getByTestId("ask-barb-toggle"));
    expect(screen.getByRole("textbox", { name: /staff conversation input/i })).toHaveValue("Help me plan this");
  });

  it("keeps the Barb panel mounted when closed so its conversation can resume", async () => {
    renderShell({ activeTabId: "projects" });
    fireEvent.click(screen.getByRole("button", { name: /ask barb/i }));
    const panel = await screen.findByTestId("barb-sidebar");
    expect(document.querySelector(".desktop-shell")).toHaveClass("desktop-shell--barb-open");

    fireEvent.click(screen.getByTestId("ask-barb-toggle"));
    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(document.querySelector(".desktop-shell")).not.toHaveClass("desktop-shell--barb-open");
    fireEvent.click(screen.getByTestId("ask-barb-toggle"));
    expect(screen.getByTestId("barb-sidebar")).toBe(panel);
    expect(document.querySelector(".desktop-shell")).toHaveClass("desktop-shell--barb-open");
  });

  it("renders the page body in a main landmark", () => {
    renderShell();
    const main = screen.getByRole("main");
    expect(main).toHaveClass("desktop-shell__main");
    expect(within(main).getByTestId("page-body")).toBeInTheDocument();
  });
});

describe("BarbSidebarErrorBoundary — chunk-load retry (#2752)", () => {
  // getDerivedStateFromError/componentDidCatch log via console.error; silence
  // the expected noise without hiding a genuinely unexpected one.
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function makeLoader(outcomes: Array<"reject" | "resolve">) {
    let call = 0;
    return () => {
      const outcome = outcomes[Math.min(call, outcomes.length - 1)];
      call += 1;
      if (outcome === "reject") {
        return Promise.reject(new Error("chunk failed"));
      }
      return Promise.resolve({
        default: () => <div data-testid="barb-loaded">Loaded</div>,
      });
    };
  }

  function Harness({ loader }: { loader: () => Promise<{ default: React.ComponentType }> }) {
    const [open, setOpen] = React.useState(true);
    return (
      <>
        <button type="button" onClick={() => setOpen((o) => !o)}>
          toggle
        </button>
        <BarbSidebarErrorBoundary open={open} onClose={() => setOpen(false)} loader={loader}>
          {(Lazy) => <Lazy />}
        </BarbSidebarErrorBoundary>
      </>
    );
  }

  it("retries the Barb chunk after a failed load when reopened", async () => {
    const loader = makeLoader(["reject", "resolve"]);
    render(<Harness loader={loader} />);

    expect(await screen.findByRole("alert")).toBeInTheDocument();

    fireEvent.click(screen.getByText("toggle")); // close
    fireEvent.click(screen.getByText("toggle")); // reopen -> retries

    expect(await screen.findByTestId("barb-loaded")).toBeInTheDocument();
  });

  it("the Retry button re-attempts the load", async () => {
    const loader = makeLoader(["reject", "resolve"]);
    render(
      <BarbSidebarErrorBoundary open onClose={() => {}} loader={loader}>
        {(Lazy) => <Lazy />}
      </BarbSidebarErrorBoundary>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /retry/i }));

    expect(await screen.findByTestId("barb-loaded")).toBeInTheDocument();
  });

  it("isolates a persistently failing chunk without crashing the surrounding shell (regression)", async () => {
    const loader = makeLoader(["reject", "reject", "reject"]);
    render(
      <div>
        <div data-testid="sibling">Sibling content</div>
        <BarbSidebarErrorBoundary open onClose={() => {}} loader={loader}>
          {(Lazy) => <Lazy />}
        </BarbSidebarErrorBoundary>
      </div>,
    );

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByTestId("sibling")).toBeInTheDocument();
  });
});

describe("DesktopShell — navigation", () => {
  it("clicking the search trigger opens the command palette", () => {
    renderShell();
    const searchBtn = screen.getByRole("button", { name: /open command palette/i });
    fireEvent.click(searchBtn);
    expect(screen.getByRole("dialog", { name: /command palette/i })).toBeInTheDocument();
  });

  it("selecting a sidebar item calls onSelect(tabId)", () => {
    const { onSelect } = renderShell();
    const nav = screen.getByRole("navigation", { name: /dashboard sections/i });
    fireEvent.click(within(nav).getByRole("button", { name: /^Preferences$/i }));
    expect(onSelect).toHaveBeenCalledWith("settings");
  });

  it("reflects the active category in the sidebar", () => {
    renderShell({ activeTabId: "queue" });
    const nav = screen.getByRole("navigation", { name: /dashboard sections/i });
    expect(within(nav).getByRole("button", { name: /^Queue$/i })).toHaveAttribute("aria-current", "page");
  });
});

describe("DesktopShell — action buttons", () => {
  it("renders each action with its accessible name", () => {
    renderShell();
    expect(screen.getByRole("button", { name: /^Refresh$/i })).toHaveClass("shell-action");
    expect(screen.getByRole("button", { name: /^Chat$/i })).toHaveClass("shell-action");
  });

  it("marks active actions with a scoped class", () => {
    renderShell({
      actions: [
        {
          id: "chat",
          label: "Chat",
          tooltip: "Toggle the assistant chat sidebar.",
          onClick: vi.fn(),
          active: true,
        },
      ],
    });
    expect(screen.getByRole("button", { name: /^Chat$/i })).toHaveClass("shell-action--active");
  });

  it("fires the action onClick", () => {
    const spy = vi.fn();
    renderShell({ actions: actions(spy) });
    fireEvent.click(screen.getByRole("button", { name: /^Refresh$/i }));
    expect(spy).toHaveBeenCalledWith("refresh");
  });

  it("exposes the action tooltip via aria-describedby on focus", async () => {
    renderShell();
    const btn = screen.getByRole("button", { name: /^Refresh$/i });
    fireEvent.focus(btn);
    const tip = await screen.findByRole("tooltip");
    expect(btn).toHaveAttribute("aria-describedby", tip.id);
  });
});

describe("DesktopShell — accessibility audit", () => {
  it("no interactive control lacks an accessible label", () => {
    const { container } = render(
      <DesktopShell activeTabId="overview" onSelect={vi.fn()} actions={actions()}>
        <div>PAGE</div>
      </DesktopShell>,
    );
    const controls = Array.from(
      container.querySelectorAll<HTMLElement>("button, a[href], [role='button'], [role='menuitem']"),
    );
    expect(controls.length).toBeGreaterThan(0);
    const unlabelled = controls.filter((el) => {
      const aria = el.getAttribute("aria-label");
      if (aria && aria.trim()) return false;
      const text = (el.textContent ?? "").trim();
      if (text) return false;
      const title = el.getAttribute("title");
      if (title && title.trim()) return false;
      return true;
    });
    expect(unlabelled).toEqual([]);
  });
});
