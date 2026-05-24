// @vitest-environment jsdom
/**
 * Tests for TabErrorBoundary (D1 / issue #720).
 *
 * Covers:
 * 1. Renders children when no error.
 * 2. Shows fallback with tab name when child throws.
 * 3. "Reload tab" button remounts the child (resets error state).
 * 4. role="alert" is present on the fallback.
 * 5. aria-live="assertive" on the fallback.
 * 6. onReset callback is called when boundary resets.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TabErrorBoundary } from '../TabErrorBoundary';

// Silence React error boundary console noise
beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

function Bomb({ shouldThrow }: { shouldThrow: boolean }): React.ReactElement {
  if (shouldThrow) throw new Error('Tab explosion');
  return <div data-testid="ok">Content loaded</div>;
}

describe('TabErrorBoundary', () => {
  it('renders children when no error occurs', () => {
    render(
      <TabErrorBoundary tabName="Fleet">
        <Bomb shouldThrow={false} />
      </TabErrorBoundary>,
    );
    expect(screen.getByTestId('ok')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('renders fallback with tab name when child throws', () => {
    render(
      <TabErrorBoundary tabName="Maxwell">
        <Bomb shouldThrow={true} />
      </TabErrorBoundary>,
    );
    const alert = screen.getByRole('alert');
    expect(alert).toBeTruthy();
    expect(alert.textContent).toContain('Maxwell');
  });

  it('shows the error message in the fallback', () => {
    render(
      <TabErrorBoundary tabName="Fleet">
        <Bomb shouldThrow={true} />
      </TabErrorBoundary>,
    );
    expect(screen.getByRole('alert').textContent).toContain('Tab explosion');
  });

  it('has aria-live="assertive" on fallback', () => {
    render(
      <TabErrorBoundary tabName="Fleet">
        <Bomb shouldThrow={true} />
      </TabErrorBoundary>,
    );
    const alert = screen.getByRole('alert');
    expect(alert.getAttribute('aria-live')).toBe('assertive');
  });

  it('"Reload tab" button resets the error state', () => {
    let shouldThrow = true;

    function ToggleBomb(): React.ReactElement {
      if (shouldThrow) throw new Error('boom');
      return <div data-testid="recovered">Recovered</div>;
    }

    const { rerender } = render(
      <TabErrorBoundary tabName="Queue">
        <ToggleBomb />
      </TabErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toBeTruthy();

    shouldThrow = false;
    fireEvent.click(screen.getByRole('button', { name: /reload tab/i }));
    rerender(
      <TabErrorBoundary tabName="Queue">
        <ToggleBomb />
      </TabErrorBoundary>,
    );
    expect(screen.getByTestId('recovered')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('calls onReset callback when boundary resets', () => {
    const onReset = vi.fn();
    let shouldThrow = true;

    function ToggleBomb(): React.ReactElement {
      if (shouldThrow) throw new Error('boom');
      return <div data-testid="ok">ok</div>;
    }

    const { rerender } = render(
      <TabErrorBoundary tabName="Remediation" onReset={onReset}>
        <ToggleBomb />
      </TabErrorBoundary>,
    );

    shouldThrow = false;
    fireEvent.click(screen.getByRole('button', { name: /reload tab/i }));
    rerender(
      <TabErrorBoundary tabName="Remediation" onReset={onReset}>
        <ToggleBomb />
      </TabErrorBoundary>,
    );
    expect(onReset).toHaveBeenCalledOnce();
  });
});
