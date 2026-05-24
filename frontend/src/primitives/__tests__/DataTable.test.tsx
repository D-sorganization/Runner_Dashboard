// @vitest-environment jsdom
/**
 * Tests for DataTable primitive (D2 / issue #721).
 *
 * Covers:
 * 1. Renders column headers.
 * 2. Renders rows with correct content.
 * 3. Empty state shown when rows.length === 0.
 * 4. Error state shown when errorState prop provided.
 * 5. Sortable header click calls onSort.
 * 6. Duplicate column keys throw in development.
 * 7. ariaLabel applied to the table element.
 * 8. Loading skeleton shown when isLoading=true.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DataTable } from '../DataTable';
import type { Column } from '../DataTable';

interface Row {
  id: string;
  name: string;
  status: string;
}

const columns: Column<Row>[] = [
  { key: 'name', label: 'Name', sortable: true },
  { key: 'status', label: 'Status' },
];

const rows: Row[] = [
  { id: '1', name: 'Alpha', status: 'active' },
  { id: '2', name: 'Beta', status: 'idle' },
  { id: '3', name: 'Gamma', status: 'error' },
];

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe('DataTable', () => {
  it('renders column headers', () => {
    render(
      <DataTable
        columns={columns}
        rows={rows}
        getRowId={(r) => r.id}
      />,
    );
    expect(screen.getByText('Name')).toBeTruthy();
    expect(screen.getByText('Status')).toBeTruthy();
  });

  it('renders row data', () => {
    render(
      <DataTable
        columns={columns}
        rows={rows}
        getRowId={(r) => r.id}
      />,
    );
    expect(screen.getByText('Alpha')).toBeTruthy();
    expect(screen.getByText('Beta')).toBeTruthy();
    expect(screen.getByText('Gamma')).toBeTruthy();
  });

  it('shows emptyState when rows is empty', () => {
    render(
      <DataTable
        columns={columns}
        rows={[]}
        getRowId={(r) => r.id}
        emptyState={<span data-testid="empty">No data</span>}
      />,
    );
    expect(screen.getByTestId('empty')).toBeTruthy();
  });

  it('shows errorState when provided', () => {
    render(
      <DataTable
        columns={columns}
        rows={[]}
        getRowId={(r) => r.id}
        errorState={<span data-testid="error-state">Load failed</span>}
      />,
    );
    expect(screen.getByTestId('error-state')).toBeTruthy();
  });

  it('calls onSort with col key and direction when sortable header clicked', () => {
    const onSort = vi.fn();
    render(
      <DataTable
        columns={columns}
        rows={rows}
        getRowId={(r) => r.id}
        onSort={onSort}
      />,
    );
    fireEvent.click(screen.getByText('Name'));
    expect(onSort).toHaveBeenCalledWith('name', expect.stringMatching(/^(asc|desc)$/));
  });

  it('toggles sort direction on second click of same column', () => {
    const onSort = vi.fn();
    render(
      <DataTable
        columns={columns}
        rows={rows}
        getRowId={(r) => r.id}
        onSort={onSort}
      />,
    );
    fireEvent.click(screen.getByText('Name'));
    const [, firstDir] = onSort.mock.calls[0];
    fireEvent.click(screen.getByText('Name'));
    const [, secondDir] = onSort.mock.calls[1];
    expect(firstDir).not.toBe(secondDir);
  });

  it('does not call onSort on non-sortable column click', () => {
    const onSort = vi.fn();
    render(
      <DataTable
        columns={columns}
        rows={rows}
        getRowId={(r) => r.id}
        onSort={onSort}
      />,
    );
    // "Status" column is not sortable
    fireEvent.click(screen.getByText('Status'));
    expect(onSort).not.toHaveBeenCalled();
  });

  it('applies ariaLabel to the table', () => {
    render(
      <DataTable
        columns={columns}
        rows={rows}
        getRowId={(r) => r.id}
        ariaLabel="Runner list"
      />,
    );
    expect(screen.getByRole('table', { name: 'Runner list' })).toBeTruthy();
  });

  it('shows loading skeletons when isLoading=true', () => {
    const { container } = render(
      <DataTable
        columns={columns}
        rows={[]}
        getRowId={(r) => r.id}
        isLoading={true}
        loadingRows={5}
      />,
    );
    // Should render skeleton rows
    const skeletonRows = container.querySelectorAll('[data-loading="true"]');
    expect(skeletonRows.length).toBeGreaterThan(0);
  });

  it('throws when duplicate column keys are provided', () => {
    const dupColumns: Column<Row>[] = [
      { key: 'name', label: 'Name' },
      { key: 'name', label: 'Duplicate Name' },
    ];
    // DataTable asserts duplicate keys in dev; expect an error to be thrown
    expect(() =>
      render(
        <DataTable
          columns={dupColumns}
          rows={rows}
          getRowId={(r) => r.id}
        />,
      ),
    ).toThrow();
  });
});
