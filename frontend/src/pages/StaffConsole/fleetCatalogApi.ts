/**
 * Skills catalog and project intake views (#2157 slice 7).
 */

export interface StaffSkillSummary {
  name: string;
  version: string;
  description?: string;
  when_to_use?: string;
  pin: string;
  roles: string[];
  repos: string[];
  task_types: string[];
}

export interface StaffProjectSummary {
  id: string;
  title: string;
  goal: string;
  status: string;
  repos: string[];
  tasks: { id: string; title: string; role: string; repo: string; task_type: string }[];
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { "X-Requested-With": "XMLHttpRequest" } });
  if (!res.ok) {
    throw new Error(`${path} returned ${res.status}`);
  }
  return (await res.json()) as T;
}

export async function fetchSkillCatalog(): Promise<StaffSkillSummary[]> {
  const data = await getJson<{ count: number; skills: StaffSkillSummary[] }>("/api/v1/staff/skills");
  return data.skills ?? [];
}

export async function fetchProjectList(): Promise<StaffProjectSummary[]> {
  const data = await getJson<{ count: number; projects: StaffProjectSummary[] }>("/api/v1/staff/projects");
  return data.projects ?? [];
}
