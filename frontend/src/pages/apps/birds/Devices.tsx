import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Smartphone, Radio, MapPin, Trash2, X, Check, Pencil, RotateCcw, Users } from "lucide-react";
import { useRecorder, type DeviceMode } from "./recorder";
import { StatusPill, timeAgo, usePoll } from "@workshop/ui";
import { API, jget, isRecording } from "./ui";

// Devices that listen. Each is adaptive (follows its current GPS) or fixed
// (pinned to one location, the way the Pi will be). A device is read-only until
// you open it with Edit; only then can you change it or remove it. Removal is a
// soft archive — the device and all its detections are kept and can be restored.
// The device you are on is highlighted; editing it updates this browser too.

type Dev = {
  token: string; name: string; mode: DeviceMode;
  fixed_location_id: number | null; fixed_location: string | null;
  location_id: number | null; location: string | null;
  created_at: string | null; last_seen: string | null;
  user_agent: string | null; detections: number; deleted_at: string | null;
  shared: boolean; owner_user_id: number | null;
};
type Loc = { id: number; name: string };

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
  const rec = useRecorder();
  const [devices, setDevices] = useState<Dev[]>([]);
  const [removed, setRemoved] = useState<Dev[]>([]);
  const [locs, setLocs] = useState<Loc[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [params] = useSearchParams();
  const focusTok = params.get("token") || "";
  const focusCard = useRef<HTMLElement | null>(null);

  const load = useCallback(() => {
    jget<{ devices: Dev[] }>(`/devices`).then((d) => setDevices(d.devices)).catch(() => {});
    jget<{ devices: Dev[] }>(`/devices?archived=1`).then((d) => setRemoved(d.devices)).catch(() => {});
    jget<{ locations: Loc[] }>(`/locations`).then((d) => setLocs(d.locations)).catch(() => {});
  }, []);
  // Poll so the "Recording" indicator reflects current liveness without a manual
  // reload (a fixed device like the yard Pi posts continuously).
  usePoll(load, 15000);

  // When arrived at via a device's edit pencil (?token=…), open that device's
  // editor and bring it into view.
  const consumedFocus = useRef(false);
  useEffect(() => {
    if (focusTok && !consumedFocus.current && devices.some((d) => d.token === focusTok)) {
      consumedFocus.current = true;
      setEditing(focusTok);
    }
  }, [focusTok, devices]);
  useEffect(() => {
    if (editing && focusCard.current) focusCard.current.scrollIntoView({ block: "center" });
  }, [editing]);

  const restore = useCallback((token: string) => {
    const fd = new FormData(); fd.append("token", token);
    fetch(`${API}/device/restore`, { method: "POST", body: fd }).then(() => setTimeout(load, 150)).catch(() => {});
  }, [load]);

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: ".75rem" }}>
        <h2 style={{ display: "flex", alignItems: "center", gap: ".5rem", margin: 0 }}>
          <Smartphone size={22} strokeWidth={1.8} /> Devices
        </h2>
        <Link to="/apps/birds/locations" className="btn sm"><MapPin size={14} /> Locations</Link>
      </div>

      <div style={{ display: "grid", gap: ".7rem" }}>
        {devices.map((dev) => (
          <DeviceCard key={dev.token} dev={dev} locs={locs} current={dev.token === rec.device.token}
            editing={editing === dev.token}
            onEdit={() => setEditing(dev.token)} onClose={() => setEditing(null)}
            onChanged={load} cardRef={editing === dev.token ? focusCard : undefined} />
        ))}
        {devices.length === 0 && <p className="muted">No devices yet. Start listening to register this one.</p>}
      </div>

      {removed.length > 0 && (
        <section style={{ marginTop: "1.5rem" }}>
          <div className="muted" style={{ fontSize: ".78rem", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: ".4rem" }}>
            Removed devices
          </div>
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

function DeviceCard({ dev, locs, current, editing, onEdit, onClose, onChanged, cardRef }: {
  dev: Dev; locs: Loc[]; current: boolean; editing: boolean;
  onEdit: () => void; onClose: () => void; onChanged: () => void;
  cardRef?: React.RefObject<HTMLElement | null>;
}) {
  const rec = useRecorder();
  const [name, setName] = useState(dev.name);
  const [mode, setMode] = useState<DeviceMode>(dev.mode);
  const [fixed, setFixed] = useState<string>(dev.fixed_location_id ? String(dev.fixed_location_id) : "");
  const [shared, setShared] = useState<boolean>(dev.shared);
  const [confirmDel, setConfirmDel] = useState(false);

  // Re-seed the draft whenever the editor opens (or the device changes under it).
  useEffect(() => {
    if (editing) {
      setName(dev.name); setMode(dev.mode);
      setFixed(dev.fixed_location_id ? String(dev.fixed_location_id) : "");
      setShared(dev.shared);
      setConfirmDel(false);
    }
  }, [editing, dev.name, dev.mode, dev.fixed_location_id, dev.shared]);

  const save = () => {
    const nm = name.trim() || dev.name;
    if (current) {
      rec.setDeviceName(nm);
      rec.setMode(mode);
      if (mode === "fixed") rec.setFixedLocation(fixed);
      rec.setShared(shared);
    } else {
      const fd = new FormData();
      fd.append("token", dev.token);
      fd.append("name", nm);
      fd.append("mode", mode);
      if (mode === "fixed") fd.append("fixed_location_id", fixed || "0");
      fd.append("shared", shared ? "true" : "false");
      fetch(`${API}/device`, { method: "POST", body: fd }).catch(() => {});
    }
    onClose(); setTimeout(onChanged, 200);
  };

  const remove = () => {
    fetch(`${API}/device?token=${encodeURIComponent(dev.token)}`, { method: "DELETE" })
      .then(() => { onClose(); setTimeout(onChanged, 150); }).catch(() => {});
  };

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

      {editing && !confirmDel && (
        <div style={{ marginTop: ".7rem", paddingTop: ".6rem", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap" }}>
          <button className="btn sm danger" onClick={() => setConfirmDel(true)}><Trash2 size={14} /> Remove</button>
          <span style={{ flex: 1 }} />
          <button className="btn sm" onClick={onClose}><X size={14} /> Cancel</button>
          <button className="btn sm primary" onClick={save}><Check size={14} /> Save</button>
        </div>
      )}

      {editing && confirmDel && (
        <div style={{ marginTop: ".7rem", paddingTop: ".6rem", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap" }}>
          <span style={{ fontSize: ".82rem", flex: 1, minWidth: "12rem" }}>
            Remove this device? {dev.detections > 0
              ? `Its ${dev.detections.toLocaleString()} detection${dev.detections === 1 ? "" : "s"} are kept and it can be restored.`
              : "It can be restored from Removed devices."}
          </span>
          <button className="btn sm" onClick={() => setConfirmDel(false)}><X size={14} /> Cancel</button>
          <button className="btn sm danger" onClick={remove}><Check size={14} /> Remove</button>
        </div>
      )}
    </section>
  );
}
