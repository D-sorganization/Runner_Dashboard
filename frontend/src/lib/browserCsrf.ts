/** Retry only an explicitly rejected browser mutation, before any action ran. */
const proofs = new Map<string, string>();

export async function fetchWithBrowserCsrf(
  url: string,
  init: RequestInit = {},
): Promise<Response> {
  const target = new URL(url, window.location.href);
  const mutable = ["POST", "PUT", "PATCH", "DELETE"].includes(
    (init.method ?? "GET").toUpperCase(),
  );
  const match = target.pathname.match(
    /^\/api\/v1\/staff\/(?:threads|panels)\/([A-Za-z0-9_-]{1,128})(?:\/.*)?$/,
  );
  const scope = ["/api/v1/staff/threads", "/api/v1/staff/panels"].includes(
    target.pathname,
  )
    ? "_new"
    : match?.[1];
  if (
    !mutable ||
    !scope ||
    target.origin !== window.location.origin ||
    (init.body && typeof init.body !== "string")
  ) {
    return fetch(url, init);
  }
  const headers = new Headers(init.headers);
  const cached = proofs.get(scope);
  if (cached && !headers.has("X-CSRF-Token"))
    headers.set("X-CSRF-Token", cached);
  const response = await fetch(url, { ...init, headers });
  if (
    response.status !== 403 ||
    response.headers.get("X-CSRF-Required") !== "1"
  )
    return response;
  proofs.delete(scope);
  const issued = await fetch("/api/auth/browser-csrf", {
    method: "POST",
    signal: init.signal,
    credentials: "same-origin",
    headers: {
      "X-Requested-With": "XMLHttpRequest",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ thread_id: scope }),
  });
  if (!issued.ok) return response;
  const body: unknown = await issued.json();
  if (
    !body ||
    typeof body !== "object" ||
    !("token" in body) ||
    typeof body.token !== "string" ||
    !body.token
  ) {
    return response;
  }
  if (proofs.size >= 64) proofs.clear();
  proofs.set(scope, body.token);
  headers.set("X-CSRF-Token", body.token);
  return fetch(url, { ...init, headers });
}
