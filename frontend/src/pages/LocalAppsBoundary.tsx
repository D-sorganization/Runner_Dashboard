import React from "react";
import type { LocalAppsProps } from "./LocalApps";

interface BoundaryState {
  hasError: boolean;
  error: Error | null;
}

interface LocalAppsBoundaryProps extends LocalAppsProps {
  children: React.ReactNode;
}

/**
 * Wraps the page body so a malformed app entry degrades to a Retry affordance
 * instead of crashing the shell.
 */
export class LocalAppsBoundary extends React.Component<
  LocalAppsBoundaryProps,
  BoundaryState
> {
  constructor(props: LocalAppsBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
    this.handleRetry = this.handleRetry.bind(this);
  }

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error("[LocalAppsTab] render error:", error, info);
  }

  handleRetry(): void {
    this.setState({ hasError: false, error: null });
  }

  render(): React.ReactNode {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 24, color: "var(--text-primary)" }}>
          <div
            style={{
              marginBottom: 12,
              color: "var(--accent-red)",
              fontWeight: 600,
            }}
          >
            Local Tools failed to render
          </div>
          <code
            style={{
              display: "block",
              fontSize: 12,
              color: "var(--accent-red)",
              marginBottom: 12,
              whiteSpace: "pre-wrap",
            }}
          >
            {String(this.state.error)}
          </code>
          <button
            className="btn"
            type="button"
            onClick={this.handleRetry}
            aria-label="Retry loading data"
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
