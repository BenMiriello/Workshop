import { useCallback, useEffect, useState } from "react";

// Theme contract — mirrors kitchen.js `applyTheme`. The pre-React bootstrap in
// index.html sets data-theme from localStorage["ws-theme"] before first paint;
// this hook keeps a React-visible copy and is the only thing that writes it back.
export type ThemeMode = "light" | "dark" | "system";

const STORAGE_KEY = "ws-theme";

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
