// Shared client-side settings for the Birds app. Currently just the non-bird
// filter: BirdNET also recognizes noise/human/machine classes (Engine, Siren,
// "Human vocal", Dog, ...) plus frogs, toads, crickets, and a couple mammals.
// Detection ALWAYS happens server-side and everything is stored; this flag only
// controls whether those non-bird hits are shown. Persisted in localStorage and
// broadcast so every open Birds view (live mic test + station detail) reacts
// without a reload.
import { useEffect, useState } from "react";
import { userStorage } from "../../../lib/storage";

const KEY = "birdShowNonBird";
const EVT = "bird-settings-change";

export function getShowNonBird(): boolean {
  return userStorage.local.getItem(KEY) === "1";
}

export function setShowNonBird(v: boolean): void {
  userStorage.local.setItem(KEY, v ? "1" : "0");
  window.dispatchEvent(new CustomEvent(EVT));
}

// Query-string fragment to thread the setting to the backend aggregates.
export function nonBirdQuery(show: boolean): string {
  return show ? "&include_nonbird=true" : "";
}

export function useShowNonBird(): [boolean, (v: boolean) => void] {
  const [v, setV] = useState(getShowNonBird);
  useEffect(() => {
    const h = () => setV(getShowNonBird());
    window.addEventListener(EVT, h);
    window.addEventListener("storage", h); // other tabs
    return () => {
      window.removeEventListener(EVT, h);
      window.removeEventListener("storage", h);
    };
  }, []);
  return [v, (nv: boolean) => { setShowNonBird(nv); setV(nv); }];
}
