// The spectrogram palette, in one place. `recorder.tsx` (the local canvas) and
// `LiveFeed.tsx` (the remote one) each carried a byte-identical copy, with
// LiveFeed's comment reading "Same palette as the recorder's spectrogram, so a
// remote feed looks identical" -- a stated invariant that nothing enforced.
// Now it is the same function, so it cannot stop being the same palette.
//
// PURE: a number in, a CSS color string out, no React and no canvas. A 2D
// canvas `fillStyle` cannot read CSS custom properties, so this palette is
// deliberately theme-independent and computed rather than tokenised; that is
// the same exemption `check_design.sh` already grants the spectrogram files.

/** Magnitude (0..255) to a color on the spectrogram's four-stop ramp:
 * near-black blue, teal, amber, near-white. Interpolated per stop rather than
 * sampled from a table so the gradient stays smooth at any canvas height. */
export function specColor(v: number): string {
  const t = v / 255;
  if (t < 0.25) { const k = t / 0.25; return `rgb(${10 + k * 10},${20 + k * 60},${40 + k * 60})`; }
  if (t < 0.55) { const k = (t - 0.25) / 0.3; return `rgb(20,${80 + k * 90},${100 + k * 60})`; }
  if (t < 0.8) { const k = (t - 0.55) / 0.25; return `rgb(${20 + k * 200},${170 + k * 60},${160 - k * 100})`; }
  const k = (t - 0.8) / 0.2; return `rgb(${220 + k * 35},${230 + k * 25},${60 + k * 195})`;
}
