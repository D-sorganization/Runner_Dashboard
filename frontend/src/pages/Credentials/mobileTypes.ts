// Shared types, constants, and small utilities for the Credentials mobile view.
// Extracted from Mobile.tsx to keep that file under the 500-line cap.

export interface CredentialProbe {
  id: string;
  label: string;
  icon: string;
  installed: boolean;
  authenticated: boolean;
  reachable: boolean;
  usable: boolean;
  status: string;
  detail: string;
  config_source: string;
  docs_url?: string;
  setup_hint?: string;
  key_provider?: string;
}

export interface CredentialSummary {
  total: number;
  ready: number;
  not_ready: number;
}

export type LockState = "locked" | "unlocking" | "unlocked" | "error";

export const INACTIVITY_TIMEOUT_MS = 60_000;

/**
 * Alias → canonical credential-provider name.
 *
 * The unified provider registry (`/api/providers/registry`, #811) is now the
 * single source of truth for provider ids and the underscore<->hyphen id
 * mapping; prefer `providerMapFromRegistry()` below when a registry is in hand.
 * This static table is retained only for the extra human/vendor aliases the
 * pinned contract does not express (e.g. `anthropic`→`claude`, `openai`→`codex`)
 * and as a synchronous fallback before the registry has loaded.
 */
export const PROVIDER_MAP: Record<string, string> = {
  claude: "claude",
  claude_code_cli: "claude",
  anthropic: "claude",
  codex: "codex",
  codex_cli: "codex",
  openai: "codex",
  gemini: "gemini",
  gemini_cli: "gemini",
  jules: "jules",
  jules_api: "jules",
  linear: "linear",
};

import type { ProviderRegistry } from "../../lib/useProviderRegistry";

/**
 * Build the alias→canonical map from the shared registry so Credentials and the
 * ProviderModelSelector agree on provider identity (DRY, #811). Registry-derived
 * entries (both the hyphenated id and the dashboard id resolve to the dashboard
 * id) are layered under the static vendor aliases above.
 */
export function providerMapFromRegistry(
  registry: ProviderRegistry,
): Record<string, string> {
  const out: Record<string, string> = { ...PROVIDER_MAP };
  for (const p of registry.providers) {
    out[p.id] = p.dashboardId;
    out[p.dashboardId] = p.dashboardId;
  }
  return out;
}

export function base64urlToBuffer(base64url: string): ArrayBuffer {
  const base64 = base64url.replace(/-/g, "+").replace(/_/g, "/");
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(base64 + padding);
  const buffer = new ArrayBuffer(binary.length);
  const view = new Uint8Array(buffer);
  for (let i = 0; i < binary.length; i++) {
    view[i] = binary.charCodeAt(i);
  }
  return buffer;
}
