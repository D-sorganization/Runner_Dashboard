/**
 * ProviderModelSelector — the ONE reusable provider->model picker used
 * everywhere an agent is chosen (issue #811, epic #809; login UX from #812).
 *
 * Cascading behaviour:
 *   1. The user picks a PROVIDER from the registry.
 *   2. A MODEL dropdown appears, populated from that provider's `models[]`.
 *      - Ollama (and any provider with a live `models_endpoint`) lists its
 *        models here.
 *      - A provider with an empty `models[]` (e.g. Claude CLI) has NO model
 *        picker — it is hidden, not a dead control.
 *
 * DbC invariant: a model can never be selected without a provider. The model
 * picker only renders once a provider with models is chosen, and `onChange`
 * always emits a `{ providerId, dashboardId, model }` triple where `model` is
 * `null` for model-less providers — never a model without a provider.
 *
 * Login clarity (#812): the selected provider's `login_status` is rendered
 * with a fix affordance (a button that calls `onRequestLogin` — typically wired
 * to open the Credentials page) and the `setup_hint` shown as a tooltip.
 *
 * Reuse: built on the existing Tooltip primitive (#801) and styled to match
 * the shell. Consumed by AgentDispatch, the persistent ActiveProviderControl,
 * and (via the shared registry) Conductor + Credentials — killing the old
 * hardcoded provider lists (DRY).
 */
import React, { useCallback, useMemo, useState } from "react";
import { Tooltip } from "./Tooltip";
import type { ProviderRegistry, LoginStatus } from "../lib/useProviderRegistry";

/** The value emitted on every change — always provider-anchored (DbC). */
export interface ProviderModelSelection {
  /** Canonical (hyphenated) provider id, or null when nothing is chosen. */
  providerId: string | null;
  /** Dashboard (underscored) id, or null when nothing is chosen. */
  dashboardId: string | null;
  /** Selected model, or null for model-less providers / no selection. */
  model: string | null;
}

export interface ProviderModelSelectorProps {
  /** The shared registry (single source of truth). */
  registry: ProviderRegistry;
  /** Controlled selection. Uncontrolled when omitted. */
  value?: ProviderModelSelection;
  /** Emitted whenever the provider or model changes. */
  onChange: (selection: ProviderModelSelection) => void;
  /**
   * Called with the providerId when the user asks to fix a provider's login
   * (e.g. open the Credentials page). #812.
   */
  onRequestLogin?: (providerId: string) => void;
  /** Optional id prefix for the rendered selects (a11y / multiple instances). */
  idPrefix?: string;
}

const EMPTY: ProviderModelSelection = { providerId: null, dashboardId: null, model: null };

const LOGIN_LABEL: Record<LoginStatus, string> = {
  authenticated: "Authenticated",
  unauthenticated: "Unauthenticated",
  error: "Login error",
};

const LOGIN_COLOR: Record<LoginStatus, string> = {
  authenticated: "var(--accent-green, #3fb950)",
  unauthenticated: "var(--accent-yellow, #d29922)",
  error: "var(--accent-red, #f85149)",
};

/**
 * Compute the next selection when a provider is chosen.
 *
 * Post: if the provider has models, the first model is the default; otherwise
 * `model` is null. Never emits a model for an unknown/empty provider.
 */
function selectionForProvider(
  registry: ProviderRegistry,
  providerId: string,
): ProviderModelSelection {
  const provider = registry.byId(providerId);
  if (!provider) return EMPTY;
  const model = provider.models.length > 0 ? provider.models[0] : null;
  return { providerId: provider.id, dashboardId: provider.dashboardId, model };
}

export function ProviderModelSelector({
  registry,
  value,
  onChange,
  onRequestLogin,
  idPrefix = "pms",
}: ProviderModelSelectorProps): React.ReactElement {
  // Controlled when `value` is provided; otherwise keep an internal selection
  // so the cascading picker works standalone (uncontrolled). Either way every
  // change is reported through `onChange` (DbC: provider-anchored triple).
  const isControlled = value !== undefined;
  const [internal, setInternal] = useState<ProviderModelSelection>(EMPTY);
  const selection = isControlled ? (value as ProviderModelSelection) : internal;

  const emit = useCallback(
    (next: ProviderModelSelection) => {
      if (!isControlled) setInternal(next);
      onChange(next);
    },
    [isControlled, onChange],
  );

  const selectedProvider = useMemo(
    () => (selection.providerId ? registry.byId(selection.providerId) ?? null : null),
    [registry, selection.providerId],
  );
  const models = selectedProvider?.models ?? [];
  const hasModels = models.length > 0;

  const handleProviderChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const id = e.target.value;
      if (!id) {
        emit(EMPTY);
        return;
      }
      emit(selectionForProvider(registry, id));
    },
    [registry, emit],
  );

  const handleModelChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      // Invariant: cannot pick a model without an active provider.
      if (!selectedProvider) return;
      emit({
        providerId: selectedProvider.id,
        dashboardId: selectedProvider.dashboardId,
        model: e.target.value || null,
      });
    },
    [selectedProvider, emit],
  );

  const providerSelectId = `${idPrefix}-provider`;
  const modelSelectId = `${idPrefix}-model`;

  const selectStyle: React.CSSProperties = {
    padding: "6px 8px",
    borderRadius: 6,
    border: "1px solid var(--border, #30363d)",
    background: "var(--bg-primary, #0f1117)",
    color: "var(--text-primary, #e6edf3)",
    fontSize: 13,
    minWidth: 160,
  };
  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: 11,
    color: "var(--text-secondary, #8b949e)",
    marginBottom: 4,
  };

  return (
    <div
      className="provider-model-selector"
      style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-start" }}
    >
      <div>
        <label htmlFor={providerSelectId} style={labelStyle}>
          Provider
        </label>
        <select
          id={providerSelectId}
          value={selection.providerId ?? ""}
          onChange={handleProviderChange}
          style={selectStyle}
        >
          <option value="">Select a provider…</option>
          {registry.providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
              {p.experimental ? " (experimental)" : ""}
            </option>
          ))}
        </select>
      </div>

      {/* Model picker: only when the chosen provider actually has models. */}
      {hasModels && (
        <div>
          <label htmlFor={modelSelectId} style={labelStyle}>
            Model
          </label>
          <select
            id={modelSelectId}
            value={selection.model ?? ""}
            onChange={handleModelChange}
            style={selectStyle}
          >
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Login status + fix affordance for the selected provider (#812). */}
      {selectedProvider && (
        <div style={{ alignSelf: "flex-end", paddingBottom: 6 }}>
          <Tooltip
            content={
              selectedProvider.setupHint ||
              selectedProvider.loginDetail ||
              `Auth: ${selectedProvider.authMode}`
            }
          >
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                fontSize: 12,
                color: LOGIN_COLOR[selectedProvider.loginStatus],
              }}
            >
              <span
                aria-hidden="true"
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: LOGIN_COLOR[selectedProvider.loginStatus],
                }}
              />
              {LOGIN_LABEL[selectedProvider.loginStatus]}
            </span>
          </Tooltip>
          {selectedProvider.loginStatus !== "authenticated" && (
            <button
              type="button"
              onClick={() => onRequestLogin?.(selectedProvider.id)}
              style={{
                marginLeft: 8,
                padding: "3px 8px",
                borderRadius: 6,
                border: "1px solid var(--border, #30363d)",
                background: "var(--bg-secondary, #161b22)",
                color: "var(--accent-blue, #58a6ff)",
                fontSize: 12,
                cursor: "pointer",
              }}
            >
              Fix login in Credentials
            </button>
          )}
        </div>
      )}
    </div>
  );
}
