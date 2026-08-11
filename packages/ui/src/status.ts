// Status = MEANING only. Five kinds, each with text / -solid dot / -soft tint
// bindings in components.css (.ws-status-*). Never use a hex literal for status;
// map your domain value to one of these and let StatusPill / StatusDot bind it.
//
//   ok       green (muted)  healthy, success, up
//   running  violet (pulses) actively executing, live
//   stopped  slate          idle, paused, disabled
//   warn     gold           degraded, needs attention
//   danger   clay-rose      failed, error, destructive
export type StatusKind = "ok" | "running" | "stopped" | "warn" | "danger";

export function statusClass(kind: StatusKind): string {
  return `ws-status-${kind}`;
}

// Categorical = DIFFERENTIATION, not status. For telling apart agents, sources,
// repos, chart series — cool tones that can't be misread as ok/warn/danger.
export type CatHue = "violet" | "turquoise" | "blue" | "indigo" | "orchid" | "slate";

export function catClass(hue: CatHue): string {
  return `ws-cat-${hue}`;
}

// Stable hue for an arbitrary key (agent id, source name) so the same entity
// always gets the same categorical color across the app.
const CAT_HUES: CatHue[] = ["violet", "turquoise", "blue", "indigo", "orchid", "slate"];

export function catFor(key: string): CatHue {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  return CAT_HUES[h % CAT_HUES.length];
}
