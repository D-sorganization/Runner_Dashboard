/**
 * TabErrorBoundary — per-tab React error boundary (D1 / issue #720).
 *
 * Provides isolated error recovery for each dashboard tab.
 * A tab crashing must not affect sibling tabs (orthogonality principle).
 *
 * Preconditions:
 *  - tabName must be a non-empty string identifying the owning tab.
 *
 * Postconditions:
 *  - On error: renders a focusable alert with the tab name, error message,
 *    and a "Reload tab" button that resets boundary state.
 *  - On successful render: passes through children unchanged.
 */

import React, { Component, ErrorInfo, ReactNode } from 'react';

export interface TabErrorBoundaryProps {
  /** Human-readable name of the tab (used in the fallback heading). */
  tabName: string;
  children: ReactNode;
  /** Optional callback fired when the user triggers a reset. */
  onReset?: () => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class TabErrorBoundary extends Component<TabErrorBoundaryProps, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error(`[TabErrorBoundary:${this.props.tabName}]`, error, info);
  }

  private reset(): void {
    this.setState({ hasError: false, error: null });
    this.props.onReset?.();
  }

  render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    const { tabName } = this.props;
    const { error } = this.state;

    return (
      <div
        role="alert"
        aria-live="assertive"
        style={{
          padding: '2rem',
          textAlign: 'center',
          fontFamily: 'system-ui, -apple-system, sans-serif',
          color: 'var(--text-primary, #e6edf3)',
        }}
      >
        <h2 style={{ marginBottom: '0.5rem', fontSize: '1.1rem', fontWeight: 600 }}>
          {tabName} tab encountered an error
        </h2>
        {error?.message && (
          <p
            style={{
              marginBottom: '1.25rem',
              color: 'var(--text-secondary, #8b949e)',
              fontSize: '0.875rem',
            }}
          >
            {error.message}
          </p>
        )}
        <button
          onClick={() => this.reset()}
          style={{
            padding: '0.5rem 1.25rem',
            background: 'var(--accent-blue, #58a6ff)',
            color: '#fff',
            border: 'none',
            borderRadius: '6px',
            cursor: 'pointer',
            fontSize: '0.875rem',
            fontWeight: 500,
          }}
        >
          Reload tab
        </button>
      </div>
    );
  }
}
