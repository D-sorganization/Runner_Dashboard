/**
 * Tests for lib/schemas/dispatch.ts — the zod contracts guarding the agent
 * dispatch form and credential-key entry. These boundaries (Design by
 * Contract) gate every POST to /api/agent-remediation/dispatch.
 */
import { describe, expect, it } from "vitest"
import {
  KNOWN_PROVIDERS,
  credentialKeySchema,
  quickDispatchSchema,
} from "../dispatch"

describe("quickDispatchSchema", () => {
  const valid = {
    prompt: "Please fix the failing CI build on main.",
    repo: "d-sorganization/runner-dashboard",
    provider: "claude_code_cli" as const,
  }

  it("accepts a well-formed dispatch payload", () => {
    const result = quickDispatchSchema.safeParse(valid)
    expect(result.success).toBe(true)
  })

  it("rejects a prompt shorter than 10 characters", () => {
    const result = quickDispatchSchema.safeParse({ ...valid, prompt: "too short" })
    expect(result.success).toBe(false)
  })

  it("rejects a prompt longer than 10,000 characters", () => {
    const result = quickDispatchSchema.safeParse({
      ...valid,
      prompt: "x".repeat(10_001),
    })
    expect(result.success).toBe(false)
  })

  it("rejects a repo slug not matching owner/repo", () => {
    const result = quickDispatchSchema.safeParse({ ...valid, repo: "no-slash" })
    expect(result.success).toBe(false)
  })

  it("rejects an empty repo slug", () => {
    const result = quickDispatchSchema.safeParse({ ...valid, repo: "" })
    expect(result.success).toBe(false)
  })

  it("rejects an unknown provider", () => {
    const result = quickDispatchSchema.safeParse({
      ...valid,
      provider: "totally_made_up",
    })
    expect(result.success).toBe(false)
  })

  it("accepts every known provider", () => {
    for (const provider of KNOWN_PROVIDERS) {
      const result = quickDispatchSchema.safeParse({ ...valid, provider })
      expect(result.success).toBe(true)
    }
  })
})

describe("credentialKeySchema", () => {
  it("accepts a non-empty key within the length bound", () => {
    expect(credentialKeySchema.safeParse({ key: "sk-abc123" }).success).toBe(true)
  })

  it("rejects an empty key", () => {
    expect(credentialKeySchema.safeParse({ key: "" }).success).toBe(false)
  })

  it("rejects a key longer than 2,000 characters", () => {
    expect(
      credentialKeySchema.safeParse({ key: "x".repeat(2_001) }).success,
    ).toBe(false)
  })
})
