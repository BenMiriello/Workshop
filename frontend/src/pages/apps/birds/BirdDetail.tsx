import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { MapPin, Smartphone, ExternalLink, Mic, Pin, PinOff, Trash2 } from "lucide-react";
import { useCrumbTrail, type Crumb } from "../../../components/appNav";
import { useBirdInfo } from "./birdinfo";
import { Check, Ban } from "lucide-react";
import { ConfirmDialog, Segmented } from "../../../ui";
import { pct, hh, jget, apost, API, Stat, StatGrid, Chart, HourSpark, CHART_SERIES } from "./ui";
import { PlayButton, fmtBytes } from "./recordings";

// One species' page: photo + description (client-side reference lookup), stats,
// every day it was heard with that day's hour-of-day mini-graph, and a recent
// list. Filterable by location, device, and date range - all calm selects, with
// location being just one dimension. Breadcrumbs give one-click navigation up.

const REFRESH_MS = 8000;

type LocOpt = { id: number; name: string; count: number };
type DevOpt = { token: string; name: string; count: number };
type Detail = {
  scientific_name: string; common_name: string; count: number;
  first_date: string | null; last_date: string | null; days_seen: number;
  max_conf: number; avg_conf: number; total_all: number;
  occurrence: number | null;
  hourly: { hour: number; count: number }[];
  by_day: { date: string; count: number; hourly: number[] }[];
  locations: LocOpt[]; devices: DevOpt[];
  save_mode: "default" | "keep" | "never";
  clip_count: number;
  recent: { id: number; at: string; confidence: number; occurrence: number | null; status: string; location_id: number | null; location: string | null; device: string | null; recording_id: number | null }[];
};

export default function BirdDetail() {
  const [params, setParams] = useSearchParams();
  const sci = params.get("sci") || "";
  const locId = params.get("location_id") || "";
  const device = params.get("device") || "";
  const days = params.get("days") || "0";
  const status = params.get("status") || "all"; // species drill-down defaults to all
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const info = useBirdInfo(sci || null, null);

  const reload = () => {
    const qs = new URLSearchParams({ sci, status });
    if (locId) qs.set("location_id", locId);
    if (device) qs.set("device", device);
    if (days !== "0") qs.set("days", days);
    return jget<Detail>(`/bird?${qs.toString()}`).then(setD).catch((e) => setErr(String(e)));
  };

  useEffect(() => {
    if (!sci) return;
    reload();
    const t = setInterval(reload, REFRESH_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sci, locId, device, days, status]);

  // Opening a species counts as reviewing its first appearance (clears the
  // new-arrival badge), per the trust model - even if you don't decide anything.
  useEffect(() => {
    if (sci) apost("/species/review", { sci }).catch(() => {});
  }, [sci]);

  const actEvent = (id: number, status: "confirmed" | "rejected") =>
    apost("/detection/status", { id, status }).then(() => reload()).catch(() => {});

  const setFilter = (key: string, val: string) => {
    const p = new URLSearchParams(params);
    if (val && val !== "0") p.set(key, val); else p.delete(key);
    setParams(p);
  };

  const common = d?.common_name || info?.common_name || sci;
  const peak = d?.hourly?.length ? d.hourly.reduce((a, b) => (b.count > a.count ? b : a), d.hourly[0]) : null;
  const place = locId ? d?.locations.find((l) => String(l.id) === locId)?.name : null;

  const trail: Crumb[] = [
    { label: "Apps", to: "/apps" },
    { label: "Birds", to: "/apps/birds" },
  ];
  if (locId) {
    trail.push({ label: "Locations", to: "/apps/birds/locations" });
    if (place) trail.push({ label: place, to: `/apps/birds/location?id=${locId}` });
  }
  trail.push({ label: common });
  useCrumbTrail(trail); // structurally dynamic → feed the toolbar directly

  // Filter visibility is based on the species' FULL option set (which doesn't
  // change with the current selection), not on what's currently selected - so
  // switching to "All locations" never makes the Location control disappear or
  // swap places with another filter.
  const showLoc = (d?.locations.length || 0) > 1;
  const showDev = (d?.devices.length || 0) > 1;

  return (
    <div>
      {/* Header card: photo + identity + description */}
      <section className="card" style={{ padding: 0, marginBottom: "1rem", overflow: "hidden" }}>
        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
          {info?.photo && (
            <img src={info.photo} alt={common} style={{ width: 200, maxWidth: "100%", height: 200, objectFit: "cover" }} />
          )}
          <div style={{ flex: 1, minWidth: 220, padding: "1rem" }}>
            <h2 style={{ margin: "0 0 .15rem" }}>{common}</h2>
            <div className="muted" style={{ fontStyle: "italic", marginBottom: ".5rem" }}>{sci}</div>
            {info?.summary && <p style={{ margin: ".2rem 0 .5rem", fontSize: ".9rem", lineHeight: 1.45 }}>{info.summary}</p>}
            {info && !info.found && <p className="muted" style={{ fontSize: ".82rem" }}>No reference entry for this label.</p>}
            <div style={{ display: "flex", gap: ".8rem", flexWrap: "wrap", fontSize: ".72rem" }} className="muted">
              {info?.attribution && <span>Photo: {info.attribution}</span>}
              {info?.wikiUrl && <a href={info.wikiUrl} target="_blank" rel="noreferrer" className="muted" style={{ display: "inline-flex", alignItems: "center", gap: ".2rem" }}>Wikipedia <ExternalLink size={11} /></a>}
            </div>
          </div>
        </div>
      </section>

      {err && !d && <p className="error">Could not load: {err}</p>}
      {!d && !err && <p className="muted">Loading…</p>}

      {d && (
        <>
          {/* Trust status: same switch as the lists, scoping this species' view. */}
          <div style={{ marginBottom: ".7rem" }}>
            <Segmented<string>
              ariaLabel="Detection status"
              value={status}
              onChange={(v) => setFilter("status", v === "all" ? "" : v)}
              options={[
                { value: "all", label: "All" },
                { value: "confirmed", label: "Confirmed" },
                { value: "suggested", label: "Suggested" },
                { value: "rejected", label: "Rejected" },
              ]}
            />
          </div>
          {/* Filters: location is one dimension among device + date range */}
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
            {d.occurrence != null && (
              <Stat label="Local likelihood" value={pct(d.occurrence)} />
            )}
          </StatGrid>

          <BirdRecordings sci={sci} common={common} initialMode={d.save_mode} clipCount={d.clip_count} onChange={reload} />

          {d.count === 0 && <p className="muted">No detections for this filter.</p>}

          {d.count > 0 && (
            <>
              <section className="card" style={card}>
                <h3 style={h3}>Time of day{peak && peak.count > 0 && <span className="muted" style={{ fontWeight: 400, fontSize: ".85rem" }}> · peak {hh(peak.hour)}</span>}</h3>
                <Chart data={d.hourly} x="hour" fill={CHART_SERIES[0]} xfmt={(h: number) => String(h)} interval={2} labelFmt={hh} height={130} />
              </section>

              <section className="card" style={card}>
                <h3 style={h3}>Each day heard ({d.by_day.length})</h3>
                <div style={{ display: "flex", flexDirection: "column" }}>
                  {d.by_day.map((day) => (
                    <div key={day.date} style={{ display: "flex", alignItems: "center", gap: ".8rem", padding: ".55rem 0", borderTop: "1px solid var(--border)" }}>
                      <div style={{ minWidth: 86 }}>
                        <div style={{ fontWeight: 600, fontSize: ".9rem" }}>{day.date.slice(5)}</div>
                        <div className="muted" style={{ fontSize: ".75rem" }}>{day.count}×</div>
                      </div>
                      <div style={{ flex: 1 }}><HourSpark hourly={day.hourly} fill={CHART_SERIES[1]} /></div>
                    </div>
                  ))}
                </div>
              </section>

              <section className="card" style={card}>
                <h3 style={h3}>Recent</h3>
                <ul style={{ listStyle: "none", padding: 0, margin: 0, maxHeight: 360, overflowY: "auto" }}>
                  {d.recent.map((r) => {
                    const title = [r.location, r.device].filter(Boolean).join(" · ");
                    return (
                      <li key={r.id}>
                        <div title={title} style={{ display: "flex", alignItems: "center", gap: ".5rem", padding: ".4rem 0", borderTop: "1px solid var(--border)", fontSize: ".84rem" }}>
                          <span className="mono muted" style={{ minWidth: 104, flexShrink: 0 }}>{r.at}</span>
                          <span style={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
                            {!locId && r.location && (
                              <Link to={`/apps/birds/location?id=${r.location_id}`} className="muted" style={{ display: "inline-flex", alignItems: "center", gap: ".25rem", textDecoration: "none" }}>
                                <MapPin size={11} /> {r.location}
                              </Link>
                            )}
                            {r.device && <span className="muted" style={{ marginLeft: r.location && !locId ? ".5rem" : 0, display: "inline-flex", alignItems: "center", gap: ".25rem", fontSize: ".78rem" }}><Smartphone size={10} /> {r.device}</span>}
                          </span>
                          {r.status !== "confirmed" && <span className="pill neutral" style={{ flexShrink: 0, textTransform: "capitalize" }}>{r.status}</span>}
                          {r.recording_id && <PlayButton recId={r.recording_id} size={24} />}
                          <span className="pill neutral" style={{ flexShrink: 0 }}>{pct(r.confidence)}</span>
                          {r.status !== "confirmed" && (
                            <button type="button" className="bird-act ok" title="Confirm this detection"
                              onClick={() => actEvent(r.id, "confirmed")}><Check size={14} /></button>
                          )}
                          {r.status !== "rejected" && (
                            <button type="button" className="bird-act danger" title="Reject this detection"
                              onClick={() => actEvent(r.id, "rejected")}><Ban size={14} /></button>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </section>
            </>
          )}
        </>
      )}
    </div>
  );
}

const card: React.CSSProperties = { padding: "1rem", marginBottom: "1rem" };
const h3: React.CSSProperties = { margin: "0 0 .4rem", fontSize: "1rem" };

// Per-bird save preference + this bird's saved clips. Deliberately separate from
// the confirm/reject trust controls: changing the save mode or deleting clips
// never changes a detection's status, and vice-versa.
type Clip = { id: number; at: string; location: string | null; bytes: number; confidence: number; pinned: boolean; protected: boolean };
type SaveMode = "default" | "keep" | "never";

function BirdRecordings({ sci, common, initialMode, clipCount, onChange }: {
  sci: string; common: string; initialMode: SaveMode; clipCount: number; onChange: () => void;
}) {
  const [mode, setMode] = useState<SaveMode>(initialMode);
  const [globalSave, setGlobalSave] = useState<boolean | null>(null);
  const [clips, setClips] = useState<Clip[] | null>(null);
  const [open, setOpen] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [confirmDel, setConfirmDel] = useState<Clip | null>(null);

  // Reflect fresh server state on parent reload.
  useEffect(() => { setMode(initialMode); }, [initialMode]);
  // The global default decides what "Default" means for this bird.
  useEffect(() => {
    jget<{ settings: { rec_save: boolean } }>("/recordings/settings")
      .then((d) => setGlobalSave(d.settings.rec_save)).catch(() => {});
    const onCh = () => jget<{ settings: { rec_save: boolean } }>("/recordings/settings")
      .then((d) => setGlobalSave(d.settings.rec_save)).catch(() => {});
    window.addEventListener("bird-settings-change", onCh);
    return () => window.removeEventListener("bird-settings-change", onCh);
  }, []);

  // Optimistic: set the control immediately, then persist (no first-click lag).
  const choose = useCallback((m: SaveMode) => {
    setMode(m);
    const fd = new FormData(); fd.append("sci", sci); fd.append("save_mode", m);
    fetch(`${API}/species/recording`, { method: "POST", body: fd })
      .then((r) => r.json())
      .then((d: { save_mode: SaveMode }) => setMode(d.save_mode))
      .then(() => { window.dispatchEvent(new CustomEvent("bird-settings-change")); onChange(); })
      .catch(() => {});
  }, [sci, onChange]);

  const loadClips = useCallback(() => {
    jget<{ items: Clip[] }>(`/recordings?sci=${encodeURIComponent(sci)}&limit=200`).then((d) => setClips(d.items)).catch(() => setClips([]));
  }, [sci]);

  const toggleOpen = () => { const n = !open; setOpen(n); if (n && !clips) loadClips(); };

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

  const defaultHint = globalSave == null ? "follow the app default"
    : globalSave ? "saving (app default is on)" : "not saving (app default is off)";
  const modeHint: Record<SaveMode, string> = {
    default: `Follow the app-wide setting — currently ${defaultHint}.`,
    keep: `Always save ${common}'s clips and never auto-delete them to free space.`,
    never: `Never save ${common}'s clips, even when the app default is on.`,
  };

  return (
    <section className="card" style={card}>
      <h3 style={{ ...h3, display: "flex", alignItems: "center", gap: ".4rem" }}>
        <Mic size={17} strokeWidth={1.8} /> Recordings
      </h3>

      <div style={{ fontWeight: 500, marginBottom: ".4rem", fontSize: ".9rem" }}>Save clips of this bird</div>
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
      <div className="muted" style={{ fontSize: ".8rem", marginTop: ".45rem" }}>{modeHint[mode]}</div>

      <div style={{ display: "flex", alignItems: "center", gap: ".6rem", marginTop: ".9rem", flexWrap: "wrap" }}>
        <button className="btn sm" onClick={toggleOpen} disabled={clipCount === 0}>
          {clipCount === 0 ? "No clips saved yet" : open ? "Hide clips" : `Show ${clipCount} saved clip${clipCount === 1 ? "" : "s"}`}
        </button>
        {clipCount > 0 && (
          <button className="bird-act danger" title="Delete all saved clips for this bird" onClick={() => setConfirmClear(true)}>
            <Trash2 size={15} />
          </button>
        )}
      </div>

      {open && clips && clips.length > 0 && (
        <div style={{ marginTop: ".6rem", display: "flex", flexDirection: "column" }}>
          {clips.map((c) => (
            <div key={c.id} style={{ display: "flex", alignItems: "center", gap: ".6rem", padding: ".45rem 0", borderTop: "1px solid var(--border)", fontSize: ".84rem" }}>
              <PlayButton recId={c.id} size={26} />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span className="mono muted">{c.at}</span>
                {c.location && <span className="muted" style={{ marginLeft: ".5rem", display: "inline-flex", alignItems: "center", gap: ".25rem" }}><MapPin size={10} /> {c.location}</span>}
              </span>
              <span className="muted" style={{ fontSize: ".74rem" }}>{fmtBytes(c.bytes)}</span>
              <span className="pill neutral">{pct(c.confidence)}</span>
              <button type="button" className={`bird-act${c.pinned ? " ok" : ""}`} onClick={() => pin(c)}
                title={c.pinned ? "Pinned — protected. Click to unpin." : "Pin — keep this recording (deleted last)"}>
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
