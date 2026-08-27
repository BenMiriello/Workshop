import { useState } from "react";
import { Pin, PinOff, Trash2, Save, MapPin } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import {
  useCrumbTrail, type Crumb, Segmented, Stepper, ConfirmDialog, formatBytes,
  Button, Notice, Skeleton, StateBlock,
} from "@workshop/ui";
import { pct, SpeciesLink, BirdThumb } from "./ui";
import { useRecStatus, useRecordingsSettings, PlayButton, type RecStatus } from "./recordings";
import {
  birdsKeys, fetchRecordings, useDeleteRecording, usePinRecording,
  useSaveRecordingsBudget, type RecordingsSettings,
} from "./queries";
import BirdsHeader from "./BirdsHeader";

// Recordings management: the storage meter, the budget settings (cap + what to do
// when full), and the full list of saved clips with per-clip pin / delete. This
// is where the homepage banner links to.
//
// THREE FACTS, THREE ENTRIES, NONE OF THEM OWNED HERE. The status, the budget
// and the clip list are each read by another page too, so all three are keyed in
// `queries.ts` and shared. The page used to hold the list in a `useState` behind
// a 12s `usePoll`, re-read it by hand after every write, and then ALSO broadcast
// a window event to tell the other pages to re-read theirs.

/** The newest N clips. In the key, because the bird page asks for a different N. */
const LIST_LIMIT = 300;
/** The list moves on its own as detections are saved, so it keeps a cadence. */
const LIST_MS = 12000;

type Rec = {
  id: number; scientific_name: string; common_name: string;
  location_id: number | null; location: string | null; at: string;
  bytes: number; confidence: number; pinned: boolean; protected: boolean;
};
type RecList = { items: Rec[]; total: number };

// One empty array, so "no clips" is referentially stable across renders.
const NO_RECS: Rec[] = [];

const cardStyle: React.CSSProperties = { padding: "1rem", marginBottom: "1rem" };

export default function Recordings() {
  useCrumbTrail([
    { label: "Apps", to: "/apps" },
    { label: "Birds", to: "/apps/birds" },
    { label: "Recordings" },
  ] as Crumb[]);

  const statusQ = useRecStatus();
  const [confirm, setConfirm] = useState<Rec | null>(null);

  // The whole list, unfiltered by species. Both parameters that reach the URL
  // are in the key.
  const listParams = { sci: null, limit: LIST_LIMIT };
  const list = useQuery({
    queryKey: birdsKeys.recordings(listParams),
    queryFn: () => fetchRecordings<RecList>(listParams),
    // Owns its cadence, so nothing sits stale over it and a failed tick is
    // followed by the next tick rather than a retry. No `keepPreviousData`: the
    // key never changes on this page, so a refetch already keeps the rows.
    refetchInterval: LIST_MS,
    staleTime: 0,
    retry: false,
  });

  const pin = usePinRecording();
  const del = useDeleteRecording();

  const items = list.data?.items ?? NO_RECS;
  const total = list.data?.total ?? 0;
  const listErr = list.isError ? String(list.error) : null;
  const writeErr = pin.error ?? del.error;

  return (
    <div>
      <BirdsHeader title="Recordings" />

      <section className="card" style={cardStyle}>
        {statusQ.data && <UsageMeter status={statusQ.data} />}
        {statusQ.isPending && <Skeleton style={{ height: 62 }} />}
        {statusQ.isError && (
          <div style={{ marginTop: statusQ.data ? ".6rem" : 0 }}>
            <Notice
              tone={statusQ.data ? "warn" : "error"}
              action={<Button size="sm" variant="ghost" onClick={() => void statusQ.refetch()}>Retry</Button>}
            >
              {statusQ.data ? "Storage usage is not refreshing." : "Could not read storage usage."}
            </Notice>
          </div>
        )}
      </section>

      <RecordingSettings />

      <section className="card" style={cardStyle}>
        <h3 style={{ margin: "0 0 .6rem", fontSize: "1rem" }}>
          Saved clips {total > 0 && <span className="muted" style={{ fontWeight: 400 }}>({total})</span>}
        </h3>

        {/* A rejected pin or delete is a line beside a list that is still on
            screen, never a region. */}
        {writeErr && (
          <div style={{ marginBottom: ".6rem" }}>
            <Notice
              tone="error"
              detail={String(writeErr)}
              action={<Button size="sm" variant="ghost" onClick={() => { pin.reset(); del.reset(); }}>Dismiss</Button>}
            >
              That did not go through.
            </Notice>
          </div>
        )}

        {listErr && list.data && (
          <div style={{ marginBottom: ".6rem" }}>
            <Notice tone="warn" action={<Button size="sm" variant="ghost" onClick={() => void list.refetch()}>Retry</Button>}>
              These clips are not refreshing.
            </Notice>
          </div>
        )}

        {listErr && !list.data ? (
          <StateBlock
            variant="error"
            title="Couldn't load recordings"
            hint={<code>GET /api/birds/recordings: {listErr}</code>}
            action={<Button size="sm" onClick={() => void list.refetch()}>Retry</Button>}
          />
        ) : list.isPending ? (
          <div style={{ display: "grid", gap: ".5rem" }}>
            {[0, 1, 2].map((i) => <Skeleton key={i} style={{ height: 48 }} />)}
          </div>
        ) : items.length === 0 ? (
          <StateBlock
            title="No recordings yet"
            hint="Open a bird and set its recording rule to Always. The audio of its detections is saved from then on."
          />
        ) : (
          <div style={{ display: "flex", flexDirection: "column" }}>
            {items.map((r) => (
              <div key={r.id} style={{ display: "flex", alignItems: "center", gap: ".6rem", padding: ".5rem 0", borderTop: "1px solid var(--border)" }}>
                <PlayButton recId={r.id} />
                <SpeciesLink sci={r.scientific_name} locationId={r.location_id} style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: ".6rem" }}>
                    <BirdThumb sci={r.scientific_name} common={r.common_name} size={36} />
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ display: "block", fontSize: ".9rem" }}>{r.common_name}</span>
                      <span className="muted" style={{ fontSize: ".75rem", display: "inline-flex", alignItems: "center", gap: ".3rem" }}>
                        {r.location && <><MapPin size={10} /> {r.location} · </>}{r.at}
                      </span>
                    </span>
                  </div>
                </SpeciesLink>
                <span className="pill neutral" style={{ flexShrink: 0 }}>{pct(r.confidence)}</span>
                <span className="muted" style={{ fontSize: ".75rem", minWidth: 56, textAlign: "right", flexShrink: 0 }}>{formatBytes(r.bytes)}</span>
                <button type="button" className={`bird-act${r.pinned ? " ok" : ""}`} disabled={pin.isPending}
                  onClick={() => pin.mutate({ id: r.id, pinned: !r.pinned })}
                  title={r.pinned ? "Pinned. Protected from auto-cleanup. Click to unpin." : "Pin to keep this recording (deleted last)"}>
                  {r.pinned ? <Pin size={15} /> : <PinOff size={15} />}
                </button>
                <button type="button" className="bird-act danger" onClick={() => setConfirm(r)} title="Delete this recording">
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
            {/* The fourth state: the list is capped, so say what is not in it. */}
            {total > items.length && (
              <p className="muted" style={{ fontSize: ".76rem", margin: ".6rem 0 0" }}>
                Showing the newest {items.length} of {total} clips.
              </p>
            )}
          </div>
        )}
      </section>

      <ConfirmDialog
        open={!!confirm}
        title="Delete recording?"
        body={confirm ? `Permanently delete this ${confirm.common_name} clip from ${confirm.at}. This cannot be undone.` : ""}
        confirmLabel="Delete"
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          if (!confirm) return;
          // The dialog closes and the outcome is reported on the page behind it:
          // a failure has nowhere to be seen inside a dialog that has gone.
          del.mutate(confirm.id);
          setConfirm(null);
        }}
      />
    </div>
  );
}

// Used / cap bar with the protected portion marked. Color escalates with state.
function UsageMeter({ status }: { status: RecStatus }) {
  const usedFrac = Math.min(1, status.cap_bytes ? status.used_bytes / status.cap_bytes : 0);
  const protFrac = Math.min(usedFrac, status.cap_bytes ? status.protected_bytes / status.cap_bytes : 0);
  const barColor = status.state === "error" ? "var(--danger)" : status.state === "warn" ? "var(--warn)" : "var(--accent)";
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: ".4rem" }}>
        <span style={{ fontWeight: 500 }}>Storage used</span>
        <span className="muted" style={{ fontSize: ".85rem" }}>
          {formatBytes(status.used_bytes)} of {formatBytes(status.cap_bytes)} · {status.count} clip{status.count === 1 ? "" : "s"}
        </span>
      </div>
      <div style={{ position: "relative", height: 10, borderRadius: 6, background: "var(--bg-inset)", overflow: "hidden" }}>
        <div style={{ position: "absolute", inset: 0, width: `${usedFrac * 100}%`, background: barColor, opacity: 0.85 }} />
        {protFrac > 0 && (
          <div title="Protected (pinned or 'save all'). Not auto-removed"
            style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${protFrac * 100}%`, background: "var(--accent)", opacity: 0.55 }} />
        )}
      </div>
      <div className="muted" style={{ fontSize: ".75rem", marginTop: ".4rem" }}>
        {status.protected_bytes > 0 && <>Protected: {formatBytes(status.protected_bytes)} · </>}
        When full: {status.when_full === "stop" ? "stop saving new recordings" : "remove oldest recordings"}
      </div>
      {status.state !== "ok" && status.message && (
        <p className="muted" style={{ fontSize: ".82rem", marginTop: ".5rem", marginBottom: 0, color: status.state === "error" ? "var(--danger)" : "var(--warn)" }}>
          {status.message}
        </p>
      )}
    </div>
  );
}

// Budget config — a config write, so it commits on an explicit Save (design rule 7).
//
// The read is the SHARED settings entry. This used to be a private `useEffect`
// fetch with `.catch(() => {})` and `if (!draft) return null`, so a settings
// route that was down removed the whole section from the page with no trace.
function RecordingSettings() {
  const q = useRecordingsSettings();

  if (q.isPending) {
    return <section className="card" style={cardStyle}><Skeleton style={{ height: 120 }} /></section>;
  }
  if (!q.data) {
    return (
      <section className="card" style={cardStyle}>
        <Notice
          tone="error"
          where="/api/birds/recordings/settings"
          action={<Button size="sm" variant="ghost" onClick={() => void q.refetch()}>Retry</Button>}
        >
          Could not load the storage settings.
        </Notice>
      </section>
    );
  }
  return <RecordingSettingsForm settings={q.data.settings} />;
}

function RecordingSettingsForm({ settings }: { settings: RecordingsSettings }) {
  // Seeded ONCE, at mount. `settings` keeps arriving -- the entry is re-read on
  // focus and after any recordings write -- and a re-seeding effect would throw
  // away whatever the operator had typed. `dirty` compares the draft against the
  // live server value instead, which is the question the Save button is actually
  // asking. Only the two fields this form owns are drafted: `rec_save` belongs
  // to the settings page and is not ours to write back.
  const [draft, setDraft] = useState({
    storageMb: settings.rec_storage_mb,
    whenFull: settings.rec_when_full,
  });
  const save = useSaveRecordingsBudget();
  const dirty = draft.storageMb !== settings.rec_storage_mb || draft.whenFull !== settings.rec_when_full;

  return (
    <section className="card" style={cardStyle}>
      <h3 style={{ margin: "0 0 .8rem", fontSize: "1rem" }}>Storage settings</h3>
      <div style={{ display: "grid", gap: "1.1rem" }}>
        <div>
          <div style={{ fontWeight: 500, marginBottom: ".35rem" }}>Total storage limit</div>
          <Stepper value={draft.storageMb} onChange={(v) => setDraft({ ...draft, storageMb: v })}
            min={50} max={20000} step={250} suffix="MB" ariaLabel="Storage limit in MB" />
          <div className="muted" style={{ fontSize: ".78rem", marginTop: ".35rem" }}>
            How much disk saved clips may use, across all birds. About {Math.round(draft.storageMb / 0.024)} clips at this size.
          </div>
        </div>
        <div>
          <div style={{ fontWeight: 500, marginBottom: ".35rem" }}>When full</div>
          <Segmented<"evict_old" | "stop">
            ariaLabel="What to do when storage is full"
            value={draft.whenFull}
            onChange={(v) => setDraft({ ...draft, whenFull: v })}
            options={[
              { value: "evict_old", label: "Remove oldest" },
              { value: "stop", label: "Stop saving" },
            ]}
          />
          <div className="muted" style={{ fontSize: ".78rem", marginTop: ".5rem" }}>
            {draft.whenFull === "evict_old"
              ? "Automatically delete the oldest recordings to make room. Pinned and 'save all' recordings are never removed."
              : "Stop saving new recordings once full, keeping everything you have. You'll see a warning when this happens."}
          </div>
        </div>
      </div>

      {/* The form is right here, so a rejected save is a line above its own
          button. It used to be swallowed by a `try/finally`, which looked
          exactly like a save that worked. */}
      {save.isError && (
        <div style={{ marginTop: ".8rem" }}>
          <Notice
            tone="error"
            detail={String(save.error)}
            action={<Button size="sm" variant="ghost" onClick={() => save.reset()}>Dismiss</Button>}
          >
            That did not save.
          </Notice>
        </div>
      )}

      <div style={{ marginTop: "1rem" }}>
        <button className="btn" disabled={!dirty || save.isPending}
          onClick={() => save.mutate({ storageMb: draft.storageMb, whenFull: draft.whenFull })}>
          <Save size={15} /> {save.isPending ? "Saving…" : "Save"}
        </button>
      </div>
    </section>
  );
}
