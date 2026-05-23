/**
 * CommandPalette — Cmd+K command palette for keyboard-driven navigation (D4 / issue #723).
 *
 * Preconditions:
 *  - commands must be an array of Command objects with unique ids.
 *
 * Postconditions:
 *  - Ctrl+K / Cmd+K opens the palette.
 *  - Escape closes the palette without running a command.
 *  - Typing filters commands by label and keywords (case-insensitive substring).
 *  - Clicking or pressing Enter on an item runs the command and closes.
 *  - Recent commands (last 10) are stored in localStorage.
 *  - Focus returns to the previously-focused element on close.
 */

import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';

export interface Command {
  id: string;
  label: string;
  group: string;
  action: () => void;
  keywords?: string[];
}

export interface CommandPaletteProps {
  commands: Command[];
}

const RECENT_KEY = 'cmdpalette:recent';
const MAX_RECENT = 10;

function loadRecent(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as string[];
  } catch {
    return [];
  }
}

function saveRecent(id: string): void {
  try {
    const recent = loadRecent().filter((r) => r !== id);
    recent.unshift(id);
    localStorage.setItem(RECENT_KEY, JSON.stringify(recent.slice(0, MAX_RECENT)));
  } catch {
    // localStorage may be unavailable in some environments
  }
}

function filterCommands(commands: Command[], query: string): Command[] {
  if (!query.trim()) return commands;
  const q = query.toLowerCase();
  return commands.filter(
    (cmd) =>
      cmd.label.toLowerCase().includes(q) ||
      cmd.keywords?.some((kw) => kw.toLowerCase().includes(q)),
  );
}

export function CommandPalette({ commands }: CommandPaletteProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIdx, setActiveIdx] = useState(0);

  const inputRef = useRef<HTMLInputElement>(null);
  const prevFocusRef = useRef<Element | null>(null);

  const filtered = useMemo(() => filterCommands(commands, query), [commands, query]);

  // Open with Ctrl+K / Cmd+K
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        prevFocusRef.current = document.activeElement;
        setOpen(true);
        setQuery('');
        setActiveIdx(0);
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        close();
      }
    };
    document.addEventListener('keydown', handleEsc);
    return () => document.removeEventListener('keydown', handleEsc);
  }, [open]);

  // Focus input when opened
  useEffect(() => {
    if (open) {
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const close = useCallback(() => {
    setOpen(false);
    // Restore focus
    if (prevFocusRef.current instanceof HTMLElement) {
      prevFocusRef.current.focus();
    }
  }, []);

  const execute = useCallback(
    (cmd: Command) => {
      saveRecent(cmd.id);
      cmd.action();
      close();
    },
    [close],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIdx((i) => Math.min(i + 1, filtered.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIdx((i) => Math.max(i - 1, 0));
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (filtered[activeIdx]) {
          execute(filtered[activeIdx]);
        }
      }
    },
    [filtered, activeIdx, execute],
  );

  if (!open) return null;

  // Group commands
  const groups = Array.from(new Set(filtered.map((c) => c.group)));

  return (
    <div
      role="presentation"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 9999,
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        paddingTop: '15vh',
        background: 'rgba(0,0,0,0.5)',
      }}
      onClick={(e) => { if (e.target === e.currentTarget) close(); }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        style={{
          width: '560px',
          maxWidth: '90vw',
          background: 'var(--bg-secondary, #161b22)',
          border: '1px solid var(--border, #30363d)',
          borderRadius: '12px',
          boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
          overflow: 'hidden',
        }}
        onKeyDown={handleKeyDown}
      >
        <input
          ref={inputRef}
          role="combobox"
          aria-expanded={filtered.length > 0}
          aria-autocomplete="list"
          aria-label="Search commands"
          placeholder="Search commands…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActiveIdx(0);
          }}
          style={{
            width: '100%',
            padding: '14px 16px',
            background: 'transparent',
            border: 'none',
            borderBottom: '1px solid var(--border, #30363d)',
            color: 'var(--text-primary, #e6edf3)',
            fontSize: '1rem',
            outline: 'none',
            boxSizing: 'border-box',
          }}
        />
        <ul
          role="listbox"
          aria-label="Commands"
          style={{
            listStyle: 'none',
            margin: 0,
            padding: '4px 0',
            maxHeight: '360px',
            overflowY: 'auto',
          }}
        >
          {filtered.length === 0 ? (
            <li
              style={{
                padding: '12px 16px',
                color: 'var(--text-muted, #8b949e)',
                fontSize: '0.875rem',
                textAlign: 'center',
              }}
            >
              No commands found
            </li>
          ) : (
            groups.map((group) => (
              <React.Fragment key={group}>
                <li
                  aria-hidden="true"
                  style={{
                    padding: '6px 16px 2px',
                    fontSize: '0.7rem',
                    fontWeight: 700,
                    letterSpacing: '0.08em',
                    textTransform: 'uppercase',
                    color: 'var(--text-muted, #8b949e)',
                  }}
                >
                  {group}
                </li>
                {filtered
                  .filter((c) => c.group === group)
                  .map((cmd, _i) => {
                    const globalIdx = filtered.indexOf(cmd);
                    const isActive = globalIdx === activeIdx;
                    return (
                      <li
                        key={cmd.id}
                        role="option"
                        aria-selected={isActive}
                        onClick={() => execute(cmd)}
                        onMouseEnter={() => setActiveIdx(globalIdx)}
                        style={{
                          padding: '8px 16px',
                          cursor: 'pointer',
                          fontSize: '0.9rem',
                          color: 'var(--text-primary, #e6edf3)',
                          background: isActive
                            ? 'var(--bg-hover, #252d3a)'
                            : 'transparent',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '10px',
                        }}
                      >
                        {cmd.label}
                      </li>
                    );
                  })}
              </React.Fragment>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}
