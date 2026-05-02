/**
 * M12 — Reports mobile view (issue #186).
 *
 * Read-mostly view of assessment/progress reports. Features:
 * - List report files as tappable cards (filename, date, size)
 * - Tap a card → BottomSheet with the report title + download/view action
 * - SegmentedControl to filter by type (All, Daily, Charts)
 * - PullToRefresh
 * - Empty state if no reports
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { SkeletonCard, SkeletonLine } from "../../primitives/Skeleton";
import { PullToRefresh } from "../../primitives/PullToRefresh";
import { BottomSheet } from "../../primitives/BottomSheet";
import { SegmentedControl } from "../../primitives/SegmentedControl";
import { useHaptic } from "../../hooks/useHaptic";

interface ReportFile {
  filename: string;
  date: string;
  size_kb: number;
  modified: string;
  has_chart: boolean;
  chart_filename: string | null;
}

type ReportFilter = "all" | "daily" | "charts";

const FILTER_OPTIONS = [
  { label: "All", value: "all" },
  { label: "Daily", value: "daily" },
  { label: "Charts", value: "charts" },
];

function formatDate(dateStr: string): string {
  try {
    const d = new Date(dateStr.includes("T") ? dateStr : dateStr + "T00:00:00");
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  } catch {
    return dateStr;
  }
}

function formatSize(kb: number): string {
  if (kb >= 1024) return `${(kb / 1024).toFixed(1)} MB`;
  return `${kb.toFixed(1)} KB`;
}

interface ReportCardProps {
  report: ReportFile;
  onClick: (report: ReportFile) => void;
}

function ReportCard({ report, onClick }: ReportCardProps) {
  const isChart = report.filename.startsWith("assessment_scores_");
  const label = isChart ? `Chart: ${report.date}` : `Report: ${report.date}`;

  return (
    <button
      aria-label={label}
      className="report-card glass-card"
      onClick={() => onClick(report)}
      style={{
        alignItems: "flex-start",
        background: "var(--bg-secondary)",
        border: "1px solid var(--border)",
        borderRadius: "12px",
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        gap: "4px",
        marginBottom: "10px",
        padding: "14px 16px",
        textAlign: "left",
        width: "100%",
      }}
      type="button"
    >
      <div
        style={{
          alignItems: "center",
          display: "flex",
          gap: "8px",
          justifyContent: "space-between",
          width: "100%",
        }}
      >
        <span
          style={{
            color: "var(--text-primary)",
            fontSize: "14px",
            fontWeight: 600,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {report.filename}
        </span>
        <span
          style={{
            background: isChart ? "var(--accent-blue, #3b82f6)" : "var(--accent-green, #22c55e)",
            borderRadius: "4px",
            color: "#fff",
            flexShrink: 0,
            fontSize: "10px",
            fontWeight: 600,
            padding: "2px 6px",
            textTransform: "uppercase",
          }}
        >
          {isChart ? "Chart" : "Report"}
        </span>
      </div>
      <div
        style={{
          color: "var(--text-secondary)",
          display: "flex",
          fontSize: "12px",
          gap: "12px",
        }}
      >
        <span>{formatDate(report.date)}</span>
        <span>{formatSize(report.size_kb)}</span>
      </div>
    </button>
  );
}

interface ReportDetailSheetProps {
  report: ReportFile | null;
  onClose: () => void;
}

function ReportDetailSheet({ report, onClose }: ReportDetailSheetProps) {
  if (!report) return null;

  const isChart = report.filename.startsWith("assessment_scores_");
  const viewUrl = isChart
    ? `/api/reports/${report.date}/chart`
    : `/api/reports/${report.date}`;

  return (
    <BottomSheet
      isOpen={report !== null}
      onClose={onClose}
      title={isChart ? `Chart — ${report.date}` : `Report — ${report.date}`}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
        <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>
          <strong>File:</strong> {report.filename}
        </div>
        <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>
          <strong>Date:</strong> {formatDate(report.date)}
        </div>
        <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>
          <strong>Size:</strong> {formatSize(report.size_kb)}
        </div>
        <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>
          <strong>Modified:</strong>{" "}
          {new Date(report.modified).toLocaleString()}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "8px", paddingTop: "8px" }}>
          <a
            className="touch-button touch-button-primary"
            href={viewUrl}
            rel="noreferrer"
            style={{
              borderRadius: "8px",
              display: "block",
              fontSize: "14px",
              fontWeight: 600,
              minHeight: "44px",
              padding: "10px 16px",
              textAlign: "center",
              textDecoration: "none",
            }}
            target="_blank"
          >
            {isChart ? "View Chart" : "View Report"}
          </a>
          <a
            className="touch-button touch-button-secondary"
            download={report.filename}
            href={viewUrl}
            style={{
              borderRadius: "8px",
              display: "block",
              fontSize: "14px",
              fontWeight: 600,
              minHeight: "44px",
              padding: "10px 16px",
              textAlign: "center",
              textDecoration: "none",
            }}
          >
            Download
          </a>
        </div>
      </div>
    </BottomSheet>
  );
}

export function ReportsMobile() {
  const [reports, setReports] = useState<ReportFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<ReportFilter>("all");
  const [refreshing, setRefreshing] = useState(false);
  const [selectedReport, setSelectedReport] = useState<ReportFile | null>(null);

  const haptic = useHaptic();

  const fetchReports = useCallback(async () => {
    try {
      const resp = await fetch("/api/reports");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json = await resp.json();
      setReports(json.reports ?? []);
      setError(null);
    } catch (e) {
      setError((e instanceof Error ? e.message : String(e)) || "Failed to load reports");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchReports();
  }, [fetchReports]);

  const filtered = useMemo(() => {
    if (filter === "daily") {
      return reports.filter((r) => r.filename.startsWith("daily_progress_report_"));
    }
    if (filter === "charts") {
      return reports.filter((r) => r.filename.startsWith("assessment_scores_"));
    }
    return reports;
  }, [reports, filter]);

  const handleRefresh = useCallback(async () => {
    haptic.medium();
    setRefreshing(true);
    await fetchReports();
    haptic.success();
  }, [fetchReports, haptic]);

  const handleCardClick = useCallback(
    (report: ReportFile) => {
      haptic.light();
      setSelectedReport(report);
    },
    [haptic],
  );

  const handleSheetClose = useCallback(() => {
    setSelectedReport(null);
  }, []);

  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-label="Loading reports"
        aria-live="polite"
        style={{ display: "flex", flexDirection: "column", gap: "10px", padding: "12px" }}
      >
        <SkeletonLine height={18} width="40%" />
        <SkeletonCard lines={2} />
        <SkeletonCard lines={2} />
        <SkeletonCard lines={2} />
      </div>
    );
  }

  if (error && reports.length === 0) {
    return (
      <div
        aria-live="assertive"
        role="alert"
        style={{ color: "var(--accent-red)", padding: "24px", textAlign: "center" }}
      >
        {error}
      </div>
    );
  }

  return (
    <section aria-label="Reports" style={{ padding: "0 12px 24px" }}>
      <div style={{ padding: "16px 0 8px" }}>
        <h1 style={{ color: "var(--text-primary)", fontSize: "18px", fontWeight: 700, margin: 0 }}>
          Reports
        </h1>
        <p style={{ color: "var(--text-secondary)", fontSize: "13px", margin: "4px 0 0" }}>
          {reports.length} report{reports.length !== 1 ? "s" : ""} available
        </p>
      </div>

      <SegmentedControl
        ariaLabel="Filter by report type"
        onChange={(v) => setFilter(v as ReportFilter)}
        options={FILTER_OPTIONS}
        value={filter}
      />

      {refreshing && (
        <div aria-live="polite" style={{ fontSize: "12px", padding: "8px 0", textAlign: "center" }}>
          Refreshing…
        </div>
      )}

      <PullToRefresh disabled={refreshing} onRefresh={handleRefresh}>
        <div style={{ marginTop: "12px", touchAction: "pan-y" }}>
          {filtered.length === 0 ? (
            <div
              aria-label="No reports found"
              role="status"
              style={{
                color: "var(--text-muted)",
                padding: "48px 16px",
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: "36px", marginBottom: "12px" }}>📋</div>
              <div style={{ fontSize: "15px", fontWeight: 600 }}>No reports found</div>
              <div style={{ fontSize: "13px", marginTop: "6px" }}>
                {filter !== "all"
                  ? "Try switching to a different filter."
                  : "Reports will appear here once generated."}
              </div>
            </div>
          ) : (
            filtered.map((report) => (
              <ReportCard key={report.filename} onClick={handleCardClick} report={report} />
            ))
          )}
        </div>
      </PullToRefresh>

      <ReportDetailSheet onClose={handleSheetClose} report={selectedReport} />
    </section>
  );
}
