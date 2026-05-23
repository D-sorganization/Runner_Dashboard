/**
 * DataTable — virtualized, sortable data table primitive (D2 / issue #721).
 *
 * Preconditions:
 *  - columns must have unique keys (throws in development if violated).
 *  - getRowId must return a stable, unique ID per row.
 *
 * Postconditions:
 *  - Renders a <table> with the given columns and rows.
 *  - When rows.length === 0, shows emptyState.
 *  - When errorState is provided, shows it instead of the table body.
 *  - When isLoading=true, shows skeleton placeholder rows.
 *  - When virtualized=true and rows > ROW_THRESHOLD, only visible rows are in the DOM.
 *  - Sortable columns track sort state internally; onSort is called on each click.
 */

import React, { useState, useRef, useCallback, ReactNode, useEffect } from 'react';

export interface Column<T> {
  key: string;
  label: string;
  sortable?: boolean;
  width?: number;
  render?: (row: T) => ReactNode;
}

export interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  getRowId: (row: T) => string;
  onSort?: (col: string, dir: 'asc' | 'desc') => void;
  virtualized?: boolean;
  stickyHeader?: boolean;
  emptyState?: ReactNode;
  loadingRows?: number;
  isLoading?: boolean;
  errorState?: ReactNode;
  ariaLabel?: string;
}

const ROW_HEIGHT = 40; // px estimate for virtualization
const OVERSCAN = 5;
const VIRTUALIZE_THRESHOLD = 50; // Only virtualize when > 50 rows

function assertUniqueKeys<T>(columns: Column<T>[]): void {
  const seen = new Set<string>();
  for (const col of columns) {
    if (seen.has(col.key)) {
      throw new Error(`[DataTable] Duplicate column key: "${col.key}". Column keys must be unique.`);
    }
    seen.add(col.key);
  }
}

export function DataTable<T>({
  columns,
  rows,
  getRowId,
  onSort,
  virtualized = true,
  stickyHeader = true,
  emptyState,
  loadingRows = 8,
  isLoading = false,
  errorState,
  ariaLabel,
}: DataTableProps<T>) {
  // Precondition: unique column keys
  assertUniqueKeys(columns);

  const [sortCol, setSortCol] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');

  const containerRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [containerHeight, setContainerHeight] = useState(400);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    setContainerHeight(el.clientHeight || 400);
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => {
      setContainerHeight(el.clientHeight || 400);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const handleScroll = useCallback(() => {
    if (containerRef.current) {
      setScrollTop(containerRef.current.scrollTop);
    }
  }, []);

  const handleSort = useCallback(
    (colKey: string) => {
      setSortCol((prev) => {
        const newDir: 'asc' | 'desc' =
          prev === colKey ? (sortDir === 'asc' ? 'desc' : 'asc') : 'asc';
        setSortDir(newDir);
        onSort?.(colKey, newDir);
        return colKey;
      });
    },
    [sortDir, onSort],
  );

  const shouldVirtualize =
    virtualized && rows.length > VIRTUALIZE_THRESHOLD;

  // Determine visible slice when virtualizing
  let startIdx = 0;
  let endIdx = rows.length;
  let paddingTop = 0;
  let paddingBottom = 0;

  if (shouldVirtualize) {
    startIdx = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
    const visibleCount = Math.ceil(containerHeight / ROW_HEIGHT) + OVERSCAN * 2;
    endIdx = Math.min(rows.length, startIdx + visibleCount);
    paddingTop = startIdx * ROW_HEIGHT;
    paddingBottom = (rows.length - endIdx) * ROW_HEIGHT;
  }

  const visibleRows = rows.slice(startIdx, endIdx);

  const tableStyle: React.CSSProperties = {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: '0.875rem',
    color: 'var(--text-primary, #e6edf3)',
  };

  const thStyle = (col: Column<T>): React.CSSProperties => ({
    padding: '8px 12px',
    textAlign: 'left',
    fontWeight: 600,
    fontSize: '0.75rem',
    color: 'var(--text-secondary, #8b949e)',
    borderBottom: '1px solid var(--border, #30363d)',
    background: stickyHeader ? 'var(--bg-secondary, #161b22)' : undefined,
    position: stickyHeader ? 'sticky' : undefined,
    top: stickyHeader ? 0 : undefined,
    zIndex: stickyHeader ? 1 : undefined,
    cursor: col.sortable ? 'pointer' : 'default',
    userSelect: 'none',
    width: col.width ? `${col.width}px` : undefined,
  });

  const tdStyle: React.CSSProperties = {
    padding: '8px 12px',
    borderBottom: '1px solid var(--border-light, #3d444d)',
    height: `${ROW_HEIGHT}px`,
  };

  // Show error state
  if (errorState) {
    return <div>{errorState}</div>;
  }

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      style={{
        overflow: 'auto',
        maxHeight: shouldVirtualize ? '600px' : undefined,
        position: 'relative',
      }}
    >
      <table role="table" aria-label={ariaLabel} style={tableStyle}>
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                style={thStyle(col)}
                onClick={col.sortable ? () => handleSort(col.key) : undefined}
                aria-sort={
                  sortCol === col.key
                    ? sortDir === 'asc'
                      ? 'ascending'
                      : 'descending'
                    : undefined
                }
              >
                {col.label}
                {col.sortable && sortCol === col.key && (
                  <span aria-hidden="true" style={{ marginLeft: '4px' }}>
                    {sortDir === 'asc' ? '↑' : '↓'}
                  </span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {isLoading ? (
            Array.from({ length: loadingRows }).map((_, i) => (
              <tr key={`skeleton-${i}`} data-loading="true">
                {columns.map((col) => (
                  <td key={col.key} style={tdStyle}>
                    <span
                      style={{
                        display: 'inline-block',
                        height: '12px',
                        width: '70%',
                        background: 'var(--bg-hover, #252d3a)',
                        borderRadius: '4px',
                        animation: 'pulse 1.5s ease-in-out infinite',
                      }}
                    />
                  </td>
                ))}
              </tr>
            ))
          ) : rows.length === 0 && emptyState ? (
            <tr>
              <td colSpan={columns.length} style={{ ...tdStyle, textAlign: 'center', padding: '2rem' }}>
                {emptyState}
              </td>
            </tr>
          ) : (
            <>
              {shouldVirtualize && paddingTop > 0 && (
                <tr aria-hidden="true" style={{ height: `${paddingTop}px` }}>
                  <td colSpan={columns.length} />
                </tr>
              )}
              {visibleRows.map((row) => (
                <tr key={getRowId(row)}>
                  {columns.map((col) => (
                    <td key={col.key} style={tdStyle}>
                      {col.render
                        ? col.render(row)
                        : String((row as Record<string, unknown>)[col.key] ?? '')}
                    </td>
                  ))}
                </tr>
              ))}
              {shouldVirtualize && paddingBottom > 0 && (
                <tr aria-hidden="true" style={{ height: `${paddingBottom}px` }}>
                  <td colSpan={columns.length} />
                </tr>
              )}
            </>
          )}
        </tbody>
      </table>
    </div>
  );
}
