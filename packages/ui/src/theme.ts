import { useCallback, useEffect, useState } from "react";
import { DEFAULT_PALETTE, isPaletteId, type PaletteId } from "./styles/palettes.gen";

// Appearance has two independent axes, both attributes on <html>:
//
//   data-theme    light | dark | system     — mode
//   data-palette  violet | cobalt | ...     — color scheme (five, from palette.mjs)
//
// The pre-React bootstrap in index.html applies both from localStorage before
// first paint; these hooks keep a React-visible copy and are the only things
// that write them back. Both are VIEW PREFERENCES: they commit on change.
export type ThemeMode = "light" | "dark" | "system";
export type { PaletteId };

const STORAGE_KEY = "ws-theme";
const PALETTE_KEY = "ws-palette";

export function getStoredTheme(): ThemeMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    /* private mode / disabled storage — fall through */
  }
  return "dark"; // dark by default; matches the index.html bootstrap fallback
}

export function applyTheme(mode: ThemeMode): void {
  document.documentElement.setAttribute("data-theme", mode);
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    /* ignore */
  }
}

// Read + set the theme. Setting persists to localStorage and updates <html>
// immediately so surfaces glide (the .ws-root transition in components.css).
export function useTheme(): [ThemeMode, (mode: ThemeMode) => void] {
  const [mode, setMode] = useState<ThemeMode>(getStoredTheme);

  // Reconcile React state with whatever the bootstrap applied (e.g. another tab
  // changed it). Runs once on mount.
  useEffect(() => {
    const current = getStoredTheme();
    setMode(current);
    applyTheme(current);
  }, []);

  const set = useCallback((next: ThemeMode) => {
    applyTheme(next);
    setMode(next);
  }, []);

  return [mode, set];
}

// ---------------------------------------------------------------------------
// Palette — which of the five color schemes is on. Orthogonal to light/dark:
// every palette ships both modes, so switching scheme never changes mode.
// ---------------------------------------------------------------------------

export function getStoredPalette(): PaletteId {
  try {
    const v = localStorage.getItem(PALETTE_KEY);
    if (isPaletteId(v)) return v;
  } catch {
    /* private mode / disabled storage — fall through */
  }
  return DEFAULT_PALETTE;
}

export function applyPalette(id: PaletteId): void {
  document.documentElement.setAttribute("data-palette", id);
  try {
    localStorage.setItem(PALETTE_KEY, id);
  } catch {
    /* ignore */
  }
}

export function usePalette(): [PaletteId, (id: PaletteId) => void] {
  const [palette, setPalette] = useState<PaletteId>(getStoredPalette);

  useEffect(() => {
    const current = getStoredPalette();
    setPalette(current);
    applyPalette(current);
  }, []);

  const set = useCallback((next: PaletteId) => {
    applyPalette(next);
    setPalette(next);
  }, []);

  return [palette, set];
}

// The mode actually in force right now — "system" resolved against the OS.
// A swatch previewing a scheme has to name a concrete mode (the palettes.css
// system block is a media query keyed on :root and won't match a nested node),
// and this follows the OS live so the previews don't go stale mid-session.
export function useResolvedMode(mode: ThemeMode): "light" | "dark" {
  const query = () =>
    typeof window !== "undefined" && window.matchMedia
      ? window.matchMedia("(prefers-color-scheme: dark)").matches
      : true;
  const [osDark, setOsDark] = useState(query);

  useEffect(() => {
    if (mode !== "system" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (e: MediaQueryListEvent) => setOsDark(e.matches);
    mq.addEventListener("change", onChange);
    setOsDark(mq.matches);
    return () => mq.removeEventListener("change", onChange);
  }, [mode]);

  if (mode === "light" || mode === "dark") return mode;
  return osDark ? "dark" : "light";
}
