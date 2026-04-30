/**
 * RootErrorBoundary — top-level React error boundary.
 *
 * Wraps all routes so a render throw in any tab cannot white-screen
 * the entire dashboard. Satisfies issue #384 acceptance criteria:
 *
 * 1. Wraps all routes; logs to console (Sentry stub in prod).
 * 2. Renders "Something went wrong – Reload" with hidden stack details.
 * 3. Error boundary state resets on route change (via `key` prop at call site).
 * 4. Surfaces request_id from X-Request-ID header when available.
 *
 * Per-route boundaries are handled by react-router errorElement.
 * This component is the last-resort catch for anything that escapes those.
 */

import React, { Component, ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** Optional request ID from the last failing backend call (X-Request-ID). */
  requestId?: string
}

interface State {
  hasError: boolean
  error: Error | null
  errorInfo: ErrorInfo | null
}

export class RootErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
    errorInfo: null,
  }

  public static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error }
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // In production this would call Sentry.captureException(error, { contexts: { react: errorInfo } })
    // eslint-disable-next-line no-console
    console.error('[RootErrorBoundary] Uncaught error:', error)
    // eslint-disable-next-line no-console
    console.error('[RootErrorBoundary] Component stack:', errorInfo.componentStack)
    this.setState({ errorInfo })
  }

  private handleReload = (): void => {
    window.location.reload()
  }

  private handleReset = (): void => {
    this.setState({ hasError: false, error: null, errorInfo: null })
  }

  public render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children
    }

    const { error, errorInfo } = this.state
    const { requestId } = this.props

    return (
      <div
        role="alert"
        aria-live="assertive"
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '60vh',
          padding: '2rem',
          fontFamily: 'system-ui, -apple-system, sans-serif',
          color: '#24292f',
          background: '#f6f8fa',
        }}
      >
        <div
          style={{
            maxWidth: '540px',
            width: '100%',
            background: '#fff',
            border: '1px solid #d0d7de',
            borderRadius: '12px',
            padding: '2rem',
            boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
          }}
        >
          <h1
            style={{
              margin: '0 0 0.5rem',
              fontSize: '1.25rem',
              fontWeight: 600,
              color: '#cf222e',
            }}
          >
            Something went wrong
          </h1>
          <p style={{ margin: '0 0 1.5rem', color: '#57606a', fontSize: '0.9rem' }}>
            A rendering error occurred in the dashboard. Your data is safe.
          </p>

          <div style={{ display: 'flex', gap: '0.75rem', marginBottom: requestId ? '1rem' : '0' }}>
            <button
              id="error-boundary-reload"
              onClick={this.handleReload}
              style={{
                padding: '0.5rem 1rem',
                background: '#0969da',
                color: '#fff',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                fontSize: '0.9rem',
                fontWeight: 500,
              }}
            >
              Reload dashboard
            </button>
            <button
              id="error-boundary-reset"
              onClick={this.handleReset}
              style={{
                padding: '0.5rem 1rem',
                background: 'transparent',
                color: '#0969da',
                border: '1px solid #0969da',
                borderRadius: '6px',
                cursor: 'pointer',
                fontSize: '0.9rem',
                fontWeight: 500,
              }}
            >
              Try again
            </button>
          </div>

          {requestId && (
            <p style={{ margin: '1rem 0 0', color: '#57606a', fontSize: '0.78rem' }}>
              Request ID: <code style={{ background: '#f6f8fa', padding: '2px 4px', borderRadius: '4px' }}>{requestId}</code>
            </p>
          )}

          <details style={{ marginTop: '1.5rem' }}>
            <summary
              id="error-boundary-details-toggle"
              style={{
                cursor: 'pointer',
                color: '#57606a',
                fontSize: '0.85rem',
                userSelect: 'none',
              }}
            >
              Show details
            </summary>
            <pre
              style={{
                marginTop: '0.75rem',
                padding: '0.75rem',
                background: '#f6f8fa',
                border: '1px solid #d0d7de',
                borderRadius: '6px',
                overflowX: 'auto',
                fontSize: '0.75rem',
                color: '#24292f',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {error?.toString()}
              {errorInfo?.componentStack && `\n\nComponent stack:${errorInfo.componentStack}`}
            </pre>
          </details>
        </div>
      </div>
    )
  }
}
