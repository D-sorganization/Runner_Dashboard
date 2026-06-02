/**
 * providerModels.ts — the static agent-provider → selectable-model registry and
 * the set of providers that accept a model override, extracted (verbatim) from
 * the legacy `App.tsx` monolith as part of the decomposition epic (#836, pass 9).
 *
 * Shared between the extracted `QuickDispatch` popover and the legacy App shell
 * (the inline Fleet-orchestration dispatch form still reads `PROVIDER_MODELS`).
 */

/** A selectable model: stable `value` sent to the API, human `label` for UI. */
export interface ProviderModel {
  value: string;
  label: string;
}

/** Provider id → the models offered for it in dispatch UIs. */
export const PROVIDER_MODELS: Record<string, ProviderModel[]> = {
  claude_code_cli: [
    { value: "claude-sonnet-4-6", label: "Sonnet 4.6" },
    { value: "claude-opus-4-6", label: "Opus 4.6" },
    { value: "claude-haiku-4-5-20251001", label: "Haiku 4.5" },
  ],
  codex_cli: [
    { value: "o4-mini", label: "o4-mini" },
    { value: "o3", label: "o3" },
    { value: "gpt-4o", label: "GPT-4o" },
  ],
  gemini_cli: [
    { value: "gemini-2.5-flash", label: "2.5 Flash" },
    { value: "gemini-2.5-pro", label: "2.5 Pro" },
    { value: "gemini-2.0-flash", label: "2.0 Flash" },
  ],
  jules_api: [{ value: "gemini-2.5-pro", label: "2.5 Pro" }],
};

/** Providers whose dispatch envelope accepts a `model` field. */
export const PROVIDERS_WITH_MODEL = [
  "claude_code_cli",
  "codex_cli",
  "gemini_cli",
  "jules_api",
];
