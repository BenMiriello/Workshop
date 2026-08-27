import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Smartphone, Radio, MapPin, Trash2, X, Check, Pencil, RotateCcw, Users } from "lucide-react";
import { Button, Notice, Skeleton, StateBlock, StatusPill, timeAgo } from "@workshop/ui";
import { useRecorder, type DeviceMode } from "./recorder";
import { isRecording } from "./ui";
import { useShowNonBird } from "./settings";
import {
  birdsKeys, fetchDevices, fetchDevicesArchived, fetchLocations,
  removeDevice, restoreDevice, saveDevice,
} from "./queries";

// Devices that listen. Each is adaptive (follows its current GPS) or fixed
// (pinned to one location, the way the Pi will be). A device is read-only until
// you open it with Edit; only then can you change it or remove it. Removal is a
// soft archive: the device and all its detections are kept and can be restored.
// The device you are on is highlighted; editing it updates this browser too.
//
// THREE FACTS, THREE QUERIES. One `usePoll` used to fire all three reads into
// three `useState`s, every one of them ending in `.catch(() => {})`, so the
// live list, the archive and the locations menu could each be from a different
// tick and a backend that was down looked like a page that had stopped
// changing. They are separate facts and they now have separate cache entries,
// on the keys in `queries.ts` -- the locations one is the same entry the
// Locations page and the scope provider read.

type Dev = {
  token: string; name: string; mode: DeviceMode;
  fixed_location_id: number | null; fixed_location: string | null;
  location_id: number | null; location: string | null;
  created_at: string | null; last_seen: string | null;
  user_agent: string | null; detections: number; deleted_at: string | null;
  shared: boolean; owner_user_id: number | null;
};
type Loc = { id: number; name: string };
type DevList = { devices: Dev[] };

// One empty array, so "no rows yet" is referentially stable and an effect that
// depends on the list does not re-run on every render before the first response.
const NO_DEVS: Dev[] = [];

const REFRESH_MS = 15000;

// Relative "last active" so a stale device reads at a glance. @workshop/ui's
// `timeAgo` is the one formatter; only the empty-input wording is local
// (a device shows "never" for no last-seen record, not an empty string).
function relTime(iso: string | null): string {
  if (!iso) return "never";
  return timeAgo(iso) || "unknown";
}
function dateLabel(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
// A human label for the browser/OS, derived from the user-agent, so you can tell
// "Chrome on macOS" apart from "Safari on iOS" when a device was never named.
function platformLabel(ua: string | null): string {
  if (!ua) return "Unknown browser";
  const os = /iPhone|iPad|iPod/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android"
    : /Macintosh|Mac OS/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows"
    : /CrOS/.test(ua) ? "ChromeOS" : /Linux/.test(ua) ? "Linux" : "";
  const br = /Edg\//.test(ua) ? "Edge" : /OPR\/|Opera/.test(ua) ? "Opera"
    : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox"
    : /Safari\//.test(ua) ? "Safari" : "Browser";
  return [br, os].filter(Boolean).join(" on ");
}
function metaParts(dev: Dev): string {
  const out = [platformLabel(dev.user_agent)];
  out.push(dev.detections > 0 ? `${dev.detections.toLocaleString()} detection${dev.detections === 1 ? "" : "s"}` : "No detections yet");
  out.push(`active ${relTime(dev.last_seen)}`);
  if (dateLabel(dev.created_at)) out.push(`added ${dateLabel(dev.created_at)}`);
  return out.join("  ·  ");
}

export default function Devices() {
  const qc = useQueryClient();
  const rec = useRecorder();
  const [showNonBird] = useShowNonBird();
  const [editing, setEditing] = useState<string | null>(null);
  const [restoreErr, setRestoreErr] = useState<string | null>(null);
  const [params] = useSearchParams();
  const focusTok = params.get("token") || "";
  const focusCard = useRef<HTMLElement | null>(null);

  // The live list keeps its cadence: the "Recording" indicator is derived from
  // `last_seen`, so liveness IS this read (a fixed device like the yard Pi posts
  // continuously). It owns that cadence, so nothing stales it and it does not
  // retry; a failed tick is followed by the next one.
  const live = useQuery({
    queryKey: birdsKeys.devices(),
    queryFn: () => fetchDevices<DevList>(),
    refetchInterval: REFRESH_MS,
    staleTime: 0,
    retry: false,
  });

  // The archive gets no cadence. Nothing moves in it on its own: a row appears
  // when this page removes a device and disappears when it restores one, and
  // both of those invalidate it. It still re-reads on the way back to the tab.
  const archived = useQuery({
    queryKey: birdsKeys.devicesArchived(),
    queryFn: () => fetchDevicesArchived<DevList>(),
    retry: false,
  });

  // Locations are ONLY the options in a fixed device's pinned-location menu, so
  // they are read when an editor is open and not on a 15s loop the menu is not
  // on screen for. Same key and same filter as the Locations page, so opening an
  // editor after visiting that page is served from cache.
  const nb = { includeNonBird: showNonBird };
  const locsQ = useQuery({
    queryKey: birdsKeys.locations(nb),
    queryFn: () => fetchLocations<{ locations: Loc[] }>(nb),
    enabled: editing !== null,
    placeholderData: keepPreviousData,
  });

  const devices = live.data?.devices ?? NO_DEVS;
  const removed = archived.data?.devices ?? NO_DEVS;

  // When arrived at via a device's edit pencil (?token=…), open that device's
  // editor and bring it into view. Keyed by the token it consumed rather than a
  // boolean, so a second device's pencil opens that one too.
  const consumedFocus = useRef<string | null>(null);
  useEffect(() => {
    if (focusTok && consumedFocus.current !== focusTok && devices.some((d) => d.token === focusTok)) {
      consumedFocus.current = focusTok;
      setEditing(focusTok);
    }
  }, [focusTok, devices]);
  useEffect(() => {
    if (editing && focusCard.current) focusCard.current.scrollIntoView({ block: "center" });
  }, [editing]);

  const restore = (token: string) => {
    setRestoreErr(null);
    restoreDevice(token)
      .then(() => qc.invalidateQueries({ queryKey: birdsKeys.devicesAll() }))
      .catch((e) => setRestoreErr(String(e)));
  };

  const listErr = live.isError ? String(live.error) : null;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: ".75rem" }}>
        <h2 style={{ display: "flex", alignItems: "center", gap: ".5rem", margin: 0 }}>
          <Smartphone size={22} strokeWidth={1.8} /> Devices
        </h2>
        <Link to="/apps/birds/locations" className="btn sm"><MapPin size={14} /> Locations</Link>
      </div>

      {/* A failed read with rows still on screen is a LINE beside them; a failed
          read with nothing to show is the REGION below. */}
      {listErr && live.data && (
        <div style={{ marginBottom: ".7rem" }}>
          <Notice tone="warn" action={<Button size="sm" variant="ghost" onClick={() => void live.refetch()}>Retry</Button>}>
            These devices are not refreshing, so Recording may be out of date.
          </Notice>
        </div>
      )}

      {listErr && !live.data ? (
        <StateBlock
          variant="error"
          title="Couldn't load devices"
          hint={<code>GET /api/birds/devices: {listErr}</code>}
          action={<Button size="sm" onClick={() => void live.refetch()}>Retry</Button>}
        />
      ) : live.isPending ? (
        <div style={{ display: "grid", gap: ".7rem" }}>
          {[0, 1, 2].map((i) => <Skeleton key={i} style={{ height: 96 }} />)}
        </div>
      ) : devices.length === 0 ? (
        <StateBlock
          title="No devices yet"
          hint="Tap Listen in the header to register this browser as a device."
        />
      ) : (
        <div style={{ display: "grid", gap: ".7rem" }}>
          {devices.map((dev) => (
            // THE KEY CARRIES THE EDITING FLAG ON PURPOSE. Opening the editor
            // mounts a fresh card, so its draft is seeded once, from the record
            // as it was at that moment. A refetch that changes the record under
            // an OPEN editor cannot re-seed it: the effect that used to do that
            // threw away whatever the operator had typed whenever the device
            // reported in from somewhere else.
            <DeviceCard key={editing === dev.token ? `${dev.token}:edit` : dev.token}
              dev={dev} locs={locsQ.data?.locations ?? []} locsFailed={locsQ.isError}
              current={dev.token === rec.device.token}
              editing={editing === dev.token}
              onEdit={() => setEditing(dev.token)} onClose={() => setEditing(null)}
              cardRef={editing === dev.token ? focusCard : undefined} />
          ))}
        </div>
      )}

      {(removed.length > 0 || archived.isError || restoreErr) && (
        <section style={{ marginTop: "1.5rem" }}>
          <div className="muted" style={{ fontSize: ".78rem", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: ".4rem" }}>
            Removed devices
          </div>
          {archived.isError && (
            <div style={{ marginBottom: ".5rem" }}>
              <Notice tone="error" action={<Button size="sm" variant="ghost" onClick={() => void archived.refetch()}>Retry</Button>}>
                Couldn't load removed devices.
              </Notice>
            </div>
          )}
          {restoreErr && (
            <div style={{ marginBottom: ".5rem" }}>
              <Notice tone="error" detail={restoreErr} action={<Button size="sm" variant="ghost" onClick={() => setRestoreErr(null)}>Dismiss</Button>}>
                Could not restore that device.
              </Notice>
            </div>
          )}
          <div style={{ display: "grid", gap: ".5rem" }}>
            {removed.map((dev) => (
              <div key={dev.token} className="card" style={{ padding: ".6rem .9rem", display: "flex", alignItems: "center", gap: ".6rem", opacity: 0.85 }}>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ fontWeight: 600 }}>{dev.name}</span>
                  <span className="muted" style={{ fontSize: ".76rem", display: "block" }}>
                    {dev.detections > 0 ? `${dev.detections.toLocaleString()} detection${dev.detections === 1 ? "" : "s"} kept` : "No detections"} · removed {relTime(dev.deleted_at)}
                  </span>
                </span>
                <button className="btn sm" onClick={() => restore(dev.token)}><RotateCcw size={14} /> Restore</button>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function DeviceCard({ dev, locs, locsFailed, current, editing, onEdit, onClose, cardRef }: {
  dev: Dev; locs: Loc[]; locsFailed: boolean; current: boolean; editing: boolean;
  onEdit: () => void; onClose: () => void;
  cardRef?: React.RefObject<HTMLElement | null>;
}) {
  const qc = useQueryClient();
  const rec = useRecorder();
  // The draft, seeded from the record AT MOUNT, which the parent's key makes the
  // moment the editor opened. There is no re-seeding effect and there must not
  // be one: while this editor is open it is the only owner of these fields.
  const [name, setName] = useState(dev.name);
  const [mode, setMode] = useState<DeviceMode>(dev.mode);
  const [fixed, setFixed] = useState<string>(dev.fixed_location_id ? String(dev.fixed_location_id) : "");
  const [shared, setShared] = useState<boolean>(dev.shared);
  const [confirmDel, setConfirmDel] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // A write, then the invalidation that used to be `setTimeout(onChanged, 200)`.
  // The editor stays OPEN and says so when a write is rejected; it used to close
  // either way, which looked exactly like a save that had worked.
  const run = (work: Promise<unknown>) => {
    setBusy(true); setErr(null);
    work
      .then(() => { void qc.invalidateQueries({ queryKey: birdsKeys.devicesAll() }); onClose(); })
      .catch((e) => { setErr(String(e)); setBusy(false); });
  };

  const save = () => {
    const nm = name.trim() || dev.name;
    // THIS browser's device is also a local preference, so the recorder writes
    // its own copy (localStorage, and its own upsert). Its setters return void,
    // which is what the 200ms sleep was standing in for; the canonical write
    // goes through the same `saveDevice` every other device uses, and the
    // invalidation is sequenced on THAT rather than on a guess. Both carry the
    // same values, and the route is an upsert.
    if (current) {
      rec.setDeviceName(nm);
      rec.setMode(mode);
      if (mode === "fixed") rec.setFixedLocation(fixed);
      rec.setShared(shared);
    }
    run(saveDevice({
      token: dev.token,
      name: nm,
      mode,
      // Sent only for a fixed device: the route reads an absent pin as "leave it
      // alone", which is how an adaptive device keeps the place it was pinned to.
      fixedLocationId: mode === "fixed" ? fixed : null,
      shared,
    }));
  };

  const remove = () => run(removeDevice(dev.token));

  return (
    <section ref={cardRef as React.RefObject<HTMLElement>} className="card"
      style={{ padding: "1rem", border: (current || editing) ? "1px solid color-mix(in srgb, var(--accent) 33%, transparent)" : undefined }}>
      <div style={{ display: "flex", alignItems: "center", gap: ".5rem", marginBottom: ".4rem" }}>
        {editing ? (
          <input className="num-in" value={name} placeholder="Device name" autoFocus
            onChange={(e) => setName(e.target.value)}
            style={{ flex: 1, minWidth: 0, fontWeight: 600 }} />
        ) : (
          <span style={{ flex: 1, minWidth: 0, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{dev.name}</span>
        )}
        {(current ? rec.recording : isRecording(dev.last_seen)) && <StatusPill status="running" className="birds-live">Recording</StatusPill>}
        {current && <span className="pill neutral" style={{ flexShrink: 0 }}><Radio size={12} style={{ verticalAlign: "-2px" }} /> this device</span>}
        {!editing && (
          <button className="btn sm" title="Edit this device" aria-label="Edit this device" onClick={onEdit} style={{ flexShrink: 0 }}>
            <Pencil size={14} />
          </button>
        )}
      </div>

      {editing ? (
        <div className="choice" style={{ marginBottom: ".5rem" }}>
          {(["adaptive", "fixed"] as DeviceMode[]).map((m) => (
            <button key={m} className={mode === m ? "active" : ""} onClick={() => setMode(m)}>
              {m === "adaptive" ? "Adaptive (follows GPS)" : "Fixed location"}
            </button>
          ))}
        </div>
      ) : (
        <div className="muted" style={{ fontSize: ".82rem", marginBottom: ".35rem", display: "flex", alignItems: "center", gap: ".3rem", flexWrap: "wrap" }}>
          {dev.mode === "fixed" ? "Fixed" : "Adaptive"}
          {dev.location && <>
            <span aria-hidden>·</span>
            <MapPin size={12} /> {dev.mode === "fixed" ? "pinned to" : "currently at"}{" "}
            <Link to={`/apps/birds/location?id=${dev.location_id}`} className="muted" style={{ textDecoration: "underline" }}>{dev.location}</Link>
          </>}
        </div>
      )}

      {editing && mode === "fixed" && (
        <label className="muted" style={{ fontSize: ".8rem", display: "flex", flexDirection: "column", gap: ".2rem", marginBottom: ".25rem" }}>
          Pinned location
          <select className="num-in" value={fixed} onChange={(e) => setFixed(e.target.value)}>
            <option value="">Choose a location…</option>
            {locs.map((L) => <option key={L.id} value={L.id}>{L.name}</option>)}
          </select>
        </label>
      )}
      {/* A field-sized slot gets a LINE, not a region: only the menu is missing,
          the rest of the card is fine. */}
      {editing && mode === "fixed" && locsFailed && (
        <div style={{ marginBottom: ".25rem" }}>
          <Notice tone="warn">Couldn't load the locations to choose from.</Notice>
        </div>
      )}

      {/* Sharing: private to you, or visible to your friends at this device's
          location. Works the same whether the device is fixed or adaptive. */}
      {editing && (
        <>
          <div className="choice" style={{ marginBottom: ".25rem" }}>
            {([["false", "Private"], ["true", "Shared with friends"]] as const).map(([v, label]) => (
              <button key={v} className={String(shared) === v ? "active" : ""}
                onClick={() => setShared(v === "true")}>{label}</button>
            ))}
          </div>
          <div className="muted" style={{ fontSize: ".74rem", marginBottom: ".25rem" }}>
            {shared ? "Friends can see this device's birds at its location."
                    : "Only you can see this device's birds."}
          </div>
        </>
      )}

      {!editing && dev.shared && (
        <div className="muted" style={{ fontSize: ".78rem", display: "flex", alignItems: "center", gap: ".3rem", marginBottom: ".1rem" }}>
          <Users size={12} /> Shared with friends
        </div>
      )}

      <div className="muted" style={{ fontSize: ".76rem", marginTop: ".5rem" }}>{metaParts(dev)}</div>

      {editing && err && (
        <div style={{ marginTop: ".6rem" }}>
          <Notice tone="error" detail={err}>That did not go through.</Notice>
        </div>
      )}

      {editing && !confirmDel && (
        <div style={{ marginTop: ".7rem", paddingTop: ".6rem", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap" }}>
          <button className="btn sm danger" onClick={() => setConfirmDel(true)} disabled={busy}><Trash2 size={14} /> Remove</button>
          <span style={{ flex: 1 }} />
          <button className="btn sm" onClick={onClose} disabled={busy}><X size={14} /> Cancel</button>
          <button className="btn sm primary" onClick={save} disabled={busy}><Check size={14} /> Save</button>
        </div>
      )}

      {editing && confirmDel && (
        <div style={{ marginTop: ".7rem", paddingTop: ".6rem", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap" }}>
          <span style={{ fontSize: ".82rem", flex: 1, minWidth: "12rem" }}>
            Remove this device? {dev.detections > 0
              ? `Its ${dev.detections.toLocaleString()} detection${dev.detections === 1 ? "" : "s"} are kept and it can be restored.`
              : "It can be restored from Removed devices."}
          </span>
          <button className="btn sm" onClick={() => setConfirmDel(false)} disabled={busy}><X size={14} /> Cancel</button>
          <button className="btn sm danger" onClick={remove} disabled={busy}><Check size={14} /> Remove</button>
        </div>
      )}
    </section>
  );
}
