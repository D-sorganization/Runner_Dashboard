/**
 * intro.ts — per-tab intro copy, seeded from the nav registry (issue #822).
 *
 * DRY: the one-line orientation shown atop each page body is derived from the
 * SAME `navRegistry` tooltip that labels the nav item — there is no second
 * place to keep tab descriptions in sync. A small `INTRO_OVERRIDES` map lets us
 * (a) expand the jargon-heavy admin tooltips (Cline, principals) into operator
 * English, and (b) attach an optional deeper line where the tooltip is too
 * terse for a header.
 *
 * LoD: consumers call `introForTab(tabId)` and receive a flat `{ title, body }`
 * (or `undefined` when a tab has no registry entry) — they never reach into the
 * registry shape themselves.
 */
import { navItemById, NAV_ITEMS } from "./navRegistry";

export interface TabIntro {
  /** The tab's short label (e.g. "Cline Launcher"). */
  title: string;
  /** One-line orientation for an operator landing on the tab. */
  body: string;
}

/**
 * Expanded, de-jargoned copy for tabs whose registry tooltip is too terse or
 * assumes insider vocabulary. Keys are nav-item ids. When present this body
 * supersedes the registry tooltip for the intro header (but NOT for the nav
 * tooltip itself, which stays compact).
 */
export const INTRO_OVERRIDES: Readonly<Record<string, string>> = {
  // "Cline" is an AI coding agent; spell out what launching a session means.
  "cline-launcher":
    "Start a Cline AI coding-agent session against a fleet repo — Cline runs the agent that edits code and opens PRs on your behalf.",
  // "Principals" is auth jargon for the identities the dashboard can act as.
  principals:
    "The identities (users, bots, service accounts) the dashboard is authenticated as, and which one it is currently acting on behalf of.",
  // "Conductor admission gate" needs unpacking for new operators.
  conductor:
    "The admission gate that decides which queued agent dispatches are allowed to run — see what's pending, admitted, or held, and adjust capacity.",
  maxwell:
    "Maxwell-Daemon is the autonomous local AI control plane. Watch its status, review tasks, and chat with it (it must be running — start it from Local Tools).",
  "local-apps":
    "Local helper processes the dashboard depends on (e.g. Maxwell-Daemon) — see whether each is running and start/stop them here.",
};

/**
 * Resolve the intro copy for a given legacy tab string / nav id.
 *
 * The shell keys the active page on a `tabId`; nav items are also addressed by
 * `id`. For the registry items in this app `id === tabId`, so we look up by id.
 *
 * Postcondition: returns `{ title, body }` when the tab maps to a registry
 * item, else `undefined` (the header simply isn't rendered).
 */
export function introForTab(tabId: string | undefined): TabIntro | undefined {
  if (!tabId) return undefined;
  const item = navItemById(tabId);
  if (!item) return undefined;
  const body = INTRO_OVERRIDES[item.id] ?? item.tooltip;
  return { title: item.label, body };
}

/** Ids that carry an expanded override — exported for the contract test. */
export const INTRO_OVERRIDE_IDS = Object.keys(INTRO_OVERRIDES);

/**
 * Design-by-Contract: every override id MUST reference a real nav item, so the
 * overrides can't silently drift from the registry. Runs at module load.
 */
(function assertOverridesReferenceRealItems(): void {
  for (const id of INTRO_OVERRIDE_IDS) {
    if (!NAV_ITEMS.some((it) => it.id === id)) {
      throw new Error(
        `intro: INTRO_OVERRIDES references unknown nav id "${id}"`,
      );
    }
  }
})();
