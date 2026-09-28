/**
 * tableFrame.ts: the bordered frame around the Operations tables (#1718).
 * It scrolls sideways, so a phone can reach the right-hand columns that
 * `overflow: hidden` used to clip.
 */
import type { CSSProperties } from "react";

export const TABLE_FRAME_STYLE: CSSProperties = {
  border: "1px solid var(--border-color, #30363d)",
  borderRadius: "6px",
  overflowX: "auto",
  background: "var(--bg-tertiary, #21262d)",
  marginBottom: "1rem",
};
