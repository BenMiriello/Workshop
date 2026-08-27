import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { Play, Pause, AlertTriangle, AlertCircle } from "lucide-react";
import { StatusPill } from "@workshop/ui";
import { API } from "./api";
import {
  birdsKeys, fetchRecordingsSettings, fetchRecordingsStatus,
  type RecordingsSettings, type SettingsRead,
} from "./queries";

// Shared pieces for the recordings system: the two reads every recordings
// surface needs, a homepage banner that renders only when there's something to
// say, and a one-at-a-time inline clip player.
//
// `API` comes from `./api`, not `./ui`: `ui.tsx` imports recharts, and this
// module is pulled in by the species rows and the Atlas for a play button.

/** How often the storage status re-reads itself. Bytes move as clips land. */
const STATUS_MS = 8000;

export type RecStatus = {
  state: "ok" | "warn" | "error";
  message: string;
  used_bytes: number;
  cap_bytes: number;
  protected_bytes: number;
  count: number;
  when_full: "evict_old" | "stop";
  storage_mb: number;
};

/**
 * THE LEGACY REFRESH BUS, BRIDGED TO ONE INVALIDATION.
 *
 * `bird-settings-change` is a window CustomEvent that Birds views dispatch after
 * a write and that others subscribe to in order to re-read. It is a hand-rolled
 * cache invalidator: every dispatch of it means "recordings data changed, read
 * it again", which is `invalidateQueries` spelled without dedup, without
 * in-flight cancellation, and reaching only whoever personally subscribed.
 *
 * The dispatches the recordings page and the bird page owned are GONE -- those
 * writes are mutations that invalidate directly. The LISTENER stays until the
 * last dispatcher does: `BirdSettings.tsx` (the app-wide save toggle) and
 * `SpeciesRow.tsx` still fire it, and removing the listener before them would
 * silently stop cross-page refresh rather than fix anything. It is ONE listener
 * over the whole recordings family now, in place of the two hand-rolled reloads
 * that used to sit in the status hook and in the bird page's settings effect.
 *
 * `settings.ts` dispatches the SAME event name for the show-non-bird
 * localStorage preference, which is a client display setting and must never
 * become an invalidation of its own. It costs a spurious refetch here, exactly
 * as it costs a spurious reload today, and that is not worth a second event
 * name for a bus that is being retired.
 */
function useRecordingsRefreshBridge(): void {
  const qc = useQueryClient();
  useEffect(() => {
    const onChange = () => { void qc.invalidateQueries({ queryKey: birdsKeys.recordingsAll() }); };
    window.addEventListener("bird-settings-change", onChange);
    return () => window.removeEventListener("bird-settings-change", onChange);
  }, [qc]);
}

/**
 * The recording-storage status. ONE ENTRY for every reader: the homepage banner
 * and the recordings page mount this independently and share a single request
 * and a single cadence.
 *
 * THERE IS NO `intervalMs` PARAMETER ANY MORE, and dropping it is the point
 * rather than a tidy-up. One caller passed 8000 and the other took the 8000
 * default, so the configurability was never used -- and it was not free:
 * `usePoll` puts `intervalMs` in its effect deps, so a CHANGED interval tore the
 * timer down, re-armed it, AND ran the fetch again. That is wrong on its own
 * terms: how often you intend to look at a number does not make the number
 * staler, and a caller computing its interval from anything that moves would
 * have fetched on every render of it. `refetchInterval` re-arms without the
 * extra read, which is what a cadence change should do. With one cadence the
 * difference is unobservable, so the honest fix is to stop offering the choice.
 */
export function useRecStatus(): UseQueryResult<RecStatus> {
  useRecordingsRefreshBridge();
  return useQuery({
    queryKey: birdsKeys.recordingsStatus(),
    queryFn: () => fetchRecordingsStatus<RecStatus>(),
    // It owns its own cadence, so no staleness window sits over it, and it does
    // not retry: a failed tick is followed by the next tick, which is exactly
    // what the poll it replaces did.
    refetchInterval: STATUS_MS,
    staleTime: 0,
    retry: false,
  });
}

/**
 * The recording budget. THREE PAGES READ IT -- the recordings page (the cap and
 * the when-full rule), the bird page (the app-wide save default, to describe
 * what "Default" means) and Birds settings -- and two write it. Each used to
 * hold its own `useEffect` + `useState` copy with its own partial hand-written
 * type, kept in step by the event bus above. One key, one entry, one shape.
 *
 * NO CADENCE. It changes only when someone saves it, a save writes the answer
 * straight into this entry (`useSaveRecordingsBudget`), and the client re-reads
 * on focus, so a change made on another device still lands. It keeps the shared
 * transient retry for the same reason: with no tick behind it, a dropped read
 * has nothing to catch it.
 */
export function useRecordingsSettings(): UseQueryResult<SettingsRead<RecordingsSettings>> {
  useRecordingsRefreshBridge();
  return useQuery({
    queryKey: birdsKeys.recordingsSettings(),
    queryFn: () => fetchRecordingsSettings<SettingsRead<RecordingsSettings>>(),
  });
}

// One calm line at the top of the Birds home, shown only when not OK. Warn =
// near full; error = recording is actually failing. Links to the recordings page.
//
// It has no loading or error state ON PURPOSE: a status that cannot be read is
// not something to put on the home page, and the recordings page it links to is
// where an unreadable status is reported.
export function RecordingsBanner() {
  const { data: s } = useRecStatus();
  if (!s || s.state === "ok") return null;
  const kind = s.state === "error" ? "danger" : "warn";
  const Icon = s.state === "error" ? AlertCircle : AlertTriangle;
  return (
    <Link to="/apps/birds/recordings" style={{ textDecoration: "none", display: "block", marginBottom: "1rem" }}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: ".5rem" }}>
        <StatusPill status={kind}>
          <Icon size={13} /> {s.message}
        </StatusPill>
      </span>
    </Link>
  );
}

// Module-level single audio element so starting one clip stops any other.
let _audio: HTMLAudioElement | null = null;
const _listeners = new Set<() => void>();
function notify() { _listeners.forEach((f) => f()); }

export function PlayButton({ recId, size = 28 }: { recId: number; size?: number }) {
  const [, force] = useState(0);
  const srcRef = useRef(`${API}/recordings/${recId}/audio`);
  useEffect(() => {
    const f = () => force((n) => n + 1);
    _listeners.add(f);
    return () => { _listeners.delete(f); };
  }, []);
  const playing = !!_audio && _audio.src.endsWith(`/recordings/${recId}/audio`) && !_audio.paused;

  const toggle = (e: React.MouseEvent) => {
    e.preventDefault(); e.stopPropagation();
    if (playing) { _audio?.pause(); notify(); return; }
    if (_audio) { _audio.pause(); }
    _audio = new Audio(srcRef.current);
    _audio.onended = notify;
    _audio.onpause = notify;
    _audio.onplay = notify;
    _audio.play().catch(() => {});
    notify();
  };

  return (
    <button type="button" className="bird-act" onClick={toggle}
      title={playing ? "Pause" : "Play recording"}
      style={{ width: size, height: size, flexShrink: 0 }}>
      {playing ? <Pause size={15} /> : <Play size={15} />}
    </button>
  );
}

// Play any clip URL through the same one-at-a-time element (so an Atlas exemplar
// and a saved recording never overlap). Used by the Soundscape Atlas labelling UI.
export function ClipPlayButton({ src, title = "Play clip", size = 28 }: { src: string; title?: string; size?: number }) {
  const [, force] = useState(0);
  useEffect(() => {
    const f = () => force((n) => n + 1);
    _listeners.add(f);
    return () => { _listeners.delete(f); };
  }, []);
  const playing = !!_audio && _audio.src.endsWith(src) && !_audio.paused;
  const toggle = (e: React.MouseEvent) => {
    e.preventDefault(); e.stopPropagation();
    if (playing) { _audio?.pause(); notify(); return; }
    if (_audio) _audio.pause();
    _audio = new Audio(src.startsWith("http") ? src : `${API}${src}`);
    _audio.onended = notify; _audio.onpause = notify; _audio.onplay = notify;
    _audio.play().catch(() => {});
    notify();
  };
  return (
    <button type="button" className="bird-act" onClick={toggle}
      title={playing ? "Pause" : title} style={{ width: size, height: size, flexShrink: 0 }}>
      {playing ? <Pause size={15} /> : <Play size={15} />}
    </button>
  );
}
