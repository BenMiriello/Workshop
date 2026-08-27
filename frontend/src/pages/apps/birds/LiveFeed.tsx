import { useEffect, useRef } from "react";
import { Radio } from "lucide-react";
import { Button, Notice } from "@workshop/ui";
import { specColor } from "./specColor";
import { useLiveFeeds, type LiveFeedRow } from "./queries";

// Viewer for live-broadcasting devices. Reads /live (scoped to who you can see)
// and renders each active feed's rolling spectrogram + "now hearing". This is how
// you watch another device's mic on your screen — the recording device pushes its
// columns to the backend (recorder.tsx), and this draws them. Near-live without a
// socket. Shows nothing when nobody is broadcasting, which is the normal state.
//
// THE READ IS NOT THIS FILE'S. It lives on one key beside its fetcher and its
// cadence in `queries.ts` (`useLiveFeeds`), shared with the aviary scene, which
// used to poll the same URL on its own 5s timer into its own copy. What this
// file owns is the CANVAS: turning a sequence of rolling windows into a smooth
// scroll, which is where the interesting failure was.

// The remote spectrogram. `cols` arrives as a rolling window re-read every ~1.2s;
// painting it whole each time makes motion jump a full window at a time. Instead we
// diff each payload to find only the NEWLY-arrived columns, queue them, and scroll
// them in continuously via requestAnimationFrame (like the local Spectrogram). The
// producer emits ~33 cols/s, so the queue drains at that rate (speeding up if it
// falls behind, freezing when starved) and motion is smooth between reads.
const SPEC_BG = "#0b1020";
const SCROLL_COLS_PER_SEC = 33;   // = the producer's column rate, so the feed stays
                                  // truly live (drain == produce, no growing latency).
// Columns shown across the canvas width. MORE columns => each is drawn narrower and
// the pixel scroll is slower: the feed is horizontally "compressed" so the data
// doesn't fly by. At 300 (vs 150) it scrolls at ~half speed and shows ~9s of history,
// while still being real-time.
const DISPLAY_COLS = 300;
const SEED_COLS = Math.min(DISPLAY_COLS, 140); // paint this many on first fill, right-aligned

// A frozen feed says so once it has been frozen long enough to mean something.
// At a 1.2s cadence a single dropped read is noise, and a notice that appears
// and vanishes every time one tick fails is worse than no notice at all.
const FEED_STALL_MS = 10_000;

const sameCol = (a: number[], b: number[]) =>
  a.length === b.length && a.every((v, i) => v === b[i]);

/**
 * How far the window advanced between two payloads: the smallest shift `k` such
 * that `prev[k:]` is a non-empty prefix of `cur`, so `cur.slice(prev.length - k)`
 * is what is new. `-1` means the two windows share NO column at all.
 *
 * TWO BOUNDS, AND BOTH OF THEM MATTER.
 *
 * The loop stops BEFORE `s === prev.length`, which is the shift with an EMPTY
 * overlap. An empty overlap matches vacuously, so the old version of this search
 * always succeeded, always returned `prev.length` for a discontinuous window,
 * and quietly re-enqueued the entire payload -- its own `k < 0` fallback was
 * unreachable. Requiring at least one shared column is what makes "these two
 * windows are not consecutive" a case the caller can actually see.
 *
 * And `s` starts at `prev.length - min(prev, cur)`, so the overlap is never
 * longer than `cur`. It used to start at 0 and read `cur[i]` past the end,
 * comparing a column against `undefined`, which throws rather than returning
 * false. The two windows are the same length in normal operation (the recorder
 * publishes its last 150 columns), so it only ever needed the first payload
 * after a restart to be short.
 */
function windowShift(prev: number[][], cur: number[][]): number {
  const widest = Math.min(prev.length, cur.length);
  for (let s = prev.length - widest; s < prev.length; s++) {
    const overlap = prev.length - s;
    let ok = true;
    for (let i = 0; i < overlap; i++) {
      if (!sameCol(prev[s + i], cur[i])) { ok = false; break; }
    }
    if (ok) return s;
  }
  return -1;
}

function paintColumn(cx: CanvasRenderingContext2D, x: number, w: number, H: number, col: number[]) {
  const bands = col.length || 40;
  const ch = H / bands;
  cx.fillStyle = SPEC_BG;
  cx.fillRect(x, 0, w, H);
  for (let b = 0; b < bands; b++) {
    cx.fillStyle = specColor(col[b] || 0);
    cx.fillRect(x, H - (b + 1) * ch, Math.ceil(w), Math.ceil(ch)); // band 0 = low freq, bottom
  }
}

function FeedCanvas({ cols }: { cols: number[][] }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const prevRef = useRef<number[][]>([]);
  const queueRef = useRef<number[][]>([]);
  const seededRef = useRef(false);
  // Set when the incoming window shares nothing with the one before it. See
  // below for why that is a JUMP and not 300 columns of new audio.
  const jumpRef = useRef(false);

  // Extract just the new columns from each rolling window and enqueue them.
  useEffect(() => {
    const cur = cols || [];
    if (!cur.length) return;
    const prev = prevRef.current;
    prevRef.current = cur;
    const q = queueRef.current;

    if (!prev.length) {                       // first payload
      for (const c of cur) q.push(c);
      return;
    }

    const k = windowShift(prev, cur);
    if (k < 0) {
      // NO SHARED COLUMN. The two windows are not consecutive: the audio in
      // between was never received, and scrolling this payload in would
      // fast-forward the newest 4.5 seconds past at twice speed while leaving
      // the 4.5 seconds from BEFORE the gap sitting beside it, unmarked, as if
      // the two were continuous. That fast-forward is the stutter.
      //
      // It is reachable in ordinary use rather than only under failure. The
      // recorder publishes its last 150 columns and the producer emits ~33 a
      // second, so a normal 1.2s read overlaps by ~110 and only a gap of ~4.5
      // seconds or more lands here -- which is exactly what a read that PAUSED
      // while the tab was hidden comes back to, and what a broadcaster that
      // restarted looks like.
      //
      // So the honest rendering is a CUT: drop the backlog and repaint the
      // newest window whole, right-aligned, on a cleared canvas. The feed
      // visibly resumes instead of lying about what happened in between.
      //
      // The other way to reach this used to be two requests in flight at once
      // on the 1.2s poll, resolving out of order. That is gone at the source
      // (one request per key at a time; see `queries.ts`), which is why this
      // branch can afford to be a hard cut rather than a guess about which
      // payload is newer.
      q.length = 0;
      for (const c of cur) q.push(c);
      jumpRef.current = true;
      return;
    }

    for (const c of cur.slice(prev.length - k)) q.push(c);
    if (q.length > DISPLAY_COLS * 2) q.splice(0, q.length - DISPLAY_COLS * 2); // bound latency
  }, [cols]);

  useEffect(() => {
    const cv = ref.current; if (!cv) return;
    const cx = cv.getContext("2d"); if (!cx) return;
    const W = cv.width, H = cv.height;
    const colPx = W / DISPLAY_COLS;
    cx.fillStyle = SPEC_BG; cx.fillRect(0, 0, W, H);
    let raf = 0, last = 0, due = 0;
    // Paint the newest window whole, right-aligned, and drop everything behind
    // it. Both the first fill and a discontinuity want exactly this.
    const paintWindow = (q: number[][]) => {
      const tail = q.splice(0, q.length).slice(-DISPLAY_COLS);
      cx.fillStyle = SPEC_BG; cx.fillRect(0, 0, W, H);
      const startX = W - tail.length * colPx;     // newest column at the right edge
      tail.forEach((col, i) => paintColumn(cx, startX + i * colPx, colPx, H, col));
      due = 0;
      seededRef.current = true;
      jumpRef.current = false;
    };
    const loop = (t: number) => {
      if (!last) last = t;
      const dt = Math.min(0.25, (t - last) / 1000);
      last = t;
      const q = queueRef.current;
      // Seed instantly once a full window is buffered, so we don't scroll in blanks.
      // A jump does not wait for SEED_COLS: the window it carries is already the
      // newest one there is, so there is nothing to buffer towards.
      if (jumpRef.current || (!seededRef.current && q.length >= SEED_COLS)) {
        paintWindow(q);
      } else if (q.length) {
        due += dt * SCROLL_COLS_PER_SEC * (q.length > 60 ? 2 : 1); // catch up if behind
        let n = Math.floor(due);
        if (n > 0) {
          due -= n;
          while (n-- > 0 && q.length) {
            cx.drawImage(cv, -colPx, 0);               // scroll left one column
            paintColumn(cx, W - colPx, colPx, H, q.shift() as number[]);
          }
        }
      } else {
        due = 0; // starved: hold, no debt
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  return <canvas ref={ref} width={600} height={140}
    style={{ width: "100%", height: 140, borderRadius: 8, display: "block", background: SPEC_BG }} />;
}

// One empty array, so "nobody is broadcasting" is referentially stable.
const NO_FEEDS: LiveFeedRow[] = [];

export default function LiveFeed({ locationId }: { locationId?: number }) {
  // The WATCHING side of the live feed (the broadcasting device is recorder.tsx,
  // on a different phone). Nobody is looking at this spectrogram while the tab is
  // hidden, so pausing while hidden and catching up on the way back is exactly
  // right, and it is what the query layer already does for every read.
  //
  // The location is in the KEY, not in a closure. It used to be read out of a
  // prop inside the poll's callback, which `usePoll` holds through a ref: a
  // changed location did not re-read at all, and the previous place's feed sat
  // there until the next tick. Now it is a different cache entry, and the reads
  // for two places cannot land on each other.
  const live = useLiveFeeds({ locationId: locationId ?? null, need: "spectrogram" });
  const feeds = live.data?.feeds ?? NO_FEEDS;

  // A failure is a LINE, never a region: this panel is EMPTY in normal use, so
  // "the space would have been empty anyway" is a yes, and a 120px dashed panel
  // on the home page every time a 1.2s read drops is the exact shape the design
  // system says not to build. And it is only said once the feed has actually
  // stopped moving for ten seconds; below that the next read has already fixed
  // it. `dataUpdatedAt` is Query's own timestamp of the last good payload, so
  // this needs no timer of its own.
  const stalled = live.isError && live.errorUpdatedAt - live.dataUpdatedAt > FEED_STALL_MS;

  if (!feeds.length) return null;
  return (
    <>
      {stalled && (
        <div style={{ marginBottom: ".7rem" }}>
          <Notice
            tone="warn"
            action={<Button size="sm" variant="ghost" onClick={() => void live.refetch()}>Retry</Button>}
          >
            This feed is not updating.
          </Notice>
        </div>
      )}
      {feeds.map((f) => {
        const top = (f.hearing || []).filter((h) => h.is_bird).sort((a, b) => b.confidence - a.confidence);
        return (
          <section key={f.device_token} className="card"
            style={{ padding: "1rem", marginBottom: "1rem", border: "1px solid color-mix(in srgb, var(--ok) 33%, transparent)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: ".5rem", marginBottom: ".5rem", flexWrap: "wrap" }}>
              <Radio size={16} className="spin-pulse" style={{ color: "var(--ok)" }} />
              <span style={{ fontWeight: 600 }}>Live</span>
              <span className="muted" style={{ fontSize: ".82rem" }}>
                {f.device || "Device"}{f.location ? ` · ${f.location}` : ""}{f.owner ? ` · ${f.owner}` : ""}
              </span>
            </div>
            <FeedCanvas cols={f.cols} />
            <div style={{ marginTop: ".5rem", display: "flex", flexWrap: "wrap", gap: ".35rem" }}>
              {top.length
                ? top.slice(0, 6).map((h, i) => (
                  <span key={i} className="pill" style={{ fontSize: ".78rem" }}>{h.common_name} {Math.round(h.confidence * 100)}%</span>
                ))
                : <span className="muted" style={{ fontSize: ".8rem" }}>no bird right now</span>}
            </div>
          </section>
        );
      })}
    </>
  );
}
