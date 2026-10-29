/**
 * RdBarbRelayBanner — 5-minute degraded relay banner for rd-barb (#3075).
 */

export const DEGRADED_AFTER_S = 300

export interface RdBarbRelayStatus {
  state: string
  failing_since?: number | null
  queued_count?: number
  unread_inbox_count?: number
  last_error?: string
  degraded_after_s?: number
}

export function isRelayBannerVisible(status: RdBarbRelayStatus | null | undefined, now?: number): boolean {
  if (!status || status.state === "ok" || status.state === "never_attempted") return false
  const threshold = status.degraded_after_s ?? DEGRADED_AFTER_S
  if (typeof status.failing_since === "number" && typeof now === "number") {
    return now - status.failing_since >= threshold
  }
  return status.state === "degraded"
}

export function RdBarbRelayBanner({ status, now }: { status: RdBarbRelayStatus | null | undefined; now?: number }) {
  if (!isRelayBannerVisible(status, now)) return null
  return (
    <div role="alert" className="rd-barb-degraded-banner" data-testid="rd-barb-degraded-banner">
      rd-barb relay degraded (failing &ge; 5 min; {status?.queued_count ?? 0} queued)
    </div>
  )
}

export default RdBarbRelayBanner
