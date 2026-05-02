/**
 * formatters.ts – pure formatting/utility helpers extracted from legacy/App.tsx (#403).
 *
 * These functions have no React dependency and can be used in any context.
 * The legacy App.tsx contains duplicates of several of these; new code should
 * import from here instead of re-implementing them inline.
 */

/** Returns a human-readable "X ago" string from a date value. */
export function timeAgo(d: string | Date | null | undefined): string {
  if (!d) return ""
  const s = (Date.now() - new Date(d).getTime()) / 1000
  if (s < 60) return Math.floor(s) + "s ago"
  if (s < 3600) return Math.floor(s / 60) + "m ago"
  if (s < 86400) return Math.floor(s / 3600) + "h ago"
  return Math.floor(s / 86400) + "d ago"
}

/** Formats a duration in seconds as "Xm Ys" or "Xs". */
export function formatDuration(s: number | null | undefined): string {
  if (!s || s < 0) return "-"
  if (s < 60) return s + "s"
  return Math.floor(s / 60) + "m " + (s % 60) + "s"
}

/** Formats a byte count as a human-readable size string. */
export function formatBytes(b: number): string {
  if (b < 1024) return b + " B"
  if (b < 1048576) return (b / 1024).toFixed(1) + " KB"
  if (b < 1073741824) return (b / 1048576).toFixed(1) + " MB"
  return (b / 1073741824).toFixed(2) + " GB"
}

/** Returns a CSS colour string for a percentage value (green/yellow/red). */
export function pColor(p: number): string {
  return p < 60 ? "green" : p < 85 ? "yellow" : "red"
}

/** Returns a translucent CSS colour for a CPU utilisation percentage. */
export function cpuColor(p: number): string {
  return p < 30
    ? "rgba(63,185,80,0.3)"
    : p < 60
      ? "rgba(63,185,80,0.6)"
      : p < 80
        ? "rgba(210,153,34,0.6)"
        : "rgba(248,81,73,0.7)"
}

/** Returns the first 7 characters of a git SHA, or "unknown". */
export function shortSha(sha: string | null | undefined): string {
  return sha ? String(sha).slice(0, 7) : "unknown"
}

/** Clamps a value to the range [0, 100]. */
export function boundedPercent(value: number): number {
  return Math.max(0, Math.min(100, value))
}

/** Map of programming language names to their canonical GitHub colours. */
export const LANG_COLORS: Record<string, string> = {
  JavaScript: "#f1e05a",
  TypeScript: "#3178c6",
  Python: "#3572A5",
  Rust: "#dea584",
  Go: "#00ADD8",
  Java: "#b07219",
  C: "#555555",
  "C++": "#f34b7d",
  "C#": "#178600",
  Ruby: "#701516",
  Shell: "#89e051",
  HTML: "#e34c26",
  CSS: "#563d7c",
  MATLAB: "#e16737",
  Jupyter: "#DA5B0B",
  Vue: "#41b883",
  Swift: "#F05138",
  Kotlin: "#A97BFF",
  Dart: "#00B4AB",
}

/**
 * safeOpen – open a URL in a new tab only when it belongs to a trusted
 * origin (issue #30). Blocks arbitrary URLs that could be injected via
 * API responses.
 */
export function safeOpen(url: string): void {
  if (
    !url.startsWith("http://localhost") &&
    !url.startsWith("https://github.com/") &&
    !url.startsWith("https://api.github.com/")
  ) {
    console.error("Blocked unsafe URL:", url)
    return
  }
  window.open(url, "_blank", "noopener,noreferrer")
}
