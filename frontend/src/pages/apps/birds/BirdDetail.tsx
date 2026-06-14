import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  MapPin, Smartphone, ExternalLink, Mic, Pin, PinOff, Trash2,
  Check, Ban, CircleDashed, ChevronDown,
} from "lucide-react";
import { useCrumbTrail, type Crumb } from "../../../components/appNav";
import { useBirdInfo } from "./birdinfo";
import { ConfirmDialog, Segmented } from "../../../ui";
import { pct, conf, jget, apost, API, Stat, StatGrid } from "./ui";
import ActivityCard from "./ActivityCard";
import BirdsHeader from "./BirdsHeader";
import { PlayButton, fmtBytes } from "./recordings";

// One species' page: photo + description (client-side reference lookup), stats,
// the same activity charts as the rest of the app (NOT subject to the status
// filter), and the individual detections. The detections list is where you
// filter by confirmed/suggested/rejected and re-classify single events; each row
// links into its device and location.

const REFRESH_MS = 8000;
type DetStatus = "all" | "confirmed" | "suggested" | "rejected";

type LocOpt = { id: number; name: string; count: number };
type DevOpt = { token: string; name: string; count: number };
type DetRow = {
  id: number; at: string; confidence: number; occurrence: number | null;
  status: "confirmed" | "suggested" | "rejected";
  location_id: number | null; location: string | null;
  device: string | null; device_token: string | null; recording_id: number | null;
};
type Detail = {
  scientific_name: string; common_name: string; count: number;
  first_date: string | null; last_date: string | null; days_seen: number;
  max_conf: number; avg_conf: number; total_all: number;
  occurrence: number | null;
  hourly: { hour: number; count: number }[];
  by_day: { date: string; count: number; hourly: number[] }[];
  locations: LocOpt[]; devices: DevOpt[];
  save_mode: "default" | "keep" | "never";
  clip_count: number; list_count: number;
  recent: DetRow[];
};

export default function BirdDetail() {
  const [params, setParams] = useSearchParams();
  const sci = params.get("sci") || "";
  const locId = params.get("location_id") || "";
  const device = params.get("device") || "";
  const days = params.get("days") || "0";
  // Detection-list filter. Falls back to the `status` a species link carried in
  // (e.g. tapping a Suggested species lands on its suggested detections).
  const status = (params.get("dstatus") || params.get("status") || "all") as DetStatus;
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const info = useBirdInfo(sci || null, null);

  const reload = useCallback(() => {
    const qs = new URLSearchParams({ sci, status });
    if (locId) qs.set("location_id", locId);
    if (device) qs.set("device", device);
    if (days !== "0") qs.set("days", days);
    return jget<Detail>(`/bird?${qs.toString()}`).then(setD).catch((e) => setErr(String(e)));
  }, [sci, locId, device, days, status]);

  useEffect(() => {
    if (!sci) return;
    reload();
    const t = setInterval(reload, REFRESH_MS);
    return () => clearInterval(t);
  }, [sci, reload]);

  const setFilter = (key: string, val: string) => {
    const p = new URLSearchParams(params);
    if (key === "dstatus") {
      // The detection filter is one source of truth: always write it (including
      // "all") and drop any legacy `status` a species link carried in, so picking
      // "All" actually clears the inherited scope instead of falling back to it.
      p.set("dstatus", val);
      p.delete("status");
    } else if (val && val !== "0") {
      p.set(key, val);
    } else {
      p.delete(key);
    }
    setParams(p);
  };

  const common = d?.common_name || params.get("common") || info?.common_name || sci;
  const place = locId ? d?.locations.find((l) => String(l.id) === locId)?.name : null;

  const trail: Crumb[] = [
    { label: "Apps", to: "/apps" },
    { label: "Birds", to: "/apps/birds" },
  ];
  if (locId && place) trail.push({ label: place, to: `/apps/birds?source=loc:${locId}` });
  trail.push({ label: common });
  useCrumbTrail(trail);

  const showLoc = (d?.locations.length || 0) > 1;
  const showDev = (d?.devices.length || 0) > 1;

  return (
    <div>
      <BirdsHeader title={common} />

      {/* Header card: photo + identity + description. The common name lives in the
          toolbar title above, so it is not repeated here. */}
      <section className="card" style={{ padding: 0, marginBottom: "1rem", overflow: "hidden" }}>
        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
          {info?.photo && (
            <img src={info.photo} alt={common}
              style={{ width: 200, maxWidth: "100%", height: 200, objectFit: "cover", alignSelf: "center" }} />
          )}
          <div style={{ flex: 1, minWidth: 220, padding: "1rem", display: "flex", flexDirection: "column" }}>
            <div className="muted" style={{ fontStyle: "italic", marginBottom: ".5rem" }}>{sci}</div>
            {info?.summary && <p style={{ margin: ".2rem 0 .5rem", fontSize: ".9rem", lineHeight: 1.45 }}>{info.summary}</p>}
            {info && !info.found && <p className="muted" style={{ fontSize: ".82rem" }}>No reference entry for this label.</p>}
            {info?.wikiUrl && (
              <div style={{ marginTop: "auto", display: "flex", justifyContent: "flex-end" }}>
                <a href={info.wikiUrl} target="_blank" rel="noreferrer" className="muted" style={{ display: "inline-flex", alignItems: "center", gap: ".2rem", fontSize: ".75rem" }}>Wikipedia <ExternalLink size={11} /></a>
              </div>
            )}
          </div>
        </div>
      </section>

      {err && !d && <p className="error">Could not load: {err}</p>}
      {!d && !err && <p className="muted">Loading…</p>}

      {d && (
        <>
          {/* Filters: location, device, date range (NOT the trust status) */}
          {(showLoc || showDev) && (
            <div className="filters">
              {showLoc && (
                <label>Location
                  <select className="num-in" value={locId} onChange={(e) => setFilter("location_id", e.target.value)}>
                    <option value="">All locations ({d.total_all})</option>
                    {d.locations.map((L) => <option key={L.id} value={L.id}>{L.name} ({L.count})</option>)}
                  </select>
                </label>
              )}
              {showDev && (
                <label>Device
                  <select className="num-in" value={device} onChange={(e) => setFilter("device", e.target.value)}>
                    <option value="">All devices</option>
                    {d.devices.map((D) => <option key={D.token} value={D.token}>{D.name} ({D.count})</option>)}
                  </select>
                </label>
              )}
              <label>When
                <select className="num-in" value={days} onChange={(e) => setFilter("days", e.target.value)}>
                  <option value="0">All time</option>
                  <option value="1">Today</option>
                  <option value="7">Last 7 days</option>
                  <option value="30">Last 30 days</option>
                </select>
              </label>
            </div>
          )}

          <StatGrid>
            <Stat label="Detections" value={d.count.toLocaleString()} />
            <Stat label="Days heard" value={d.days_seen} />
            <Stat label="Best" value={pct(d.max_conf)} />
            <Stat label="Avg" value={pct(d.avg_conf)} />
            {d.occurrence != null && <Stat label="Local likelihood" value={pct(d.occurrence)} />}
          </StatGrid>

          <BirdRecordings sci={sci} common={common} initialMode={d.save_mode} clipCount={d.clip_count} onChange={reload} />

          {d.count > 0 && <ActivityCard daily={d.by_day} hourly={d.hourly} />}

          <section className="card" style={card}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: ".6rem", flexWrap: "wrap", marginBottom: ".5rem" }}>
              <h3 style={{ ...h3, margin: 0 }}>Detections ({d.list_count ?? d.recent.length})</h3>
              <Segmented<DetStatus>
                ariaLabel="Filter detections by status"
                value={status}
                onChange={(v) => setFilter("dstatus", v)}
                options={[
                  { value: "all", label: "All" },
                  { value: "confirmed", label: "Confirmed" },
                  { value: "suggested", label: "Suggested" },
                  { value: "rejected", label: "Rejected" },
                ]}
              />
            </div>
            {d.recent.length === 0
              ? <p className="muted" style={{ margin: ".3rem 0 0" }}>No {status === "all" ? "" : status + " "}detections for this filter.</p>
              : (
                <div>
                  {d.recent.map((r) => (
                    <DetectionRow key={r.id} r={r} hideLoc={!!locId} onChanged={reload}
                      onDevice={(t) => setFilter("device", t)} />
                  ))}
                </div>
              )}
          </section>
        </>
      )}
    </div>
  );
}

const card: React.CSSProperties = { padding: "1rem", marginBottom: "1rem" };
const h3: React.CSSProperties = { margin: "0 0 .4rem", fontSize: "1rem" };

// One detection. The body shows time + confidence; the device and location are
// clickable chips (inline when there's room, revealed by the chevron on narrow
// screens). The trio of buttons re-classifies this single event between
// confirmed / suggested / rejected.
function DetectionRow({ r, hideLoc, onChanged, onDevice }: {
  r: DetRow; hideLoc: boolean; onChanged: () => void; onDevice: (token: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const set = (status: "confirmed" | "suggested" | "rejected") => {
    if (status === r.status || busy) return;
    setBusy(true);
    apost("/detection/status", { id: r.id, status }).then(() => onChanged()).finally(() => setBusy(false));
  };
  const chips = (
    <span className="bird-det-meta">
      {!hideLoc && r.location && r.location_id != null && (
        <Link className="bird-chip" to={`/apps/birds?source=loc:${r.location_id}`}>
          <MapPin size={12} /> {r.location}
        </Link>
      )}
      {r.device && r.device_token && (
        <button type="button" className="bird-chip" title="Show only this device" onClick={() => onDevice(r.device_token!)}>
          <Smartphone size={12} /> {r.device}
        </button>
      )}
    </span>
  );
  const hasChips = (!hideLoc && r.location) || r.device;
  return (
    <div className={`bird-det-row${open ? " is-open" : ""}`}>
      <span className="mono muted" style={{ fontSize: ".8rem" }}>{r.at}</span>
      <span className="muted" style={{ fontSize: ".8rem" }}>{conf(r.confidence)}</span>
      {chips}
      <span className="bird-det-ctl">
        {r.recording_id && <PlayButton recId={r.recording_id} size={24} />}
        <button type="button" className={`bird-act ok${r.status === "confirmed" ? " on" : ""}`} disabled={busy}
          title="Confirmed" aria-pressed={r.status === "confirmed"} onClick={() => set("confirmed")}><Check size={14} /></button>
        <button type="button" className={`bird-act${r.status === "suggested" ? " on" : ""}`} disabled={busy}
          title="Suggested" aria-pressed={r.status === "suggested"} onClick={() => set("suggested")}><CircleDashed size={14} /></button>
        <button type="button" className={`bird-act danger${r.status === "rejected" ? " on" : ""}`} disabled={busy}
          title="Rejected" aria-pressed={r.status === "rejected"} onClick={() => set("rejected")}><Ban size={14} /></button>
        {hasChips && (
          <button type="button" className="bird-act bird-det-expand" aria-expanded={open}
            title={open ? "Hide where" : "Show where"} onClick={() => setOpen((v) => !v)}><ChevronDown size={14} /></button>
        )}
      </span>
    </div>
  );
}

// Per-bird save preference + this bird's saved clips. Compact: the heading, the
// inline Default/Always/Never toggle (detail on hover), then the clips below.
// Deliberately separate from the confirm/reject controls: changing the save mode
// never changes a detection's status.
type Clip = { id: number; at: string; location: string | null; bytes: number; confidence: number; pinned: boolean; protected: boolean };
type SaveMode = "default" | "keep" | "never";

function BirdRecordings({ sci, common, initialMode, clipCount, onChange }: {
  sci: string; common: string; initialMode: SaveMode; clipCount: number; onChange: () => void;
}) {
  const [mode, setMode] = useState<SaveMode>(initialMode);
  const [globalSave, setGlobalSave] = useState<boolean | null>(null);
  const [clips, setClips] = useState<Clip[] | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [confirmDel, setConfirmDel] = useState<Clip | null>(null);

  useEffect(() => { setMode(initialMode); }, [initialMode]);
  useEffect(() => {
    const fetchGlobal = () => jget<{ settings: { rec_save: boolean } }>("/recordings/settings")
      .then((d) => setGlobalSave(d.settings.rec_save)).catch(() => {});
    fetchGlobal();
    window.addEventListener("bird-settings-change", fetchGlobal);
    return () => window.removeEventListener("bird-settings-change", fetchGlobal);
  }, []);

  const loadClips = useCallback(() => {
    jget<{ items: Clip[] }>(`/recordings?sci=${encodeURIComponent(sci)}&limit=200`)
      .then((d) => setClips(d.items)).catch(() => setClips([]));
  }, [sci]);
  useEffect(() => { if (clipCount > 0) loadClips(); else setClips([]); }, [clipCount, loadClips]);

  const choose = useCallback((m: SaveMode) => {
    setMode(m);
    const fd = new FormData(); fd.append("sci", sci); fd.append("save_mode", m);
    fetch(`${API}/species/recording`, { method: "POST", body: fd })
      .then((r) => r.json())
      .then((d: { save_mode: SaveMode }) => setMode(d.save_mode))
      .then(() => { window.dispatchEvent(new CustomEvent("bird-settings-change")); onChange(); })
      .catch(() => {});
  }, [sci, onChange]);

  const pin = (c: Clip) => {
    const fd = new FormData(); fd.append("pinned", String(!c.pinned));
    fetch(`${API}/recordings/${c.id}/pin`, { method: "POST", body: fd })
      .then(() => { loadClips(); window.dispatchEvent(new CustomEvent("bird-settings-change")); });
  };
  const del = (c: Clip) => {
    fetch(`${API}/recordings/${c.id}`, { method: "DELETE" })
      .then(() => { setConfirmDel(null); loadClips(); onChange(); window.dispatchEvent(new CustomEvent("bird-settings-change")); });
  };
  const clearAll = () => {
    fetch(`${API}/recordings?sci=${encodeURIComponent(sci)}`, { method: "DELETE" })
      .then(() => { setConfirmClear(false); setClips([]); onChange(); window.dispatchEvent(new CustomEvent("bird-settings-change")); });
  };

  const defaultState = globalSave == null ? "the app default" : globalSave ? "saving (app default on)" : "not saving (app default off)";
  const hint = `Default: follow ${defaultState}. Always: keep every clip, never auto-delete. Never: skip this bird.`;

  return (
    <section className="card" style={card}>
      <div style={{ display: "flex", alignItems: "center", gap: ".6rem", flexWrap: "wrap" }}>
        <h3 style={{ ...h3, margin: 0, display: "flex", alignItems: "center", gap: ".4rem" }}>
          <Mic size={17} strokeWidth={1.8} /> Recordings
        </h3>
        <span title={hint} style={{ display: "inline-flex" }}>
          <Segmented<SaveMode>
            ariaLabel="Save recordings for this bird"
            value={mode}
            onChange={choose}
            options={[
              { value: "default", label: "Default" },
              { value: "keep", label: "Always" },
              { value: "never", label: "Never" },
            ]}
          />
        </span>
        {clipCount > 0 && (
          <button className="bird-act danger" title="Delete all saved clips for this bird"
            style={{ marginLeft: "auto" }} onClick={() => setConfirmClear(true)}>
            <Trash2 size={15} />
          </button>
        )}
      </div>

      {clipCount === 0
        ? <p className="muted" style={{ fontSize: ".82rem", margin: ".5rem 0 0" }}>No clips saved yet.</p>
        : clips && clips.length > 0 && (
          <div style={{ marginTop: ".5rem", display: "flex", flexDirection: "column", maxHeight: 320, overflowY: "auto" }}>
            {clips.map((c) => (
              <div key={c.id} style={{ display: "flex", alignItems: "center", gap: ".6rem", padding: ".45rem 0", borderTop: "1px solid var(--border)", fontSize: ".84rem" }}>
                <PlayButton recId={c.id} size={26} />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span className="mono muted">{c.at}</span>
                  {c.location && <span className="muted" style={{ marginLeft: ".5rem", display: "inline-flex", alignItems: "center", gap: ".25rem" }}><MapPin size={10} /> {c.location}</span>}
                </span>
                <span className="muted" style={{ fontSize: ".74rem" }}>{fmtBytes(c.bytes)}</span>
                <span className="muted" style={{ fontSize: ".78rem" }}>{conf(c.confidence)}</span>
                <button type="button" className={`bird-act${c.pinned ? " ok" : ""}`} onClick={() => pin(c)}
                  title={c.pinned ? "Pinned. Click to unpin." : "Pin to keep this recording (deleted last)"}>
                  {c.pinned ? <Pin size={14} /> : <PinOff size={14} />}
                </button>
                <button type="button" className="bird-act danger" onClick={() => setConfirmDel(c)} title="Delete this recording">
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}

      <ConfirmDialog
        open={confirmClear}
        title={`Clear all recordings for ${common}?`}
        body={`Permanently delete all ${clipCount} saved clip${clipCount === 1 ? "" : "s"} of ${common}, including pinned ones. Detections and stats are unaffected. This cannot be undone.`}
        confirmLabel="Delete all"
        onCancel={() => setConfirmClear(false)}
        onConfirm={clearAll}
      />
      <ConfirmDialog
        open={!!confirmDel}
        title="Delete recording?"
        body={confirmDel ? `Permanently delete this clip from ${confirmDel.at}. This cannot be undone.` : ""}
        confirmLabel="Delete"
        onCancel={() => setConfirmDel(null)}
        onConfirm={() => confirmDel && del(confirmDel)}
      />
    </section>
  );
}
