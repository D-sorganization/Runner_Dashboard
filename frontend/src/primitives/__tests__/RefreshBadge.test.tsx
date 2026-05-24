// @vitest-environment jsdom
/**
 * Tests for RefreshBadge component (D3 / issue #722).
 *
 * Covers:
 * 1. Renders "Live now" when fresh.
 * 2. Shows timestamp info when stale.
 * 3. Shows "Network error" when error.
 * 4. "Retry" is clickable in error state and calls onRetry.
 * 5. aria-live="polite" present.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RefreshBadge } from '../RefreshBadge';
import type { Staleness } from '../../hooks/useStalenessWarning';

const NOW = new Date('2026-05-23T12:00:00Z').getTime();

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

const fresh: Staleness = {
  state: 'fresh',
  lastSuccessAt: new Date(NOW - 5000),
  failureCount: 0,
  isFetching: false,
};

const stale: Staleness = {
  state: 'stale',
  lastSuccessAt: new Date(NOW - 120_000),
  failureCount: 0,
  isFetching: false,
};

const error: Staleness = {
  state: 'error',
  lastSuccessAt: new Date(NOW - 300_000),
  failureCount: 3,
  isFetching: false,
};

describe('RefreshBadge', () => {
  it('renders "Live now" when fresh', () => {
    render(<RefreshBadge staleness={fresh} onRetry={vi.fn()} />);
    expect(screen.getByText(/live now/i)).toBeTruthy();
  });

  it('renders staleness info when stale', () => {
    render(<RefreshBadge staleness={stale} onRetry={vi.fn()} />);
    // Should show something indicating last update time
    const el = screen.getByRole('status');
    expect(el.textContent).toBeTruthy();
    expect(el.textContent?.toLowerCase()).toContain('ago');
  });

  it('renders "Network error" when error', () => {
    render(<RefreshBadge staleness={error} onRetry={vi.fn()} />);
    expect(screen.getByText(/network error/i)).toBeTruthy();
  });

  it('shows "Retry" button in error state', () => {
    render(<RefreshBadge staleness={error} onRetry={vi.fn()} />);
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy();
  });

  it('calls onRetry when Retry button is clicked', () => {
    const onRetry = vi.fn();
    render(<RefreshBadge staleness={error} onRetry={onRetry} />);
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('has aria-live="polite" on the container', () => {
    render(<RefreshBadge staleness={fresh} onRetry={vi.fn()} />);
    const el = screen.getByRole('status');
    expect(el.getAttribute('aria-live')).toBe('polite');
  });

  it('does not render Retry button when fresh', () => {
    render(<RefreshBadge staleness={fresh} onRetry={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull();
  });
});
