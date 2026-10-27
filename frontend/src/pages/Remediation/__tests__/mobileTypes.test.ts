/**
 * Tests for Remediation/mobileTypes.ts — the pure provider-selection and
 * label helpers backing the Remediation mobile view's agent dispatch UI.
 */
import { describe, expect, it } from "vitest"
import { parseRegistry, type ProviderRegistryResponse } from "../../../lib/useProviderRegistry"
import {
  type AgentProvider,
  type ProviderAvailability,
  elapsedLabel,
  getProviderLabel,
  pickRecommendedProvider,
} from "../mobileTypes"

const REGISTRY: ProviderRegistryResponse = {
  schema_version: "1.0.0",
  providers: [
    {
      id: "codex-cli",
      dashboard_id: "codex_cli",
      label: "Codex CLI",
      auth_mode: "github_app",
      resource: "runner",
      enabled: true,
    },
    {
      id: "claude-cli",
      dashboard_id: "claude_code_cli",
      label: "Claude CLI",
      auth_mode: "github_app",
      resource: "runner",
      enabled: true,
    },
    {
      id: "jules-api",
      dashboard_id: "jules_api",
      label: "Jules API",
      auth_mode: "api_key",
      resource: "runner",
      enabled: false,
    },
  ],
  auth_kinds: [],
  task_classes: [],
  capabilities: [],
}

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
  const registry = parseRegistry(REGISTRY)

  it("picks the first available provider in registry order", () => {
    const providers = {
      claude_code_cli: provider("claude_code_cli", "Claude Code CLI"),
      codex_cli: provider("codex_cli", "Codex CLI"),
    }
    const avail = {
      claude_code_cli: availability("claude_code_cli", true),
      codex_cli: availability("codex_cli", true),
    }
    expect(pickRecommendedProvider(registry, providers, avail)).toBe("codex_cli")
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
    expect(pickRecommendedProvider(registry, providers, avail)).toBe("claude_code_cli")
  })

  it("never recommends a registry-disabled provider", () => {
    const providers = {
      jules_api: provider("jules_api", "Jules API"),
      codex_cli: provider("codex_cli", "Codex CLI"),
    }
    const avail = {
      jules_api: availability("jules_api", true),
      codex_cli: availability("codex_cli", true),
    }
    expect(pickRecommendedProvider(registry, providers, avail)).toBe("codex_cli")
  })
})

describe("getProviderLabel", () => {
  it("returns the configured label or the id", () => {
    const providers = {
      codex_cli: provider("codex_cli", "Codex CLI"),
    }
    expect(getProviderLabel(providers, "codex_cli")).toBe("Codex CLI")
    expect(getProviderLabel(providers, "missing")).toBe("missing")
  })
})

describe("elapsedLabel", () => {
  it("formats sub-minute durations", () => {
    const now = Date.now()
    expect(elapsedLabel(now - 5000)).toMatch(/^\d+s$/)
  })
})
