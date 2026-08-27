import { useEffect, useRef, useState } from "react";
import { Toggle, userStorage } from "@workshop/ui";
import { useShowNonBird } from "./settings";
import { useLiveFeeds, useYardFeed } from "./queries";
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
//
// IT READS NOTHING OF ITS OWN ANY MORE. This file used to run a raw 5s
// `setInterval` over a local `tick` that awaited `/detections?limit=80` and then
// `/live`, with a `let alive` flag to drop a response that landed after unmount.
// Both URLs already had another reader on the same screen: `/live` at 1.2s from
// the live-feed viewer, `/detections` at limit=1000 from the recent card. Three
// timers, four copies of two payloads, and this file's copies could be five
// seconds older than the ones rendered directly above it. Both reads are now the
// shared entries in `queries.ts`, so the scene and the spectrogram are looking at
// the same bytes; the `alive` flag goes with them, because a cache entry that
// nothing is subscribed to has nowhere to deliver anything.

const PRESENT_WINDOW_MIN = 15;          // "heard recently" = within this many minutes
const TAGS_KEY = "aviaryNameTags";      // view preference, persisted, commits on change

// scientific name -> renderable profile id. The generated NA set takes precedence
// over the handoff's built-in demo species when they collide on a scientific name.
const SCI_TO_ID: Record<string, string> = {};
for (const [id, p] of Object.entries(AviaryVoxel.SPECIES)) SCI_TO_ID[p.sci] = id;
for (const [id, p] of Object.entries(SPECIES_NA)) SCI_TO_ID[p.sci] = id;

// Parse a yard detection's local date ('YYYY-MM-DD') + time ('HH:MM[:SS]') to epoch ms.
function detTime(date: string | undefined, time: string): number | null {
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
  // per-read union would fly birds out and back in every few seconds.
  const lastSeenRef = useRef<Record<string, number>>({});

  const [nameTags, setNameTags] = useState<boolean>(() => userStorage.local.getItem(TAGS_KEY) === "1");
  const [showNonBird] = useShowNonBird();

  // The two sources, on the two shared keys. The non-bird filter is in the
  // detections key because it is in the URL; this scene does not care either way
  // (it renders only species it has a model for), so it asks for what the card
  // beside it is already asking for and they share one entry instead of two.
  const detQ = useYardFeed({ includeNonBird: showNonBird });
  const liveQ = useLiveFeeds({ locationId: null, need: "presence" });

  // Mount the engine once. Register the full NA species set, keep the demo
  // simulation OFF (real detections drive the scene), then tear down on unmount.
  useEffect(() => {
    const stage = stageRef.current, scenery = sceneryRef.current, field = fieldRef.current;
    if (!stage || !scenery || !field) return;
    const api = createAviary({ stage, scenery, field, countchip: chipRef.current });
    api.registerSpecies(SPECIES_NA);
    api.simulate(false);
    api.setNameTags(userStorage.local.getItem(TAGS_KEY) === "1");
    apiRef.current = api;
    if (import.meta.env.DEV) (window as unknown as { __aviary?: AviaryApi }).__aviary = api;
    return () => { api.destroy(); apiRef.current = null; };
  }, []);

  // Name-tags toggle is a view preference: commit on change, persist, push to engine.
  useEffect(() => {
    userStorage.local.setItem(TAGS_KEY, nameTags ? "1" : "0");
    apiRef.current?.setNameTags(nameTags);
  }, [nameTags]);

  // PRESENCE DECAYS ON A CLOCK, AND THE CLOCK IS THE READ THAT IS ALREADY
  // HAPPENING. Both sources only ever PUSH a species' timer forward, so a bird
  // leaves the scene because fifteen minutes passed, not because one read came
  // back quiet — that is what keeps the scene stable instead of churning.
  //
  // Which means this has to re-run on every tick, INCLUDING the ticks where
  // nothing changed. Depending on `detQ.data` would not do it: Query's
  // structural sharing hands back the identical object when a payload is deeply
  // equal, so a quiet yard would freeze the decay and leave birds standing there
  // forever. `dataUpdatedAt` moves on every successful read regardless, and
  // `errorUpdatedAt` moves on every failed one, so the pair is a tick counter
  // for "a read resolved, one way or the other" — which is exactly what the 5s
  // interval used to be, minus the interval. Reading them here is also what
  // subscribes this component to them (Query tracks the fields you touch).
  const detTick = Math.max(detQ.dataUpdatedAt, detQ.errorUpdatedAt);
  const liveTick = Math.max(liveQ.dataUpdatedAt, liveQ.errorUpdatedAt);
  useEffect(() => {
    const now = Date.now();
    const seen = lastSeenRef.current;

    for (const r of detQ.data?.detections ?? []) {
      const t = detTime(r.date, r.time);
      const id = SCI_TO_ID[r.scientific_name];
      if (id && t != null && t > (seen[id] || 0)) seen[id] = t;
    }
    for (const f of liveQ.data?.feeds ?? []) {
      for (const h of f.hearing || []) {
        if (!h.is_bird) continue;
        const id = SCI_TO_ID[h.scientific_name];
        if (id) seen[id] = Math.max(seen[id] || 0, now);
      }
    }

    const cutoff = now - PRESENT_WINDOW_MIN * 60_000;
    apiRef.current?.setPresent(Object.keys(seen).filter((id) => seen[id] >= cutoff));
    // A failed read is not a reason to empty the yard: `seen` is untouched by
    // one, and the entries in it age out on their own. That is the behaviour the
    // two swallowed `try/catch`es used to spell by hand.
  }, [detTick, liveTick, detQ.data, liveQ.data]);

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
