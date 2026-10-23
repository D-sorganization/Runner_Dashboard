// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import {
  PROVIDER_MODELS,
  PROVIDERS_WITH_MODEL,
  getDefaultModelForProvider,
} from "../providerModels";

describe("providerModels", () => {
  it("lists Gemini 3.x models for antigravity with gemini-3.8-flash-high as default", () => {
    const models = PROVIDER_MODELS.antigravity;
    expect(models).toBeDefined();
    expect(models.map((m) => m.id)).toEqual([
      "gemini-3.8-flash-high",
      "gemini-3.8-pro-high",
      "gemini-3.5-flash",
      "gemini-3.5-pro",
    ]);
    expect(getDefaultModelForProvider("antigravity")).toBe("gemini-3.8-flash-high");
    expect(PROVIDERS_WITH_MODEL.has("antigravity")).toBe(true);
  });

  it("provides models for claude_code_cli and codex_cli and excludes retired gemini_cli/jules_api", () => {
    expect(PROVIDERS_WITH_MODEL.has("claude_code_cli")).toBe(true);
    expect(PROVIDERS_WITH_MODEL.has("codex_cli")).toBe(true);
    expect(PROVIDERS_WITH_MODEL.has("gemini_cli")).toBe(false);
    expect(PROVIDERS_WITH_MODEL.has("jules_api")).toBe(false);
    expect(PROVIDER_MODELS.gemini_cli).toBeUndefined();
    expect(PROVIDER_MODELS.jules_api).toBeUndefined();
    expect(getDefaultModelForProvider("jules_api")).toBe("");
    expect(getDefaultModelForProvider("gemini_cli")).toBe("");
    expect(getDefaultModelForProvider("unknown_provider")).toBe("");
  });
});
