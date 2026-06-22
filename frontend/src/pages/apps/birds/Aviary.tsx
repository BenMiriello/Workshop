import { useEffect, useRef, useState } from "react";
import { Toggle } from "../../../ui";
import { jget } from "./ui";
import { createAviary, type AviaryApi } from "./aviary/aviary-engine.js";
import { AviaryVoxel } from "./aviary/aviary-voxel.js";
import { SPECIES_NA } from "./aviary/species-na.js";
import "./aviary/aviary.css";

// Live Aviary scene — the ambient, isometric "who's in the yard right now" view.
// It is the live card's alternate (Aviary) representation, reachable from the
// card's Histogram <-> Aviary toggle and openable fullscreen ("cast"). Reuses the
// ./aviary/ engine as-is; this is the React chrome + data seam. A species is
// PRESENT iff the yard mic has heard it in the rolling window (real detections
// only — no simulation). The same live data the histogram reads.

const PRESENT_WINDOW_MIN = 15;          // "heard recently" = within this many minutes
const POLL_MS = 5000;
const TAGS_KEY = "aviaryNameTags";      // view preference, persisted, commits on change

// scientific name -> renderable profile id. The generated NA set takes precedence
// over the handoff's built-in demo species when they collide on a scientific name.
const SCI_TO_ID: Record<string, string> = {};
for (const [id, p] of Object.entries(AviaryVoxel.SPECIES)) SCI_TO_ID[p.sci] = id;
for (const [id, p] of Object.entries(SPECIES_NA)) SCI_TO_ID[p.sci] = id;

type Hear = { scientific_name: string; is_bird: boolean };
type Feed = { hearing: Hear[] };
type Det = { date: string; time: string; scientific_name: string };

// Parse a yard detection's local date ('YYYY-MM-DD') + time ('HH:MM[:SS]') to epoch ms.
function detTime(date: string, time: string): number | null {
  if (!date || !time) return null;
  const t = Date.parse(`${date}T${time}`);
  return Number.isNaN(t) ? null : t;
}

export default function Aviary({ fullscreen = false }: { fullscreen?: boolean }) {
  const stageRef = useRef<HTMLDivElement>(null);
  const sceneryRef = useRef<SVGSVGElement>(null);
  const fieldRef = useRef<HTMLDivElement>(null);
  const chipRef = useRef<HTMLSpanElement>(null);
  const apiRef = useRef<AviaryApi | null>(null);
  // Rolling "last heard" per renderable species id (epoch ms). A species stays
  // present until PRESENT_WINDOW_MIN after its most recent signal; every new
  // detection or live hearing refreshes the timer. Sticky on purpose: /live is
  // momentary (a calling bird blips in and out of it), so reconciling the raw
  // per-poll union would fly birds out and back in every few seconds.
  const lastSeenRef = useRef<Record<string, number>>({});

  const [nameTags, setNameTags] = useState<boolean>(() => localStorage.getItem(TAGS_KEY) === "1");

  // Mount the engine once. Register the full NA species set, keep the demo
  // simulation OFF (real detections drive the scene), then tear down on unmount.
  useEffect(() => {
    const stage = stageRef.current, scenery = sceneryRef.current, field = fieldRef.current;
    if (!stage || !scenery || !field) return;
    const api = createAviary({ stage, scenery, field, countchip: chipRef.current });
    api.registerSpecies(SPECIES_NA);
    api.simulate(false);
    api.setNameTags(localStorage.getItem(TAGS_KEY) === "1");
    apiRef.current = api;
    if (import.meta.env.DEV) (window as unknown as { __aviary?: AviaryApi }).__aviary = api;
    return () => { api.destroy(); apiRef.current = null; };
  }, []);

  // Name-tags toggle is a view preference: commit on change, persist, push to engine.
  useEffect(() => {
    localStorage.setItem(TAGS_KEY, nameTags ? "1" : "0");
    apiRef.current?.setNameTags(nameTags);
  }, [nameTags]);

  // Poll real signal and refresh the rolling "last heard" map, then hand the
  // engine the species still inside the 15-min window. Both sources only ever
  // PUSH a species' timer forward — presence decays on the clock, not on a
  // single quiet poll — so the scene is stable instead of churning.
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const now = Date.now();
      const seen = lastSeenRef.current;
      try {
        const d = await jget<{ detections: Det[] }>(`/detections?limit=80`);
        for (const r of d.detections || []) {
          const t = detTime(r.date, r.time);
          const id = SCI_TO_ID[r.scientific_name];
          if (id && t != null && t > (seen[id] || 0)) seen[id] = t;
        }
      } catch { /* endpoint unavailable (e.g. public build) -> rely on what we have */ }
      try {
        const l = await jget<{ feeds: Feed[] }>(`/live`);
        for (const f of l.feeds || []) {
          for (const h of f.hearing || []) {
            if (!h.is_bird) continue;
            const id = SCI_TO_ID[h.scientific_name];
            if (id) seen[id] = Math.max(seen[id] || 0, now);
          }
        }
      } catch { /* no live feed -> detections only */ }
      const cutoff = now - PRESENT_WINDOW_MIN * 60_000;
      const present = Object.keys(seen).filter((id) => seen[id] >= cutoff);
      if (alive) apiRef.current?.setPresent(present);
    };
    tick();
    const iv = setInterval(tick, POLL_MS);
    return () => { alive = false; clearInterval(iv); };
  }, []);

  return (
    <div className={`ws-aviary${fullscreen ? " ws-aviary--full" : ""}`}>
      <div className="ws-aviary__stage" ref={stageRef}>
        <svg className="ws-aviary__scenery" ref={sceneryRef} />
        <div className="ws-aviary__field" ref={fieldRef} />
        <div className="ws-aviary__top"><span className="scnt" ref={chipRef} /></div>
        <div className="ws-aviary__tags">
          <Toggle checked={nameTags} onChange={setNameTags} label="Name tags" />
        </div>
      </div>
    </div>
  );
}
