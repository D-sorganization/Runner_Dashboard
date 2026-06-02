/**
 * Tests for Remediation/mobileTypes.ts — the pure provider-selection and
 * label helpers backing the Remediation mobile view's agent dispatch UI.
 */
import { describe, expect, it } from "vitest"
import {
  type AgentProvider,
  type ProviderAvailability,
  elapsedLabel,
  getProviderLabel,
  pickRecommendedProvider,
} from "../mobileTypes"

function provider(id: string, label: string): AgentProvider {
  return {
    provider_id: id,
    label,
    execution_mode: "cli",
    dispatch_mode: "workflow",
    notes: "",
    experimental: false,
    remote: true,
    editable: false,
  }
}

function availability(id: string, available: boolean): ProviderAvailability {
  return {
    provider_id: id,
    available,
    status: available ? "available" : "unavailable",
    detail: "",
  }
}

describe("pickRecommendedProvider", () => {
  it("picks the first available provider in default priority order", () => {
    const providers = {
      claude_code_cli: provider("claude_code_cli", "Claude Code CLI"),
      codex_cli: provider("codex_cli", "Codex CLI"),
    }
    const avail = {
      claude_code_cli: availability("claude_code_cli", true),
      codex_cli: availability("codex_cli", true),
    }
    // codex_cli ranks ahead of claude_code_cli in DEFAULT_PROVIDER_ORDER.
    expect(pickRecommendedProvider(providers, avail)).toBe("codex_cli")
  })

  it("skips providers that are present but unavailable", () => {
    const providers = {
      codex_cli: provider("codex_cli", "Codex CLI"),
      claude_code_cli: provider("claude_code_cli", "Claude Code CLI"),
    }
    const avail = {
      codex_cli: availability("codex_cli", false),
      claude_code_cli: availability("claude_code_cli", true),
    }
    expect(pickRecommendedProvider(providers, avail)).toBe("claude_code_cli")
  })

  it("falls back to any available provider outside the priority list", () => {
    const providers = { custom_agent: provider("custom_agent", "Custom") }
    const avail = { custom_agent: availability("custom_agent", true) }
    expect(pickRecommendedProvider(providers, avail)).toBe("custom_agent")
  })

  it("falls back to claude_code_cli when nothing is available", () => {
    const providers = { codex_cli: provider("codex_cli", "Codex CLI") }
    const avail = { codex_cli: availability("codex_cli", false) }
    expect(pickRecommendedProvider(providers, avail)).toBe("claude_code_cli")
  })
})

describe("getProviderLabel", () => {
  it("returns the provider's label when known", () => {
    const providers = { codex_cli: provider("codex_cli", "Codex CLI") }
    expect(getProviderLabel(providers, "codex_cli")).toBe("Codex CLI")
  })

  it("falls back to the raw id for unknown providers", () => {
    expect(getProviderLabel({}, "mystery")).toBe("mystery")
  })
})

describe("elapsedLabel", () => {
  it("formats seconds, minutes and hours since start", () => {
    const now = Date.now()
    expect(elapsedLabel(now - 5_000)).toBe("5s")
    expect(elapsedLabel(now - 90_000)).toMatch(/^1m \d+s$/)
    expect(elapsedLabel(now - 3 * 3_600_000)).toMatch(/^3h \d+m$/)
  })
})
