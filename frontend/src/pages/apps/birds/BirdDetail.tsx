import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  MapPin, Smartphone, ExternalLink, Mic, Pin, PinOff, Trash2,
  Check, Ban, CircleDashed, ChevronDown,
} from "lucide-react";
import {
  useCrumbTrail, type Crumb, ConfirmDialog, Segmented, formatBytes,
  Button, Notice, Skeleton, StateBlock,
} from "@workshop/ui";
import { useBirdInfo } from "./birdinfo";
import { pct, conf, Stat, StatGrid } from "./ui";
import ActivityCard from "./ActivityCard";
import BirdsHeader from "./BirdsHeader";
import { useBirdsScope } from "./scope";
import { PlayButton, useRecordingsSettings } from "./recordings";
import {
  birdsKeys, fetchBirdDetail, fetchRecordings,
  useClearSpeciesRecordings, useDeleteRecording, usePinRecording,
  useSetDetectionStatus, useSetSpeciesSaveMode,
  type DetectionStatus, type SpeciesSaveMode,
} from "./queries";

// One species' page: photo + description (client-side reference lookup), stats,
// the same activity charts as the rest of the app (NOT subject to the status
// filter), and the individual detections. The detections list is where you
// filter by confirmed/suggested/rejected and re-classify single events; each row
// links into its device and location.
//
// THE FIVE PARAMETERS ARE IN THE KEY. `/bird` takes sci, status, location_id,
// device and days, and all five used to live in a `useCallback` closure behind a
// `usePoll`, with a companion effect re-running the read whenever that closure
// changed. Nothing distinguished two requests, so switching the time window or
// the status filter while a slow request was in flight could land the OLD
// filter's answer on top of the new one. A different URL is a different cache
// entry now, and the stale one has nowhere to land.

const REFRESH_MS = 8000;
/** The newest N clips of one bird. In the key; the recordings page asks for 300. */
const CLIP_LIMIT = 200;

type DetStatus = "all" | "confirmed" | "suggested" | "rejected";

type LocOpt = { id: number; name: string; count: number };
type DevOpt = { token: string; name: string; count: number };
type DetRow = {
  id: number; at: string; confidence: number; occurrence: number | null;
  status: DetectionStatus;
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
  save_mode: SpeciesSaveMode;
  clip_count: number; list_count: number;
  recent: DetRow[];
};

export default function BirdDetail() {
  const [params] = useSearchParams();
  const sci = params.get("sci") || "";
  // A DIFFERENT SPECIES IS A DIFFERENT RECORD, so it gets a different component
  // instance. `placeholderData: keepPreviousData` below is what stops a filter
  // change from blanking the page, and it must not span a species change: it
  // would paint one bird's stats, clips and detections under another bird's
  // name for as long as the new read takes. Remounting on `sci` is the one line
  // that keeps "keep the rows on screen" honest.
  return <BirdBody key={sci} sci={sci} />;
}

function BirdBody({ sci }: { sci: string }) {
  const [params, setParams] = useSearchParams();
  // Bird detail uses the global Location · Device · Time scope (no local pickers).
  const { selected, deviceTok, days, setDevice } = useBirdsScope();
  const locationId = selected?.kind === "loc" && selected.id != null ? selected.id : null;
  // Detection-list filter (page-local, not scope). Falls back to the `status` a
  // species link carried in (e.g. tapping a Suggested species lands on it).
  const status = (params.get("dstatus") || params.get("status") || "all") as DetStatus;
  const info = useBirdInfo(sci || null, null);

  const detailParams = { sci, status, locationId, device: deviceTok, days };
  const q = useQuery({
    queryKey: birdsKeys.birdDetail(detailParams),
    queryFn: () => fetchBirdDetail<Detail>(detailParams),
    enabled: !!sci,
    // Owns its own cadence, so nothing sits stale over it and a failed tick is
    // followed by the next tick rather than a retry, as the poll it replaces
    // did. There is NO companion effect: changing a filter changes the key and
    // the query refetches by itself. Porting the old one would have meant two
    // requests and a blank frame between them.
    refetchInterval: REFRESH_MS,
    staleTime: 0,
    retry: false,
    placeholderData: keepPreviousData,
  });
  const d = q.data;
  const err = q.isError ? String(q.error) : null;

  // One mutation for the whole list, so one line reports a rejected write. Each
  // row used to own a `busy` flag and a `.finally`, which swallowed the failure
  // entirely: a re-classify the server refused looked exactly like one it took.
  const setStatus = useSetDetectionStatus();

  // Only the detection-status filter is page-local now; Location/Device/Time come
  // from the global scope. Always write it (including "all") and drop any legacy
  // `status` a species link carried in, so "All" clears the inherited scope.
  const setStatusFilter = (val: string) => {
    const p = new URLSearchParams(params);
    p.set("dstatus", val);
    p.delete("status");
    setParams(p);
  };

  const common = d?.common_name || params.get("common") || info?.common_name || sci;
  const place = locationId != null ? d?.locations.find((l) => l.id === locationId)?.name : null;

  const trail: Crumb[] = [
    { label: "Apps", to: "/apps" },
    { label: "Birds", to: "/apps/birds" },
  ];
  if (locationId != null && place) trail.push({ label: place, to: `/apps/birds?source=loc:${locationId}` });
  trail.push({ label: common });
  useCrumbTrail(trail);

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

      {/* Arriving with no `sci` used to sit on "Loading…" forever: the read was
          disabled and nothing said why. */}
      {!sci && (
        <StateBlock
          title="No species selected"
          hint="Open a bird from the species list, or from a detection row."
        />
      )}

      {sci && err && !d && (
        <StateBlock
          variant="error"
          title="Couldn't load this bird"
          hint={<code>GET /api/birds/bird: {err}</code>}
          action={<Button size="sm" onClick={() => void q.refetch()}>Retry</Button>}
        />
      )}

      {sci && err && d && (
        <div style={{ marginBottom: ".7rem" }}>
          <Notice tone="warn" action={<Button size="sm" variant="ghost" onClick={() => void q.refetch()}>Retry</Button>}>
            This page is not refreshing.
          </Notice>
        </div>
      )}

      {sci && !d && !err && (
        <div style={{ display: "grid", gap: ".7rem" }}>
          {[0, 1, 2].map((i) => <Skeleton key={i} style={{ height: 96 }} />)}
        </div>
      )}

      {d && (
        <>
          <StatGrid>
            <Stat label="Detections" value={d.count.toLocaleString()} />
            <Stat label="Days heard" value={d.days_seen} />
            <Stat label="Best" value={pct(d.max_conf)} />
            <Stat label="Avg" value={pct(d.avg_conf)} />
            {d.occurrence != null && <Stat label="Local likelihood" value={pct(d.occurrence)} />}
          </StatGrid>

          <BirdRecordings sci={sci} common={common} mode={d.save_mode} clipCount={d.clip_count} />

          {d.count > 0 && <ActivityCard daily={d.by_day} hourly={d.hourly} />}

          <section className="card" style={card}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: ".6rem", flexWrap: "wrap", marginBottom: ".5rem" }}>
              <h3 style={{ ...h3, margin: 0 }}>Detections ({d.list_count ?? d.recent.length})</h3>
              <Segmented<DetStatus>
                ariaLabel="Filter detections by status"
                value={status}
                onChange={(v) => setStatusFilter(v)}
                options={[
                  { value: "all", label: "All" },
                  { value: "confirmed", label: "Confirmed" },
                  { value: "suggested", label: "Suggested" },
                  { value: "rejected", label: "Rejected" },
                ]}
              />
            </div>

            {setStatus.isError && (
              <div style={{ marginBottom: ".5rem" }}>
                <Notice
                  tone="error"
                  detail={String(setStatus.error)}
                  action={<Button size="sm" variant="ghost" onClick={() => setStatus.reset()}>Dismiss</Button>}
                >
                  Could not change that detection.
                </Notice>
              </div>
            )}

            {d.recent.length === 0
              ? (
                <StateBlock
                  title={status === "all" ? "No detections in this range" : `No ${status} detections`}
                  hint="Change the filter above, or widen the time window in the scope bar."
                />
              )
              : (
                <div>
                  {d.recent.map((r) => (
                    <DetectionRow key={r.id} r={r} hideLoc={locationId != null}
                      busy={setStatus.isPending && setStatus.variables?.id === r.id}
                      onSetStatus={(s) => setStatus.mutate({ id: r.id, status: s })}
                      onDevice={setDevice} />
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
function DetectionRow({ r, hideLoc, busy, onSetStatus, onDevice }: {
  r: DetRow; hideLoc: boolean; busy: boolean;
  onSetStatus: (status: DetectionStatus) => void; onDevice: (token: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const set = (status: DetectionStatus) => {
    if (status === r.status || busy) return;
    onSetStatus(status);
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
//
// NOTHING HERE IS MIRRORED IN LOCAL STATE ANY MORE. The mode was a `useState`
// re-seeded from its prop by an effect (two owners of one fact); the app-wide
// save default was a private fetch plus a window-event subscription; the clips
// were a `useState` filled by an effect that also blanked it. All three are the
// server's, read from the entries that own them.
type Clip = { id: number; at: string; location: string | null; bytes: number; confidence: number; pinned: boolean; protected: boolean };

const NO_CLIPS: Clip[] = [];

function BirdRecordings({ sci, common, mode, clipCount }: {
  sci: string; common: string; mode: SpeciesSaveMode; clipCount: number;
}) {
  const [confirmClear, setConfirmClear] = useState(false);
  const [confirmDel, setConfirmDel] = useState<Clip | null>(null);

  // The shared settings entry, read here only to say what "Default" means.
  const settings = useRecordingsSettings();
  const globalSave = settings.data?.settings.rec_save ?? null;

  const clipParams = { sci, limit: CLIP_LIMIT };
  const clipsQ = useQuery({
    queryKey: birdsKeys.recordings(clipParams),
    queryFn: () => fetchRecordings<{ items: Clip[] }>(clipParams),
    // Nothing to ask for until the bird has clips, and the count arrives in the
    // page's own payload. `enabled` is the whole of the effect that used to fire
    // this read and blank the list on the way.
    enabled: clipCount > 0,
    retry: false,
  });
  const clips = clipsQ.data?.items ?? NO_CLIPS;

  const saveMode = useSetSpeciesSaveMode();
  // What is in flight while it is in flight, and the server's answer as soon as
  // there is one. No mirror, so no re-seeding effect and no way for the control
  // to disagree with the page it sits on.
  const shownMode = saveMode.isPending && saveMode.variables ? saveMode.variables.mode : mode;

  const pin = usePinRecording();
  const del = useDeleteRecording();
  const clear = useClearSpeciesRecordings();
  const writeErr = saveMode.error ?? pin.error ?? del.error ?? clear.error;
  const resetWrites = () => { saveMode.reset(); pin.reset(); del.reset(); clear.reset(); };

  const defaultState = globalSave == null ? "the app default" : globalSave ? "saving (app default on)" : "not saving (app default off)";
  const hint = `Default: follow ${defaultState}. Always: keep every clip, never auto-delete. Never: skip this bird.`;

  return (
    <section className="card" style={card}>
      <div style={{ display: "flex", alignItems: "center", gap: ".6rem", flexWrap: "wrap" }}>
        <h3 style={{ ...h3, margin: 0, display: "flex", alignItems: "center", gap: ".4rem" }}>
          <Mic size={17} strokeWidth={1.8} /> Recordings
        </h3>
        <span title={hint} style={{ display: "inline-flex" }}>
          <Segmented<SpeciesSaveMode>
            ariaLabel="Save recordings for this bird"
            value={shownMode}
            onChange={(m) => saveMode.mutate({ sci, mode: m })}
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

      {writeErr && (
        <div style={{ marginTop: ".6rem" }}>
          <Notice
            tone="error"
            detail={String(writeErr)}
            action={<Button size="sm" variant="ghost" onClick={resetWrites}>Dismiss</Button>}
          >
            That did not go through.
          </Notice>
        </div>
      )}

      {clipCount === 0 ? (
        <p className="muted" style={{ fontSize: ".82rem", margin: ".5rem 0 0" }}>
          No clips saved yet. Set this bird to Always to keep the audio of every detection.
        </p>
      ) : (
        <>
          {clipsQ.isPending && (
            <div style={{ marginTop: ".5rem", display: "grid", gap: ".35rem" }}>
              {[0, 1].map((i) => <Skeleton key={i} style={{ height: 34 }} />)}
            </div>
          )}

          {/* A failed clip read used to `.catch(() => setClips([]))`, so it was
              indistinguishable from a bird with no clips. */}
          {clipsQ.isError && (
            <div style={{ marginTop: ".5rem" }}>
              <Notice tone="error" action={<Button size="sm" variant="ghost" onClick={() => void clipsQ.refetch()}>Retry</Button>}>
                Could not load the saved clips.
              </Notice>
            </div>
          )}

          {clipsQ.isSuccess && clips.length === 0 && (
            <p className="muted" style={{ fontSize: ".82rem", margin: ".5rem 0 0" }}>
              These clips have been removed.
            </p>
          )}

          {clips.length > 0 && (
            <div style={{ marginTop: ".5rem", display: "flex", flexDirection: "column", maxHeight: 320, overflowY: "auto" }}>
              {clips.map((c) => (
                <div key={c.id} style={{ display: "flex", alignItems: "center", gap: ".6rem", padding: ".45rem 0", borderTop: "1px solid var(--border)", fontSize: ".84rem" }}>
                  <PlayButton recId={c.id} size={26} />
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span className="mono muted">{c.at}</span>
                    {c.location && <span className="muted" style={{ marginLeft: ".5rem", display: "inline-flex", alignItems: "center", gap: ".25rem" }}><MapPin size={10} /> {c.location}</span>}
                  </span>
                  <span className="muted" style={{ fontSize: ".74rem" }}>{formatBytes(c.bytes)}</span>
                  <span className="muted" style={{ fontSize: ".78rem" }}>{conf(c.confidence)}</span>
                  <button type="button" className={`bird-act${c.pinned ? " ok" : ""}`} disabled={pin.isPending}
                    onClick={() => pin.mutate({ id: c.id, pinned: !c.pinned })}
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

          {/* The list is capped, so say what is not in it. */}
          {clipCount > clips.length && clips.length > 0 && (
            <p className="muted" style={{ fontSize: ".76rem", margin: ".5rem 0 0" }}>
              Showing the newest {clips.length} of {clipCount} clips.
            </p>
          )}
        </>
      )}

      <ConfirmDialog
        open={confirmClear}
        title={`Clear all recordings for ${common}?`}
        body={`Permanently delete all ${clipCount} saved clip${clipCount === 1 ? "" : "s"} of ${common}, including pinned ones. Detections and stats are unaffected. This cannot be undone.`}
        confirmLabel="Delete all"
        onCancel={() => setConfirmClear(false)}
        onConfirm={() => { clear.mutate(sci); setConfirmClear(false); }}
      />
      <ConfirmDialog
        open={!!confirmDel}
        title="Delete recording?"
        body={confirmDel ? `Permanently delete this clip from ${confirmDel.at}. This cannot be undone.` : ""}
        confirmLabel="Delete"
        onCancel={() => setConfirmDel(null)}
        onConfirm={() => {
          if (!confirmDel) return;
          // The dialog closes and the outcome is reported behind it: a failure
          // has nowhere to be seen inside a dialog that has gone.
          del.mutate(confirmDel.id);
          setConfirmDel(null);
        }}
      />
    </section>
  );
}
