/** Return href only for http(s) URLs; blocks javascript:, data:, etc. from API data. */
export function safeHttpHref(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol === "https:" || parsed.protocol === "http:") {
      return parsed.href;
    }
  } catch {
    /* invalid URL */
  }
  return null;
}
