import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Play, Pause, AlertTriangle, AlertCircle } from "lucide-react";
import { StatusPill } from "@workshop/ui";
import { API, jget } from "./ui";
import { usePoll } from "../../../lib/usePoll";

// Shared pieces for the recordings system: the storage-status hook, a homepage
// banner that renders only when there's something to say, byte formatting, and a
// one-at-a-time inline clip player.

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

// Polls the recording-storage status. Also refetches when any Birds view
// broadcasts a settings change (so toggling a pref updates the banner at once).
export function useRecStatus(intervalMs = 8000): RecStatus | null {
  const [s, setS] = useState<RecStatus | null>(null);
  const load = useCallback(() => {
    jget<RecStatus>("/recordings/status").then(setS).catch(() => {});
  }, []);
  usePoll(load, intervalMs);
  useEffect(() => {
    const onChange = () => load();
    window.addEventListener("bird-settings-change", onChange);
    return () => window.removeEventListener("bird-settings-change", onChange);
  }, [load]);
  return s;
}

// One calm line at the top of the Birds home, shown only when not OK. Warn =
// near full; error = recording is actually failing. Links to the recordings page.
export function RecordingsBanner() {
  const s = useRecStatus();
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
