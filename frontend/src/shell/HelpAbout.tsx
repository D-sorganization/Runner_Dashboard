/**
 * HelpAbout — the in-app Help/About surface (issue #822).
 *
 * A '?' button in the shell topbar opens an accessible dialog that gives a new
 * operator an in-app starting point:
 *   - what the dashboard is;
 *   - the running version (`GET /api/version`);
 *   - quick links to the key tabs (driven by the nav registry — DRY);
 *   - a short "first things to check" checklist;
 *   - the keyboard shortcuts.
 *
 * It is also the intended mount point for the codebase chat assistant (a later
 * issue) — kept as a self-contained surface so that can slot in without
 * touching the shell.
 *
 * Accessibility: built on the `Dialog` primitive (focus trap, Escape-to-close,
 * focus restore, `aria-modal`, inert siblings). The trigger is a labelled
 * button with a tooltip; pressing "?" anywhere (outside a text field) opens it.
 *
 * LoD: receives a flat `onNavigate(tabId)` callback and a list of quick-link
 * tab ids; it never reaches into shell or page internals. `fetchImpl` is
 * injectable so tests drive the version probe without the network.
 */
import React, { useCallback, useEffect, useState } from "react";
import { Dialog, DialogTitle, DialogContent, DialogActions, DialogClose } from "../primitives/Dialog";
import { Tooltip } from "../primitives/Tooltip";
import { navItemById } from "./navRegistry";
import { CodebaseChat } from "../pages/Maxwell";

/** Shape of `GET /api/version` we care about (extra fields tolerated). */
export interface VersionInfo {
  dashboard?: string;
  git_sha?: string;
  build_time?: string;
}

export interface HelpAboutProps {
  /** Navigate the shell to a tab (quick links). */
  onNavigate: (tabId: string) => void;
  /** Nav ids surfaced as quick links. Defaults to the common operator tabs. */
  quickLinkIds?: string[];
  /** Injectable fetch (tests). Defaults to global `fetch`. */
  fetchImpl?: typeof fetch;
}

/** Key tabs a new operator most likely needs first. */
const DEFAULT_QUICK_LINKS = [
  "overview",
  "queue",
  "remediation",
  "credentials",
  "local-apps",
  "diagnostics",
];

/** First-things-to-check checklist (kept to five concise lines). */
const FIRST_CHECKS: readonly string[] = [
  "Are you signed in? The topbar shows Login vs Logout.",
  "Is the runner fleet healthy? Open the Fleet tab.",
  "Is anything stuck? Check the Queue tab for long-waiting jobs.",
  "AI features quiet? Maxwell-Daemon must be running — start it from Local Tools.",
  "Something failing? The Diagnostics tab runs self-checks.",
];

/** Keyboard shortcuts surfaced to operators. */
const SHORTCUTS: readonly { keys: string; label: string }[] = [
  { keys: "Ctrl / ⌘ + K", label: "Open the command palette" },
  { keys: "?", label: "Open this Help & About panel" },
  { keys: "Esc", label: "Close a dialog or palette" },
];

function VersionLine({
  version,
  loading,
  error,
}: {
  version: VersionInfo | null;
  loading: boolean;
  error: boolean;
}): React.ReactElement {
  if (loading) return <span className="help-about__muted">checking…</span>;
  if (error || !version) return <span className="help-about__muted">unavailable</span>;
  const sha = version.git_sha && version.git_sha !== "unknown" ? version.git_sha.slice(0, 7) : null;
  return (
    <span className="help-about__version">
      {version.dashboard ?? "unknown"}
      {sha ? <span className="help-about__muted"> ({sha})</span> : null}
    </span>
  );
}

export function HelpAbout({
  onNavigate,
  quickLinkIds = DEFAULT_QUICK_LINKS,
  fetchImpl,
}: HelpAboutProps): React.ReactElement {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"help" | "chat">("help");
  const [version, setVersion] = useState<VersionInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  // Fetch the version once, lazily, the first time the panel is opened.
  useEffect(() => {
    if (!open || version || loading) return;
    const doFetch = fetchImpl ?? (typeof fetch !== "undefined" ? fetch : undefined);
    if (!doFetch) return;
    setLoading(true);
    setError(false);
    doFetch("/api/version", { headers: { Accept: "application/json" } })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: VersionInfo) => {
        setVersion(data);
        setLoading(false);
      })
      .catch(() => {
        setError(true);
        setLoading(false);
      });
  }, [open, version, loading, fetchImpl]);

  // "?" opens the panel from anywhere outside a text-entry field.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "?" || e.ctrlKey || e.metaKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) return;
      e.preventDefault();
      setOpen(true);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);

  const go = useCallback(
    (tabId: string) => {
      setOpen(false);
      onNavigate(tabId);
    },
    [onNavigate],
  );

  const quickLinks = quickLinkIds
    .map((id) => navItemById(id))
    .filter((it): it is NonNullable<typeof it> => Boolean(it));

  return (
    <>
      <Tooltip content="Help & About: what this dashboard is, version, key tabs and shortcuts." placement="bottom">
        <button
          type="button"
          className="shell-action shell-help-trigger"
          aria-label="Open Help and About panel"
          aria-haspopup="dialog"
          onClick={() => setOpen(true)}
        >
          ?
        </button>
      </Tooltip>

      <Dialog open={open} onClose={() => setOpen(false)}>
        <DialogTitle>Runner Dashboard — Help &amp; About</DialogTitle>
        <DialogContent>
          <div role="tablist" aria-label="Help and About sections" className="help-about__tabs">
            <button
              type="button"
              role="tab"
              id="help-tab-help"
              aria-selected={tab === "help"}
              aria-controls="help-panel-help"
              onClick={() => setTab("help")}
              className={`help-about__tab ${tab === "help" ? "help-about__tab--active" : ""}`}
            >
              Help &amp; About
            </button>
            <button
              type="button"
              role="tab"
              id="help-tab-chat"
              aria-selected={tab === "chat"}
              aria-controls="help-panel-chat"
              onClick={() => setTab("chat")}
              className={`help-about__tab ${tab === "chat" ? "help-about__tab--active" : ""}`}
            >
              Ask the codebase
            </button>
          </div>

          {tab === "chat" ? (
            <div role="tabpanel" id="help-panel-chat" aria-labelledby="help-tab-chat">
              <CodebaseChat fetchImpl={fetchImpl} />
            </div>
          ) : (
            <div role="tabpanel" id="help-panel-help" aria-labelledby="help-tab-help">
              <p className="help-about__intro">
                The operator console for the self-hosted GitHub Actions runner fleet:
                monitor runner health, manage the job queue, dispatch AI remediation
                agents, and orchestrate the fleet from one place.
              </p>

              <dl className="help-about__meta">
                <dt className="help-about__muted">Version</dt>
                <dd className="help-about__meta-value">
                  <VersionLine version={version} loading={loading} error={error} />
                </dd>
              </dl>

              <section aria-label="Key tabs" className="help-about__section">
                <h3 className="help-about__heading">Key tabs</h3>
                <div className="help-about__quick-links">
                  {quickLinks.map((it) => (
                    <button
                      key={it.id}
                      type="button"
                      onClick={() => go(it.tabId)}
                      title={it.tooltip}
                      className="help-about__quick-link"
                    >
                      {it.label}
                    </button>
                  ))}
                </div>
              </section>

              <section aria-label="First things to check" className="help-about__section">
                <h3 className="help-about__heading">First things to check</h3>
                <ol className="help-about__checklist">
                  {FIRST_CHECKS.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ol>
              </section>

              <section aria-label="Keyboard shortcuts" className="help-about__section">
                <h3 className="help-about__heading">Keyboard shortcuts</h3>
                <dl className="help-about__shortcuts">
                  {SHORTCUTS.map((s) => (
                    <React.Fragment key={s.keys}>
                      <dt>
                        <kbd className="help-about__kbd">{s.keys}</kbd>
                      </dt>
                      <dd className="help-about__shortcut-label">{s.label}</dd>
                    </React.Fragment>
                  ))}
                </dl>
              </section>
            </div>
          )}
        </DialogContent>
        <DialogActions>
          <DialogClose>Close</DialogClose>
        </DialogActions>
      </Dialog>
    </>
  );
}
