// @vitest-environment jsdom
/**
 * Tests for TimeAgo component (D6 / issue #725).
 *
 * Covers:
 * 1. Renders a <time> element.
 * 2. dateTime attribute matches the ISO string.
 * 3. title attribute shows the full ISO string.
 * 4. "just now" for very recent dates.
 * 5. Custom className is forwarded.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TimeAgo } from '../TimeAgo';

const NOW = new Date('2026-05-23T12:00:00Z').getTime();

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('TimeAgo', () => {
  it('renders a <time> element', () => {
    const iso = new Date(NOW - 5000).toISOString();
    const { container } = render(<TimeAgo date={iso} />);
    const el = container.querySelector('time');
    expect(el).toBeTruthy();
  });

  it('sets dateTime attribute to ISO string', () => {
    const iso = new Date(NOW - 5000).toISOString();
    const { container } = render(<TimeAgo date={iso} live={false} />);
    const el = container.querySelector('time')!;
    expect(el.getAttribute('dateTime')).toBe(iso);
  });

  it('sets title attribute to ISO string for tooltip', () => {
    const iso = new Date(NOW - 5000).toISOString();
    const { container } = render(<TimeAgo date={iso} live={false} />);
    const el = container.querySelector('time')!;
    expect(el.getAttribute('title')).toBe(iso);
  });

  it('shows "just now" for very recent date', () => {
    const iso = new Date(NOW - 5000).toISOString();
    render(<TimeAgo date={iso} live={false} />);
    expect(screen.getByText('just now')).toBeTruthy();
  });

  it('accepts Date object and sets dateTime to ISO string', () => {
    const dateObj = new Date(NOW - 5000);
    const { container } = render(<TimeAgo date={dateObj} live={false} />);
    const el = container.querySelector('time')!;
    expect(el.getAttribute('dateTime')).toBe(dateObj.toISOString());
  });

  it('forwards className prop', () => {
    const iso = new Date(NOW - 5000).toISOString();
    const { container } = render(<TimeAgo date={iso} className="custom-class" live={false} />);
    const el = container.querySelector('time')!;
    expect(el.classList.contains('custom-class')).toBe(true);
  });
});
