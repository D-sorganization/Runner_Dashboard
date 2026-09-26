/**
 * Hermeticity guard for the Staff Console e2e suite (#1556).
 *
 * start_staff_backend.py redirects the real backend's stdout/stderr onto the
 * file playwright.config.ts passes it via ``--log-file`` (path in
 * STAFF_E2E_BACKEND_LOG, relative to the repo root). After every test has
 * run, fail here (not in a random spec) if that log shows the backend
 * reached a real fleet peer or api.github.com — the two ways this harness
 * used to leak outside the fake provider CLIs.
 */

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

// One list, checked once, so a new non-hermetic call site only needs a new
// entry here instead of a new assertion somewhere else (DRY).
const FORBIDDEN_PATTERNS = [
  "api.github.com",
  // fetch_peer_board (backend/staff/fleet.py) is the only caller of this path;
  // its presence means FLEET_NODES/AUTODERIVE_FLEET_NODES did not stay pinned
  // to this node alone.
  "board?local=1",
];

export default async function globalTeardown(): Promise<void> {
  const logFileRelative = process.env.STAFF_E2E_BACKEND_LOG;
  if (!logFileRelative) {
    // STAFF_E2E_BACKEND_URL reuse mode: no log to scan, nothing to check.
    return;
  }
  // Relative to the cwd `npx playwright test` was run from (repo root),
  // matching how playwright.config.ts set it and start_staff_backend.py
  // wrote it.
  const logFile = resolve(process.cwd(), logFileRelative);
  if (!existsSync(logFile)) {
    // The launcher always writes it when it starts the backend; a missing log
    // would make this guard vacuous, so treat it as a failure.
    throw new Error(`Staff e2e backend log ${logFile} is missing; the hermeticity guard cannot run.`);
  }

  const lines = readFileSync(logFile, "utf-8").split("\n");
  const offending = lines.filter((line) => FORBIDDEN_PATTERNS.some((pattern) => line.includes(pattern)));

  if (offending.length > 0) {
    throw new Error(
      `Staff e2e backend log (${logFile}) is not hermetic — ` +
        `${offending.length} outbound request(s) reached a real fleet peer or GitHub:\n` +
        offending.join("\n"),
    );
  }
}
