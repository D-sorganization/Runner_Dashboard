/**
 * useActiveProvider — the persistent/global active provider+model selection
 * (issue #811). The user can see and change the active provider AT ALL TIMES
 * via the shell's ActiveProviderControl; the choice is persisted to
 * localStorage and restored on mount so it survives reloads and is shared
 * across every surface (DRY).
 *
 * Keys are deliberately simple top-level strings (per the issue's pinned
 * convention) rather than the schema-validated `storage` registry, because the
 * active provider is a single id/string pair with no migration surface.
 */
import { useCallback, useState } from "react";
import type { ProviderModelSelection } from "../primitives/ProviderModelSelector";

export const ACTIVE_PROVIDER_KEY = "dashboard.activeProvider";
export const ACTIVE_MODEL_KEY = "dashboard.activeModel";

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable — non-fatal */
  }
}

function readActive(): ProviderModelSelection {
  const providerId = readStorage(ACTIVE_PROVIDER_KEY);
  const model = readStorage(ACTIVE_MODEL_KEY);
  return {
    providerId: providerId || null,
    // dashboardId is resolved by the consumer via the registry; not persisted
    // separately because it is derivable from providerId.
    dashboardId: null,
    model: model || null,
  };
}

export interface UseActiveProviderResult {
  active: ProviderModelSelection;
  setActive: (selection: ProviderModelSelection) => void;
  clear: () => void;
}

export function useActiveProvider(): UseActiveProviderResult {
  const [active, setActiveState] = useState<ProviderModelSelection>(readActive);

  const setActive = useCallback((selection: ProviderModelSelection) => {
    writeStorage(ACTIVE_PROVIDER_KEY, selection.providerId);
    writeStorage(ACTIVE_MODEL_KEY, selection.model);
    setActiveState(selection);
  }, []);

  const clear = useCallback(() => {
    writeStorage(ACTIVE_PROVIDER_KEY, null);
    writeStorage(ACTIVE_MODEL_KEY, null);
    setActiveState({ providerId: null, dashboardId: null, model: null });
  }, []);

  return { active, setActive, clear };
}
