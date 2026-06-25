import { useEffect, useState } from "react";

// Subscribe to a CSS media query and re-render on change. Seeds synchronously
// from matchMedia so the first paint already reflects the right breakpoint
// (no desktop-then-mobile flash). SSR-safe: falls back to false when there is
// no window.
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== "undefined" && window.matchMedia(query).matches,
  );
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia(query);
    const on = () => setMatches(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return matches;
}
