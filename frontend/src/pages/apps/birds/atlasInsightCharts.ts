// Soundscape insight charts for the Atlas — ported near-verbatim from the
// redesign handoff's reference renderers (renderPresenceScatter / renderHeat /
// renderNovelty), adapted to real data and design tokens. They build SVG strings
// the React components inject; recharts handles scatter/heatmap/novelty poorly,
// so these stay hand-built SVG per the spec.
import { catFor } from "@workshop/ui";
import { hh } from "./ui";

function niceMax(v: number): number {
  if (v <= 4) return 4;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}
function ticks(max: number, n = 4): number[] {
  const out: number[] = [];
  for (let i = 0; i <= n; i++) out.push(Math.round(max * i / n));
  return out;
}
const hue = (name: string) => `var(--cat-${catFor(name)})`;

// 5.1 — presence vs raw detections; the dashed diagonal is the typical
// detections-per-presence ratio. Above it = over-talkers, below = present-but-quiet.
export type ScatterPt = { name: string; win: number; det: number };
export function scatterSVG(SP: ScatterPt[]): string {
  if (!SP.length) return "";
  const W = 680, H = 330, m = { l: 50, r: 30, t: 22, b: 48 };
  const pl = m.l, pr = W - m.r, pt = m.t, pb = H - m.b, pw = pr - pl, ph = pb - pt;
  const xmax = niceMax(Math.max(...SP.map((s) => s.win))), ymax = niceMax(Math.max(...SP.map((s) => s.det)));
  const X = (v: number) => pl + pw * (v / xmax), Y = (v: number) => pb - ph * (v / ymax);
  const k = SP.reduce((a, s) => a + s.det, 0) / Math.max(1, SP.reduce((a, s) => a + s.win, 0));
  let svg = `<svg viewBox="0 0 ${W} ${H}" class="chart" preserveAspectRatio="xMidYMid meet">`;
  ticks(ymax).forEach((t) => { const y = Y(t); svg += `<line x1="${pl}" y1="${y.toFixed(1)}" x2="${pr}" y2="${y.toFixed(1)}" class="grid"/><text x="${pl - 7}" y="${(y + 3.5).toFixed(1)}" class="ax-y">${t}</text>`; });
  ticks(xmax).forEach((t) => { const x = X(t); svg += `<text x="${x.toFixed(1)}" y="${pb + 16}" class="ax-x">${t}</text>`; });
  const yend = Math.min(ymax, k * xmax);
  svg += `<line x1="${X(0)}" y1="${Y(0)}" x2="${X(yend / k).toFixed(1)}" y2="${Y(yend).toFixed(1)}" class="sc-diag"/>`;
  svg += `<text x="${(X(yend / k) - 4).toFixed(1)}" y="${(Y(yend) - 6).toFixed(1)}" class="sc-zone" text-anchor="end">typical talk : presence</text>`;
  SP.forEach((s) => { svg += `<circle cx="${X(s.win).toFixed(1)}" cy="${Y(s.det).toFixed(1)}" r="6" fill="${hue(s.name)}" fill-opacity=".88"/>`; });
  // label the points furthest from the diagonal (the clearest signals), with a
  // small offset above/below depending on which side of the line they fall.
  [...SP].map((s) => ({ s, dev: Math.abs(s.det - k * s.win) })).sort((a, b) => b.dev - a.dev).slice(0, 6)
    .forEach(({ s }) => { const above = s.det > k * s.win; svg += `<text x="${(X(s.win) + 9).toFixed(1)}" y="${(Y(s.det) + (above ? -8 : 12)).toFixed(1)}" class="sc-lab">${s.name}</text>`; });
  svg += `<text x="${pl + 8}" y="${pt + 12}" class="sc-zone" fill="var(--cat-orchid)">▲ more talkative</text>`;
  svg += `<text x="${pr - 6}" y="${pb - 8}" class="sc-zone" text-anchor="end" fill="var(--cat-turquoise)">present, quieter ▶</text>`;
  svg += `<text x="${pl}" y="${pb + 36}" class="ax-cap" text-anchor="start">presence → 15-min windows</text>`;
  svg += `<text transform="translate(${pl - 38},${(pt + ph / 2).toFixed(1)}) rotate(-90)" class="ax-cap" text-anchor="middle">raw detections ↑</text>`;
  svg += `</svg>`;
  return svg;
}

// 5.2 — call-type x hour-of-day intensity heatmap.
export function heatSVG(callTypes: string[], heat: number[][], sunrise = 6.5, sunset = 20): string {
  const max = Math.max(1, ...heat.flat());
  const W = 720, H = 58 + callTypes.length * 30, m = { l: 74, r: 16, t: 30, b: 24 };
  const pl = m.l, pr = W - m.r, pt = m.t, pb = H - m.b, pw = pr - pl, rowH = (pb - pt) / Math.max(1, callTypes.length);
  const cw = pw / 24;
  let svg = `<svg viewBox="0 0 ${W} ${H}" class="chart" preserveAspectRatio="xMidYMid meet">`;
  for (let h = 0; h < 24; h += 3) svg += `<text x="${(pl + cw * (h + 0.5)).toFixed(1)}" y="${pt - 9}" class="ax-x">${hh(h)}</text>`;
  [sunrise, sunset].forEach((t, i) => { const x = pl + cw * t; svg += `<line x1="${x.toFixed(1)}" y1="${pt - 4}" x2="${x.toFixed(1)}" y2="${pb}" class="mk-line"/><text x="${x.toFixed(1)}" y="${pb + 16}" class="mk-cap" text-anchor="middle">${i ? "sunset" : "sunrise"}</text>`; });
  heat.forEach((row, r) => {
    const y = pt + rowH * r;
    svg += `<text x="${pl - 9}" y="${(y + rowH / 2 + 3.5).toFixed(1)}" class="ax-y" text-anchor="end">${callTypes[r]}</text>`;
    row.forEach((v, h) => { const x = pl + cw * h, op = v === 0 ? 0.04 : 0.12 + 0.88 * (v / max); svg += `<rect x="${x.toFixed(1)}" y="${(y + 2).toFixed(1)}" width="${(cw - 1.5).toFixed(1)}" height="${(rowH - 4).toFixed(1)}" rx="2" fill="var(--cat-indigo)" fill-opacity="${op.toFixed(2)}"/>`; });
  });
  svg += `</svg>`;
  return svg;
}

// 5.3 — acoustic-novelty timeline: each point is a first appearance over ~30 days;
// `day` is 0 (30d ago) .. 30 (today). Recent arrivals are flagged (ringed).
export type NoveltyPt = { day: number; label: string; kind: "species" | "call"; flag: boolean };
export function noveltySVG(items: NoveltyPt[]): string {
  if (!items.length) return "";
  const W = 720, H = 150, m = { l: 16, r: 16, t: 42, b: 30 };
  const pl = m.l, pr = W - m.r, pt = m.t, pb = H - m.b, pw = pr - pl, axisY = (pt + pb) / 2;
  const fx = (d: number) => pl + pw * (d / 30);
  let svg = `<svg viewBox="0 0 ${W} ${H}" class="chart" preserveAspectRatio="xMidYMid meet">`;
  svg += `<line x1="${pl}" y1="${axisY}" x2="${pr}" y2="${axisY}" class="axis"/>`;
  for (let d = 0; d <= 30; d += 5) svg += `<text x="${fx(d).toFixed(1)}" y="${pb + 18}" class="ax-x">${d === 0 ? "30d ago" : d === 30 ? "today" : "−" + (30 - d) + "d"}</text>`;
  items.forEach((ev, i) => {
    const x = fx(ev.day), up = i % 2 === 0, ly = up ? pt + 8 : pb - 8;
    const col = ev.kind === "species" ? "var(--cat-violet)" : "var(--cat-turquoise)";
    svg += `<line x1="${x.toFixed(1)}" y1="${axisY}" x2="${x.toFixed(1)}" y2="${ly.toFixed(1)}" stroke="${col}" stroke-width="1.4" opacity=".5"/>`;
    svg += `<circle cx="${x.toFixed(1)}" cy="${axisY}" r="${ev.flag ? 5 : 3.5}" fill="${col}" ${ev.flag ? 'stroke="var(--accent)" stroke-width="2"' : ""}/>`;
    svg += `<text x="${x.toFixed(1)}" y="${(up ? ly - 4 : ly + 12).toFixed(1)}" class="nov-lab" fill="${col}" text-anchor="middle">${ev.label}</text>`;
  });
  svg += `</svg>`;
  return svg;
}
