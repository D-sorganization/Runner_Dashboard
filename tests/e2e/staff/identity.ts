/**
 * Bearer headers for the fixture principals seeded by start_staff_backend.py (#1341).
 * One source of truth: tests/e2e/fakes/identity.json.
 */

import fs from "fs";

interface FixturePrincipal {
  id: string;
  token: string;
}

const identity = JSON.parse(
  fs.readFileSync(new URL("../fakes/identity.json", import.meta.url), "utf-8"),
) as { principals: FixturePrincipal[] };

function headersFor(id: string): Record<string, string> {
  const principal = identity.principals.find((p) => p.id === id);
  if (!principal) throw new Error(`fixture principal ${id} missing from identity.json`);
  return { Authorization: `Bearer ${principal.token}` };
}

export const operatorHeaders = headersFor("e2e-operator");
export const viewerHeaders = headersFor("e2e-viewer");
