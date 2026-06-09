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
const NONE_BOUND_S = 2.4;     // a time bound this wide with no decent blob → none

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
      // isolation: how much this peak dominates a WIDER ring around it (a lone
      // chirp dominates; one chirp amid a busy band does not).
      let ringMax = 0;
      for (let dj = -6; dj <= 6; dj++) {
        for (let db = -COLLIDE_BANDS; db <= COLLIDE_BANDS; db++) {
          if (Math.abs(dj) <= 2 && Math.abs(db) <= 2) continue;
          const cj = ci + dj, bb = b + db;
          if (cj < 0 || cj >= cols.length || bb < 0 || bb >= NB) continue;
          const nv = sal(cj, bb);
          if (nv > ringMax) ringMax = nv;
        }
      }
      const isolation = v / (v + ringMax + 1);
      blobs.push({ t: cols[ci].t, band: b, strength: v, isolation });
    }
  }
  return blobs.sort((a, b) => b.strength * b.isolation - a.strength * a.isolation);
}

function bandToYFrac(band: number, nb: number): number {
  return 1 - (band + 0.5) / nb; // band 0 (low freq) -> bottom; high band -> top
}

// Score a candidate blob for an event: strong + isolated + close to the model's
// time estimate, and inside the geometric bound.
function blobScore(blob: Blob, tEst: number, bound: [number, number]): number {
  const within = blob.t >= bound[0] - ms(0.3) && blob.t <= bound[1] + ms(0.3);
  if (!within) return 0;
  // A blob inside the bound is already plausible, so proximity to the (often
  // soft) centroid estimate is only a gentle tiebreaker — energy leads when the
  // window-coverage bound is wide (few overlapping windows).
  const prox = Math.exp(-Math.abs(blob.t - tEst) / ms(1.2));
  const str = Math.min(1, blob.strength / 90);
  return str * blob.isolation * (0.6 + 0.4 * prox);
}

// Mean band energy over the bound — used for the vertical position when we have
// no single clean blob (approximate placement).
function dominantBand(history: SpecCol[], t0: number, t1: number): number | null {
  const cols = history.filter((c) => c.t >= t0 && c.t <= t1);
  if (!cols.length) return null;
  const NB = cols[0].bands.length;
  let best = -1, bestV = -1;
  for (let b = 0; b < NB; b++) {
    let s = 0;
    for (const c of cols) s += c.bands[b] ?? 0;
    if (s > bestV) { bestV = s; best = b; }
  }
  return best >= 0 ? best : null;
}

// Localize every active species event jointly so we can differentiate
// simultaneous birds (a blob is claimed by at most one species).
export function localizeEvents(events: SpeciesEvent[], history: SpecCol[]): Placement[] {
  const nb = history.find((c) => c.bands.length)?.bands.length ?? 40;

  // Per-event: time bound, model time estimate, and candidate blobs ranked for it.
  const ctx = events.map((ev) => {
    const bound = coverageBound(ev.samples);
    const tEstRaw = confCentroid(ev.samples);
    const tEst = Math.min(bound[1], Math.max(bound[0], tEstRaw));
    const blobs = salientBlobs(history, bound[0], bound[1])
      .map((b) => ({ b, s: blobScore(b, tEst, bound) }))
      .filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s);
    const peakConf = Math.max(0, ...ev.samples.map((s) => s.conf));
    return { ev, bound, tEst, blobs, peakConf };
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
    let chosen: { b: Blob; s: number } | null = null;
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
        let bestAlt: { b: Blob; s: number } | null = null, bestExcl = -Infinity;
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

    if (chosen && chosen.s >= PRECISE_SCORE) {
      claimed.push(chosen.b);
      out.push({
        sci: c.ev.sci, t: chosen.b.t, yFrac: bandToYFrac(chosen.b.band, nb),
        tStart: c.bound[0], tEnd: c.bound[1], mode: "precise", score: chosen.s,
      });
      continue;
    }

    // No clean, uncontested blob → approximate (place at the model estimate with
    // the dominant band for height) unless the bound is too wide / energy too
    // weak, in which case forgo the position entirely.
    const boundWide = c.bound[1] - c.bound[0] >= ms(NONE_BOUND_S);
    const altScore = chosen?.s ?? 0;
    const db = dominantBand(history, c.bound[0], c.bound[1]);
    if (altScore >= APPROX_SCORE && !boundWide && db != null) {
      if (chosen) claimed.push(chosen.b);
      out.push({
        sci: c.ev.sci, t: c.tEst, yFrac: bandToYFrac(db, nb),
        tStart: c.bound[0], tEnd: c.bound[1], mode: "approx", score: altScore,
      });
    } else {
      out.push({
        sci: c.ev.sci, t: c.tEst, yFrac: 0.5,
        tStart: c.bound[0], tEnd: c.bound[1], mode: "none", score: altScore,
      });
    }
  }

  // Preserve the caller's event order in the result.
  const bySci = new Map(out.map((p) => [p.sci, p]));
  return events.map((e) => bySci.get(e.sci)!).filter(Boolean);
}
