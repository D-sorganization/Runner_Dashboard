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
import { Tooltip } from "../primitives/Tooltip";
import { useActiveProvider } from "./useActiveProvider";
import type { ProviderRegistry } from "../lib/useProviderRegistry";

function ProviderIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="3" y="11" width="18" height="10" rx="2" />
      <circle cx="12" cy="5" r="2" />
      <path d="M12 7v4" />
      <line x1="8" y1="16" x2="8" y2="16" />
      <line x1="16" y1="16" x2="16" y2="16" />
    </svg>
  );
}

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
      <Tooltip content={triggerLabel} placement="bottom">
        <button
          type="button"
          aria-haspopup="dialog"
          aria-expanded={open}
          title={triggerLabel}
          onClick={() => setOpen((v) => !v)}
          className="shell-action shell-icon-btn"
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 6,
            height: 32,
            minHeight: 32,
            padding: "0 8px",
            borderRadius: "var(--radius-sm, 6px)",
            border: "1px solid var(--border, #30363d)",
            background: "var(--bg-primary, #0f1117)",
            color: "var(--text-secondary, #8b949e)",
            fontSize: 12,
            cursor: "pointer",
          }}
        >
          <ProviderIcon />
          <span className="active-provider-label">{triggerLabel}</span>
        </button>
      </Tooltip>
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
            background: "var(--bg-card)",
            border: "1px solid var(--border, #30363d)",
            borderRadius: "var(--radius-md, 10px)",
            boxShadow: "var(--shadow-card)",
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
