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
