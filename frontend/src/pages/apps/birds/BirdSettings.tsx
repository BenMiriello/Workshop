import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bird, Filter, Target, Check, History, Mic, HardDrive, Waypoints, User, LogOut, RefreshCw } from "lucide-react";
import { useShowNonBird } from "./settings";
import BirdsHeader from "./BirdsHeader";
import { Notice, Toggle, Button, Segmented, timeAbsoluteShort } from "@workshop/ui";
import { API, pct } from "./ui";
import {
  authKeys, birdsKeys, fetchAnalyzeSettings, fetchAtlasSettings, fetchAtlasStatus,
  fetchRecordingsSettings, fetchSession, rebuildAtlas, recomputeDetections,
  saveAnalyzeSettings, saveAtlasSettings, saveRecordingsSettings,
  type RecordingsSettings, type SettingsRead,
} from "./queries";

// SIX HAND-ROLLED SERVER READS USED TO LIVE IN THIS FILE, one `useState` +
// `useEffect` per section, and two of them read the SAME endpoint. They are all
// cache entries now, on the keys in `queries.ts` -- which is also how the
// recordings toggle here and the ones on the Recordings page and a bird's page
// came to be the same fact rather than three copies of it.
//
// WHAT IS *NOT* IN THE CACHE, deliberately: the accuracy form's draft. A
// half-moved slider is not a server fact and must not be one; a background
// re-read that landed on it would drag the control out from under the operator
// mid-drag. The draft is local, seeded once, and the server value beside it is
// what Save writes over and what "unsaved" is measured against.

// Settings for the Birds app. The non-bird filter is display-only: BirdNET keeps
// detecting and storing every class (so nothing is lost and the toggle is
// reversible after the fact) — this just decides whether non-bird hits show up
// in the live feed, the per-location species lists, and the activity charts.

const NON_BIRD_EXAMPLES = [
  "Human (voice, whistle, non-vocal)", "Engine", "Siren", "Power tools",
  "Fireworks", "Gun", "Dog", "Coyote / wolf", "Frogs & toads", "Crickets",
  "Environmental / Noise",
];

/**
 * WHO IS SIGNED IN. One read, one entry, two readers.
 *
 * This endpoint was fetched TWICE in this file: once for the account card and
 * once for `useIsAdmin`. Two requests for one answer, and they could not even
 * be relied on to agree -- a failure meant "hide the account card" to one and
 * "you are the admin" to the other, so a flaky moment could show a member the
 * admin-only controls while telling them they were not signed in.
 *
 * It does not retry: a 401 or a 404 is the server ANSWERING (the tailnet
 * Workshop build has no session concept at this path), and a network failure
 * means offline, where the honest fallback is "treat me as the operator" --
 * which is what both hand-rolled reads did in their `.catch`.
 */
function useSession() {
  return useQuery({
    queryKey: authKeys.session(),
    queryFn: () => fetchSession(),
    retry: false,
  });
}

// Account card. Only renders in the standalone app (where /api/auth/me resolves a
// session); on the tailnet Workshop app there is no session at that path, so this
// stays hidden.
function AccountSection() {
  const user = useSession().data?.user;
  if (!user) return null; // loading, no auth, or unreachable -> nothing

  const signOut = async () => {
    try { await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }); } catch { /* ignore */ }
    window.location.href = "/";
  };

  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
      <h3 style={{ margin: "0 0 .6rem", display: "flex", alignItems: "center", gap: ".4rem", fontSize: "1rem" }}>
        <User size={17} strokeWidth={1.8} /> Account
      </h3>
      <div style={{ display: "flex", alignItems: "center", gap: ".7rem", flexWrap: "wrap" }}>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ fontWeight: 500 }}>{user.name || user.email}</span>
          <span className="muted" style={{ display: "block", fontSize: ".84rem" }}>Signed in as {user.email}</span>
        </span>
        <Button onClick={signOut}><LogOut size={15} /> Sign out</Button>
      </div>
    </section>
  );
}

// Admin vs member, off the SAME entry the account card reads. On the tailnet
// Workshop app there is no session at that path, so we treat "no auth" as the
// operator (admin). In the standalone, a 'member' is not admin. `null` while the
// answer is still outstanding, so admin-only sections don't flash for a member.
function useIsAdmin(): boolean | null {
  const q = useSession();
  if (q.isPending) return null;
  return q.data?.user ? q.data.user.role === "admin" : true;
}

export default function BirdSettings() {
  const [showNonBird, setShowNonBird] = useShowNonBird();
  const isAdmin = useIsAdmin();

  return (
    <div>
      <BirdsHeader title="Settings" />

      <AccountSection />

      <AccuracySettings />

      <RecordingSettings />

      <AtlasSettings />

      {/* Non-bird filter is an admin-only control; members never see it. */}
      {isAdmin && (
        <>
          <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
            <h3 style={{ margin: "0 0 .5rem", display: "flex", alignItems: "center", gap: ".4rem", fontSize: "1rem" }}>
              <Filter size={17} strokeWidth={1.8} /> Non-bird detections
            </h3>

            <label style={{ display: "flex", alignItems: "flex-start", gap: ".7rem", cursor: "pointer" }}>
              <span style={{ paddingTop: ".15rem" }}>
                <Switch on={showNonBird} onChange={setShowNonBird} />
              </span>
              <span>
                <span style={{ fontWeight: 500 }}>Show non-bird detections</span>
                <span className="muted" style={{ display: "block", fontSize: ".84rem", marginTop: ".15rem" }}>
                  {showNonBird
                    ? "Non-bird classes are shown alongside birds, tagged so you can tell them apart."
                    : "Only birds are shown. Non-bird classes are still detected and stored. Turn this on to reveal them."}
                </span>
              </span>
            </label>

            <div style={{ marginTop: ".9rem" }}>
              <div className="muted" style={{ fontSize: ".75rem", marginBottom: ".35rem", display: "flex", alignItems: "center", gap: ".3rem" }}>
                <Bird size={13} /> Counted as non-bird
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: ".35rem" }}>
                {NON_BIRD_EXAMPLES.map((n) => (
                  <span key={n} className="pill muted" style={{ fontSize: ".78rem" }}>{n}</span>
                ))}
              </div>
            </div>
          </section>

          <p className="muted" style={{ fontSize: ".82rem" }}>
            Detection always runs on everything the microphone hears. This only changes
            what is shown.
          </p>
        </>
      )}
    </div>
  );
}

// Design-system toggle (white thumb, accent track, focus ring all live in
// components.css). Thin wrapper keeps the existing on/onChange call sites.
function Switch({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return <Toggle checked={on} onChange={onChange} />;
}

/**
 * A settings section whose read FAILED. All three of these used to return
 * `null` on a failed read -- the section simply was not on the page, with the
 * failure swallowed in a `.catch(() => {})` and one of them carrying a comment
 * that said so ("offline: section just stays in loading"). A control that is
 * missing and a control that does not exist look identical, so the honest
 * reading of a Birds Settings page with the backend down was "this build has no
 * accuracy settings".
 *
 * A LINE, NOT A REGION, and the heading is why. The section keeps its title, so
 * the screen still says WHICH settings could not be read and one thing went
 * wrong beside that. Three stacked 120px dashed panels for one backend being
 * down is the exact shape `frontend/CLAUDE.md` calls out as the wrong answer.
 */
function SectionFailed({ title, icon, where, onRetry }: {
  title: string; icon: React.ReactNode; where: string; onRetry: () => void;
}) {
  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
      <h3 style={{ margin: "0 0 .6rem", display: "flex", alignItems: "center", gap: ".4rem", fontSize: "1rem" }}>
        {icon} {title}
      </h3>
      <Notice tone="error" detail={where}
        action={<Button size="sm" variant="ghost" onClick={onRetry}>Retry</Button>}>
        Couldn't load these settings.
      </Notice>
    </section>
  );
}

type Smart = {
  min_conf: number; sensitivity: number; sf_thresh: number;
  new_species_hits: number; prior_floor: number;
};

// Range-filter strictness as plain presets — raw eBird occurrence cutoffs are
// unintuitive. Lower keeps more rare-but-possible local species; higher is
// stricter. "Balanced" matches the backend default.
type RangeKey = "lenient" | "balanced" | "strict";
const RANGE_PRESETS: { key: RangeKey; label: string; value: number; hint: string }[] = [
  { key: "lenient", label: "Lenient", value: 0.001,
    hint: "Allows rare-but-possible local visitors (owls, seasonal vagrants). Catches the most, including some long shots." },
  { key: "balanced", label: "Balanced", value: 0.01,
    hint: "Ignores species that are unlikely at your location this time of year. Recommended for most setups." },
  { key: "strict", label: "Strict", value: 0.05,
    hint: "Only species clearly expected at your location right now. Fewest false matches." },
];

function presetKey(v: number): RangeKey {
  return RANGE_PRESETS.reduce((best, p) =>
    Math.abs(p.value - v) < Math.abs(best.value - v) ? p : best, RANGE_PRESETS[1]).key;
}
const presetValue = (k: RangeKey) => RANGE_PRESETS.find((p) => p.key === k)!.value;

// Recordings: the app-wide default for whether detection audio is SAVED. The mic
// always analyzes; this only controls keeping the clip. Per-bird overrides
// (Always / Never) live on each bird's page; storage + saved clips are managed on
// the Recordings page. A single boolean → commit on change.
function RecordingSettings() {
  const qc = useQueryClient();
  // The SAME key the Recordings page and a bird's page read. One entry, so this
  // toggle and the copy of it over there cannot disagree.
  const q = useQuery({
    queryKey: birdsKeys.recordingsSettings(),
    queryFn: () => fetchRecordingsSettings<SettingsRead<RecordingsSettings>>(),
  });

  // OPTIMISTIC, AND CANCELLED-IN. The toggle moves at once because a boolean is
  // cheap and reversible, but a read already in flight has to be dropped first
  // or it lands afterwards carrying the PRE-write value and flips the switch
  // back. Same shape as `useSetHome` in `queries.ts`, and the same reason.
  //
  // The write REPLACED a `window.dispatchEvent(new CustomEvent(
  // "bird-settings-change"))`: a hand-rolled invalidation that every interested
  // page had to remember to subscribe to, telling them all to re-read
  // everything. An invalidation of the recordings prefix says which fact moved.
  const toggle = useMutation({
    mutationFn: (v: boolean) => saveRecordingsSettings<RecordingsSettings>({ recSave: v }),
    onMutate: async (v: boolean) => {
      await qc.cancelQueries({ queryKey: birdsKeys.recordingsSettings() });
      const prev = qc.getQueryData<SettingsRead<RecordingsSettings>>(birdsKeys.recordingsSettings());
      if (prev) qc.setQueryData(birdsKeys.recordingsSettings(), { settings: { ...prev.settings, rec_save: v } });
      return { prev };
    },
    onSuccess: (d) => { qc.setQueryData(birdsKeys.recordingsSettings(), d); },
    onError: (_e, _v, ctx) => { qc.setQueryData(birdsKeys.recordingsSettings(), ctx?.prev); },
    // Whether clips are being saved changes the storage picture and the list of
    // what is on disk, so the whole recordings prefix catches up.
    onSettled: () => { void qc.invalidateQueries({ queryKey: birdsKeys.recordingsAll() }); },
  });

  if (q.isError) {
    return <SectionFailed title="Recordings" icon={<Mic size={17} strokeWidth={1.8} />}
      where={`GET ${API}/recordings/settings: ${String(q.error)}`} onRetry={() => void q.refetch()} />;
  }
  const save = q.data ? !!q.data.settings.rec_save : null;
  if (save == null) return null;
  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
      <h3 style={{ margin: "0 0 .5rem", display: "flex", alignItems: "center", gap: ".4rem", fontSize: "1rem" }}>
        <Mic size={17} strokeWidth={1.8} /> Recordings
      </h3>
      <label style={{ display: "flex", alignItems: "flex-start", gap: ".7rem", cursor: "pointer" }}>
        <span style={{ paddingTop: ".15rem" }}><Toggle checked={save} onChange={(v) => toggle.mutate(v)} /></span>
        <span>
          <span style={{ fontWeight: 500 }}>Save recordings of detections</span>
          <span className="muted" style={{ display: "block", fontSize: ".84rem", marginTop: ".15rem" }}>
            {save
              ? "Every bird detection's audio clip is saved by default. Set individual birds to Never on their page to skip them."
              : "Audio clips are not saved by default. Turn this on to save them all, or set individual birds to Always on their page."}
          </span>
        </span>
      </label>
      {/* The control is right here, so a rejected write is a line under it. */}
      {toggle.isError && (
        <div style={{ marginTop: ".7rem" }}>
          <Notice tone="error" detail={String(toggle.error)}
            action={<Button size="sm" variant="ghost" onClick={() => toggle.reset()}>Dismiss</Button>}>
            That did not save. The switch is back to what the server has.
          </Notice>
        </div>
      )}
      <p className="muted" style={{ fontSize: ".82rem", margin: ".9rem 0 0", display: "flex", alignItems: "center", gap: ".4rem" }}>
        <HardDrive size={14} />
        <Link to="/apps/birds/recordings" style={{ color: "var(--accent)", textDecoration: "none" }}>
          Manage storage &amp; saved clips
        </Link>
      </p>
    </section>
  );
}

// Independent of the recordings setting above: the Atlas keeps a few audio
// exemplars per acoustic cluster so each can be labelled by ear. Own toggle, own
// small budget — runs alongside or instead of the recordings save.
type AtlasStat = { embedded: number; clusters: number; labeled_clusters: number; last_build: string | null; version: number };
type AtlasCfg = { atlas_clip_save: boolean };

// How often to look for the rebuild to land, and how long to keep looking. The
// build is a detached batch job; the only thing that says it finished is the
// version moving, and if the job dies nothing ever says so, which is why the
// watch is bounded rather than open-ended.
const REBUILD_WATCH_MS = 2500;
const REBUILD_GIVE_UP_MS = 120_000;

function AtlasSettings() {
  const qc = useQueryClient();
  const cfgQ = useQuery({
    queryKey: birdsKeys.atlasSettings(),
    queryFn: () => fetchAtlasSettings<SettingsRead<AtlasCfg>>(),
  });

  // THE REBUILD WATCH, AS A STOP CONDITION RATHER THAN A TIMER. This was a
  // `setInterval` that re-read `/atlas/status` every 2.5s into a component-local
  // `useState`, plus a `setTimeout` to clear it -- a second, private copy of a
  // fact the Atlas page also polls, on its own cadence, and neither could see
  // the other. It is the SAME cache entry now, and the watch is a STOP
  // CONDITION on that entry: `refetchInterval` is asked again after every tick
  // and answers "keep watching" only while a rebuild we asked for has not
  // landed. The version moving ends it, and there is no timer to clear and
  // nothing to leak if this section unmounts mid-build.
  const [watch, setWatch] = useState<{ fromVersion: number } | null>(null);
  const statQ = useQuery({
    queryKey: birdsKeys.atlasStatus(),
    queryFn: () => fetchAtlasStatus<AtlasStat>(),
    retry: false,
    // No cadence at rest. It ticks only between "we asked for a rebuild" and
    // "the version moved", and the version moving IS the stop condition.
    refetchInterval: (q) => {
      if (!watch) return false;
      return (q.state.data?.version ?? watch.fromVersion) === watch.fromVersion
        ? REBUILD_WATCH_MS : false;
    },
  });
  const stat = statQ.data ?? null;
  // Landed = the version we started from is not the version we see now.
  const watching = watch != null && (stat?.version ?? watch.fromVersion) === watch.fromVersion;

  const rebuild = useMutation({
    mutationFn: rebuildAtlas,
    // Only watch if the job actually STARTED. The route answers 200 with
    // `{started: false}` when the builder is not installed, and the old code
    // treated that as a launch: the button spun for the full two minutes and
    // then gave up in silence, on a machine where a rebuild is not possible at
    // all. There is also nothing to watch for if we never learned the version
    // we are watching away from.
    onSuccess: (r) => { if (r.started && stat) setWatch({ fromVersion: stat.version }); },
  });
  const building = rebuild.isPending || watching;

  // The give-up bound. A one-shot timeout, not a poll: if the detached job never
  // reports, the button has to come back rather than say "Rebuilding…" forever.
  useEffect(() => {
    if (!watching) return;
    const id = window.setTimeout(() => setWatch(null), REBUILD_GIVE_UP_MS);
    return () => window.clearTimeout(id);
  }, [watching]);

  const fmtBuilt = (iso: string | null) => (iso ? timeAbsoluteShort(iso) || "never" : "never");

  const toggle = useMutation({
    mutationFn: (v: boolean) => saveAtlasSettings<AtlasCfg>({ clipSave: v }),
    onMutate: async (v: boolean) => {
      await qc.cancelQueries({ queryKey: birdsKeys.atlasSettings() });
      const prev = qc.getQueryData<SettingsRead<AtlasCfg>>(birdsKeys.atlasSettings());
      if (prev) qc.setQueryData(birdsKeys.atlasSettings(), { settings: { ...prev.settings, atlas_clip_save: v } });
      return { prev };
    },
    onSuccess: (d) => { qc.setQueryData(birdsKeys.atlasSettings(), d); },
    onError: (_e, _v, ctx) => { qc.setQueryData(birdsKeys.atlasSettings(), ctx?.prev); },
    // Replaces a `bird-settings-change` broadcast. The Atlas page's status line
    // reads `clip_save` to decide whether to offer "turn on Atlas clips", so
    // status is the one other entry this moves -- NOT the projection itself,
    // which a clip budget cannot change.
    onSettled: () => { void qc.invalidateQueries({ queryKey: birdsKeys.atlasStatus() }); },
  });

  if (cfgQ.isError) {
    return <SectionFailed title="Soundscape Atlas" icon={<Waypoints size={17} strokeWidth={1.8} />}
      where={`GET ${API}/atlas/settings: ${String(cfgQ.error)}`} onRetry={() => void cfgQ.refetch()} />;
  }
  const save = cfgQ.data ? !!cfgQ.data.settings.atlas_clip_save : null;
  if (save == null) return null;
  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
      <h3 style={{ margin: "0 0 .5rem", display: "flex", alignItems: "center", gap: ".4rem", fontSize: "1rem" }}>
        <Waypoints size={17} strokeWidth={1.8} /> Soundscape Atlas
      </h3>
      <label style={{ display: "flex", alignItems: "flex-start", gap: ".7rem", cursor: "pointer" }}>
        <span style={{ paddingTop: ".15rem" }}><Toggle checked={save} onChange={(v) => toggle.mutate(v)} /></span>
        <span>
          <span style={{ fontWeight: 500 }}>Save call-type exemplar clips</span>
          <span className="muted" style={{ display: "block", fontSize: ".84rem", marginTop: ".15rem" }}>
            Keeps a few audio clips per acoustic cluster (its own small budget) so you can label each
            cluster's call type by ear in the Atlas. Independent of the recordings setting above.
          </span>
        </span>
      </label>
      <div style={{ display: "flex", alignItems: "center", gap: ".7rem", flexWrap: "wrap", margin: "1rem 0 0", paddingTop: ".9rem", borderTop: "1px solid var(--border)" }}>
        <div style={{ minWidth: 0 }}>
          <span style={{ fontWeight: 500 }}>Rebuild the map</span>
          <span className="muted" style={{ display: "block", fontSize: ".84rem", marginTop: ".15rem" }}>
            {stat
              ? `${stat.embedded.toLocaleString()} calls · ${stat.clusters} call type${stat.clusters === 1 ? "" : "s"} · updated ${fmtBuilt(stat.last_build)}`
              : "Re-projects and re-clusters the acoustic map."} It rebuilds itself nightly; use this for an on-demand refresh.
          </span>
        </div>
        <span style={{ marginLeft: "auto" }}>
          <Button onClick={() => rebuild.mutate()} disabled={building}>
            <RefreshCw size={15} className={building ? "spin" : undefined} /> {building ? "Rebuilding…" : "Rebuild now"}
          </Button>
        </span>
      </div>
      {/* Both controls are right here, so a rejected write is a line, never a
          region. The rebuild POST used to be fire-and-forget (`.catch(() => {})`),
          so a builder that is not installed looked exactly like one that had
          started: the button spun for two minutes and then gave up in silence. */}
      {toggle.isError && (
        <div style={{ marginTop: ".7rem" }}>
          <Notice tone="error" detail={String(toggle.error)}
            action={<Button size="sm" variant="ghost" onClick={() => toggle.reset()}>Dismiss</Button>}>
            That did not save. The switch is back to what the server has.
          </Notice>
        </div>
      )}
      {(rebuild.isError || rebuild.data?.started === false) && (
        <div style={{ marginTop: ".7rem" }}>
          <Notice tone="error" detail={rebuild.error ? String(rebuild.error) : rebuild.data?.error}
            action={<Button size="sm" variant="ghost" onClick={() => rebuild.reset()}>Dismiss</Button>}>
            The rebuild did not start.
          </Notice>
        </div>
      )}
      <p className="muted" style={{ fontSize: ".82rem", margin: ".9rem 0 0", display: "flex", alignItems: "center", gap: ".4rem" }}>
        <Waypoints size={14} />
        <Link to="/apps/birds/atlas" style={{ color: "var(--accent)", textDecoration: "none" }}>
          Open the Soundscape Atlas
        </Link>
      </p>
    </section>
  );
}

// Detection accuracy: the knobs that stop BirdNET reporting birds it never heard.
// These are config writes, so they commit on an explicit Save (design rule 7).
function AccuracySettings() {
  const q = useQuery({
    queryKey: birdsKeys.analyzeSettings(),
    queryFn: () => fetchAnalyzeSettings<SettingsRead<Smart>>(),
  });
  // The form seeds its draft AT MOUNT, so it mounts once the settings are here
  // and not before. Same idiom as the inline editors on the Locations page: the
  // draft is seeded by mounting rather than by an effect, which is what makes
  // it impossible for a later re-read to reach in and re-seed it.
  if (q.isError) {
    return <SectionFailed title="Detection accuracy" icon={<Target size={17} strokeWidth={1.8} />}
      where={`GET ${API}/analyze/settings: ${String(q.error)}`} onRetry={() => void q.refetch()} />;
  }
  if (!q.data) return null;
  return <AccuracyForm settings={q.data.settings} />;
}

function AccuracyForm({ settings }: { settings: Smart }) {
  const qc = useQueryClient();
  // THE DRAFT IS LOCAL AND STAYS LOCAL. A config form is the one place a
  // background refetch must not reach: the operator is mid-drag on a slider and
  // the value on screen is theirs, not the server's, until they press Save.
  const [draft, setDraft] = useState<Smart>(settings);
  const [saved, setSaved] = useState(false);

  // Measured against the SERVER value, not against a frozen copy of it. If the
  // settings move under this screen, the form is genuinely out of step with the
  // server and "unsaved" is the true thing to say.
  const dirty = settings.min_conf !== draft.min_conf
    || presetKey(settings.sf_thresh) !== presetKey(draft.sf_thresh)
    || settings.new_species_hits !== draft.new_species_hits;

  const save = useMutation({
    mutationFn: () => saveAnalyzeSettings<Smart>({
      minConf: draft.min_conf,
      sfThresh: presetValue(presetKey(draft.sf_thresh)),
      newSpeciesHits: draft.new_species_hits,
    }),
    onSuccess: (d) => {
      qc.setQueryData(birdsKeys.analyzeSettings(), d);
      setDraft(d.settings);
      setSaved(true);
    },
  });

  // Re-scoring history hides or reveals detections EVERYWHERE, so this is the
  // one write in this file whose blast radius really is the whole app. It used
  // to say so by broadcasting `bird-settings-change` and hoping every page had
  // subscribed; it says so now by invalidating the app's prefix, which reaches
  // the pages that are mounted and marks the rest stale for when they are.
  const recompute = useMutation({
    mutationFn: recomputeDetections,
    onSuccess: () => { void qc.invalidateQueries({ queryKey: birdsKeys.all() }); },
  });
  const recomputed = recompute.data
    ? { hidden: recompute.data.hidden ?? 0, species: (recompute.data.species_hidden || []).length }
    : null;

  const curKey = presetKey(draft.sf_thresh);

  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
      <h3 style={{ margin: "0 0 .15rem", display: "flex", alignItems: "center", gap: ".4rem", fontSize: "1rem" }}>
        <Target size={17} strokeWidth={1.8} /> Detection accuracy
      </h3>
      <p className="muted" style={{ fontSize: ".82rem", margin: "0 0 1rem" }}>
        How cautious detection is. Stricter settings report fewer birds, and the ones
        they do report are far more trustworthy.
      </p>

      <div style={{ display: "grid", gap: "1.1rem" }}>
        <div>
          <label style={{ display: "flex", justifyContent: "space-between", fontWeight: 500, marginBottom: ".3rem" }}>
            <span>Minimum confidence</span>
            <span className="muted">{pct(draft.min_conf)}</span>
          </label>
          <input type="range" min={0.1} max={0.95} step={0.05} value={draft.min_conf}
            onChange={(e) => setDraft({ ...draft, min_conf: Number(e.target.value) })}
            style={{ width: "100%" }} />
          <div className="muted" style={{ fontSize: ".78rem" }}>
            How sure the model must be before a sound counts. 50–70% is a good range
            for a phone mic; higher cuts more false matches.
          </div>
        </div>

        <div>
          <div style={{ fontWeight: 500, marginBottom: ".15rem" }}>Location filter</div>
          <div className="muted" style={{ fontSize: ".78rem", marginBottom: ".55rem" }}>
            Uses your location and the time of year to ignore species that don't
            occur where you are. The strongest defense against impossible birds.
          </div>
          <Segmented<RangeKey>
            ariaLabel="Location filter strictness"
            options={RANGE_PRESETS.map((p) => ({ value: p.key, label: p.label }))}
            value={curKey}
            onChange={(k) => setDraft({ ...draft, sf_thresh: presetValue(k) })}
          />
          <div className="muted" style={{ fontSize: ".78rem", marginTop: ".5rem" }}>
            {RANGE_PRESETS.find((p) => p.key === curKey)?.hint}
          </div>
        </div>

        <div>
          <label style={{ display: "flex", justifyContent: "space-between", fontWeight: 500, marginBottom: ".3rem" }}>
            <span>New-species confirmations</span>
            <span className="muted">{draft.new_species_hits}×</span>
          </label>
          <input type="range" min={1} max={6} step={1} value={draft.new_species_hits}
            onChange={(e) => setDraft({ ...draft, new_species_hits: Number(e.target.value) })}
            style={{ width: "100%" }} />
          <div className="muted" style={{ fontSize: ".78rem" }}>
            How many times in a row a new species must be heard before it counts.
            Birds already seen here are recorded immediately.
          </div>
        </div>
      </div>

      {save.isError && (
        <div style={{ marginTop: "1rem" }}>
          <Notice tone="error" detail={String(save.error)}
            action={<Button size="sm" variant="ghost" onClick={() => save.reset()}>Dismiss</Button>}>
            These settings did not save. Nothing on the server changed.
          </Notice>
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: ".6rem", marginTop: "1rem" }}>
        <Button onClick={() => { setSaved(false); save.mutate(); }} disabled={!dirty || save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
        {saved && !dirty && (
          <span className="muted" style={{ fontSize: ".82rem", display: "flex", alignItems: "center", gap: ".3rem" }}>
            <Check size={14} /> Saved
          </span>
        )}
      </div>

      <div style={{ borderTop: "1px solid var(--border)", marginTop: "1.1rem", paddingTop: "1rem" }}>
        <div style={{ fontWeight: 500, marginBottom: ".15rem", display: "flex", alignItems: "center", gap: ".4rem" }}>
          <History size={15} strokeWidth={1.8} /> Clean up past detections
        </div>
        <div className="muted" style={{ fontSize: ".78rem", marginBottom: ".6rem" }}>
          Applies the location filter to everything already recorded. Impossible species
          are hidden, not deleted, and running it again can bring them back.
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: ".6rem", flexWrap: "wrap" }}>
          <Button variant="ghost" onClick={() => recompute.mutate()} disabled={recompute.isPending}>
            {recompute.isPending ? "Re-checking…" : "Re-apply to past detections"}
          </Button>
          {recomputed && (
            <span className="muted" style={{ fontSize: ".82rem" }}>
              {recomputed.hidden > 0
                ? `Hid ${recomputed.hidden} detection${recomputed.hidden === 1 ? "" : "s"} across ${recomputed.species} impossible species.`
                : "Nothing to hide. All past detections are plausible here."}
            </span>
          )}
        </div>
        {recompute.isError && (
          <div style={{ marginTop: ".7rem" }}>
            <Notice tone="error" detail={String(recompute.error)}
              action={<Button size="sm" variant="ghost" onClick={() => recompute.reset()}>Dismiss</Button>}>
              Could not re-check past detections.
            </Notice>
          </div>
        )}
      </div>
    </section>
  );
}
