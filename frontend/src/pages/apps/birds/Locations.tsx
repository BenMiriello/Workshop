import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { MapPin, Star, Smartphone, RotateCcw, Pencil, Check, X, Trash2, ChevronRight } from "lucide-react";
import { useShowNonBird, nonBirdQuery } from "./settings";
import { API, jget } from "./ui";
import AddressPicker from "./AddressPicker";
import type { Place } from "./geocode";

// All the places birds have been heard. Mirrors the Devices page: each place is a
// read-only card with a per-card Edit pencil that opens an INLINE editor (no
// separate page), a home star INSIDE the card that toggles on/off, and a soft
// remove that keeps every detection and is restorable.

const REFRESH_MS = 8000;

type Loc = {
  id: number; name: string; lat: number | null; lon: number | null; address?: string | null;
  detections: number; species: number; last_ts: string | null; devices: string[];
};

export default function Locations() {
  const [locs, setLocs] = useState<Loc[]>([]);
  const [removed, setRemoved] = useState<Loc[]>([]);
  const [home, setHome] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [showNonBird] = useShowNonBird();
  const nb = nonBirdQuery(showNonBird);
  const [params] = useSearchParams();
  const focusId = Number(params.get("id") || 0);
  const focusCard = useRef<HTMLElement | null>(null);

  const load = useCallback(() => {
    jget<{ locations: Loc[]; home: string | null }>(`/locations?_=1${nb}`)
      .then((d) => { setLocs(d.locations); setHome(d.home); }).catch(() => {});
    jget<{ locations: Loc[] }>(`/locations?archived=1${nb}`)
      .then((d) => setRemoved(d.locations)).catch(() => {});
  }, [nb]);
  useEffect(() => { load(); const t = setInterval(load, REFRESH_MS); return () => clearInterval(t); }, [load]);

  // Arrived at via a place's edit pencil (?id=…, e.g. from the Place dropdown):
  // open that card's editor and bring it into view — same as Devices' ?token=.
  const consumedFocus = useRef(false);
  useEffect(() => {
    if (focusId && !consumedFocus.current && locs.some((l) => l.id === focusId)) {
      consumedFocus.current = true;
      setEditing(focusId);
    }
  }, [focusId, locs]);
  useEffect(() => {
    if (editing && focusCard.current) focusCard.current.scrollIntoView({ block: "center" });
  }, [editing]);

  // Home is one shared setting. Clicking the star toggles it: set this place, or
  // clear home entirely (empty source) when it is already home.
  const toggleHome = useCallback((id: number, makeHome: boolean) => {
    const fd = new FormData(); fd.append("source", makeHome ? `loc:${id}` : "");
    setHome(makeHome ? `loc:${id}` : ""); // optimistic
    fetch(`${API}/home`, { method: "POST", body: fd }).catch(() => {});
  }, []);

  const restore = useCallback((id: number) => {
    const fd = new FormData(); fd.append("location_id", String(id));
    fetch(`${API}/location/restore`, { method: "POST", body: fd }).then(() => setTimeout(load, 150)).catch(() => {});
  }, [load]);

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: ".75rem" }}>
        <h2 style={{ display: "flex", alignItems: "center", gap: ".5rem", margin: 0 }}>
          <MapPin size={22} strokeWidth={1.8} /> Locations
        </h2>
        <Link to="/apps/birds/devices" className="btn sm"><Smartphone size={14} /> Devices</Link>
      </div>

      <div style={{ display: "grid", gap: ".7rem" }}>
        {locs.map((L) => (
          <LocationCard key={L.id} L={L} isHome={home === `loc:${L.id}`}
            editing={editing === L.id}
            onEdit={() => setEditing(L.id)} onClose={() => setEditing(null)}
            onToggleHome={(mk) => toggleHome(L.id, mk)}
            onChanged={load} others={locs.filter((o) => o.id !== L.id)}
            cardRef={editing === L.id ? focusCard : undefined} />
        ))}
        {locs.length === 0 && (
          <p className="muted">No locations yet. Start listening on a device to create one.</p>
        )}
      </div>

      {removed.length > 0 && (
        <section style={{ marginTop: "1.5rem" }}>
          <div className="muted" style={{ fontSize: ".78rem", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: ".4rem" }}>
            Removed locations
          </div>
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

function LocationCard({ L, isHome, editing, onEdit, onClose, onToggleHome, onChanged, others, cardRef }: {
  L: Loc; isHome: boolean; editing: boolean;
  onEdit: () => void; onClose: () => void; onToggleHome: (makeHome: boolean) => void;
  onChanged: () => void; others: { id: number; name: string }[]; cardRef?: React.RefObject<HTMLElement | null>;
}) {
  const hasPlace = L.lat != null && L.lon != null;
  const needsAddress = !hasPlace;
  // The "Address or GPS" field shows the ACTUAL address (not the display name);
  // the display name is its own field below.
  const seedPlace = (): Place | null => hasPlace
    ? { short: L.address || `${L.lat!.toFixed(5)}, ${L.lon!.toFixed(5)}`, label: L.address || "", lat: L.lat!, lon: L.lon! }
    : null;
  const [name, setName] = useState(L.name && L.name !== "Untitled location" ? L.name : "");
  const [place, setPlace] = useState<Place | null>(seedPlace);
  const [confirmDel, setConfirmDel] = useState(false);
  const [mergeTarget, setMergeTarget] = useState("");
  const [busy, setBusy] = useState(false);

  // Re-seed the draft whenever the editor opens (or the place changes under it).
  useEffect(() => {
    if (editing) {
      setName(L.name && L.name !== "Untitled location" ? L.name : "");
      setPlace(seedPlace());
      setConfirmDel(false); setMergeTarget("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing, L.name, L.address, L.lat, L.lon]);

  const save = () => {
    if (!place) return;
    setBusy(true);
    const fd = new FormData();
    fd.append("location_id", String(L.id));
    fd.append("name", name.trim() || place.short);
    fd.append("lat", String(place.lat));
    fd.append("lon", String(place.lon));
    fd.append("address", place.label || place.short);
    fetch(`${API}/location`, { method: "POST", body: fd })
      .finally(() => { setBusy(false); onClose(); setTimeout(onChanged, 200); });
  };

  const remove = () => {
    setBusy(true);
    fetch(`${API}/location?location_id=${L.id}`, { method: "DELETE" })
      .finally(() => { setBusy(false); onClose(); setTimeout(onChanged, 150); });
  };

  const merge = () => {
    if (!mergeTarget) return;
    setBusy(true);
    const fd = new FormData();
    fd.append("src", String(L.id)); fd.append("dst", mergeTarget);
    fetch(`${API}/location/merge`, { method: "POST", body: fd })
      .finally(() => { setBusy(false); onClose(); setTimeout(onChanged, 200); });
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
