/**
 * Unit tests for lib/providerModels — the static agent-provider model registry
 * extracted from the legacy App.tsx (decomposition #836, pass 9).
 */
import { describe, expect, it } from "vitest";
import { PROVIDER_MODELS, PROVIDERS_WITH_MODEL } from "../providerModels";

describe("PROVIDER_MODELS", () => {
  it("exposes the four known providers", () => {
    expect(Object.keys(PROVIDER_MODELS).sort()).toEqual([
      "claude_code_cli",
      "codex_cli",
      "gemini_cli",
      "jules_api",
    ]);
  });

  it("lists Sonnet 4.6 first for claude_code_cli (the default model)", () => {
    expect(PROVIDER_MODELS.claude_code_cli[0]).toEqual({
      value: "claude-sonnet-4-6",
      label: "Sonnet 4.6",
    });
  });

  it("offers a single model for jules_api", () => {
    expect(PROVIDER_MODELS.jules_api).toHaveLength(1);
    expect(PROVIDER_MODELS.jules_api[0].value).toBe("gemini-2.5-pro");
  });

  it("gives every model both a value and a label", () => {
    for (const models of Object.values(PROVIDER_MODELS)) {
      for (const m of models) {
        expect(typeof m.value).toBe("string");
        expect(m.value.length).toBeGreaterThan(0);
        expect(typeof m.label).toBe("string");
        expect(m.label.length).toBeGreaterThan(0);
      }
    }
  });
});

describe("PROVIDERS_WITH_MODEL", () => {
  it("matches the set of providers that have a model list", () => {
    expect([...PROVIDERS_WITH_MODEL].sort()).toEqual(
      Object.keys(PROVIDER_MODELS).sort(),
    );
  });

  it("does not include providers without a model override (e.g. ollama)", () => {
    expect(PROVIDERS_WITH_MODEL).not.toContain("ollama");
    expect(PROVIDERS_WITH_MODEL).not.toContain("cline");
  });
});
