import { apiRequest } from "../../lib/api";

export type HostPreference = "grokbot-first" | "rd-first" | "both";
export type HostId = "grokbot" | "rd";

export interface HostSettingsBlock {
  preference: HostPreference;
  hosts: { grokbot: { enabled: boolean }; rd: { enabled: boolean } };
}

export interface HostSettingsPayload {
  schema_version: number;
  global: HostSettingsBlock;
  roles: Record<string, HostSettingsBlock>;
}

export interface ResolvedRoleHost {
  role: string;
  preference: HostPreference;
  leader: HostId | null;
  followers: string[];
}

export interface HostSettingsResponse {
  settings: HostSettingsPayload;
  resolved: ResolvedRoleHost[];
  path: string;
}

export function leaderLabel(row: ResolvedRoleHost): string {
  if (!row.leader) return "none";
  return row.followers.length ? `${row.leader} (+ ${row.followers.join(", ")})` : row.leader;
}

export function resolveBlock(role: string, settings: HostSettingsPayload): HostSettingsBlock {
  return settings.roles[role] ?? settings.global;
}

export function previewRoleLeader(role: string, settings: HostSettingsPayload): ResolvedRoleHost {
  const block = resolveBlock(role, settings);
  const enabled = (["grokbot", "rd"] as const).filter((h) => block.hosts[h].enabled);
  const order = block.preference === "rd-first" ? (["rd", "grokbot"] as const) : (["grokbot", "rd"] as const);
  let leader: HostId | null = null;
  const followers: HostId[] = [];
  for (const h of order) {
    if (!enabled.includes(h)) continue;
    if (!leader) leader = h;
    else followers.push(h);
  }
  if (block.preference === "both" && enabled.length === 2 && leader) {
    return { role, preference: block.preference, leader, followers: enabled.filter((h) => h !== leader) };
  }
  return { role, preference: block.preference, leader, followers };
}

export function fetchHostSettings(signal?: AbortSignal): Promise<HostSettingsResponse> {
  return apiRequest<HostSettingsResponse>("/api/staff/host-settings", { signal });
}

export function putHostSettings(settings: HostSettingsPayload, confirmed: boolean): Promise<HostSettingsResponse> {
  return apiRequest<HostSettingsResponse>("/api/staff/host-settings", {
    method: "PUT",
    body: JSON.stringify({ ...settings, confirmed }),
  });
}
