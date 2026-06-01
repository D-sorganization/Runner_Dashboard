/**
 * layoutFlag.ts — resolves whether to render the modern desktop shell (#802).
 *
 * The modern shell (left Sidebar + slim TopToolstrip + dropdown/tooltip
 * primitives) is the DEFAULT desktop layout, but the integration is fully
 * reversible: an operator can pin the legacy shell at runtime via
 * `localStorage["dashboard.layout"] = "legacy"`, or a build can opt out via the
 * `VITE_DESKTOP_SHELL` env var. Precedence (highest wins):
 *
 *   1. localStorage `dashboard.layout` (`"legacy"` | `"modern"`) — runtime,
 *      per-browser escape hatch, no rebuild needed;
 *   2. build-time env `VITE_DESKTOP_SHELL`;
 *   3. default → modern.
 *
 * Pure + injectable so it is trivially testable (LoD): callers pass the env
 * value and an optional storage reader; production wires the real ones.
 */
export const LAYOUT_STORAGE_KEY = "dashboard.layout";

export interface LayoutFlagInputs {
  /** Raw value of import.meta.env.VITE_DESKTOP_SHELL, if any. */
  env?: string;
  /** Storage reader (defaults to window.localStorage). */
  readStorage?: (key: string) => string | null;
}

function truthy(value: string): boolean | undefined {
  const v = value.trim().toLowerCase();
  if (v === "1" || v === "on" || v === "true" || v === "modern") return true;
  if (v === "0" || v === "off" || v === "false" || v === "legacy") return false;
  return undefined;
}

/**
 * Resolve the desktop layout. Returns true for the modern desktop shell.
 *
 * Postcondition: always returns a boolean; never throws even if storage is
 * unavailable (privacy mode / SSR) — it falls back to env then default.
 */
export function resolveDesktopShellLayout(inputs: LayoutFlagInputs = {}): boolean {
  const readStorage =
    inputs.readStorage ??
    ((key: string) => {
      try {
        return window.localStorage.getItem(key);
      } catch {
        return null;
      }
    });

  // 1. localStorage override (highest precedence).
  const stored = readStorage(LAYOUT_STORAGE_KEY);
  if (stored != null) {
    const decided = truthy(stored);
    if (decided !== undefined) return decided;
  }

  // 2. build-time env.
  if (inputs.env != null) {
    const decided = truthy(inputs.env);
    if (decided !== undefined) return decided;
  }

  // 3. default: modern.
  return true;
}

/** Production convenience: read the real env + localStorage. */
export function useDesktopShellLayout(): boolean {
  const env = (import.meta.env as Record<string, string | undefined>)
    ?.VITE_DESKTOP_SHELL;
  return resolveDesktopShellLayout({ env });
}
