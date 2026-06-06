import { useEffect, useRef } from "react";
import { useRecorder } from "./recorder";

// Live scrolling spectrogram, the way Merlin draws one: frequency on the Y axis
// (low at the bottom), time scrolling right-to-left, magnitude as brightness.
// Reads the recorder's AnalyserNode each animation frame; renders nothing useful
// until recording starts.

const MAX_HZ = 12000; // bird song mostly lives below this; crop the top of the FFT

// Dark -> teal -> yellow -> white ramp for magnitude 0..255.
function color(v: number): string {
  const t = v / 255;
  if (t < 0.25) { const k = t / 0.25; return `rgb(${10 + k * 10},${20 + k * 60},${40 + k * 60})`; }
  if (t < 0.55) { const k = (t - 0.25) / 0.3; return `rgb(${20},${80 + k * 90},${100 + k * 60})`; }
  if (t < 0.8) { const k = (t - 0.55) / 0.25; return `rgb(${20 + k * 200},${170 + k * 60},${160 - k * 100})`; }
  const k = (t - 0.8) / 0.2; return `rgb(${220 + k * 35},${230 + k * 25},${60 + k * 195})`;
}

export default function Spectrogram({ height = 160 }: { height?: number }) {
  const { analyserRef, recording } = useRecorder();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const draw = () => {
      rafRef.current = requestAnimationFrame(draw);
      const analyser = analyserRef.current;
      const w = canvas.width, h = canvas.height;
      if (!analyser) return;
      const bins = analyser.frequencyBinCount;
      const data = new Uint8Array(bins);
      analyser.getByteFrequencyData(data);
      // Scroll existing image left by 1px, then paint the newest column at right.
      ctx.drawImage(canvas, -1, 0);
      const nyquist = (analyser.context.sampleRate || 48000) / 2;
      const topBin = Math.min(bins, Math.ceil((MAX_HZ / nyquist) * bins));
      for (let y = 0; y < h; y++) {
        // y=0 is the top of the canvas -> highest frequency.
        const bin = Math.floor((1 - y / h) * topBin);
        ctx.fillStyle = color(data[Math.max(0, Math.min(topBin - 1, bin))] || 0);
        ctx.fillRect(w - 1, y, 1, 1);
      }
    };

    if (recording) {
      draw();
    } else {
      ctx.fillStyle = "#0b1020";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [recording, analyserRef]);

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
      <div className="muted" style={{ position: "absolute", left: 6, top: 4, fontSize: ".62rem", opacity: .7 }}>{Math.round(MAX_HZ / 1000)} kHz</div>
      <div className="muted" style={{ position: "absolute", left: 6, bottom: 4, fontSize: ".62rem", opacity: .7 }}>0 kHz</div>
    </div>
  );
}
