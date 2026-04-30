import type { CSSProperties } from "react";

/**
 * Skeleton primitive (issue #427)
 *
 * Replaces bare "Loading…" text on data-fetching surfaces with placeholder
 * shapes that approximate the eventual layout. The shapes carry a subtle
 * shimmer animation by default, but the animation is disabled when the user
 * has expressed `prefers-reduced-motion: reduce` — in that case we render a
 * static neutral block instead. The reduced-motion media query is enforced
 * globally in `frontend/src/index.css` (search for `prefers-reduced-motion`)
 * which sets `animation-duration: 0.01ms` on every element; the local CSS
 * below additionally swaps the shimmer gradient for a flat fill so the bar
 * does not sit "frozen" on a single keyframe.
 *
 * All variants accept `width`, `height`, and `className` so callers can match
 * the eventual content shape and minimise Cumulative Layout Shift on swap.
 */

export type SkeletonSize = number | string;

export interface SkeletonProps {
  ariaLabel?: string;
  className?: string;
  height?: SkeletonSize;
  radius?: SkeletonSize;
  style?: CSSProperties;
  width?: SkeletonSize;
}

export interface SkeletonLineProps extends SkeletonProps {
  /** Optional last-line shorter width to mimic a paragraph tail. */
  trailing?: boolean;
}

export interface SkeletonCardProps {
  className?: string;
  height?: SkeletonSize;
  lines?: number;
  style?: CSSProperties;
  width?: SkeletonSize;
}

export interface SkeletonTableProps {
  className?: string;
  columns?: number;
  rows?: number;
  style?: CSSProperties;
  width?: SkeletonSize;
}

const SKELETON_CLASS = "skeleton";
const SKELETON_LINE_CLASS = "skeleton-line";
const SKELETON_CARD_CLASS = "skeleton-card";
const SKELETON_TABLE_CLASS = "skeleton-table";
const SKELETON_TABLE_ROW_CLASS = "skeleton-table-row";
const SKELETON_TABLE_CELL_CLASS = "skeleton-table-cell";

function toCssSize(value: SkeletonSize | undefined): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value === "number") return `${value}px`;
  return value;
}

function joinClass(...parts: Array<string | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/**
 * Generic rectangular skeleton block with configurable width/height/radius.
 * Use this as a building block when none of the higher-level variants fit.
 */
export function Skeleton({
  ariaLabel,
  className,
  height,
  radius,
  style,
  width,
}: SkeletonProps) {
  const inlineStyle: CSSProperties = {
    ...(style ?? {}),
    ...(width !== undefined ? { width: toCssSize(width) } : {}),
    ...(height !== undefined ? { height: toCssSize(height) } : {}),
    ...(radius !== undefined ? { borderRadius: toCssSize(radius) } : {}),
  };
  return (
    <span
      aria-busy="true"
      aria-hidden={ariaLabel ? undefined : true}
      aria-label={ariaLabel}
      className={joinClass(SKELETON_CLASS, className)}
      data-touch-primitive="Skeleton"
      role={ariaLabel ? "status" : undefined}
      style={inlineStyle}
    />
  );
}

/**
 * Single horizontal line skeleton, sized like a line of body text by default.
 * `trailing` knocks the width down to mimic a paragraph's last line.
 */
export function SkeletonLine({
  ariaLabel,
  className,
  height = 12,
  radius = 4,
  style,
  trailing,
  width,
}: SkeletonLineProps) {
  const resolvedWidth = width ?? (trailing ? "60%" : "100%");
  return (
    <Skeleton
      ariaLabel={ariaLabel}
      className={joinClass(SKELETON_LINE_CLASS, className)}
      height={height}
      radius={radius}
      style={style}
      width={resolvedWidth}
    />
  );
}

/**
 * Card-shaped skeleton with a header bar plus a configurable number of body
 * lines. Defaults to a 3-line body to match the issue spec.
 */
export function SkeletonCard({
  className,
  height,
  lines = 3,
  style,
  width = "100%",
}: SkeletonCardProps) {
  const safeLines = Math.max(1, Math.floor(lines));
  const cardStyle: CSSProperties = {
    ...(style ?? {}),
    ...(width !== undefined ? { width: toCssSize(width) } : {}),
    ...(height !== undefined ? { height: toCssSize(height) } : {}),
  };
  const bodyLines: number[] = [];
  for (let i = 0; i < safeLines; i += 1) bodyLines.push(i);
  return (
    <div
      aria-busy="true"
      aria-label="Loading content"
      className={joinClass(SKELETON_CARD_CLASS, className)}
      data-touch-primitive="SkeletonCard"
      role="status"
      style={cardStyle}
    >
      <SkeletonLine className="skeleton-card-header" height={16} width="40%" />
      {bodyLines.map((idx) => (
        <SkeletonLine
          height={12}
          key={idx}
          trailing={idx === safeLines - 1}
        />
      ))}
    </div>
  );
}

/**
 * Tabular skeleton with configurable rows and columns. Renders a header row
 * plus the requested data rows, each cell sharing the shimmer animation.
 */
export function SkeletonTable({
  className,
  columns = 4,
  rows = 5,
  style,
  width = "100%",
}: SkeletonTableProps) {
  const safeColumns = Math.max(1, Math.floor(columns));
  const safeRows = Math.max(1, Math.floor(rows));
  const tableStyle: CSSProperties = {
    ...(style ?? {}),
    ...(width !== undefined ? { width: toCssSize(width) } : {}),
  };
  const colKeys: number[] = [];
  for (let c = 0; c < safeColumns; c += 1) colKeys.push(c);
  const rowKeys: number[] = [];
  for (let r = 0; r < safeRows; r += 1) rowKeys.push(r);
  return (
    <div
      aria-busy="true"
      aria-label="Loading table"
      className={joinClass(SKELETON_TABLE_CLASS, className)}
      data-touch-primitive="SkeletonTable"
      role="status"
      style={tableStyle}
    >
      <div className={joinClass(SKELETON_TABLE_ROW_CLASS, "skeleton-table-head")}>
        {colKeys.map((c) => (
          <SkeletonLine
            className={SKELETON_TABLE_CELL_CLASS}
            height={14}
            key={`h-${c}`}
            width="80%"
          />
        ))}
      </div>
      {rowKeys.map((r) => (
        <div className={SKELETON_TABLE_ROW_CLASS} key={`r-${r}`}>
          {colKeys.map((c) => (
            <SkeletonLine
              className={SKELETON_TABLE_CELL_CLASS}
              height={12}
              key={`r-${r}-c-${c}`}
              width={c === safeColumns - 1 ? "60%" : "100%"}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
