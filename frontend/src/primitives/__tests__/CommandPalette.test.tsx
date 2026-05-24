// @vitest-environment jsdom
/**
 * Tests for CommandPalette (D4 / issue #723).
 *
 * Covers:
 * 1. Cmd+K (or Ctrl+K) opens the palette.
 * 2. Typing "fle" shows items matching "fleet".
 * 3. Enter activates the highlighted command and closes.
 * 4. Escape closes without navigating.
 * 5. Palette is not visible initially.
 * 6. Empty input shows all (or recent) commands.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CommandPalette } from '../CommandPalette';

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  // Clear localStorage between tests
  localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
});

const defaultCommands = [
  { id: 'fleet', label: 'Go to Fleet', group: 'Tabs', action: vi.fn(), keywords: ['fleet'] },
  { id: 'queue', label: 'Go to Queue', group: 'Tabs', action: vi.fn(), keywords: ['queue'] },
  { id: 'maxwell', label: 'Go to Maxwell', group: 'Tabs', action: vi.fn(), keywords: ['maxwell'] },
];

describe('CommandPalette', () => {
  it('is not visible initially', () => {
    render(<CommandPalette commands={defaultCommands} />);
    // The dialog/modal should not be in the document when closed
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('opens on Ctrl+K', () => {
    render(<CommandPalette commands={defaultCommands} />);
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('opens on Meta+K (Cmd+K)', () => {
    render(<CommandPalette commands={defaultCommands} />);
    fireEvent.keyDown(document, { key: 'k', metaKey: true });
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('closes on Escape', () => {
    render(<CommandPalette commands={defaultCommands} />);
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
    expect(screen.getByRole('dialog')).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('filters commands when typing', () => {
    render(<CommandPalette commands={defaultCommands} />);
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
    const input = screen.getByRole('combobox');
    fireEvent.change(input, { target: { value: 'fle' } });
    expect(screen.getByText('Go to Fleet')).toBeTruthy();
    // Queue and Maxwell should not be visible
    expect(screen.queryByText('Go to Queue')).toBeNull();
    expect(screen.queryByText('Go to Maxwell')).toBeNull();
  });

  it('shows all commands on empty input', () => {
    render(<CommandPalette commands={defaultCommands} />);
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
    const input = screen.getByRole('combobox');
    fireEvent.change(input, { target: { value: '' } });
    expect(screen.getByText('Go to Fleet')).toBeTruthy();
    expect(screen.getByText('Go to Queue')).toBeTruthy();
    expect(screen.getByText('Go to Maxwell')).toBeTruthy();
  });

  it('calls command action and closes when item is clicked', () => {
    const action = vi.fn();
    const commands = [
      { id: 'fleet', label: 'Go to Fleet', group: 'Tabs', action, keywords: ['fleet'] },
    ];
    render(<CommandPalette commands={commands} />);
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
    fireEvent.click(screen.getByText('Go to Fleet'));
    expect(action).toHaveBeenCalledOnce();
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
