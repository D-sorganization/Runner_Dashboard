/**
 * personaGalleryUtils.ts — Presentation preferences and ordering helpers (#3502).
 */
import { apiRequest } from "../../lib/api";
import type { components } from "../../lib/api-types";
import type { StaffRoleItem } from "./types";

export type PresentationPrefsResponse = components["schemas"]["PresentationPrefsResponse"];
export type PresentationPrefsSaveBody = components["schemas"]["PresentationPrefsSaveBody"];
const PREFS_BASE = "/api/v1/staff/presentation-prefs";

export async function fetchPresentationPrefs(signal?: AbortSignal): Promise<PresentationPrefsResponse> {
  return apiRequest<PresentationPrefsResponse>(PREFS_BASE, { signal });
}

export async function savePresentationPrefs(
  body: PresentationPrefsSaveBody,
  signal?: AbortSignal,
): Promise<PresentationPrefsResponse> {
  return apiRequest<PresentationPrefsResponse>(PREFS_BASE, { method: "PUT", body, signal });
}

export function orderRoles(roles: StaffRoleItem[], orderNames: string[]): StaffRoleItem[] {
  if (!orderNames || orderNames.length === 0) return roles;
  const roleMap = new Map<string, StaffRoleItem>();
  for (const r of roles) roleMap.set(r.name, r);
  const ordered: StaffRoleItem[] = [];
  const seen = new Set<string>();
  for (const name of orderNames) {
    const role = roleMap.get(name);
    if (role && !seen.has(name)) {
      ordered.push(role);
      seen.add(name);
    }
  }
  for (const r of roles) {
    if (!seen.has(r.name)) {
      ordered.push(r);
      seen.add(r.name);
    }
  }
  return ordered;
}
