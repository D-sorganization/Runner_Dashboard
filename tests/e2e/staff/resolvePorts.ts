/**
 * Pick free loopback ports when CI/local did not set STAFF_E2E_*_PORT (#1341).
 *
 * Reuses the Python allocator so local runs do not collide with a backend on
 * the legacy default 5092.
 */

import { execSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "../../..");

export function ensureStaffE2ePorts(): void {
  if (process.env.STAFF_E2E_BACKEND_PORT && process.env.STAFF_E2E_UI_PORT) {
    return;
  }
  const dir = mkdtempSync(join(tmpdir(), "staff-e2e-ports-"));
  const envPath = join(dir, "github-env");
  execSync("python3 tests/e2e/staff/port_allocator.py", {
    cwd: REPO_ROOT,
    env: { ...process.env, GITHUB_ENV: envPath },
    stdio: ["ignore", "pipe", "inherit"],
  });
  for (const line of readFileSync(envPath, "utf8").split(/\r?\n/)) {
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq);
    const value = line.slice(eq + 1);
    process.env[key] = value;
  }
}
