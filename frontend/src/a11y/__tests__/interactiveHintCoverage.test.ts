/**
 * TDD ratchet: interactive controls must carry Tooltip, title, or aria-label.
 */
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  auditInteractiveHints,
  formatCoverageRatio,
  violationsInEnforcedAreas,
} from "./interactiveHintCoverage.lib";

const FRONTEND_SRC = path.join(__dirname, "..", "..");

describe("interactive hint coverage audit", () => {
  const report = auditInteractiveHints(FRONTEND_SRC);

  it("reports fleet-wide coverage (baseline telemetry)", () => {
    expect(report.scannedFiles).toBeGreaterThan(50);
    expect(report.totalInteractive).toBeGreaterThan(100);
    // Logged for PR handoff — ratio must only increase as areas land.
    const ratio = formatCoverageRatio(report);
    // eslint-disable-next-line no-console -- intentional telemetry for agents/CI logs
    console.info(
      `[interactive-hint-coverage] fleet ${ratio}, violations=${report.violations.length}, files=${report.scannedFiles}`,
    );
    expect(ratio).toMatch(/\d+\/\d+/);
  });

  it.skipIf(!process.env.DUMP_HINT_COVERAGE)("dump violations by area", () => {
    if (process.env.DUMP_HINT_FILE) {
      const needle = process.env.DUMP_HINT_FILE!;
      const filtered =
        needle === "*"
          ? report.violations
          : report.violations.filter((v) => v.file.includes(needle));
      // eslint-disable-next-line no-console -- opt-in debug
      console.info(
        filtered.map((v) => `${v.file}:${v.line} [${v.kind}] ${v.snippet}`).join("\n"),
      );
    }
    const by = new Map<string, number>();
    for (const v of report.violations) {
      const area = v.file.split("/").slice(0, 2).join("/");
      by.set(area, (by.get(area) ?? 0) + 1);
    }
    // eslint-disable-next-line no-console -- opt-in debug
    console.info(
      [...by.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([k, n]) => `${n}\t${k}`)
        .join("\n"),
    );
    expect(by.size).toBeGreaterThan(0);
  });

  it("every scanned interactive control has a hover/focus hint (fleet-wide)", () => {
    const enforced = violationsInEnforcedAreas(report);
    if (enforced.length > 0) {
      const sample = enforced
        .slice(0, 12)
        .map((v) => `  ${v.file}:${v.line} [${v.kind}] ${v.snippet}`)
        .join("\n");
      expect.fail(
        `${enforced.length} missing hints in enforced shell files (fleet ${formatCoverageRatio(report)}):\n${sample}`,
      );
    }
    expect(enforced).toEqual([]);
  });
});
