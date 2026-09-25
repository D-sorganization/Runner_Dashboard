/**
 * pinsApi.ts — Server-side persistent pins client with localStorage fallback (SC-D3, Issue #1317).
 */

const LOCAL_STORAGE_KEY = "staff_console_pinned_roles";

function getLocalPins(): string[] {
  try {
    const raw = localStorage.getItem(LOCAL_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        return parsed.map((item) => String(item));
      }
    }
  } catch {
    // Ignore localStorage errors
  }
  return [];
}

function setLocalPins(pins: string[]): void {
  try {
    localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(pins));
  } catch {
    // Ignore localStorage errors
  }
}

export async function fetchPins(): Promise<string[]> {
  try {
    const res = await fetch("/api/v1/staff/pins", {
      headers: { Accept: "application/json" },
    });
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data?.pins)) {
        setLocalPins(data.pins);
        return data.pins;
      }
    }
  } catch {
    // Fall back to localStorage on network error
  }
  return getLocalPins();
}

export async function pinRole(roleName: string): Promise<string[]> {
  const current = getLocalPins();
  const next = Array.from(new Set([...current, roleName]));
  setLocalPins(next);

  try {
    const res = await fetch(`/api/v1/staff/pins/${encodeURIComponent(roleName)}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
    });
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data?.pins)) {
        setLocalPins(data.pins);
        return data.pins;
      }
    }
  } catch {
    // Keep local change on error
  }
  return next;
}

export async function unpinRole(roleName: string): Promise<string[]> {
  const current = getLocalPins();
  const next = current.filter((r) => r !== roleName);
  setLocalPins(next);

  try {
    const res = await fetch(`/api/v1/staff/pins/${encodeURIComponent(roleName)}`, {
      method: "DELETE",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
    });
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data?.pins)) {
        setLocalPins(data.pins);
        return data.pins;
      }
    }
  } catch {
    // Keep local change on error
  }
  return next;
}
