import { useEffect, useRef } from "react";
import { useRecorder } from "./recorder";

// Visible spectrogram. The actual FFT drawing happens in the always-mounted
// provider into an offscreen canvas, so the image keeps scrolling and its
// history survives navigating between pages. Here we just blit that offscreen
// canvas onto a visible one each frame.

export default function Spectrogram({ height = 160 }: { height?: number }) {
  const { specCanvasRef, recording } = useRecorder();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const blit = () => {
      rafRef.current = requestAnimationFrame(blit);
      const src = specCanvasRef.current;
      if (src) ctx.drawImage(src, 0, 0, canvas.width, canvas.height);
    };
    blit();
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [specCanvasRef]);

  return (
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
    </div>
  );
}
