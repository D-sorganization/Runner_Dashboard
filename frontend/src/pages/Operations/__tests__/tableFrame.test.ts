/**
 * Operations tables on a phone (#1718): the frames used `overflow: hidden`, so
 * the right-hand columns were cut off with no way to scroll to them.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { TABLE_FRAME_STYLE } from "../tableFrame";

const SECTIONS = [
  "OperationsDeploySection.tsx",
  "OperationsRunnerHoursSection.tsx",
  "OperationsScheduledWorkflowsSection.tsx",
];

describe("Operations table frames", () => {
  it("scroll sideways instead of clipping", () => {
    expect(TABLE_FRAME_STYLE.overflowX).toBe("auto");
    expect(TABLE_FRAME_STYLE).not.toHaveProperty("overflow");
  });

  it.each(SECTIONS)("%s wraps its table in the shared frame", (file) => {
    const source = readFileSync(path.join(__dirname, "..", file), "utf8");
    expect(source).toContain("<div style={TABLE_FRAME_STYLE}>");
    expect(source).not.toMatch(/overflow: "hidden",\s*background: "var\(--bg-tertiary/);
  });
});
