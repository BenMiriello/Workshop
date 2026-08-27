import { useCallback, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Check, X, RotateCcw, Sparkles } from "lucide-react";
import { Button, Notice } from "@workshop/ui";

// Auto-confirmed species are shown exactly like confirmed ones; only the hover
// text distinguishes them, per the operator's request.
const AUTO_HOVER = "Auto-confirmed due to a separate full confirmation, confidence threshold, and location settings";
import { pct, apost, BirdThumb, SpeciesLink } from "./ui";
import { birdsKeys, fetchSpeciesEvents } from "./queries";
import { PlayButton } from "./recordings";

// One species in a dashboard list, with ONE consistent interaction rule:
//   - tapping the row body navigates to the species page (like every list);
//   - the chevron on the right is the only thing that expands the row in place.
// Expanding reveals the individual detections so you can act on single events
// (reject/restore) without leaving, and a footer for species-level actions.
//
// Actions are demoted by status: confirmed rows are clean (chevron only); a
// suggested row offers one calm Confirm; the blanket "stop suggesting here" is
// only in the expanded footer and asks for confirmation (it moves the species to
// Rejected and is reversible from there).

export type TrustStatus = "confirmed" | "suggested" | "rejected";
export type SpeciesAgg = {
  scientific_name: string; common_name: string; count: number; max_conf: number;
  // Trust state from the backend: is_new = first heard at this location within
  // the last 24h (the "New" badge); auto = auto-confirm is armed for this species.
  is_new?: boolean; auto?: boolean;
};
type Ev = { id: number; at: string; confidence: number; device: string | null; status: string; location: string | null; recording_id: number | null };

export default function SpeciesRow({ s, locId, status, lat, lon }: {
  s: SpeciesAgg; locId?: number; status: TrustStatus;
  lat?: number | null; lon?: number | null;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [confirmMute, setConfirmMute] = useState(false);
  const canAct = !!(lat && lon);

  // THE DETECTIONS BEHIND THIS ROW, READ BY BEING OPEN. Expanding does not
  // "trigger a load" any more: `enabled` is the open flag, so opening a row that
  // was already opened is served from the cache instead of re-fetching, and
  // closing one cancels nothing that is still needed. The species, the status
  // and the place are all in the key (`queries.ts`) rather than in a callback's
  // closure, which is what kept a slow answer for one status from landing in a
  // row the operator had already switched.
  const eventsQ = useQuery({
    queryKey: birdsKeys.speciesEvents({ sci: s.scientific_name, status, locationId: locId ?? null }),
    queryFn: () => fetchSpeciesEvents<{ events: Ev[] }>({
      sci: s.scientific_name, status, locationId: locId ?? null,
    }),
    enabled: open,
    retry: false,
  });
  const events = eventsQ.data?.events ?? null;

  // Expanding a row only reveals its detections — it does NOT acknowledge the
  // species or touch the "New" badge, so the row never jumps position when you
  // open it. "New" is purely a time window (first heard < 24h ago), set by the
  // backend; nothing the user does here changes it.
  const toggle = useCallback(() => setOpen((v) => !v), []);

  // WHAT A WRITE HERE CHANGES, IT SAYS. Confirming, rejecting or muting moves a
  // species between the three status lists AND changes this place's counts, the
  // dashboard's top birds, and the detections behind every status of this row.
  // That is the whole app prefix, so it is invalidated as one -- and only what
  // is mounted re-reads; the rest is simply marked stale. It replaces an
  // `onChanged` callback threaded down from the page, which re-ran that page's
  // loader and left every other view holding numbers the write had just changed.
  const invalidate = useCallback(
    () => qc.invalidateQueries({ queryKey: birdsKeys.all() }), [qc]);

  // A rejected write used to be invisible: `try { ... } finally { setBusy(false) }`
  // with no catch, so the row simply did not change and the operator was left to
  // guess. `apost` throws on a non-2xx, and the row now says so.
  const write = useCallback(async (work: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await work(); await invalidate(); }
    catch (e) { setErr(String(e)); }
    finally { setBusy(false); }
  }, [invalidate]);

  const setEvent = useCallback(
    (id: number, to: "rejected" | "suggested" | "confirmed") =>
      write(() => apost("/detection/status", { id, status: to })),
    [write]);

  // Species-level actions (need coords). confirm arms auto-confirm + sweeps;
  // mute moves to Rejected + stops future; unmute reverses it.
  const speciesAct = useCallback(async (kind: "confirm" | "mute" | "unmute") => {
    if (!canAct) return;
    await write(async () => {
      const body = { sci: s.scientific_name, common: s.common_name, lat, lon } as Record<string, string | number | boolean>;
      if (kind === "confirm") await apost("/species/auto", { ...body, on: true });
      else if (kind === "mute") await apost("/species/mute", body);
      else await apost("/species/unmute", body);
      window.dispatchEvent(new CustomEvent("bird-settings-change"));
      setConfirmMute(false);
    });
  }, [canAct, s, lat, lon, write]);

  return (
    <div style={{ borderTop: "1px solid var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: ".4rem", padding: ".5rem 0" }}>
        {/* whole body navigates to the species page */}
        <SpeciesLink sci={s.scientific_name} common={s.common_name} locationId={locId} status={status} style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: ".7rem" }}>
            <BirdThumb sci={s.scientific_name} common={s.common_name} />
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ display: "flex", alignItems: "center", gap: ".4rem" }}>
                <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.common_name}</span>
                {/* What this row IS, not just its name: a brand-new arrival in the
                    Suggested tab, or an auto-confirming species in Confirmed. */}
                {status === "suggested" && s.is_new && (
                  <span className="pill new" title="First heard at this location in the last 24 hours">
                    <Sparkles size={11} /> New
                  </span>
                )}
                {status === "confirmed" && (
                  <span className="pill ok" title={s.auto ? AUTO_HOVER : "Confirmed"}>
                    <Check size={11} />
                  </span>
                )}
              </span>
              <span className="muted" style={{ fontStyle: "italic", fontSize: ".78rem" }}>{s.scientific_name}</span>
            </span>
            <span className="muted" style={{ fontSize: ".8rem", textAlign: "right" }}>{s.count}×<br />best {pct(s.max_conf)}</span>
          </div>
        </SpeciesLink>
        {/* one calm affirmative on suggested rows; confirmed/rejected stay clean */}
        {status === "suggested" && canAct && (
          <button type="button" className="bird-act ok" disabled={busy} title="Confirm: a real bird here"
            onClick={() => speciesAct("confirm")}><Check size={16} /></button>
        )}
        {/* the ONLY expander */}
        <button type="button" className="bird-act" onClick={toggle} aria-expanded={open}
          title={open ? "Hide detections" : "Show detections"}>
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
      </div>

      {open && (
        <div style={{ padding: "0 0 .7rem .2rem" }}>
          {/* The row is open and its content is a few lines, so a failure here is
              a line too, in the space the detections would have filled. */}
          {eventsQ.isError && (
            <div style={{ margin: ".3rem 0" }}>
              <Notice tone="error" action={<Button size="sm" variant="ghost" onClick={() => void eventsQ.refetch()}>Retry</Button>}>
                Couldn't load these detections.
              </Notice>
            </div>
          )}
          {err && (
            <div style={{ margin: ".3rem 0" }}>
              <Notice tone="error" detail={err} action={<Button size="sm" variant="ghost" onClick={() => setErr(null)}>Dismiss</Button>}>
                That did not go through.
              </Notice>
            </div>
          )}
          {eventsQ.isPending && <p className="muted" style={{ fontSize: ".8rem", margin: ".2rem 0" }}>Loading…</p>}
          {events && events.length === 0 && <p className="muted" style={{ fontSize: ".8rem", margin: ".2rem 0" }}>No detections.</p>}
          {events && events.map((e) => (
            <div key={e.id} style={{ display: "flex", alignItems: "center", gap: ".5rem", padding: ".25rem 0", fontSize: ".82rem" }}>
              <span className="mono muted" style={{ minWidth: 98 }}>{e.at}</span>
              <span className="pill neutral" style={{ flexShrink: 0 }}>{pct(e.confidence)}</span>
              <span className="muted" style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.device || ""}</span>
              {e.recording_id && <PlayButton recId={e.recording_id} size={24} />}
              {e.status === "rejected"
                ? <button type="button" className="bird-act" disabled={busy} title="Restore this detection" onClick={() => setEvent(e.id, "suggested")}><RotateCcw size={14} /></button>
                : <button type="button" className="bird-act danger" disabled={busy} title="Reject this detection" onClick={() => setEvent(e.id, "rejected")}><X size={14} /></button>}
            </div>
          ))}

          {/* Footer: contextual species-level actions */}
          {canAct && (
            <div style={{ marginTop: ".6rem" }}>
              {status === "rejected" ? (
                <button type="button" className="btn sm" disabled={busy} onClick={() => speciesAct("unmute")}>
                  <RotateCcw size={14} /> Suggest again
                </button>
              ) : confirmMute ? (
                <div style={{ display: "flex", alignItems: "center", gap: ".6rem", flexWrap: "wrap" }}>
                  <span className="muted" style={{ fontSize: ".8rem" }}>
                    Move {s.common_name} to Rejected and stop suggesting it here? (undo from the Rejected tab)
                  </span>
                  <button type="button" className="btn sm danger" disabled={busy} onClick={() => speciesAct("mute")}>Stop suggesting</button>
                  <button type="button" className="btn sm" disabled={busy} onClick={() => setConfirmMute(false)}>Cancel</button>
                </div>
              ) : (
                <div style={{ display: "flex", alignItems: "center", gap: ".8rem", flexWrap: "wrap" }}>
                  {status === "suggested" && (
                    <button type="button" className="btn sm" disabled={busy} onClick={() => speciesAct("confirm")}>
                      <Check size={14} /> Confirm species
                    </button>
                  )}
                  <button type="button" className="muted" disabled={busy} onClick={() => setConfirmMute(true)}
                    style={{ background: "none", border: "none", cursor: "pointer", fontSize: ".8rem", textDecoration: "underline" }}>
                    Stop suggesting here
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
