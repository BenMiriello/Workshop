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

function FeedCanvas({ cols }: { cols: number[][] }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const cv = ref.current; if (!cv) return;
    const cx = cv.getContext("2d"); if (!cx) return;
    const W = cv.width, H = cv.height;
    cx.fillStyle = "#0b1020"; cx.fillRect(0, 0, W, H);
    const n = cols.length; if (!n) return;
    const bands = cols[0]?.length || 40;
    const cw = W / n, ch = H / bands;
    for (let i = 0; i < n; i++) {
      const col = cols[i] || [];
      for (let b = 0; b < bands; b++) {
        cx.fillStyle = specColor(col[b] || 0);
        cx.fillRect(i * cw, H - (b + 1) * ch, Math.ceil(cw), Math.ceil(ch)); // band 0 = low freq, bottom
      }
    }
  }, [cols]);
  return <canvas ref={ref} width={600} height={140}
    style={{ width: "100%", height: 140, borderRadius: 8, display: "block", background: "#0b1020" }} />;
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
