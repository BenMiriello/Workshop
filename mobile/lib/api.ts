// Shared UI colors for the native shell screens (loading / error / diagnostics).
//
// These come from the Workshop Design System, NOT from hand-picked hexes. CSS
// custom properties don't reach React Native, so the kit ships the same values
// as a plain object: `@workshop/ui/tokens.js` is generated from the very model
// (packages/ui/src/styles/palette.mjs) that emits the web's palettes.css, so the
// app and the web UI cannot drift apart.
//
// Until 2026-08-11 this file hardcoded eight hexes copied from a theme that no
// longer exists — `#0b0f17` / `#60a5fa`, a blue accent that is in none of the
// five current palettes. The mobile shell was rendering a color scheme the rest
// of the system had abandoned.
//
// NOTE: this file intentionally no longer exports a fetch helper. It previously
// carried an unused `api()` that defaulted to cleartext http://127.0.0.1:8000 —
// removed 2026-07-18 (security-audit-2026-07-18 L4). All real transport goes
// through native/config.ts `apiUrl()` (HTTPS, single backend origin).

import { makeTheme, tokens } from "@workshop/ui/tokens.js";

// Dark is the Workshop default and the only mode these shell screens render in,
// so the theme is resolved once here rather than threaded through a provider.
// `makeTheme(scheme, paletteId)` takes a palette id as its second argument if
// this app ever grows a palette picker.
const theme = makeTheme("dark");

export const colors = {
  bg: theme.bg,
  card: theme.card,
  border: theme.border,
  fg: theme.fg,
  muted: theme.muted,
  accent: theme.accent,
  // Status is meaning: `danger` and `ok` are byte-identical across all five
  // palettes, so these two stay put no matter which scheme is active.
  err: theme.danger,
  good: theme.ok,
};

// The theme-independent scales (space, type, radii, control sizing) mirror
// tokens.css. Use these instead of literal numbers in StyleSheet:
//   padding: tokens.space[4], borderRadius: tokens.radius.lg
export { tokens, theme };
