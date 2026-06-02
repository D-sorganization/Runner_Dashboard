/**
 * Tests for Queue/mobileTypes.ts — the pure helpers that back the Queue mobile
 * view's duration, timing-breakdown and status-tone rendering.
 */
import { describe, expect, it } from "vitest"
import {
  type WorkflowRun,
  elapsedLabel,
  elapsedSeconds,
  formatDuration,
  runRepo,
  runnerName,
  statusLabel,
  statusTone,
  timingLabel,
  triggeredBy,
} from "../mobileTypes"

describe("formatDuration", () => {
  it("returns a dash for nullish or negative seconds", () => {
    expect(formatDuration(0)).toBe("-")
    expect(formatDuration(-3)).toBe("-")
  })

  it("formats sub-minute and multi-minute durations", () => {
    expect(formatDuration(42)).toBe("42s")
    expect(formatDuration(125)).toBe("2m 5s")
  })
})

describe("elapsedSeconds / elapsedLabel", () => {
  it("returns 0 when no start timestamp is present", () => {
    expect(elapsedSeconds({ id: 1 })).toBe(0)
    expect(elapsedLabel({ id: 1 })).toBe("-")
  })

  it("prefers run_started_at over created_at", () => {
    const run: WorkflowRun = {
      id: 1,
      run_started_at: new Date(Date.now() - 90_000).toISOString(),
      created_at: new Date(Date.now() - 600_000).toISOString(),
    }
    expect(elapsedSeconds(run)).toBeGreaterThanOrEqual(89)
    expect(elapsedSeconds(run)).toBeLessThanOrEqual(92)
  })
})

describe("runRepo / triggeredBy / runnerName", () => {
  it("extracts repository name or empty string", () => {
    expect(runRepo({ id: 1, repository: { name: "rd" } })).toBe("rd")
    expect(runRepo({ id: 1 })).toBe("")
  })

  it("falls back across actor fields", () => {
    expect(triggeredBy({ id: 1, triggering_actor: { login: "alice" } })).toBe("alice")
    expect(triggeredBy({ id: 1, actor: { login: "bob" } })).toBe("bob")
    expect(triggeredBy({ id: 1 })).toBe("unknown")
  })

  it("falls back across runner fields", () => {
    expect(runnerName({ id: 1, runner_name: "fleet-1" })).toBe("fleet-1")
    expect(runnerName({ id: 1, runner: { name: "fleet-2" } })).toBe("fleet-2")
    expect(runnerName({ id: 1 })).toBe("-")
  })
})

describe("timingLabel", () => {
  it("returns an empty string when timing data is absent", () => {
    expect(timingLabel({ id: 1 })).toBe("")
  })

  it("shows only the queue wait for not-yet-executing runs", () => {
    expect(
      timingLabel({ id: 1, timing: { queue_wait_seconds: 90, exec_seconds: 0 } }),
    ).toBe("Queue: 1m 30s")
  })

  it("shows queue and exec breakdown for in-progress runs", () => {
    expect(
      timingLabel({ id: 1, timing: { queue_wait_seconds: 30, exec_seconds: 65 } }),
    ).toBe("Queue: 30s | Exec: 1m 5s")
  })
})

describe("statusTone / statusLabel", () => {
  it("maps each status to its badge tone", () => {
    expect(statusTone("running")).toBe("warning")
    expect(statusTone("queued")).toBe("info")
    expect(statusTone("failed")).toBe("danger")
    expect(statusTone("stale")).toBe("neutral")
    expect(statusTone("all")).toBe("neutral")
  })

  it("maps each status to its label", () => {
    expect(statusLabel("running")).toBe("running")
    expect(statusLabel("queued")).toBe("queued")
    expect(statusLabel("failed")).toBe("failed")
    expect(statusLabel("stale")).toBe("stale")
    expect(statusLabel("all")).toBe("unknown")
  })
})
