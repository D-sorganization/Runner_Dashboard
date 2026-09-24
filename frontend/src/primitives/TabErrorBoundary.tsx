/**
 * TabErrorBoundary — per-tab React error boundary (D1 / issues #720, #1292).
 *
 * Provides isolated error recovery for each dashboard tab.
 * A tab crashing must not affect sibling tabs or the shell navigation (orthogonality principle).
 *
 * Preconditions:
 *  - tabName must be a non-empty string identifying the owning tab.
 *
 * Postconditions:
 *  - On error: renders a focusable alert with the tab name, error message,
 *    "Reload tab" / "Retry" button, "Copy details" button, and prefilled "Report issue" link.
 *  - Reports caught errors to POST /api/client-errors (rate-limited, never throws).
 *  - Resets automatically when resetKey (e.g. activeTab) changes.
 *  - On successful render: passes through children unchanged.
 */

import React, { Component, ErrorInfo, ReactNode } from "react";

export interface ClientErrorReport {
  page: string;
  message: string;
  stack?: string;
  build_sha?: string;
  component?: string;
}

export interface TabErrorBoundaryProps {
  /** Human-readable name of the tab (used in the fallback heading). */
  tabName: string;
  children: ReactNode;
  /** Optional callback fired when the user triggers a reset. */
  onReset?: () => void;
  /** Optional key (e.g. activeTab or route path) that resets the boundary on change. */
  resetKey?: string | number;
  /** Optional build SHA/ID for bug report prefill and beaconing. */
  buildSha?: string;
  /** Optional custom reporting function (injectable for unit tests). */
  reportErrorFn?: (payload: ClientErrorReport) => Promise<unknown>;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
  copied: boolean;
}

// Client-side rate-limiting window (drops reports after N per minute)
const MAX_CLIENT_REPORTS_PER_MINUTE = 10;
const _clientReportTimestamps: number[] = [];

/**
 * Report caught error to backend beacon. Safe against network failures and loops.
 */
export function reportClientError(report: ClientErrorReport): Promise<void> {
  try {
    const now = Date.now();
    const cutoff = now - 60_000;
    while (
      _clientReportTimestamps.length > 0 &&
      _clientReportTimestamps[0] < cutoff
    ) {
      _clientReportTimestamps.shift();
    }
    if (_clientReportTimestamps.length >= MAX_CLIENT_REPORTS_PER_MINUTE) {
      // eslint-disable-next-line no-console
      console.warn(
        `[reportClientError] Rate limit reached (${MAX_CLIENT_REPORTS_PER_MINUTE}/min). Dropping report for ${report.page}.`,
      );
      return Promise.resolve();
    }
    _clientReportTimestamps.push(now);

    const endpoint =
      typeof window !== "undefined" &&
      window.location &&
      window.location.origin &&
      window.location.origin !== "null"
        ? `${window.location.origin}/api/client-errors`
        : "/api/client-errors";

    return fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(report),
    })
      .then(() => {})
      .catch((err) => {
        // eslint-disable-next-line no-console
        console.warn("[reportClientError] Failed to send error report:", err);
      });
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn(
      "[reportClientError] Error reporting threw synchronously:",
      err,
    );
    return Promise.resolve();
  }
}

export class TabErrorBoundary extends Component<TabErrorBoundaryProps, State> {
  state: State = {
    hasError: false,
    error: null,
    errorInfo: null,
    copied: false,
  };

  private copyTimeout: ReturnType<typeof setTimeout> | null = null;

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.setState({ errorInfo: info });
    // eslint-disable-next-line no-console
    console.error(`[TabErrorBoundary:${this.props.tabName}]`, error, info);

    const buildSha =
      this.props.buildSha ||
      (import.meta.env as Record<string, string>)?.VITE_BUILD_ID ||
      "dev";
    const reportFn = this.props.reportErrorFn || reportClientError;

    reportFn({
      page: this.props.tabName,
      message: error.message || String(error),
      stack: error.stack || info.componentStack || undefined,
      build_sha: buildSha,
      component: this.props.tabName,
    }).catch(() => {});
  }

  componentDidUpdate(prevProps: TabErrorBoundaryProps): void {
    if (
      this.props.resetKey !== undefined &&
      prevProps.resetKey !== this.props.resetKey &&
      this.state.hasError
    ) {
      this.reset();
    }
  }

  componentWillUnmount(): void {
    if (this.copyTimeout) {
      clearTimeout(this.copyTimeout);
    }
  }

  private reset(): void {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
      copied: false,
    });
    this.props.onReset?.();
  }

  private copyDetails(): void {
    const { tabName } = this.props;
    const { error, errorInfo } = this.state;
    const buildSha =
      this.props.buildSha ||
      (import.meta.env as Record<string, string>)?.VITE_BUILD_ID ||
      "dev";

    const details = [
      `Tab: ${tabName}`,
      `Build: ${buildSha}`,
      `Error: ${error?.message || "Unknown error"}`,
      `Time: ${new Date().toISOString()}`,
      `Stack:\n${error?.stack || errorInfo?.componentStack || "No stack trace available"}`,
    ].join("\n");

    if (navigator?.clipboard?.writeText) {
      navigator.clipboard
        .writeText(details)
        .then(() => {
          this.setState({ copied: true });
          if (this.copyTimeout) clearTimeout(this.copyTimeout);
          this.copyTimeout = setTimeout(() => {
            this.setState({ copied: false });
          }, 2000);
        })
        .catch(() => {});
    }
  }

  render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    const { tabName } = this.props;
    const { error, errorInfo, copied } = this.state;
    const buildSha =
      this.props.buildSha ||
      (import.meta.env as Record<string, string>)?.VITE_BUILD_ID ||
      "dev";

    const titleParam = encodeURIComponent(
      `[Bug]: Tab error in ${tabName}: ${error?.message?.slice(0, 60) || "render failure"}`,
    );
    const bodyParam = encodeURIComponent(
      `### Bug Description\n\nA render error occurred in the **${tabName}** tab.\n\n` +
        `**Error message:**\n\`\`\`\n${error?.message || "Unknown error"}\n\`\`\`\n\n` +
        `**Build SHA:** \`${buildSha}\`\n\n` +
        `**Stack trace:**\n\`\`\`\n${error?.stack || errorInfo?.componentStack || "No stack trace"}\n\`\`\`\n`,
    );
    const reportUrl = `https://github.com/D-sorganization/Runner_Dashboard/issues/new?title=${titleParam}&body=${bodyParam}&labels=bug,frontend`;

    return (
      <div
        role="alert"
        aria-live="assertive"
        style={{
          padding: "2.5rem 1.5rem",
          maxWidth: "680px",
          margin: "2rem auto",
          textAlign: "center",
          fontFamily: "system-ui, -apple-system, sans-serif",
          color: "var(--text-primary, #e6edf3)",
          background: "var(--bg-secondary, #161b22)",
          border: "1px solid var(--border-color, #30363d)",
          borderRadius: "8px",
          boxShadow: "0 4px 12px rgba(0, 0, 0, 0.15)",
        }}
      >
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: "48px",
            height: "48px",
            borderRadius: "50%",
            background: "rgba(248, 81, 73, 0.15)",
            color: "var(--accent-red, #f85149)",
            marginBottom: "1rem",
            fontSize: "1.5rem",
            fontWeight: 700,
          }}
        >
          !
        </div>
        <h2
          style={{
            marginBottom: "0.5rem",
            fontSize: "1.25rem",
            fontWeight: 600,
          }}
        >
          {tabName} tab encountered an error
        </h2>
        {error?.message && (
          <p
            style={{
              marginBottom: "1.5rem",
              color: "var(--text-secondary, #8b949e)",
              fontSize: "0.875rem",
              wordBreak: "break-word",
            }}
          >
            {error.message}
          </p>
        )}
        <div
          style={{
            display: "flex",
            gap: "0.75rem",
            justifyContent: "center",
            flexWrap: "wrap",
            alignItems: "center",
          }}
        >
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => this.reset()}
            style={{
              padding: "0.5rem 1.25rem",
              background: "var(--accent-blue, #58a6ff)",
              color: "#fff",
              border: "none",
              borderRadius: "6px",
              cursor: "pointer",
              fontSize: "0.875rem",
              fontWeight: 500,
            }}
          >
            Reload tab
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => this.copyDetails()}
            style={{
              padding: "0.5rem 1.25rem",
              background: "var(--bg-tertiary, #21262d)",
              color: "var(--text-primary, #e6edf3)",
              border: "1px solid var(--border-color, #30363d)",
              borderRadius: "6px",
              cursor: "pointer",
              fontSize: "0.875rem",
              fontWeight: 500,
            }}
          >
            {copied ? "Copied!" : "Copy details"}
          </button>
          <a
            href={reportUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="btn"
            style={{
              display: "inline-block",
              padding: "0.5rem 1.25rem",
              background: "transparent",
              color: "var(--accent-blue, #58a6ff)",
              border: "1px solid var(--border-color, #30363d)",
              borderRadius: "6px",
              textDecoration: "none",
              fontSize: "0.875rem",
              fontWeight: 500,
            }}
          >
            Report issue
          </a>
        </div>
      </div>
    );
  }
}
