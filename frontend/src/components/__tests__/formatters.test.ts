/**
 * Tests for components/formatters.ts — pure formatting helpers extracted from
 * legacy/App.tsx (#403). These give the legacy data-flow utilities real smoke
 * coverage without touching App.tsx itself.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import {
  LANG_COLORS,
  boundedPercent,
  cpuColor,
  formatBytes,
  formatDuration,
  pColor,
  safeOpen,
  shortSha,
  timeAgo,
} from "../formatters"

describe("timeAgo", () => {
  it("returns an empty string for nullish input", () => {
    expect(timeAgo(null)).toBe("")
    expect(timeAgo(undefined)).toBe("")
  })

  it("formats seconds, minutes, hours and days", () => {
    const now = Date.now()
    expect(timeAgo(new Date(now - 5_000))).toBe("5s ago")
    expect(timeAgo(new Date(now - 5 * 60_000))).toBe("5m ago")
    expect(timeAgo(new Date(now - 5 * 3_600_000))).toBe("5h ago")
    expect(timeAgo(new Date(now - 5 * 86_400_000))).toBe("5d ago")
  })

  it("accepts ISO date strings", () => {
    const iso = new Date(Date.now() - 90_000).toISOString()
    expect(timeAgo(iso)).toBe("1m ago")
  })
})

describe("formatDuration", () => {
  it("returns a dash for nullish or negative values", () => {
    expect(formatDuration(null)).toBe("-")
    expect(formatDuration(undefined)).toBe("-")
    expect(formatDuration(-1)).toBe("-")
  })

  it("formats sub-minute durations in seconds", () => {
    expect(formatDuration(45)).toBe("45s")
  })

  it("formats durations of a minute or more as Xm Ys", () => {
    expect(formatDuration(90)).toBe("1m 30s")
    expect(formatDuration(120)).toBe("2m 0s")
  })
})

describe("formatBytes", () => {
  it("formats bytes, KB, MB and GB", () => {
    expect(formatBytes(512)).toBe("512 B")
    expect(formatBytes(2048)).toBe("2.0 KB")
    expect(formatBytes(5 * 1_048_576)).toBe("5.0 MB")
    expect(formatBytes(3 * 1_073_741_824)).toBe("3.00 GB")
  })
})

describe("pColor", () => {
  it("maps percentages to green/yellow/red thresholds", () => {
    expect(pColor(10)).toBe("green")
    expect(pColor(70)).toBe("yellow")
    expect(pColor(90)).toBe("red")
  })
})

describe("cpuColor", () => {
  it("returns translucent colours across the four utilisation bands", () => {
    expect(cpuColor(10)).toBe("rgba(63,185,80,0.3)")
    expect(cpuColor(45)).toBe("rgba(63,185,80,0.6)")
    expect(cpuColor(70)).toBe("rgba(210,153,34,0.6)")
    expect(cpuColor(95)).toBe("rgba(248,81,73,0.7)")
  })
})

describe("shortSha", () => {
  it("truncates to seven characters", () => {
    expect(shortSha("0123456789abcdef")).toBe("0123456")
  })

  it("returns 'unknown' for nullish input", () => {
    expect(shortSha(null)).toBe("unknown")
    expect(shortSha(undefined)).toBe("unknown")
  })
})

describe("boundedPercent", () => {
  it("clamps to the [0, 100] range", () => {
    expect(boundedPercent(-5)).toBe(0)
    expect(boundedPercent(50)).toBe(50)
    expect(boundedPercent(150)).toBe(100)
  })
})

describe("LANG_COLORS", () => {
  it("exposes canonical GitHub language colours", () => {
    expect(LANG_COLORS.TypeScript).toBe("#3178c6")
    expect(LANG_COLORS.Python).toBe("#3572A5")
  })
})

describe("safeOpen", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("opens trusted origins in a new tab", () => {
    const open = vi.fn()
    vi.stubGlobal("window", { ...window, open })
    safeOpen("https://github.com/org/repo")
    expect(open).toHaveBeenCalledWith(
      "https://github.com/org/repo",
      "_blank",
      "noopener,noreferrer",
    )
    vi.unstubAllGlobals()
  })

  it("blocks untrusted origins and logs an error", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null)
    const err = vi.spyOn(console, "error").mockImplementation(() => {})
    safeOpen("https://evil.example.com/steal")
    expect(open).not.toHaveBeenCalled()
    expect(err).toHaveBeenCalled()
  })
})
