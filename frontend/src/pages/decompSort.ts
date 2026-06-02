/**
 * decompSort.ts — pure, React-free sort helpers shared by tabs extracted from
 * the legacy `App.tsx` monolith (decomposition #836).
 *
 * Reproduces, 1:1, the legacy `sortRows` / `normalizeSortValue` / `sortStateNext`
 * helpers. The matching `SortTh` header cell lives in `decompSort.tsx`; the pure
 * logic is kept here (a `.ts` file) so the component file only exports
 * components — satisfying `react-refresh/only-export-components` without an
 * eslint override.
 */

export interface SortState {
  key: string;
  dir: "asc" | "desc";
}

export type SortAccessor<T> = (row: T) => unknown;
export type SortAccessors<T> = Record<string, SortAccessor<T>>;

/** Cycles a column's sort direction (asc → desc → asc), matching legacy. */
export function sortStateNext(
  current: SortState | null | undefined,
  key: string,
): SortState {
  if (current && current.key === key) {
    return { key, dir: current.dir === "asc" ? "desc" : "asc" };
  }
  return { key, dir: "asc" };
}

/** Coerces a heterogeneous cell value into a comparable primitive (legacy 1:1). */
export function normalizeSortValue(value: unknown): number | string {
  if (value == null) return "";
  if (typeof value === "number") return value;
  if (typeof value === "boolean") return value ? 1 : 0;
  const text = String(value);
  const asDate = Date.parse(text);
  if (!Number.isNaN(asDate) && /\d{4}-\d{2}-\d{2}|T\d{2}:/.test(text)) {
    return asDate;
  }
  const numeric = Number(text.replace(/[^0-9.-]/g, ""));
  if (text.trim() && !Number.isNaN(numeric) && /[0-9]/.test(text)) {
    return numeric;
  }
  return text.toLowerCase();
}

/** Returns a stable-sorted copy of `rows` per `sort` and `accessors` (legacy 1:1). */
export function sortRows<T>(
  rows: T[],
  sort: SortState | null | undefined,
  accessors: SortAccessors<T>,
): T[] {
  if (!sort || !sort.key || !accessors || !accessors[sort.key]) {
    return rows.slice();
  }
  const dir = sort.dir === "desc" ? -1 : 1;
  const accessor = accessors[sort.key];
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      const av = normalizeSortValue(accessor(a.row));
      const bv = normalizeSortValue(accessor(b.row));
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return a.index - b.index;
    })
    .map((entry) => entry.row);
}
