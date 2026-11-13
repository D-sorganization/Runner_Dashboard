/**
 * personaGalleryUtils.ts — Presentation preferences and ordering helpers (#3502, #3499).
 */
import { apiRequest, ApiClientError } from "../../lib/api";
import { fetchWithBrowserCsrf } from "../../lib/browserCsrf";
import type { components } from "../../lib/api-types";
import type { StaffRoleItem } from "./types";

export type PresentationPrefsResponse = components["schemas"]["PresentationPrefsResponse"];
export type PresentationPrefsSaveBody = components["schemas"]["PresentationPrefsSaveBody"];
const PREFS_BASE = "/api/v1/staff/presentation-prefs";

export const MAX_DISPLAY_NAME_CHARS = 80;
export const MAX_AVATAR_BYTES = 1024 * 1024; // 1 MiB

export async function fetchPresentationPrefs(signal?: AbortSignal): Promise<PresentationPrefsResponse> {
  return apiRequest<PresentationPrefsResponse>(PREFS_BASE, { signal });
}

export async function savePresentationPrefs(
  body: PresentationPrefsSaveBody,
  signal?: AbortSignal,
): Promise<PresentationPrefsResponse> {
  return apiRequest<PresentationPrefsResponse>(PREFS_BASE, { method: "PUT", body, signal });
}

export function validateDisplayName(name: string): string | null {
  if (name.length > MAX_DISPLAY_NAME_CHARS) return `Display name cannot exceed ${MAX_DISPLAY_NAME_CHARS} characters`;
  for (let i = 0; i < name.length; i++) {
    if (name.charCodeAt(i) < 0x20) return "Display name must be plain text (no control characters)";
  }
  return null;
}

export function validateAvatarFile(file: { size: number; type: string }): string | null {
  if (file.size > MAX_AVATAR_BYTES) return "Image exceeds 1MB limit";
  if (!["image/png", "image/webp"].includes(file.type)) return "Invalid image format: must be PNG or WebP";
  return null;
}

export async function uploadCustomAvatar(
  roleId: string,
  data: Blob | ArrayBuffer,
  expectedRevision?: number,
  signal?: AbortSignal,
): Promise<{ ok: boolean; operator_id: string; role_id: string; avatar_url: string }> {
  const base = `/api/v1/staff/avatars/custom/${encodeURIComponent(roleId)}`;
  const url = expectedRevision !== undefined ? `${base}?expected_revision=${expectedRevision}` : base;
  const contentType = data instanceof Blob && data.type ? data.type : "image/webp";
  const resp = await fetchWithBrowserCsrf(url, {
    method: "PUT",
    headers: { "Content-Type": contentType, "X-Requested-With": "XMLHttpRequest" },
    body: data as BodyInit,
    signal,
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const err = (await resp.json()) as { detail?: string; error?: { message?: string } };
      detail = err.detail || err.error?.message || detail;
    } catch {
      // Ignore body decode errors; keep status message fallback
    }
    throw new ApiClientError(resp.status, detail, url);
  }
  return resp.json() as Promise<{ ok: boolean; operator_id: string; role_id: string; avatar_url: string }>;
}

export async function resetCustomAvatar(
  roleId: string,
  expectedRevision?: number,
  signal?: AbortSignal,
): Promise<{ ok: boolean; role_id: string; reset: boolean }> {
  const base = `/api/v1/staff/avatars/custom/${encodeURIComponent(roleId)}/reset`;
  const url = expectedRevision !== undefined ? `${base}?expected_revision=${expectedRevision}` : base;
  return apiRequest(url, { method: "POST", signal });
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
