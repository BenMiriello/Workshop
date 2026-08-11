import { useEffect, useMemo, useRef } from "react";
import { useRecorder, SPEC_W, specMarkerX } from "./recorder";
import { BirdThumb } from "./ui";

// Visible spectrogram. The actual FFT drawing happens in the always-mounted
// provider into an offscreen canvas, so the image keeps scrolling and its
// history survives navigating between pages. Here we just blit that offscreen
// canvas onto a visible one each frame.
//
// On top of the image we pin one marker per vocalization EVENT. Its placement
// degrades honestly with how well we can localize the call (see specLocalize):
//   precise → solid dot at the (time, frequency) energy blob, with a guide line
//             down to the chip and an up-triangle on the chip pointing back;
//   approx  → faded dot at the model's time estimate (no clean blob to snap to);
//   none    → chip only, no dot (couldn't disambiguate — e.g. two birds at the
//             same instant and band), shown with a dashed chip and no triangle.
// The marker set only re-renders when it changes (markerEpoch); each marker's
// horizontal position is recomputed imperatively every frame via specMarkerX()
// — a pure function of wall-clock time and the shared scroll speed — so it stays
// glued to its column as the spectrogram scrolls and can never drift.

export default function Spectrogram({ height = 160 }: { height?: number }) {
  const { specCanvasRef, recording, specMarkersRef, markerEpoch } = useRecorder();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const dotRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const labelRefs = useRef<Map<number, HTMLDivElement>>(new Map());

  // Recomputed when the marker set/modes change; positions update per-frame in
  // the blit loop, not here.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const markers = useMemo(() => [...specMarkersRef.current], [markerEpoch]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const draw = () => {
      const src = specCanvasRef.current;
      if (src) ctx.drawImage(src, 0, 0, canvas.width, canvas.height);
      // Glue every marker's dot + chip to its scrolling column. Position is a
      // pure function of time (same clock + px/s the image scrolls at), so it
      // tracks the column exactly with no accumulated error.
      const now = performance.now();
      for (const m of specMarkersRef.current) {
        const leftPct = `${(specMarkerX(m, now) / SPEC_W) * 100}%`;
        const dot = dotRefs.current.get(m.id);
        if (dot) dot.style.left = leftPct;
        const lab = labelRefs.current.get(m.id);
        if (lab) lab.style.left = leftPct;
      }
    };

    // ONLY ANIMATE WHILE LISTENING. The offscreen canvas is written by the
    // recorder, so when the mic is off the image is not changing and the
    // columns are not scrolling: one paint shows the retained history and is
    // the whole job. This loop used to run unconditionally, so simply having
    // the Birds page mounted repainted a 600x160 canvas at 60fps forever,
    // idle, which on a phone is the difference between warm and not.
    //
    // Freezing is also the more correct rendering: marker positions are a
    // function of wall-clock time, so continuing to advance them against a
    // stopped image walked them away from the columns they belong to.
    if (!recording) { draw(); return; }

    const blit = () => { rafRef.current = requestAnimationFrame(blit); draw(); };
    blit();
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [specCanvasRef, specMarkersRef, recording, markerEpoch]);

  // Initial left for freshly-rendered markers; the blit loop refines it every
  // frame. Same time-based formula, so there's no flash.
  const now0 = performance.now();
  return (
    <div style={{ position: "relative", width: "100%" }}>
      <div style={{ position: "relative", width: "100%", height, borderRadius: 8, overflow: "hidden", background: "#0b1020" }}>
        <canvas ref={canvasRef} width={600} height={height}
          style={{ width: "100%", height: "100%", display: "block", imageRendering: "pixelated" }} />
        {!recording && (
          <div className="muted" style={{ position: "absolute", inset: 0, display: "flex",
            alignItems: "center", justifyContent: "center", fontSize: ".85rem" }}>
            Spectrogram appears while listening
          </div>
        )}
        <div className="muted" style={{ position: "absolute", left: 6, top: 4, fontSize: ".62rem", opacity: .7 }}>12 kHz</div>
        <div className="muted" style={{ position: "absolute", left: 6, bottom: 4, fontSize: ".62rem", opacity: .7 }}>0 kHz</div>

        {/* Detection dots: the exact (time, frequency) spot. Solid + guide line
            when precise; faded and lineless when only approximate; omitted when
            we couldn't place it at all. */}
        {markers.filter((m) => m.mode !== "none").map((m) => (
          <div key={m.id}
            ref={(el) => { if (el) dotRefs.current.set(m.id, el); else dotRefs.current.delete(m.id); }}
            style={{ position: "absolute", top: 0, bottom: 0, left: `${(specMarkerX(m, now0) / SPEC_W) * 100}%`,
              width: 0, pointerEvents: "none", opacity: m.mode === "precise" ? 1 : 0.45 }}>
            <span style={{ position: "absolute", top: `${m.yFrac * 100}%`, left: 0,
              transform: "translate(-50%, -50%)", width: 11, height: 11, borderRadius: "50%",
              border: "2px solid var(--accent)", background: "color-mix(in srgb, var(--accent) 35%, transparent)",
              boxShadow: "0 0 0 2px rgba(0,0,0,.4)" }} />
            {m.mode === "precise" && (
              <span style={{ position: "absolute", top: `${m.yFrac * 100}%`, bottom: 0, left: 0,
                transform: "translateX(-50%)", width: 2,
                background: "linear-gradient(var(--accent), transparent)", opacity: .45 }} />
            )}
          </div>
        ))}
      </div>

      {/* Chip band beneath the spectrogram. Each chip is centered under its
          column; a precise chip points up at its dot, an approximate one points
          faintly, and a position-less one has no pointer and a dashed edge. The
          band keeps a fixed height while listening so the layout doesn't jump. */}
      <div style={{ position: "relative", height: recording ? 42 : 0, marginTop: recording ? 6 : 0,
        transition: "height var(--dur-fast) var(--ease-out)" }}>
        {markers.map((m) => (
          <div key={m.id}
            ref={(el) => { if (el) labelRefs.current.set(m.id, el); else labelRefs.current.delete(m.id); }}
            title={`${m.common} · ${Math.round(m.conf * 100)}%${m.mode === "none" ? " · position uncertain" : ""}`}
            style={{ position: "absolute", top: 0, left: `${(specMarkerX(m, now0) / SPEC_W) * 100}%`,
              transform: "translateX(-50%)", display: "flex", alignItems: "center", gap: 6,
              padding: "3px 8px 3px 3px", borderRadius: 8, background: "var(--card)",
              border: m.mode === "none" ? "1px dashed var(--border)" : "1px solid var(--border)",
              boxShadow: "var(--shadow-md)", whiteSpace: "nowrap", zIndex: m.id }}>
            {m.mode !== "none" && (
              <span style={{ position: "absolute", top: -5, left: "50%", transform: "translateX(-50%)",
                width: 0, height: 0, borderLeft: "5px solid transparent", borderRight: "5px solid transparent",
                borderBottom: "5px solid var(--accent)", opacity: m.mode === "precise" ? 1 : 0.5 }} />
            )}
            <BirdThumb sci={m.sci} common={m.common} size={26} />
            <span style={{ fontSize: ".72rem", fontWeight: 600, maxWidth: 120,
              overflow: "hidden", textOverflow: "ellipsis" }}>{m.common}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
