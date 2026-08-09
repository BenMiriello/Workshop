import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { MapPin, Radio, Star, Pencil, Check, X, AlertTriangle, ChevronRight, Trash2 } from "lucide-react";
import { useCrumbLabel } from "../../../components/appNav";
import { API, jget } from "./ui";
import AddressPicker from "./AddressPicker";
import type { Place } from "./geocode";

// The editing surface for one place: rename it, pin its coordinates (so adaptive
// devices merge into it), set it home, merge it into another, and see the devices
// reporting here. Browsing the birds heard here is the Place dropdown on the
// home page — this page links straight into that view.

const REFRESH_MS = 12000;

type Summary = {
  id: number; name: string; lat: number | null; lon: number | null; address: string | null;
  devices: string[]; detections: number; species: number;
};

export default function LocationDetail() {
  const [params] = useSearchParams();
  const id = Number(params.get("id") || 0);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [home, setHome] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  useCrumbLabel(summary?.name);

  const refresh = useCallback(() => {
    if (!id) return;
    Promise.all([
      jget<Summary>(`/location/summary?location_id=${id}`),
      jget<{ home: string | null }>(`/home`),
    ]).then(([s, h]) => { setSummary(s); setHome(h.home); })
      .catch((e) => setErr(String(e)));
  }, [id]);

  useEffect(() => { refresh(); const t = setInterval(refresh, REFRESH_MS); return () => clearInterval(t); }, [refresh]);

  // A location with no pinned place needs one — open the editor automatically.
  const autoOpened = useRef(false);
  useEffect(() => {
    if (summary && summary.lat == null && !autoOpened.current) { autoOpened.current = true; setEditing(true); }
  }, [summary]);

  // Toggle home: set this place, or clear home entirely (empty source) when it is
  // already home.
  const toggleHome = useCallback((makeHome: boolean) => {
    const fd = new FormData(); fd.append("source", makeHome ? `loc:${id}` : "");
    setHome(makeHome ? `loc:${id}` : "");
    fetch(`${API}/home`, { method: "POST", body: fd }).catch(() => {});
  }, [id]);

  if (!id) return <Wrap><p className="error">No location selected.</p></Wrap>;
  if (err && !summary) return <Wrap><p className="error">Could not load: {err}</p></Wrap>;
  if (!summary) return <Wrap><p className="muted">Loading…</p></Wrap>;

  const isHome = home === `loc:${id}`;

  return (
    <Wrap name={summary.name}>
      {/* Identity card: the name, the home star, and the Edit pencil all live
          INSIDE this card (not in a header above it); editing expands in place. */}
      <section className="card" style={{ padding: "1rem", marginBottom: "1rem",
        border: (isHome || editing) ? "1px solid color-mix(in srgb, var(--accent) 33%, transparent)" : undefined }}>
        <div style={{ display: "flex", alignItems: "center", gap: ".5rem", marginBottom: editing ? ".6rem" : 0 }}>
          <MapPin size={16} className="muted" style={{ flexShrink: 0 }} />
          <span style={{ flex: 1, minWidth: 0, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{summary.name}</span>
          <button onClick={() => toggleHome(!isHome)} title={isHome ? "Home (tap to unset)" : "Set as home"}
            aria-pressed={isHome} aria-label={isHome ? "Unset as home" : "Set as home"}
            style={{ background: "none", border: "none", cursor: "pointer", color: isHome ? "var(--warn-solid)" : "var(--muted)", flexShrink: 0, padding: ".15rem" }}>
            <Star size={18} fill={isHome ? "var(--warn-solid)" : "none"} />
          </button>
          {!editing && (
            <button className="btn sm" title="Edit this location" aria-label="Edit this location" onClick={() => setEditing(true)} style={{ flexShrink: 0 }}>
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
            <button className="btn sm" onClick={() => setEditing(true)}>Set address</button>
          </div>
        )}
        {editing && <EditLocation summary={summary} onDone={() => { setEditing(false); refresh(); }} />}
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
  const hasPlace = summary.lat != null && summary.lon != null;
  const [name, setName] = useState(summary.name && summary.name !== "Untitled location" ? summary.name : "");
  // The "Address or GPS" field shows the real address, not the display name
  // (which has its own field below).
  const [place, setPlace] = useState<Place | null>(
    hasPlace ? { short: summary.address || `${summary.lat!.toFixed(5)}, ${summary.lon!.toFixed(5)}`, label: summary.address || "", lat: summary.lat!, lon: summary.lon! } : null);
  const [busy, setBusy] = useState(false);
  const [others, setOthers] = useState<{ id: number; name: string }[]>([]);
  const [target, setTarget] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);

  useEffect(() => {
    jget<{ locations: { id: number; name: string }[] }>(`/locations`)
      .then((d) => setOthers(d.locations.filter((l) => l.id !== summary.id)))
      .catch(() => {});
  }, [summary.id]);

  const merge = () => {
    if (!target) return;
    setBusy(true);
    const fd = new FormData();
    fd.append("src", String(summary.id));
    fd.append("dst", target);
    fetch(`${API}/location/merge`, { method: "POST", body: fd })
      .then(() => nav(`/apps/birds/location?id=${target}`))
      .catch(() => setBusy(false));
  };

  const remove = () => {
    setBusy(true);
    fetch(`${API}/location?location_id=${summary.id}`, { method: "DELETE" })
      .then(() => nav("/apps/birds"))
      .catch(() => setBusy(false));
  };

  const save = () => {
    if (!place) return;
    setBusy(true);
    const fd = new FormData();
    fd.append("location_id", String(summary.id));
    fd.append("name", name.trim() || place.short);
    fd.append("lat", String(place.lat));
    fd.append("lon", String(place.lon));
    fd.append("address", place.label || place.short);
    fetch(`${API}/location`, { method: "POST", body: fd }).finally(() => { setBusy(false); onDone(); });
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
        <div style={{ display: "flex", gap: ".5rem", flexWrap: "wrap" }}>
          <button className="btn primary" onClick={save} disabled={busy || !place}><Check size={14} /> Save</button>
          {hasPlace && <button className="btn" onClick={onDone}><X size={14} /> Cancel</button>}
        </div>

        {others.length > 0 && (
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

// Page chrome: just the title now — the home star and Edit pencil live INSIDE the
// identity card (above), not in this header.
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
