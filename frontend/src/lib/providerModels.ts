/**
 * Curated list of models per provider for the Runner Dashboard UI.
 *
 * Keep in sync with staff/config.yml and runner capabilities.
 * Retired standalone Gemini CLI and Jules API surfaces are intentionally excluded.
 */

export interface ModelOption {
  id: string;
  label: string;
  default?: boolean;
}

export const PROVIDER_MODELS: Record<string, ModelOption[]> = {
  antigravity: [
    { id: "gemini-3.8-flash-high", label: "Gemini 3.8 Flash (High)", default: true },
    { id: "gemini-3.8-pro-high", label: "Gemini 3.8 Pro (High)" },
    { id: "gemini-3.5-flash", label: "Gemini 3.5 Flash" },
    { id: "gemini-3.5-pro", label: "Gemini 3.5 Pro" },
  ],
  claude_code_cli: [
    { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", default: true },
    { id: "claude-opus-4-7", label: "Claude Opus 4.7" },
    { id: "claude-haiku-4-5", label: "Claude Haiku 4.5" },
  ],
  codex_cli: [
    { id: "gpt-5.4", label: "GPT-5.4", default: true },
    { id: "gpt-5.4-mini", label: "GPT-5.4 Mini" },
    { id: "o3", label: "o3" },
  ],
};

/** Providers that expose a model selector in the UI. */
export const PROVIDERS_WITH_MODEL = new Set<string>(
  Object.entries(PROVIDER_MODELS)
    .filter(([, models]) => models.length > 0)
    .map(([provider]) => provider),
);

/** Returns the default model ID for a provider, or empty string if none. */
export function getDefaultModelForProvider(provider: string): string {
  const models = PROVIDER_MODELS[provider];
  if (!models || models.length === 0) return "";
  const def = models.find((m) => m.default);
  return def ? def.id : models[0].id;
}
