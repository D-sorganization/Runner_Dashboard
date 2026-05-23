/**
 * Tests for <TimeAgo /> primitive.
 *
 * Covers issue #725 (D6).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TimeAgo } from '../TimeAgo';

const NOW = new Date('2026-05-22T12:00:00Z').getTime();

describe('<TimeAgo />', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders a <time> element with the ISO dateTime attribute', () => {
    const iso = new Date(NOW - 60_000).toISOString();
    render(<TimeAgo iso={iso} />);
    const node = screen.getByText('1m ago');
    expect(node.tagName).toBe('TIME');
    expect(node).toHaveAttribute('datetime', iso);
  });

  it('exposes the full ISO timestamp as a tooltip via the title attribute', () => {
    const iso = new Date(NOW - 60_000).toISOString();
    render(<TimeAgo iso={iso} />);
    expect(screen.getByText('1m ago')).toHaveAttribute('title', iso);
  });

  it('honours a custom title override (e.g. localized full date)', () => {
    const iso = new Date(NOW - 60_000).toISOString();
    render(<TimeAgo iso={iso} title="2026-05-22 11:59 UTC" />);
    expect(screen.getByText('1m ago')).toHaveAttribute(
      'title',
      '2026-05-22 11:59 UTC',
    );
  });

  it('falls back gracefully on invalid input by rendering the raw value', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(<TimeAgo iso="not-a-date" />);
    expect(screen.getByText('not-a-date')).toBeInTheDocument();
    warnSpy.mockRestore();
  });
});
