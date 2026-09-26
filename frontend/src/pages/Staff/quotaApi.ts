/**
 * quotaApi.ts — plan quota types and fetchers for Staff Hub (#1587, #1588, #1593).
 *
 * Extracted from staffApi.ts to keep modules under 500 lines.
 */
import { apiRequest } from "../../lib/api";

export interface QuotaWindow {
  name: string;
  used_percent: number;
  resets_at: string | null;
}

export interface QuotaSnapshot {
  account: string;
  observed_at: string;
  source: string;
  plan: string | null;
  limited_until: string | null;
  peak_percent: number | null;
  windows: QuotaWindow[];
}

export interface QuotaReport {
  generated_at: string;
  providers: {
    provider: string;
    billing: string;
    readable: boolean;
    quota: QuotaSnapshot | null;
  }[];
}

export interface QuotaRow {
  provider: string;
  peak: number | null;
  tone: "success" | "warning" | "danger" | "neutral";
  summary: string;
  limitedUntil: string | null;
}

/** The node default window ceiling; roles may lower it (`budget.max_window_percent`). */
export const QUOTA_CEILING_PERCENT = 85;

const WINDOW_SHORT: Record<string, string> = {
  five_hour: "5h",
  seven_day: "7d",
};

export function fetchQuota(signal?: AbortSignal): Promise<QuotaReport> {
  return apiRequest<QuotaReport>("/api/staff/quota", { signal });
}

/** Subscription plans only, each window fullest first; tone flags the ceiling. */
export function quotaRows(report: QuotaReport): QuotaRow[] {
  return report.providers
    .filter((row) => row.billing === "subscription")
    .map((row) => {
      const quota = row.quota;
      if (!quota) {
        return {
          provider: row.provider,
          peak: null,
          tone: "neutral" as const,
          summary: row.readable ? "no data yet" : "not readable",
          limitedUntil: null,
        };
      }
      const windows = [...quota.windows].sort(
        (a, b) => b.used_percent - a.used_percent,
      );
      const peak = quota.peak_percent;
      const tone =
        quota.limited_until || (peak ?? 0) >= QUOTA_CEILING_PERCENT
          ? ("danger" as const)
          : (peak ?? 0) >= 60
            ? ("warning" as const)
            : ("success" as const);
      return {
        provider: row.provider,
        peak,
        tone,
        summary: windows
          .map(
            (w) =>
              `${WINDOW_SHORT[w.name] ?? w.name} ${Math.round(w.used_percent)}%`,
          )
          .join(" · "),
        limitedUntil: quota.limited_until,
      };
    });
}
