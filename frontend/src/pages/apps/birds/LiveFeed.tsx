import { useEffect, useRef, useState } from "react";
import { Radio } from "lucide-react";
import { jget } from "./ui";

// Viewer for live-broadcasting devices. Polls /live (scoped to who you can see)
// and renders each active feed's rolling spectrogram + "now hearing". This is how
// you watch another device's mic on your screen — the recording device pushes its
// columns to the backend (recorder.tsx), and this draws them. Poll-based (~1.2s),
// so it's near-live without a socket. Shows nothing when nobody is broadcasting.

type Hear = { common_name: string; scientific_name: string; confidence: number; is_bird: boolean };
type Feed = {
  device_token: string; device: string | null; location: string | null;
  owner: string | null; cols: number[][]; hearing: Hear[];
};

// Same palette as the recorder's spectrogram, so a remote feed looks identical.
function specColor(v: number): string {
  const t = v / 255;
  if (t < 0.25) { const k = t / 0.25; return `rgb(${10 + k * 10},${20 + k * 60},${40 + k * 60})`; }
  if (t < 0.55) { const k = (t - 0.25) / 0.3; return `rgb(20,${80 + k * 90},${100 + k * 60})`; }
  if (t < 0.8) { const k = (t - 0.55) / 0.25; return `rgb(${20 + k * 200},${170 + k * 60},${160 - k * 100})`; }
  const k = (t - 0.8) / 0.2; return `rgb(${220 + k * 35},${230 + k * 25},${60 + k * 195})`;
}

// The remote spectrogram. `cols` arrives as a rolling window re-polled every ~1.2s;
// painting it whole each poll makes motion jump a full window at a time. Instead we
// diff each payload to find only the NEWLY-arrived columns, queue them, and scroll
// them in continuously via requestAnimationFrame (like the local Spectrogram). The
// producer emits ~33 cols/s, so the queue drains at that rate (speeding up if it
// falls behind, freezing when starved) and motion is smooth between polls.
const SPEC_BG = "#0b1020";
const SCROLL_COLS_PER_SEC = 33;   // = the producer's column rate, so the feed stays
                                  // truly live (drain == produce, no growing latency).
// Columns shown across the canvas width. MORE columns => each is drawn narrower and
// the pixel scroll is slower: the feed is horizontally "compressed" so the data
// doesn't fly by. At 300 (vs 150) it scrolls at ~half speed and shows ~9s of history,
// while still being real-time.
const DISPLAY_COLS = 300;
const SEED_COLS = Math.min(DISPLAY_COLS, 140); // paint this many on first fill, right-aligned

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

  // Extract just the new columns from each polled rolling window and enqueue them.
  useEffect(() => {
    const prev = prevRef.current;
    const cur = cols || [];
    if (!cur.length) return;
    const eqCol = (a: number[], b: number[]) => a.length === b.length && a.every((v, i) => v === b[i]);
    let newCols: number[][];
    if (!prev.length) {
      newCols = cur; // first payload
    } else {
      // smallest shift k where prev[k:] is a prefix of cur -> the rest of cur is new
      let k = -1;
      for (let s = 0; s <= prev.length; s++) {
        const overlap = prev.length - s;
        let ok = true;
        for (let i = 0; i < overlap; i++) { if (!eqCol(prev[s + i], cur[i])) { ok = false; break; } }
        if (ok) { k = s; break; }
      }
      newCols = k < 0 ? cur : cur.slice(prev.length - k);
    }
    prevRef.current = cur;
    const q = queueRef.current;
    for (const c of newCols) q.push(c);
    if (q.length > DISPLAY_COLS * 2) q.splice(0, q.length - DISPLAY_COLS * 2); // bound latency
  }, [cols]);

  useEffect(() => {
    const cv = ref.current; if (!cv) return;
    const cx = cv.getContext("2d"); if (!cx) return;
    const W = cv.width, H = cv.height;
    const colPx = W / DISPLAY_COLS;
    cx.fillStyle = SPEC_BG; cx.fillRect(0, 0, W, H);
    let raf = 0, last = 0, due = 0;
    const loop = (t: number) => {
      if (!last) last = t;
      const dt = Math.min(0.25, (t - last) / 1000);
      last = t;
      const q = queueRef.current;
      // Seed instantly once a full window is buffered, so we don't scroll in blanks.
      if (!seededRef.current && q.length >= SEED_COLS) {
        const tail = q.splice(0, q.length).slice(-DISPLAY_COLS);
        cx.fillStyle = SPEC_BG; cx.fillRect(0, 0, W, H);
        const startX = W - tail.length * colPx;     // newest column at the right edge
        tail.forEach((col, i) => paintColumn(cx, startX + i * colPx, colPx, H, col));
        seededRef.current = true;
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

export default function LiveFeed({ locationId }: { locationId?: number }) {
  const [feeds, setFeeds] = useState<Feed[]>([]);
  useEffect(() => {
    let alive = true;
    const load = () => jget<{ feeds: Feed[] }>(`/live${locationId ? `?location_id=${locationId}` : ""}`)
      .then((d) => { if (alive) setFeeds(d.feeds || []); }).catch(() => {});
    load();
    const t = setInterval(load, 1200);
    return () => { alive = false; clearInterval(t); };
  }, [locationId]);

  if (!feeds.length) return null;
  return (
    <>
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
