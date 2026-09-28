/**
 * Build identifier for the service worker URL (/sw.js?build=<id>), #1740.
 *
 * Pure so it can be unit-tested; vite.config.ts supplies the inputs at build
 * time. Every build must get a distinct id, otherwise the worker never updates
 * after a deploy and its cache never rotates.
 */
export interface BuildIdInputs {
  /** VITE_BUILD_ID from the environment, if the deploy pipeline set one. */
  explicit: string | undefined
  /** Output of `git rev-parse --short HEAD`, or null when git is unavailable. */
  gitSha: string | null
  /** Build time in epoch milliseconds. */
  now: number
}

export function resolveBuildId({ explicit, gitSha, now }: BuildIdInputs): string {
  const fromEnv = explicit?.trim()
  if (fromEnv) return fromEnv
  const sha = gitSha?.trim()
  if (sha) return sha
  return `t${now}`
}
