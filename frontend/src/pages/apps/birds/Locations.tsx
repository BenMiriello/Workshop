import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { MapPin, Star, Smartphone, RotateCcw, Pencil, Check, X, Trash2, ChevronRight } from "lucide-react";
import { Button, Notice, Skeleton, StateBlock } from "@workshop/ui";
import { useShowNonBird } from "./settings";
import AddressPicker from "./AddressPicker";
import type { Place } from "./geocode";
import {
  birdsKeys, fetchHome, fetchLocations, fetchLocationsArchived,
  mergeLocations, removeLocation, restoreLocation, saveLocation, useSetHome,
  type HomeRead,
} from "./queries";

// All the places birds have been heard. Mirrors the Devices page: each place is a
// read-only card with a per-card Edit pencil that opens an INLINE editor (no
// separate page), a home star INSIDE the card that toggles on/off, and a soft
// remove that keeps every detection and is restorable.
//
// THREE FACTS, THREE QUERIES. This page ran one `usePoll` that fired two reads
// into two `useState`s with `.catch(() => {})` on both, so the live list and the
// archive could be from different ticks and a backend that had been down for ten
// minutes looked like a page that simply never changed. The live list, the
// archive and "which place is home" are separate facts with separate lifetimes,
// and they are now separate cache entries on the keys in `queries.ts` -- the same
// keys the scope provider and the Devices page read, so the list is fetched once
// for all three.

const REFRESH_MS = 8000;

type Loc = {
  id: number; name: string; lat: number | null; lon: number | null; address?: string | null;
  detections: number; species: number; last_ts: string | null; devices: string[];
};
type LocList = { locations: Loc[] };

// One empty array, so "no rows yet" is referentially stable and an effect that
// depends on the list does not re-run on every render before the first response.
const NO_LOCS: Loc[] = [];

export default function Locations() {
  const qc = useQueryClient();
  const [showNonBird] = useShowNonBird();
  const nb = { includeNonBird: showNonBird };
  const [editing, setEditing] = useState<number | null>(null);
  const [restoreErr, setRestoreErr] = useState<string | null>(null);
  const [params] = useSearchParams();
  const focusId = Number(params.get("id") || 0);
  const focusCard = useRef<HTMLElement | null>(null);

  // The live list. It owns its own cadence (the counts move as detections land),
  // so no staleness window sits over it and it does not retry: a failed tick is
  // followed by the next tick, which is what the poll it replaces did.
  const live = useQuery({
    queryKey: birdsKeys.locations(nb),
    queryFn: () => fetchLocations<LocList>(nb),
    refetchInterval: REFRESH_MS,
    staleTime: 0,
    retry: false,
    // Toggling the non-bird filter changes the key. Keep the rows on screen
    // while the new ones load rather than blanking the page.
    placeholderData: keepPreviousData,
  });

  // The archive does NOT get a cadence. Nothing moves in it on its own: a row
  // appears when this page removes a place and disappears when it restores one,
  // and both of those invalidate it. It still re-reads when the tab comes back
  // (the client's global focus handling), so a change made on another device is
  // picked up on return instead of costing a request every eight seconds.
  const archived = useQuery({
    queryKey: birdsKeys.locationsArchived(nb),
    queryFn: () => fetchLocationsArchived<LocList>(nb),
    retry: false,
    placeholderData: keepPreviousData,
  });

  // Home is one shared setting, and this is the ONLY thing the star reads. It
  // also arrives inside the `/locations` payload, and reading it from there is
  // what let a list refetch overwrite an optimistic toggle (see `useSetHome`).
  // A setting only the operator changes needs no poll of its own: the write
  // updates it, and the client re-reads on focus.
  const homeQ = useQuery({ queryKey: birdsKeys.home(), queryFn: () => fetchHome<HomeRead>() });
  const setHome = useSetHome();

  const locs = live.data?.locations ?? NO_LOCS;
  const removed = archived.data?.locations ?? NO_LOCS;
  const home = homeQ.data?.home ?? null;

  // Arrived at via a place's edit pencil (?id=…, e.g. from the Place dropdown):
  // open that card's editor and bring it into view, the same as Devices' ?token=.
  // Keyed by the id it consumed rather than a boolean, so arriving at a SECOND
  // place's pencil without leaving the page opens that one too.
  const consumedFocus = useRef<number | null>(null);
  useEffect(() => {
    if (focusId && consumedFocus.current !== focusId && locs.some((l) => l.id === focusId)) {
      consumedFocus.current = focusId;
      setEditing(focusId);
    }
  }, [focusId, locs]);
  useEffect(() => {
    if (editing && focusCard.current) focusCard.current.scrollIntoView({ block: "center" });
  }, [editing]);

  const restore = (id: number) => {
    setRestoreErr(null);
    restoreLocation(id)
      .then(() => qc.invalidateQueries({ queryKey: birdsKeys.locationsAll() }))
      .catch((e) => setRestoreErr(String(e)));
  };

  const listErr = live.isError ? String(live.error) : null;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: ".75rem" }}>
        <h2 style={{ display: "flex", alignItems: "center", gap: ".5rem", margin: 0 }}>
          <MapPin size={22} strokeWidth={1.8} /> Locations
        </h2>
        <Link to="/apps/birds/devices" className="btn sm"><Smartphone size={14} /> Devices</Link>
      </div>

      {setHome.isError && (
        <div style={{ marginBottom: ".7rem" }}>
          <Notice tone="error" action={<Button size="sm" variant="ghost" onClick={() => setHome.reset()}>Dismiss</Button>}>
            Could not change the home location. The star is back to what the server has.
          </Notice>
        </div>
      )}

      {/* A failed read with rows still on screen is a LINE beside them; a failed
          read with nothing to show is the REGION below. */}
      {listErr && live.data && (
        <div style={{ marginBottom: ".7rem" }}>
          <Notice tone="warn" action={<Button size="sm" variant="ghost" onClick={() => void live.refetch()}>Retry</Button>}>
            These places are not refreshing.
          </Notice>
        </div>
      )}

      {listErr && !live.data ? (
        <StateBlock
          variant="error"
          title="Couldn't load locations"
          hint={<code>GET /api/birds/locations: {listErr}</code>}
          action={<Button size="sm" onClick={() => void live.refetch()}>Retry</Button>}
        />
      ) : live.isPending ? (
        <div style={{ display: "grid", gap: ".7rem" }}>
          {[0, 1, 2].map((i) => <Skeleton key={i} style={{ height: 78 }} />)}
        </div>
      ) : locs.length === 0 ? (
        <StateBlock
          title="No locations yet"
          hint="Start listening on a device. The first detection creates the place it was heard at."
        />
      ) : (
        <div style={{ display: "grid", gap: ".7rem" }}>
          {locs.map((L) => (
            // THE KEY CARRIES THE EDITING FLAG ON PURPOSE. Opening the editor
            // mounts a fresh card, so its draft is seeded once, from the record
            // as it was at that moment. A refetch that changes the record under
            // an OPEN editor cannot re-seed it, because nothing remounts: the
            // effect that used to do the re-seeding threw away whatever the
            // operator had typed whenever another device touched the same place.
            <LocationCard key={editing === L.id ? `${L.id}:edit` : L.id}
              L={L} isHome={home === `loc:${L.id}`}
              editing={editing === L.id}
              onEdit={() => setEditing(L.id)} onClose={() => setEditing(null)}
              onToggleHome={(mk) => setHome.mutate(mk ? `loc:${L.id}` : "")}
              others={locs.filter((o) => o.id !== L.id)}
              cardRef={editing === L.id ? focusCard : undefined} />
          ))}
        </div>
      )}

      {(removed.length > 0 || archived.isError || restoreErr) && (
        <section style={{ marginTop: "1.5rem" }}>
          <div className="muted" style={{ fontSize: ".78rem", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: ".4rem" }}>
            Removed locations
          </div>
          {archived.isError && (
            <div style={{ marginBottom: ".5rem" }}>
              <Notice tone="error" action={<Button size="sm" variant="ghost" onClick={() => void archived.refetch()}>Retry</Button>}>
                Couldn't load removed locations.
              </Notice>
            </div>
          )}
          {restoreErr && (
            <div style={{ marginBottom: ".5rem" }}>
              <Notice tone="error" detail={restoreErr} action={<Button size="sm" variant="ghost" onClick={() => setRestoreErr(null)}>Dismiss</Button>}>
                Could not restore that location.
              </Notice>
            </div>
          )}
          <div style={{ display: "grid", gap: ".5rem" }}>
            {removed.map((L) => (
              <div key={L.id} className="card" style={{ padding: ".6rem .9rem", display: "flex", alignItems: "center", gap: ".6rem", opacity: 0.85 }}>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ fontWeight: 600 }}>{L.name}</span>
                  <span className="muted" style={{ fontSize: ".76rem", display: "block" }}>
                    {L.species} species · {L.detections.toLocaleString()} detections kept
                  </span>
                </span>
                <button className="btn sm" onClick={() => restore(L.id)}><RotateCcw size={14} /> Restore</button>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function LocationCard({ L, isHome, editing, onEdit, onClose, onToggleHome, others, cardRef }: {
  L: Loc; isHome: boolean; editing: boolean;
  onEdit: () => void; onClose: () => void; onToggleHome: (makeHome: boolean) => void;
  others: { id: number; name: string }[]; cardRef?: React.RefObject<HTMLElement | null>;
}) {
  const qc = useQueryClient();
  const hasPlace = L.lat != null && L.lon != null;
  const needsAddress = !hasPlace;
  // The draft, seeded from the record AT MOUNT, which the parent's key makes the
  // moment the editor opened. There is no re-seeding effect and there must not
  // be one: while this editor is open it is the only owner of these fields.
  const [name, setName] = useState(L.name && L.name !== "Untitled location" ? L.name : "");
  // The "Address or GPS" field shows the ACTUAL address (not the display name);
  // the display name is its own field below.
  const [place, setPlace] = useState<Place | null>(() => hasPlace
    ? { short: L.address || `${L.lat!.toFixed(5)}, ${L.lon!.toFixed(5)}`, label: L.address || "", lat: L.lat!, lon: L.lon! }
    : null);
  const [confirmDel, setConfirmDel] = useState(false);
  const [mergeTarget, setMergeTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // A write, then the invalidation that used to be `setTimeout(load, 150)`. The
  // editor stays OPEN on a failure and says so: it used to close in `.finally`,
  // which looked exactly like a save that worked.
  const run = (work: Promise<unknown>, invalidate: readonly (readonly unknown[])[]) => {
    setBusy(true); setErr(null);
    work
      .then(() => {
        for (const key of invalidate) void qc.invalidateQueries({ queryKey: key });
        onClose();
      })
      .catch((e) => { setErr(String(e)); setBusy(false); });
  };

  const save = () => {
    if (!place) return;
    run(saveLocation({
      id: L.id,
      name: name.trim() || place.short,
      lat: place.lat,
      lon: place.lon,
      address: place.label || place.short,
    }), [birdsKeys.locationsAll(), birdsKeys.locationAll(L.id)]);
  };

  // A remove or a merge moves this place's detections, so every count in the app
  // is downstream of it. Only what is mounted re-reads; the rest is marked stale.
  const remove = () => run(removeLocation(L.id), [birdsKeys.all()]);
  const merge = () => {
    if (!mergeTarget) return;
    run(mergeLocations({ src: L.id, dst: Number(mergeTarget) }), [birdsKeys.all()]);
  };

  const sub = `${L.species} species · ${L.detections.toLocaleString()} detections${L.devices.length ? ` · ${L.devices.join(", ")}` : ""}`;

  return (
    <section ref={cardRef as React.RefObject<HTMLElement>} className="card"
      style={{ padding: "1rem", border: (isHome || editing) ? "1px solid color-mix(in srgb, var(--accent) 33%, transparent)" : undefined }}>
      <div style={{ display: "flex", alignItems: "center", gap: ".5rem", marginBottom: editing ? ".5rem" : ".25rem" }}>
        <MapPin size={16} className="muted" style={{ flexShrink: 0 }} />
        {editing ? (
          <input className="num-in" value={name} placeholder="Display name (e.g. Home, Back deck)" autoFocus
            onChange={(e) => setName(e.target.value)} style={{ flex: 1, minWidth: 0, fontWeight: 600 }} />
        ) : (
          <Link to={`/apps/birds?source=loc:${L.id}`} style={{ flex: 1, minWidth: 0, textDecoration: "none", color: "inherit", display: "flex", alignItems: "center", gap: ".4rem" }}>
            <span style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{L.name}</span>
            {needsAddress && <span className="pill" style={{ fontSize: ".68rem", color: "var(--warn)", borderColor: "color-mix(in srgb, var(--warn) 33%, transparent)" }}>set address</span>}
          </Link>
        )}
        {/* Home star: INSIDE the card, toggles on/off. */}
        <button onClick={() => onToggleHome(!isHome)} title={isHome ? "Home (tap to unset)" : "Set as home"}
          aria-pressed={isHome} aria-label={isHome ? "Unset as home" : "Set as home"}
          style={{ background: "none", border: "none", cursor: "pointer", color: isHome ? "var(--warn-solid)" : "var(--muted)", flexShrink: 0, padding: ".15rem" }}>
          <Star size={18} fill={isHome ? "var(--warn-solid)" : "none"} />
        </button>
        {!editing && (
          <button className="btn sm" title="Edit this location" aria-label="Edit this location" onClick={onEdit} style={{ flexShrink: 0 }}>
            <Pencil size={14} />
          </button>
        )}
      </div>

      {!editing && (
        <Link to={`/apps/birds?source=loc:${L.id}`} style={{ textDecoration: "none", color: "inherit", display: "flex", alignItems: "center", gap: ".4rem" }}>
          <span className="muted" style={{ flex: 1, fontSize: ".82rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sub}</span>
          <ChevronRight size={15} className="muted" style={{ flexShrink: 0 }} />
        </Link>
      )}

      {editing && (
        <div style={{ display: "grid", gap: ".6rem" }}>
          <label className="muted" style={{ display: "flex", flexDirection: "column", gap: ".2rem", fontSize: ".78rem" }}>
            Address or GPS
            <AddressPicker value={place} onPick={setPlace} />
          </label>
          <p className="muted" style={{ fontSize: ".74rem", margin: 0 }}>
            A location must be a verified address or GPS point. Adaptive devices within ~40&nbsp;m merge into it automatically.
          </p>

          {/* The form is right here, so a rejected write is a line above the
              buttons, never a region. */}
          {err && <Notice tone="error" detail={err}>That did not go through.</Notice>}

          {!confirmDel ? (
            <div style={{ display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap", paddingTop: ".5rem", borderTop: "1px solid var(--border)" }}>
              <button className="btn sm danger" onClick={() => setConfirmDel(true)} disabled={busy}><Trash2 size={14} /> Remove</button>
              <span style={{ flex: 1 }} />
              <button className="btn sm" onClick={onClose} disabled={busy}><X size={14} /> Cancel</button>
              <button className="btn sm primary" onClick={save} disabled={busy || !place}><Check size={14} /> Save</button>
            </div>
          ) : (
            <div style={{ display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap", paddingTop: ".5rem", borderTop: "1px solid var(--border)" }}>
              <span style={{ fontSize: ".82rem", flex: 1, minWidth: "12rem" }}>
                Remove this location? Its {L.detections.toLocaleString()} detection{L.detections === 1 ? "" : "s"} are kept and it can be restored below.
              </span>
              <button className="btn sm" onClick={() => setConfirmDel(false)} disabled={busy}><X size={14} /> Cancel</button>
              <button className="btn sm danger" onClick={remove} disabled={busy}><Check size={14} /> Remove</button>
            </div>
          )}

          {others.length > 0 && (
            <div style={{ paddingTop: ".5rem", borderTop: "1px solid var(--border)" }}>
              <div className="muted" style={{ fontSize: ".74rem", marginBottom: ".25rem" }}>Or merge this place into another (keeps all detections)</div>
              <div style={{ display: "flex", gap: ".5rem", flexWrap: "wrap" }}>
                <select className="num-in" value={mergeTarget} onChange={(e) => setMergeTarget(e.target.value)} style={{ width: "auto" }}>
                  <option value="">Choose a location…</option>
                  {others.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                </select>
                <button className="btn sm" onClick={merge} disabled={!mergeTarget || busy}>Merge</button>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
