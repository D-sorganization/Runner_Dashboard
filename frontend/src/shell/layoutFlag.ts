/**
 * layoutFlag.ts — retires the Classic (legacy) layout preference (#1345).
 *
 * The modern shell is the only layout. Browsers that pinned the Classic layout
 * with `localStorage["dashboard.layout"] = "legacy"` (the old "Classic layout"
 * action) still carry that key; the shell calls `retireLegacyLayoutPreference`
 * once on mount to remove it and show a single notice.
 */
export const LAYOUT_STORAGE_KEY = "dashboard.layout";

type LayoutStorage = Pick<Storage, "getItem" | "removeItem">;

function browserStorage(): LayoutStorage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/**
 * Remove any stored layout preference.
 *
 * Postcondition: returns true exactly when a Classic ("legacy") preference was
 * stored, so the caller shows its notice once; never throws when storage is
 * unavailable (privacy mode) — that reads as "nothing stored".
 */
export function retireLegacyLayoutPreference(
  storage: LayoutStorage | null = browserStorage(),
): boolean {
  if (!storage) return false;
  try {
    const stored = storage.getItem(LAYOUT_STORAGE_KEY);
    if (stored == null) return false;
    storage.removeItem(LAYOUT_STORAGE_KEY);
    return stored.trim().toLowerCase() === "legacy";
  } catch {
    return false;
  }
}
