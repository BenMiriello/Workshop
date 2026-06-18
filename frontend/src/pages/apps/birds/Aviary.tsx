import { useEffect, useRef, useState } from "react";
import { Card, Toggle } from "../../../ui";
import { jget } from "./ui";
import { createAviary, type AviaryApi } from "./aviary/aviary-engine.js";
import { AviaryVoxel } from "./aviary/aviary-voxel.js";
import { SPECIES_NA } from "./aviary/species-na.js";
import "./aviary/aviary.css";

// Live Aviary — an ambient, isometric "who's in the yard right now" diorama, shown
// as one element on the Birds home. The voxel renderer + scene engine live in
// ./aviary/ (our original Aviary build, integrated as-is); this component is the
// React chrome and the data seam. A species is PRESENT iff the yard mic has heard
// it in the rolling
// window below — no random simulation, only real detections. The one control is the
// name-tags toggle.

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

export default function Aviary() {
  const stageRef = useRef<HTMLDivElement>(null);
  const sceneryRef = useRef<SVGSVGElement>(null);
  const fieldRef = useRef<HTMLDivElement>(null);
  const chipRef = useRef<HTMLSpanElement>(null);
  const apiRef = useRef<AviaryApi | null>(null);

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

  // Poll real detections and reconcile the present set. Present = species heard in
  // the rolling window (durable /detections) plus anything calling right now (/live).
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const cutoff = Date.now() - PRESENT_WINDOW_MIN * 60_000;
      const present = new Set<string>();
      try {
        const d = await jget<{ detections: Det[] }>(`/detections?limit=80`);
        for (const r of d.detections || []) {
          const t = detTime(r.date, r.time);
          if (t != null && t >= cutoff) {
            const id = SCI_TO_ID[r.scientific_name];
            if (id) present.add(id);
          }
        }
      } catch { /* endpoint unavailable (e.g. public build) -> quiet yard */ }
      try {
        const l = await jget<{ feeds: Feed[] }>(`/live`);
        for (const f of l.feeds || []) {
          for (const h of f.hearing || []) {
            if (!h.is_bird) continue;
            const id = SCI_TO_ID[h.scientific_name];
            if (id) present.add(id);
          }
        }
      } catch { /* no live feed -> rely on detections only */ }
      if (alive) apiRef.current?.setPresent([...present]);
    };
    tick();
    const iv = setInterval(tick, POLL_MS);
    return () => { alive = false; clearInterval(iv); };
  }, []);

  return (
    <Card pad0 className="aviary-card">
      <div className="aviary-card__head">
        <span className="aviary-card__title">Live Aviary</span>
        <span className="aviary-card__sub">who's in the yard now</span>
        <Toggle className="aviary-card__tags" checked={nameTags} onChange={setNameTags} label="Name tags" />
      </div>
      <div className="ws-aviary">
        <div className="ws-aviary__stage" ref={stageRef}>
          <svg className="ws-aviary__scenery" ref={sceneryRef} />
          <div className="ws-aviary__field" ref={fieldRef} />
          <div className="ws-aviary__top"><span className="scnt" ref={chipRef} /></div>
        </div>
      </div>
    </Card>
  );
}
