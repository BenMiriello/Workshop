// Spectrogram detection localization — pure, DOM-free logic (so it can be
// unit-verified against synthetic scenarios).
//
// THE PROBLEM
// BirdNET is a 3-second CLIP classifier: it tells us "species X is somewhere in
// these 3 seconds" with a confidence, but NOT when within the window, and not
// which spectral blob. We want to pin an ID card on the actual chirp (time +
// frequency) on a live, scrolling spectrogram — and do something intelligent
// when two birds call at once.
//
// WHAT WE EXPLOIT
//  1. OVERLAP. The recorder posts a 3s window every STEP seconds, so a brief
//     call is seen by several overlapping windows. The set of windows that
//     detect a species geometrically bounds the call's time: if a window posted
//     at tPost detects it, the call lies in [tPost-3, tPost]; intersecting over
//     all detecting windows narrows it (→ ~STEP-wide for a brief call). The
//     CONFIDENCE across those windows peaks when the call is best captured, so
//     the confidence-weighted centroid sharpens the estimate further.
//  2. PER-SPECIES curves. BirdNET scores each species independently per window,
//     so two simultaneous species localize independently in TIME from their own
//     curves — energy never has to guess the species.
//  3. HEATMAP (energy). Within the model's time bound we look at the actual
//     spectrogram energy to (a) snap to the precise chirp column and (b) read
//     the frequency band (vertical position). Energy is the sub-second
//     fine-tuner, constrained by the per-species time bound — not the lead.
//
// SCENARIOS WE HANDLE (see specLocalize.verify.ts for executable cases)
//  - One bird, one chirp                → precise dot at the energy blob.
//  - One bird, sustained song           → wide time bound, weak single blob →
//                                          approximate (faded), don't fake it.
//  - Two birds, different freq bands    → both precise, separated vertically.
//  - Two birds, same time AND band      → DIFFERENTIATE before giving up:
//      • assign the prominent (louder/stronger) blob to the species more
//        confident at that instant; the other takes its next-best blob;
//      • if a species has no clean blob of its own, prefer the moment where it
//        is most UNIQUELY present (highest confidence relative to the rival)
//        even if that instant is not the globally loudest — trading raw
//        loudness for species-specificity;
//      • only if still unresolved → approximate, and as a last resort drop the
//        position entirely (card only).
//  - One detecting window only          → 3s bound, no overlap help → approx or
//                                          none unless a single blob clearly stands out.

export type Sample = { t: number; conf: number }; // one analysis window: post time + confidence
export type SpeciesEvent = { sci: string; common: string; samples: Sample[] };
// One reduced spectrogram column kept in the rolling history. `bands[0]` is the
// lowest frequency, `bands[NB-1]` the highest; values are 0..~255 energy.
export type SpecCol = { t: number; bands: number[] };
export type PlaceMode = "precise" | "approx" | "none";
export type Placement = {
  sci: string;
  t: number;        // chosen time (the chirp, or the bound's center when approx/none)
  yFrac: number;    // 0..1 from the TOP of the spectrogram (high freq = small)
  tStart: number;   // geometric time bound (for the uncertainty bracket)
  tEnd: number;
  mode: PlaceMode;
  score: number;    // position confidence 0..1 (how much we trust the dot)
};

// --- tunables ---------------------------------------------------------------
const WIN_S = 3;              // BirdNET window length (matches recorder WINDOW_S)
const CENTER_BIAS = WIN_S / 2; // a brief call sits ~window-center of its detecting windows
const COLLIDE_S = 0.6;        // two placements closer than this in time may collide
const COLLIDE_BANDS = 4;      // ...and within this many bands → same blob / contested
const PRECISE_SCORE = 0.45;   // blob score needed to draw a precise dot
const APPROX_SCORE = 0.18;    // below this → no position at all (card only)

const ms = (s: number) => s * 1000;

// Geometric time bound from window coverage: the call lies in
// [max(tPost)-WIN, min(tPost)]. One window → full 3s; overlapping windows narrow it.
export function coverageBound(samples: Sample[]): [number, number] {
  let lo = Infinity, hi = -Infinity;
  for (const s of samples) { if (s.t < lo) lo = s.t; if (s.t > hi) hi = s.t; }
  const tStart = hi - ms(WIN_S);
  const tEnd = lo;
  return tStart <= tEnd ? [tStart, tEnd] : [tEnd, tStart];
}

// Confidence-weighted centroid of post times, pulled back by ~half a window so
// it estimates the call instant rather than the windows' center of mass.
export function confCentroid(samples: Sample[]): number {
  let sw = 0, swt = 0;
  for (const s of samples) { const w = Math.max(0, s.conf); sw += w; swt += w * s.t; }
  const centroid = sw > 0 ? swt / sw : samples[samples.length - 1]?.t ?? 0;
  return centroid - ms(CENTER_BIAS);
}

// Linear-interpolate a species' confidence at time t from its samples (0 outside).
function confAt(samples: Sample[], t: number): number {
  if (!samples.length) return 0;
  const xs = [...samples].sort((a, b) => a.t - b.t);
  if (t <= xs[0].t) return xs[0].conf;
  if (t >= xs[xs.length - 1].t) return xs[xs.length - 1].conf;
  for (let i = 1; i < xs.length; i++) {
    if (t <= xs[i].t) {
      const a = xs[i - 1], b = xs[i];
      const f = (t - a.t) / (b.t - a.t || 1);
      return a.conf + f * (b.conf - a.conf);
    }
  }
  return xs[xs.length - 1].conf;
}

type Blob = { t: number; band: number; strength: number; isolation: number };

// Find salient energy blobs in [t0,t1]: cells that stand out from their own
// band's recent baseline (a transient), then keep local maxima. `isolation`
// rewards a blob with no comparably-strong neighbor nearby — i.e. one we can
// attribute cleanly to a single source.
export function salientBlobs(history: SpecCol[], t0: number, t1: number): Blob[] {
  const cols = history.filter((c) => c.t >= t0 - ms(0.4) && c.t <= t1 + ms(0.4));
  if (cols.length < 2) return [];
  const NB = cols[0].bands.length;
  // Per-band baseline = median-ish (use the 30th percentile to track the floor).
  const baseline: number[] = [];
  for (let b = 0; b < NB; b++) {
    const vals = cols.map((c) => c.bands[b] ?? 0).sort((x, y) => x - y);
    baseline[b] = vals[Math.floor(vals.length * 0.3)] ?? 0;
  }
  const sal = (ci: number, b: number) => Math.max(0, (cols[ci].bands[b] ?? 0) - baseline[b]);
  const blobs: Blob[] = [];
  for (let ci = 0; ci < cols.length; ci++) {
    for (let b = 0; b < NB; b++) {
      const v = sal(ci, b);
      if (v < 18) continue; // ignore floor noise
      // local maximum in a small (time,band) neighborhood
      let isMax = true, neighborMax = 0;
      for (let dj = -2; dj <= 2 && isMax; dj++) {
        for (let db = -2; db <= 2; db++) {
          if (dj === 0 && db === 0) continue;
          const cj = ci + dj, bb = b + db;
          if (cj < 0 || cj >= cols.length || bb < 0 || bb >= NB) continue;
          const nv = sal(cj, bb);
          if (nv > v) { isMax = false; break; }
          if (nv > neighborMax) neighborMax = nv;
        }
      }
      if (!isMax) continue;
      // isolation: is there ANOTHER comparable peak in the SAME frequency band
      // nearby in time (a competing call we couldn't cleanly attribute)? We look
      // only at the same band and SKIP this blob's own temporal core/tail, and
      // we don't penalise energy in OTHER bands — a second bird at a different
      // frequency is still cleanly placeable. A lone chirp scores ~1.
      let nearSame = 0;
      for (let dj = -14; dj <= 14; dj++) {
        if (Math.abs(dj) <= 6) continue; // skip the blob's own spread
        for (let db = -COLLIDE_BANDS; db <= COLLIDE_BANDS; db++) {
          const cj = ci + dj, bb = b + db;
          if (cj < 0 || cj >= cols.length || bb < 0 || bb >= NB) continue;
          const nv = sal(cj, bb);
          if (nv > nearSame) nearSame = nv;
        }
      }
      const isolation = v / (v + nearSame + 1);
      blobs.push({ t: cols[ci].t, band: b, strength: v, isolation });
    }
  }
  return blobs.sort((a, b) => b.strength * b.isolation - a.strength * a.isolation);
}

function bandToYFrac(band: number, nb: number): number {
  return 1 - (band + 0.5) / nb; // band 0 (low freq) -> bottom; high band -> top
}

// How much we TRUST a blob as a position (0..1): loud + spectrally clean. The
// precise/approx/none tier is judged on this alone — recency must not lower it.
function blobQuality(blob: Blob): number {
  return Math.min(1, blob.strength / 90) * blob.isolation;
}
// SELECTION score: quality, then prefer the most RECENT blob in the range. This
// is what makes a repeated-chirp event hop to the newest call (so the final
// chirp gets tagged); a lone chirp is the only blob, so recency is moot.
function blobSelect(blob: Blob, range: [number, number]): number {
  const span = range[1] - range[0] || 1;
  const recency = Math.min(1, Math.max(0, (blob.t - range[0]) / span));
  return blobQuality(blob) * (0.65 + 0.35 * recency);
}

// Localize every active species event jointly so we can differentiate
// simultaneous birds (a blob is claimed by at most one species).
export function localizeEvents(events: SpeciesEvent[], history: SpecCol[]): Placement[] {
  const nb = history.find((c) => c.bands.length)?.bands.length ?? 40;

  // Per-event: search the MOST RECENT window of audio (so the pin tracks the
  // newest chirp and the final one is reached), and rank candidate blobs there.
  const ctx = events.map((ev) => {
    const maxPost = Math.max(...ev.samples.map((s) => s.t));
    const range: [number, number] = [maxPost - ms(WIN_S), maxPost];
    const inRange = (t: number) => t >= range[0] - ms(0.3) && t <= range[1] + ms(0.3);
    const blobs = salientBlobs(history, range[0], range[1])
      .filter((b) => inRange(b.t))
      .map((b) => ({ b, s: blobSelect(b, range), q: blobQuality(b) }))
      .sort((a, b) => b.s - a.s); // selection order: quality, recency-preferred
    const peakConf = Math.max(0, ...ev.samples.map((s) => s.conf));
    const tFallback = maxPost - ms(1); // a plausible-recent time when no blob is usable
    return { ev, range, blobs, peakConf, tFallback };
  });

  const claimed: Blob[] = [];
  const tooClose = (a: Blob, b: Blob) =>
    Math.abs(a.t - b.t) < ms(COLLIDE_S) && Math.abs(a.band - b.band) <= COLLIDE_BANDS;

  // Greedy joint assignment, processing the most confident events first so they
  // get first pick of the prominent blobs; rivals fall back to their next-best
  // (most species-specific) blob, then to approximate, then to none.
  const order = [...ctx].sort((a, b) => b.peakConf - a.peakConf);
  const out: Placement[] = [];

  for (const c of order) {
    // First UNCLAIMED candidate blob for this event.
    let chosen: { b: Blob; s: number; q: number } | null = null;
    for (const cand of c.blobs) {
      if (claimed.some((cl) => tooClose(cl, cand.b))) continue;
      chosen = cand;
      break;
    }

    // Differentiation: if our only good blob is contested, prefer the instant
    // where THIS species is most uniquely present (conf relative to whoever
    // else is around then), even if it isn't the loudest blob.
    if (chosen) {
      const contestedTop = c.blobs[0] && claimed.some((cl) => tooClose(cl, c.blobs[0].b));
      if (contestedTop) {
        const rivals = order.filter((o) => o !== c);
        let bestAlt: { b: Blob; s: number; q: number } | null = null, bestExcl = -Infinity;
        for (const cand of c.blobs) {
          if (claimed.some((cl) => tooClose(cl, cand.b))) continue;
          const mine = confAt(c.ev.samples, cand.b.t);
          const other = Math.max(0, ...rivals.map((r) => confAt(r.ev.samples, cand.b.t)));
          const exclusivity = mine - other;           // species-specificity of this instant
          const v = exclusivity * 1.0 + cand.s * 0.5;  // trade some loudness for uniqueness
          if (v > bestExcl) { bestExcl = v; bestAlt = cand; }
        }
        if (bestAlt) chosen = bestAlt;
      }
    }

    // Tier on the chosen blob's QUALITY (loud + spectrally clean) — never on its
    // recency-weighted selection score: a strong clean blob → precise dot; a
    // borderline one → approximate (faded) dot; nothing usable → card only.
    const q = chosen?.q ?? 0;
    if (chosen && q >= PRECISE_SCORE) {
      claimed.push(chosen.b);
      out.push({
        sci: c.ev.sci, t: chosen.b.t, yFrac: bandToYFrac(chosen.b.band, nb),
        tStart: c.range[0], tEnd: c.range[1], mode: "precise", score: q,
      });
    } else if (chosen && q >= APPROX_SCORE) {
      claimed.push(chosen.b);
      out.push({
        sci: c.ev.sci, t: chosen.b.t, yFrac: bandToYFrac(chosen.b.band, nb),
        tStart: c.range[0], tEnd: c.range[1], mode: "approx", score: q,
      });
    } else {
      out.push({
        sci: c.ev.sci, t: c.tFallback, yFrac: 0.5,
        tStart: c.range[0], tEnd: c.range[1], mode: "none", score: q,
      });
    }
  }

  // Preserve the caller's event order in the result.
  const bySci = new Map(out.map((p) => [p.sci, p]));
  return events.map((e) => bySci.get(e.sci)!).filter(Boolean);
}
