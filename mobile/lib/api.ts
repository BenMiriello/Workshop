// Shared UI colors for the native shell screens (loading / error / diagnostics).
//
// NOTE: this file intentionally no longer exports a fetch helper. It previously
// carried an unused `api()` that defaulted to cleartext http://127.0.0.1:8000 —
// removed 2026-07-18 (security-audit-2026-07-18 L4). All real transport goes
// through native/config.ts `apiUrl()` (HTTPS, single backend origin).

export const colors = {
  bg: "#0b0f17",
  card: "#131a26",
  border: "#1f2a3a",
  fg: "#e7ecf3",
  muted: "#94a3b8",
  accent: "#60a5fa",
  err: "#fca5a5",
  good: "#86efac",
};
