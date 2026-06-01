/**
 * ActiveProviderControl — the compact, always-visible provider+model control
 * surfaced in the desktop shell's top toolstrip (issue #811).
 *
 * It lets the operator see and change the GLOBAL active provider+model at all
 * times. The trigger shows the current active provider's label (resolved
 * through the shared registry); clicking it opens a small popover hosting the
 * reusable ProviderModelSelector. The selection is persisted via
 * useActiveProvider (localStorage) so it survives reloads and is shared across
 * surfaces (DRY).
 *
 * Reuse / orthogonality: this control owns no provider data of its own — it
 * reads the same `ProviderRegistry` every other consumer uses and emits the
 * same `ProviderModelSelection` shape, so the persistent control and the
 * in-flow dispatch picker can never drift.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ProviderModelSelector,
  type ProviderModelSelection,
} from "../primitives/ProviderModelSelector";
import { useActiveProvider } from "./useActiveProvider";
import type { ProviderRegistry } from "../lib/useProviderRegistry";

export interface ActiveProviderControlProps {
  registry: ProviderRegistry;
  /** Optional: open the Credentials surface for a provider (login fix, #812). */
  onRequestLogin?: (providerId: string) => void;
}

export function ActiveProviderControl({
  registry,
  onRequestLogin,
}: ActiveProviderControlProps): React.ReactElement {
  const { active, setActive } = useActiveProvider();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // Resolve the displayed selection through the registry so the dashboardId is
  // always correct even though only the provider id + model are persisted.
  const resolved = useMemo<ProviderModelSelection>(() => {
    const provider = active.providerId ? registry.byId(active.providerId) ?? null : null;
    return {
      providerId: provider?.id ?? null,
      dashboardId: provider?.dashboardId ?? null,
      model: active.model,
    };
  }, [active, registry]);

  const activeProvider = resolved.providerId ? registry.byId(resolved.providerId) : undefined;
  const triggerLabel = activeProvider
    ? `Provider: ${activeProvider.label}${resolved.model ? ` · ${resolved.model}` : ""}`
    : "Provider: none";

  const handleChange = useCallback(
    (selection: ProviderModelSelection) => {
      setActive(selection);
    },
    [setActive],
  );

  // Close on outside mousedown (matches the Dropdown primitive behaviour).
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <div ref={rootRef} style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          padding: "5px 10px",
          borderRadius: 6,
          border: "1px solid var(--border, #30363d)",
          background: "var(--bg-primary, #0f1117)",
          color: "var(--text-secondary, #8b949e)",
          fontSize: 12,
          cursor: "pointer",
          whiteSpace: "nowrap",
        }}
      >
        {triggerLabel}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Active agent selection"
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            right: 0,
            zIndex: 10000,
            minWidth: 320,
            background: "var(--bg-secondary, #161b22)",
            border: "1px solid var(--border, #30363d)",
            borderRadius: 8,
            boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
            padding: 12,
          }}
        >
          <ProviderModelSelector
            registry={registry}
            value={resolved}
            onChange={handleChange}
            onRequestLogin={onRequestLogin}
            idPrefix="active-provider"
          />
        </div>
      )}
    </div>
  );
}
