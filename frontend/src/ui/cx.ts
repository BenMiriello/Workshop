// Tiny classNames joiner. Accepts anything; keeps only non-empty strings, so
// callers can write cx("ws-btn", primary && "ws-btn--primary", err && "...")
// regardless of whether the guard value is a boolean, number, node, or null.
export function cx(...parts: unknown[]): string {
  return parts.filter((p): p is string => typeof p === "string" && p.length > 0).join(" ");
}
