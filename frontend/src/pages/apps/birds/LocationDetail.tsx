import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { MapPin, Radio, Star, Pencil, Check, X, AlertTriangle, ChevronRight, Trash2 } from "lucide-react";
import { Button, Notice, Skeleton, StateBlock, useCrumbLabel } from "@workshop/ui";
import { useShowNonBird } from "./settings";
import AddressPicker from "./AddressPicker";
import type { Place } from "./geocode";
import {
  birdsKeys, fetchHome, fetchLocationSummary, fetchLocations,
  mergeLocations, removeLocation, saveLocation, useSetHome,
  type HomeRead,
} from "./queries";

// The editing surface for one place: rename it, pin its coordinates (so adaptive
// devices merge into it), set it home, merge it into another, and see the devices
// reporting here. Browsing the birds heard here is the Place dropdown on the
// home page; this page links straight into that view.
//
// TWO FACTS, TWO QUERIES. This page read `/location/summary` and `/home` in one
// `Promise.all` behind one `usePoll`, so a failure in either wiped both and the
// error state could not tell you which one broke. They are unrelated: one is
// this record, one is a global setting, and `/home` is the same entry the
// Locations page and the header read.
//
// THE COMPANION EFFECT IS GONE. `usePoll` reads its callback through a ref, so a
// changed closure never re-ran it and the page compensated with
// `useEffect(() => refresh(), [id, refresh])` plus `immediate: false`. A query
// refetches when its key changes, by itself; porting the effect would have meant
// two reads per navigation.

const REFRESH_MS = 12000;

type Summary = {
  id: number; name: string; lat: number | null; lon: number | null; address: string | null;
  devices: string[]; detections: number; species: number;
};

export default function LocationDetail() {
  const [params] = useSearchParams();
  const id = Number(params.get("id") || 0);
  const [showNonBird] = useShowNonBird();
  // The non-bird filter is sent now, where it used to be dropped. These counts
  // and the ones on the page behind "View birds heard here" are the same
  // question, and they disagreed whenever the filter was on because only one of
  // them asked it.
  const sp = { locationId: id, includeNonBird: showNonBird, device: "" };

  // NO `keepPreviousData` here, and that is not an oversight. This key changes
  // only when the RECORD changes, so the previous data is a DIFFERENT place;
  // holding it would put another location's name and counts under this one's
  // title. A refetch on an unchanged key never blanks the page on its own, which
  // is what that rule is there to protect.
  const summaryQ = useQuery({
    queryKey: birdsKeys.locationSummary(sp),
    queryFn: () => fetchLocationSummary<Summary>(sp),
    enabled: id > 0,
    refetchInterval: REFRESH_MS,
    staleTime: 0,
    retry: false,
  });

  // Which place is home: a shared setting, its own entry, and the only thing the
  // star reads or writes. No cadence of its own; the write updates it and the
  // client re-reads it when the tab comes back.
  const homeQ = useQuery({ queryKey: birdsKeys.home(), queryFn: () => fetchHome<HomeRead>() });
  const setHome = useSetHome();

  const summary = summaryQ.data ?? null;
  useCrumbLabel(summary?.name);

  // The open editor is identified by the record it belongs to, not by a boolean:
  // navigating to another place (the merge redirect does exactly that) closes it
  // with no effect and no reset.
  const [editingId, setEditingId] = useState<number | null>(null);
  const editing = id > 0 && editingId === id;

  // A location with no pinned place needs one, so the editor opens itself.
  // ONCE PER PLACE: the ref holds the id it fired for, so a 12s refetch that
  // returns the same record cannot re-open an editor the operator moved on
  // from, and arriving at a SECOND place with no coordinates still opens.
  const autoOpenedFor = useRef<number | null>(null);
  useEffect(() => {
    if (!summary || summary.lat != null || autoOpenedFor.current === id) return;
    autoOpenedFor.current = id;
    setEditingId(id);
  }, [id, summary]);

  if (!id) {
    return (
      <Wrap>
        <StateBlock
          title="No location selected"
          hint="Open a place from the Locations list, or from a device's card."
          action={<Link to="/apps/birds/locations" className="btn sm">Locations</Link>}
        />
      </Wrap>
    );
  }

  if (summaryQ.isError && !summary) {
    return (
      <Wrap>
        <StateBlock
          variant="error"
          title="Couldn't load this location"
          hint={<code>GET /api/birds/location/summary: {String(summaryQ.error)}</code>}
          action={<Button size="sm" onClick={() => void summaryQ.refetch()}>Retry</Button>}
        />
      </Wrap>
    );
  }

  if (!summary) {
    return (
      <Wrap>
        <Skeleton style={{ height: 96, marginBottom: "1rem" }} />
        <Skeleton style={{ height: 64 }} />
      </Wrap>
    );
  }

  const isHome = homeQ.data?.home === `loc:${id}`;

  return (
    <Wrap name={summary.name}>
      {/* The record is on screen, so a dropped refresh or a rejected write is a
          line beside it, never a region over it. */}
      {summaryQ.isError && (
        <div style={{ marginBottom: ".7rem" }}>
          <Notice tone="warn" action={<Button size="sm" variant="ghost" onClick={() => void summaryQ.refetch()}>Retry</Button>}>
            This place is not refreshing.
          </Notice>
        </div>
      )}
      {setHome.isError && (
        <div style={{ marginBottom: ".7rem" }}>
          <Notice tone="error" action={<Button size="sm" variant="ghost" onClick={() => setHome.reset()}>Dismiss</Button>}>
            Could not change the home location. The star is back to what the server has.
          </Notice>
        </div>
      )}

      {/* Identity card: the name, the home star, and the Edit pencil all live
          INSIDE this card (not in a header above it); editing expands in place. */}
      <section className="card" style={{ padding: "1rem", marginBottom: "1rem",
        border: (isHome || editing) ? "1px solid color-mix(in srgb, var(--accent) 33%, transparent)" : undefined }}>
        <div style={{ display: "flex", alignItems: "center", gap: ".5rem", marginBottom: editing ? ".6rem" : 0 }}>
          <MapPin size={16} className="muted" style={{ flexShrink: 0 }} />
          <span style={{ flex: 1, minWidth: 0, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{summary.name}</span>
          <button onClick={() => setHome.mutate(isHome ? "" : `loc:${id}`)} title={isHome ? "Home (tap to unset)" : "Set as home"}
            aria-pressed={isHome} aria-label={isHome ? "Unset as home" : "Set as home"}
            style={{ background: "none", border: "none", cursor: "pointer", color: isHome ? "var(--warn-solid)" : "var(--muted)", flexShrink: 0, padding: ".15rem" }}>
            <Star size={18} fill={isHome ? "var(--warn-solid)" : "none"} />
          </button>
          {!editing && (
            <button className="btn sm" title="Edit this location" aria-label="Edit this location" onClick={() => setEditingId(id)} style={{ flexShrink: 0 }}>
              <Pencil size={14} />
            </button>
          )}
        </div>
        {!editing && summary.address && (
          <div className="muted" style={{ fontSize: ".82rem" }}>{summary.address}</div>
        )}
        {!editing && summary.lat == null && (
          <div style={{ marginTop: ".5rem", display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap" }}>
            <AlertTriangle size={15} style={{ color: "var(--warn)" }} />
            <span style={{ flex: 1, fontSize: ".85rem" }}>This location has no real place yet.</span>
            <button className="btn sm" onClick={() => setEditingId(id)}>Set address</button>
          </div>
        )}
        {/* Mounted only while open, so its draft is seeded once, from the record
            as it was when the editor opened. A 12s refetch under an open editor
            cannot overwrite what the operator has typed. */}
        {editing && <EditLocation summary={summary} onDone={() => setEditingId(null)} />}
      </section>

      <Link to={`/apps/birds?source=loc:${id}`} className="card"
        style={{ display: "flex", alignItems: "center", gap: ".6rem", padding: ".8rem 1rem", marginBottom: "1rem", textDecoration: "none", color: "inherit" }}>
        <span style={{ flex: 1 }}>
          <div style={{ fontWeight: 600 }}>View birds heard here</div>
          <div className="muted" style={{ fontSize: ".82rem" }}>{summary.species} species · {summary.detections.toLocaleString()} detections</div>
        </span>
        <ChevronRight size={18} className="muted" />
      </Link>

      {summary.devices.length > 0 && (
        <p className="muted" style={{ margin: "0 0 .8rem", fontSize: ".85rem", display: "flex", alignItems: "center", gap: ".3rem", flexWrap: "wrap" }}>
          <Radio size={13} style={{ verticalAlign: "-2px" }} /> Devices here:
          {summary.devices.map((dv, i) => (
            <Link key={dv} to="/apps/birds/devices" className="muted" style={{ textDecoration: "underline" }}>
              {dv}{i < summary.devices.length - 1 ? "," : ""}
            </Link>
          ))}
        </p>
      )}
    </Wrap>
  );
}

function EditLocation({ summary, onDone }: { summary: Summary; onDone: () => void }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [showNonBird] = useShowNonBird();
  const nb = { includeNonBird: showNonBird };
  const hasPlace = summary.lat != null && summary.lon != null;
  const [name, setName] = useState(summary.name && summary.name !== "Untitled location" ? summary.name : "");
  // The "Address or GPS" field shows the real address, not the display name
  // (which has its own field below).
  const [place, setPlace] = useState<Place | null>(() =>
    hasPlace ? { short: summary.address || `${summary.lat!.toFixed(5)}, ${summary.lon!.toFixed(5)}`, label: summary.address || "", lat: summary.lat!, lon: summary.lon! } : null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [target, setTarget] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);

  // The merge menu is the SAME list the Locations page shows, on the same key,
  // so opening this editor after visiting that page costs nothing. It used to be
  // a third spelling of `/locations` in its own `useState`.
  const locsQ = useQuery({
    queryKey: birdsKeys.locations(nb),
    queryFn: () => fetchLocations<{ locations: { id: number; name: string }[] }>(nb),
    placeholderData: keepPreviousData,
  });
  const others = (locsQ.data?.locations ?? []).filter((l) => l.id !== summary.id);

  // A write, then the invalidation that used to be a caller's re-read. The
  // editor stays open and says so when one is rejected; it used to close in
  // `.finally`, which looked exactly like a save that worked.
  const run = (work: Promise<unknown>, then: () => void) => {
    setBusy(true); setErr(null);
    work.then(then).catch((e) => { setErr(String(e)); setBusy(false); });
  };

  const save = () => {
    if (!place) return;
    run(saveLocation({
      id: summary.id,
      name: name.trim() || place.short,
      lat: place.lat,
      lon: place.lon,
      address: place.label || place.short,
    }), () => {
      void qc.invalidateQueries({ queryKey: birdsKeys.locationAll(summary.id) });
      void qc.invalidateQueries({ queryKey: birdsKeys.locationsAll() });
      onDone();
    });
  };

  // A merge or a remove moves this place's detections, so every count in the app
  // is downstream of it. Only what is mounted re-reads; the rest is marked stale.
  const merge = () => {
    if (!target) return;
    run(mergeLocations({ src: summary.id, dst: Number(target) }), () => {
      void qc.invalidateQueries({ queryKey: birdsKeys.all() });
      nav(`/apps/birds/location?id=${target}`);
    });
  };

  const remove = () => {
    run(removeLocation(summary.id), () => {
      void qc.invalidateQueries({ queryKey: birdsKeys.all() });
      nav("/apps/birds");
    });
  };

  return (
    <div>
      <div style={{ display: "grid", gap: ".6rem", maxWidth: 460 }}>
        <div>
          <div className="muted" style={lbl}>Address or GPS</div>
          <AddressPicker value={place} onPick={setPlace} />
        </div>
        <label className="muted" style={lbl}>Display name (optional)
          <input className="num-in" value={name} placeholder={place?.short || "e.g. Home, Back deck"}
            onChange={(e) => setName(e.target.value)} style={inp} />
        </label>
        <p className="muted" style={{ fontSize: ".75rem", margin: 0 }}>
          A location must be a verified address or GPS point. Adaptive devices within ~40 m merge into it automatically.
        </p>
        {err && <Notice tone="error" detail={err}>That did not go through.</Notice>}
        <div style={{ display: "flex", gap: ".5rem", flexWrap: "wrap" }}>
          <button className="btn primary" onClick={save} disabled={busy || !place}><Check size={14} /> Save</button>
          {hasPlace && <button className="btn" onClick={onDone} disabled={busy}><X size={14} /> Cancel</button>}
        </div>

        {locsQ.isError ? (
          <Notice tone="warn" action={<Button size="sm" variant="ghost" onClick={() => void locsQ.refetch()}>Retry</Button>}>
            Couldn't load the other locations, so merging is unavailable.
          </Notice>
        ) : others.length > 0 && (
          <div style={{ marginTop: ".6rem", paddingTop: ".7rem", borderTop: "1px solid var(--border)" }}>
            <div className="muted" style={lbl}>Or merge this location into another</div>
            <div style={{ display: "flex", gap: ".5rem", flexWrap: "wrap", marginTop: ".25rem" }}>
              <select className="num-in" value={target} onChange={(e) => setTarget(e.target.value)} style={{ width: "auto" }}>
                <option value="">Choose a location…</option>
                {others.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
              </select>
              <button className="btn" onClick={merge} disabled={!target || busy}>Merge into it</button>
            </div>
            <p className="muted" style={{ fontSize: ".72rem", margin: ".3rem 0 0" }}>
              Moves all detections and devices here into the chosen location, then removes this one.
            </p>
          </div>
        )}

        <div style={{ marginTop: ".6rem", paddingTop: ".7rem", borderTop: "1px solid var(--border)" }}>
          {!confirmDel ? (
            <button className="btn danger" onClick={() => setConfirmDel(true)} disabled={busy}>
              <Trash2 size={14} /> Remove this location
            </button>
          ) : (
            <div style={{ display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap" }}>
              <span style={{ fontSize: ".82rem", flex: 1, minWidth: "12rem" }}>
                Remove this location? Its {summary.detections.toLocaleString()} detection{summary.detections === 1 ? "" : "s"} are kept and it can be restored from the Locations page.
              </span>
              <button className="btn" onClick={() => setConfirmDel(false)} disabled={busy}><X size={14} /> Cancel</button>
              <button className="btn danger" onClick={remove} disabled={busy}><Check size={14} /> Remove</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Page chrome: just the title now, since the home star and Edit pencil live
// INSIDE the identity card (above), not in this header.
function Wrap({ children, name }: { children: React.ReactNode; name?: string }) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: ".5rem", marginBottom: ".75rem" }}>
        <h2 style={{ display: "flex", alignItems: "center", gap: ".5rem", margin: 0 }}>
          <MapPin size={22} strokeWidth={1.8} /> {name || "Location"}
        </h2>
      </div>
      {children}
    </div>
  );
}

const lbl: React.CSSProperties = { display: "flex", flexDirection: "column", gap: ".2rem", fontSize: ".78rem" };
const inp: React.CSSProperties = { marginTop: ".1rem" };
