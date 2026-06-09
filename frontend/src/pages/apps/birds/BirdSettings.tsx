import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { SlidersHorizontal, Bird, Filter, Target, Check, History, Mic, HardDrive, Waypoints } from "lucide-react";
import { useShowNonBird } from "./settings";
import { Toggle, Button, Segmented } from "../../../ui";
import { API, pct } from "./ui";

// Settings for the Birds app. The non-bird filter is display-only: BirdNET keeps
// detecting and storing every class (so nothing is lost and the toggle is
// reversible after the fact) — this just decides whether non-bird hits show up
// in the live feed, the per-location species lists, and the activity charts.

const NON_BIRD_EXAMPLES = [
  "Human (voice, whistle, non-vocal)", "Engine", "Siren", "Power tools",
  "Fireworks", "Gun", "Dog", "Coyote / wolf", "Frogs & toads", "Crickets",
  "Environmental / Noise",
];

export default function BirdSettings() {
  const [showNonBird, setShowNonBird] = useShowNonBird();

  return (
    <div>
      <h2 style={{ display: "flex", alignItems: "center", gap: ".5rem", marginTop: 0 }}>
        <SlidersHorizontal size={22} strokeWidth={1.8} /> Bird settings
      </h2>

      <AccuracySettings />

      <RecordingSettings />

      <AtlasSettings />

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
        Detection always runs on everything the microphone hears; this setting only
        changes what is displayed, and applies everywhere in the Birds app.
      </p>
    </div>
  );
}

// Design-system toggle (white thumb, accent track, focus ring all live in
// components.css). Thin wrapper keeps the existing on/onChange call sites.
function Switch({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return <Toggle checked={on} onChange={onChange} />;
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
  const [save, setSave] = useState<boolean | null>(null);

  useEffect(() => {
    fetch(`${API}/recordings/settings`).then((r) => r.json())
      .then((d) => setSave(!!d.settings.rec_save)).catch(() => {});
  }, []);

  const toggle = useCallback((v: boolean) => {
    setSave(v); // optimistic
    const fd = new FormData(); fd.append("rec_save", String(v));
    fetch(`${API}/recordings/settings`, { method: "POST", body: fd })
      .then((r) => r.json())
      .then((d) => setSave(!!d.settings.rec_save))
      .then(() => window.dispatchEvent(new CustomEvent("bird-settings-change")))
      .catch(() => {});
  }, []);

  if (save == null) return null;
  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
      <h3 style={{ margin: "0 0 .5rem", display: "flex", alignItems: "center", gap: ".4rem", fontSize: "1rem" }}>
        <Mic size={17} strokeWidth={1.8} /> Recordings
      </h3>
      <label style={{ display: "flex", alignItems: "flex-start", gap: ".7rem", cursor: "pointer" }}>
        <span style={{ paddingTop: ".15rem" }}><Toggle checked={save} onChange={toggle} /></span>
        <span>
          <span style={{ fontWeight: 500 }}>Save recordings of detections</span>
          <span className="muted" style={{ display: "block", fontSize: ".84rem", marginTop: ".15rem" }}>
            {save
              ? "Every bird detection's audio clip is saved by default. Set individual birds to Never on their page to skip them."
              : "Audio clips are not saved by default. Turn this on to save them all, or set individual birds to Always on their page."}
          </span>
        </span>
      </label>
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
function AtlasSettings() {
  const [save, setSave] = useState<boolean | null>(null);

  useEffect(() => {
    fetch(`${API}/atlas/settings`).then((r) => r.json())
      .then((d) => setSave(!!d.settings.atlas_clip_save)).catch(() => {});
  }, []);

  const toggle = useCallback((v: boolean) => {
    setSave(v);
    const fd = new FormData(); fd.append("atlas_clip_save", String(v));
    fetch(`${API}/atlas/settings`, { method: "POST", body: fd })
      .then((r) => r.json())
      .then((d) => setSave(!!d.settings.atlas_clip_save))
      .then(() => window.dispatchEvent(new CustomEvent("bird-settings-change")))
      .catch(() => {});
  }, []);

  if (save == null) return null;
  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
      <h3 style={{ margin: "0 0 .5rem", display: "flex", alignItems: "center", gap: ".4rem", fontSize: "1rem" }}>
        <Waypoints size={17} strokeWidth={1.8} /> Soundscape Atlas
      </h3>
      <label style={{ display: "flex", alignItems: "flex-start", gap: ".7rem", cursor: "pointer" }}>
        <span style={{ paddingTop: ".15rem" }}><Toggle checked={save} onChange={toggle} /></span>
        <span>
          <span style={{ fontWeight: 500 }}>Save call-type exemplar clips</span>
          <span className="muted" style={{ display: "block", fontSize: ".84rem", marginTop: ".15rem" }}>
            Keeps a few audio clips per acoustic cluster (its own small budget) so you can label each
            cluster's call type by ear in the Atlas. Independent of the recordings setting above.
          </span>
        </span>
      </label>
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
  const [base, setBase] = useState<Smart | null>(null);
  const [draft, setDraft] = useState<Smart | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    fetch(`${API}/analyze/settings`).then((r) => r.json())
      .then((d) => { setBase(d.settings); setDraft(d.settings); })
      .catch(() => { /* offline: section just stays in loading */ });
  }, []);

  const [recomputing, setRecomputing] = useState(false);
  const [recomputed, setRecomputed] = useState<{ hidden: number; species: number } | null>(null);

  const dirty = !!base && !!draft && (
    base.min_conf !== draft.min_conf ||
    presetKey(base.sf_thresh) !== presetKey(draft.sf_thresh) ||
    base.new_species_hits !== draft.new_species_hits);

  const save = useCallback(async () => {
    if (!draft) return;
    setSaving(true); setSaved(false);
    const fd = new FormData();
    fd.append("min_conf", String(draft.min_conf));
    fd.append("sf_thresh", String(presetValue(presetKey(draft.sf_thresh))));
    fd.append("new_species_hits", String(draft.new_species_hits));
    try {
      const r = await fetch(`${API}/analyze/settings`, { method: "POST", body: fd });
      const d = await r.json();
      setBase(d.settings); setDraft(d.settings); setSaved(true);
    } finally { setSaving(false); }
  }, [draft]);

  const recompute = useCallback(async () => {
    setRecomputing(true); setRecomputed(null);
    try {
      const r = await fetch(`${API}/analyze/recompute`, { method: "POST" });
      const d = await r.json();
      setRecomputed({ hidden: d.hidden ?? 0, species: (d.species_hidden || []).length });
      // Tell every open Birds view to refetch now-filtered data.
      window.dispatchEvent(new CustomEvent("bird-settings-change"));
    } finally { setRecomputing(false); }
  }, []);

  if (!draft) return null;
  const curKey = presetKey(draft.sf_thresh);

  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
      <h3 style={{ margin: "0 0 .15rem", display: "flex", alignItems: "center", gap: ".4rem", fontSize: "1rem" }}>
        <Target size={17} strokeWidth={1.8} /> Detection accuracy
      </h3>
      <p className="muted" style={{ fontSize: ".82rem", margin: "0 0 1rem" }}>
        How cautious detection is. Stricter settings report fewer birds but make the
        ones it reports far more trustworthy. The confidence threshold and location
        filter below are also what an auto-confirmed species is judged against.
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
            A species never heard here before must be detected this many times in a
            row before it's recorded, so a single odd blip never becomes a sighting.
            Birds already seen here are recorded immediately.
          </div>
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: ".6rem", marginTop: "1rem" }}>
        <Button onClick={save} disabled={!dirty || saving}>
          {saving ? "Saving…" : "Save"}
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
          Apply the location filter to everything already recorded, using where and
          when each detection happened. Impossible species are hidden from all stats
          and lists. Nothing is deleted; running it again can bring them back.
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: ".6rem", flexWrap: "wrap" }}>
          <Button variant="ghost" onClick={recompute} disabled={recomputing}>
            {recomputing ? "Re-checking…" : "Re-apply to past detections"}
          </Button>
          {recomputed && (
            <span className="muted" style={{ fontSize: ".82rem" }}>
              {recomputed.hidden > 0
                ? `Hid ${recomputed.hidden} detection${recomputed.hidden === 1 ? "" : "s"} across ${recomputed.species} impossible species.`
                : "Nothing to hide. All past detections are plausible here."}
            </span>
          )}
        </div>
      </div>
    </section>
  );
}
