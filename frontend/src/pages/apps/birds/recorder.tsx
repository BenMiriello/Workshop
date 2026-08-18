import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import { reverseGeocode } from "./geocode";
import { specColor } from "./specColor";
import { localizeEvents, type SpecCol, type SpeciesEvent, type Sample } from "./specLocalize";
import { userStorage } from "../../../lib/storage";
import { usePoll } from "../../../lib/usePoll";

// App-root recorder: captures THIS device's mic and streams live BirdNET
// detections. Lives above the router so listening keeps running while you move
// between pages. Closing the tab/app still stops it (a browser cannot capture
// the mic in the background) - a screen wake-lock is the best-effort mitigation.
//
// Audio is captured as raw PCM. BirdNET's native window is 3s, so we keep a
// rolling 3s buffer and post it once per second: a 1s live cadence with full 3s
// context. The backend dedups overlapping windows so a continuous song is not
// counted several times.

const API = "/api/birds";
const WINDOW_S = 3;
const STEP_S = 0.5; // post every 0.5s (6 overlapping 3s windows) — tighter time
                    // localization for the spectrogram markers; ~2x analyzer load.
const MAX_SECONDS = 3600; // safety auto-stop at 1h

const TOKEN_KEY = "birdDeviceToken";
const NAME_KEY = "birdDeviceName";
const MODE_KEY = "birdDeviceMode";
const FIXED_KEY = "birdDeviceFixedLoc";
const SHARED_KEY = "birdDeviceShared";

export type DeviceMode = "adaptive" | "fixed";
export type Device = { token: string; name: string; mode: DeviceMode; fixedLocationId: string; shared: boolean };
export type Det = {
  scientific_name: string; common_name: string; confidence: number;
  is_bird: boolean; stored?: boolean;
  in_range?: boolean | null; filtered?: string; occurrence?: number | null;
};
export type LogEntry = Det & { at: string };
// A detection pinned onto the live spectrogram. One marker per vocalization
// EVENT (a burst of overlapping windows), not per window. Its horizontal pixel
// position is derived from `tDetect` (a performance.now timestamp) via
// specMarkerX(), so it shares the same clock + px/sec the image scrolls at and
// can never drift. `tDetect` eases toward `tTarget` (the latest localized chirp
// time) so the pin slides to its refined spot as the burst completes.
//   mode "precise" → dot at the energy blob (time + frequency);
//   mode "approx"  → faded dot at the model estimate (couldn't isolate a blob);
//   mode "none"    → card only, no position (ambiguous / unresolvable).
// `yFrac` is 0..1 from the top (high frequency = small).
export type SpecMarker = {
  id: number;
  tDetect: number;
  tTarget: number;
  yFrac: number;
  common: string;
  sci: string;
  conf: number;
  mode: "precise" | "approx" | "none";
};
export type SpeciesRow = {
  scientific_name: string; common_name: string; hits: number; max_conf: number; is_bird: boolean;
};

type Ctx = {
  device: Device;
  setDeviceName: (n: string) => void;
  adoptDevice: (token: string, name: string) => void;
  setMode: (m: DeviceMode) => void;
  setFixedLocation: (id: string) => void;
  setShared: (shared: boolean) => void;
  recording: boolean;
  status: "idle" | "listening" | "analyzing";
  elapsed: number;
  location: string | null;
  needsLocation: boolean;
  wasListening: boolean;
  nowHearing: Det[];
  liveLog: LogEntry[];
  sessionSpecies: SpeciesRow[];
  analyzerUp: boolean | null;
  err: string | null;
  analyserRef: React.MutableRefObject<AnalyserNode | null>;
  specCanvasRef: React.MutableRefObject<HTMLCanvasElement | null>;
  specMarkersRef: React.MutableRefObject<SpecMarker[]>;
  markerEpoch: number;
  start: () => Promise<void>;
  stop: () => void;
};

const RecorderContext = createContext<Ctx | null>(null);
export const useRecorder = () => {
  const c = useContext(RecorderContext);
  if (!c) throw new Error("useRecorder must be used inside <RecorderProvider>");
  return c;
};

// The token is the only durable client identity. name/mode/fixed/shared are read
// from localStorage as an OFFLINE CACHE for the first paint, then immediately
// overwritten by the server's authoritative copy (see hydrateFromServer): the
// backend is the source of truth, so a device's mode set from any screen (or the
// Pi's admin view) drives this device too — localStorage no longer wins.
function loadDevice(): Device {
  let token = userStorage.local.getItem(TOKEN_KEY) || userStorage.local.getItem("birdStationToken") || "";
  if (!token) token = crypto.randomUUID?.() || String(Math.random()).slice(2);
  userStorage.local.setItem(TOKEN_KEY, token);
  const name = userStorage.local.getItem(NAME_KEY) ?? userStorage.local.getItem("birdStationLabel") ?? "";
  const mode = (userStorage.local.getItem(MODE_KEY) as DeviceMode) || "adaptive";
  const fixedLocationId = userStorage.local.getItem(FIXED_KEY) || "";
  const shared = userStorage.local.getItem(SHARED_KEY) === "1";
  return { token, name, mode, fixedLocationId, shared };
}

// Encode Float32 PCM [-1,1] as a 16-bit mono WAV blob at the given sample rate.
function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const wr = (o: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  wr(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); wr(8, "WAVE");
  wr(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  wr(36, "data"); v.setUint32(40, samples.length * 2, true);
  let o = 44;
  for (let i = 0; i < samples.length; i++, o += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}

// Offscreen spectrogram, drawn by the always-mounted provider so it keeps
// scrolling and its history survives navigating between pages. The visible
// <Spectrogram> just blits this canvas.
export const SPEC_W = 600;
const SPEC_H = 160, SPEC_MAX_HZ = 12000;
// Spectrogram scroll speed. The image scrolls this many px/s AND every marker's
// horizontal position is derived from this same constant + wall-clock time, so
// the two share one time->pixel mapping and never drift apart. 600px / 80 ≈ 7.5s
// of visible history.
export const SPEC_PX_PER_SEC = 80;
// A marker's current x in offscreen-canvas px, as a pure function of time.
// Positive-to-negative as it scrolls left off the screen.
export function specMarkerX(m: SpecMarker, now: number): number {
  return SPEC_W - 1 - ((now - m.tDetect) * SPEC_PX_PER_SEC) / 1000;
}

// --- detection localization plumbing ---------------------------------------
const SPEC_BANDS = 40;     // freq resolution of the retained energy heatmap
const SPEC_HIST_MS = 5500; // how much energy history to keep for localization
const SPEC_COL_MS = 30;    // min spacing between retained columns (~33/s)
const EVENT_GAP_MS = 1600; // silence longer than this ends a vocalization event
const TARGET_EASE = 0.18;  // how fast a pin slides to a refined position

// Pool an FFT magnitude column (bins 0..topBin) into SPEC_BANDS energy bands,
// band 0 = lowest frequency. Average within each band.
function reduceColumn(data: Uint8Array, topBin: number): number[] {
  const out = new Array(SPEC_BANDS).fill(0);
  for (let b = 0; b < SPEC_BANDS; b++) {
    const lo = Math.floor((b / SPEC_BANDS) * topBin);
    const hi = Math.max(lo + 1, Math.floor(((b + 1) / SPEC_BANDS) * topBin));
    let s = 0;
    for (let i = lo; i < hi; i++) s += data[i] || 0;
    out[b] = s / (hi - lo);
  }
  return out;
}

// Per-species rolling state used to group overlapping windows into one event.
type SeriesEntry = { common: string; samples: Sample[]; lastSeen: number; markerId: number };

// Session view (live log + species + "was listening") is mirrored to localStorage
// so it survives a full page reload, not just navigation.
const SESS_SPECIES_KEY = "birdSessionSpecies";
const SESS_LOG_KEY = "birdSessionLog";
const WAS_KEY = "birdWasListening";
function loadJSON<T>(k: string, fallback: T): T {
  try { const r = userStorage.local.getItem(k); return r ? (JSON.parse(r) as T) : fallback; } catch { return fallback; }
}

// Metres between two lat/lon points - used to decide when an adaptive device has
// moved far enough to re-name (re-geocode) its location while recording.
function distM(aLat: number, aLon: number, bLat: number, bLon: number): number {
  const R = 6371000, p1 = aLat * Math.PI / 180, p2 = bLat * Math.PI / 180;
  const dphi = (bLat - aLat) * Math.PI / 180, dl = (bLon - aLon) * Math.PI / 180;
  const h = Math.sin(dphi / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

async function postDevice(d: Device) {
  const fd = new FormData();
  fd.append("token", d.token);
  if (d.name) fd.append("name", d.name);
  fd.append("mode", d.mode);
  if (d.mode === "fixed" && d.fixedLocationId) fd.append("fixed_location_id", d.fixedLocationId);
  fd.append("shared", d.shared ? "true" : "false");
  if (typeof navigator !== "undefined" && navigator.userAgent) fd.append("user_agent", navigator.userAgent);
  try { await fetch(`${API}/device`, { method: "POST", body: fd }); } catch { /* offline ok */ }
}

export function RecorderProvider({ children }: { children: React.ReactNode }) {
  const [device, setDevice] = useState<Device>(loadDevice);
  const [recording, setRecording] = useState(false);
  const [status, setStatus] = useState<"idle" | "listening" | "analyzing">("idle");
  const [elapsed, setElapsed] = useState(0);
  const [location, setLocation] = useState<string | null>(null);
  const [needsLocation, setNeedsLocation] = useState(false);
  const [wasListening, setWasListening] = useState<boolean>(() => userStorage.local.getItem(WAS_KEY) === "1");
  const [nowHearing, setNowHearing] = useState<Det[]>([]);
  const [liveLog, setLiveLog] = useState<LogEntry[]>(() => loadJSON<LogEntry[]>(SESS_LOG_KEY, []));
  const [sessionSpecies, setSessionSpecies] = useState<SpeciesRow[]>(() => loadJSON<SpeciesRow[]>(SESS_SPECIES_KEY, []));
  const [analyzerUp, setAnalyzerUp] = useState<boolean | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Bumped only when the SET of spectrogram markers changes (one resolves to a
  // bird, or one scrolls off) — NOT every frame. Per-frame x updates are done
  // imperatively in <Spectrogram>, so this stays a handful of renders a second.
  const [markerEpoch, setMarkerEpoch] = useState(0);

  const ctxRef = useRef<AudioContext | null>(null);
  const nodeRef = useRef<ScriptProcessorNode | null>(null);
  const srcRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const specCanvasRef = useRef<HTMLCanvasElement | null>(null);
  if (!specCanvasRef.current && typeof document !== "undefined") {
    const cv = document.createElement("canvas");
    cv.width = SPEC_W; cv.height = SPEC_H;
    const cx = cv.getContext("2d");
    if (cx) { cx.fillStyle = "#0b1020"; cx.fillRect(0, 0, SPEC_W, SPEC_H); }
    specCanvasRef.current = cv;
  }
  const specRafRef = useRef<number | null>(null);
  const specMarkersRef = useRef<SpecMarker[]>([]);
  const markerSeqRef = useRef(0);
  const specHistRef = useRef<SpecCol[]>([]);          // rolling energy heatmap for localization
  const lastColRef = useRef(0);                       // performance.now of the last retained column
  const seriesRef = useRef<Map<string, SeriesEntry>>(new Map()); // per-species event state
  const lastDrawRef = useRef(0); // performance.now of the previous scroll frame
  const carryRef = useRef(0);    // sub-pixel scroll remainder, so px/s is exact over time
  const bufRef = useRef<Float32Array[]>([]);
  const bufLenRef = useRef(0);
  const sinceStepRef = useRef(0);
  const posRef = useRef<{ lat: number; lon: number; address: string } | null>(null);
  const watchRef = useRef<number | null>(null);
  const timerRef = useRef<number | null>(null);
  const wakeRef = useRef<WakeLockSentinel | null>(null);
  const inFlightRef = useRef(0);
  const deviceRef = useRef(device);
  deviceRef.current = device;
  const recordingRef = useRef(recording);
  recordingRef.current = recording;
  const nowHearingRef = useRef(nowHearing);
  nowHearingRef.current = nowHearing;

  // Live broadcast: while listening, a SHARED device pushes its rolling
  // spectrogram columns + current detections so anyone who can see this device
  // can watch the feed live on another screen. Private devices never broadcast.
  useEffect(() => {
    if (!recording) return;
    const tick = () => {
      const dev = deviceRef.current;
      if (!dev.shared || !dev.token) return;
      const hist = specHistRef.current;
      if (!hist.length) return;
      const cols = hist.slice(-150).map((c) => c.bands.map((b) => Math.round(b)));
      const hearing = (nowHearingRef.current || []).map((d) => ({
        common_name: d.common_name, scientific_name: d.scientific_name,
        confidence: d.confidence, is_bird: d.is_bird,
      }));
      fetch(`${API}/live/publish`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ token: dev.token, cols, hearing }),
      }).catch(() => { /* transient */ });
    };
    const id = window.setInterval(tick, 800);
    return () => clearInterval(id);
  }, [recording]);

  // Pull this device's authoritative settings from the server and adopt them,
  // overwriting the localStorage cache. This is what makes the server the source
  // of truth: set this device to "fixed" from your laptop (or the Pi admin view)
  // and the next hydrate here flips it, no matter what this browser had cached.
  // Skipped while recording so a refresh mid-session can't yank the live mode.
  const hydrateFromServer = useCallback(() => {
    const tok = deviceRef.current.token;
    if (!tok || recordingRef.current) return;
    fetch(`${API}/device?token=${encodeURIComponent(tok)}`)
      .then((r) => r.json())
      .then((res) => {
        const sd = res.device;
        if (!sd) return; // not registered server-side yet; keep the local cache
        const mode: DeviceMode = sd.mode === "fixed" ? "fixed" : "adaptive";
        const fixedLocationId = sd.fixed_location_id ? String(sd.fixed_location_id) : "";
        const shared = !!sd.shared;
        const name = sd.name || deviceRef.current.name;
        userStorage.local.setItem(MODE_KEY, mode);
        userStorage.local.setItem(FIXED_KEY, fixedLocationId);
        userStorage.local.setItem(SHARED_KEY, shared ? "1" : "0");
        if (name) userStorage.local.setItem(NAME_KEY, name);
        setDevice((d) => ({ ...d, mode, fixedLocationId, shared, name }));
      })
      .catch(() => { /* offline: keep cache */ });
  }, []);

  // Poll the analyzer health, don't just check once at mount. A single failed
  // probe (e.g. the backend was briefly restarting) used to set analyzerUp=false
  // permanently, which DISABLES the Listen button until a manual reload. Re-probing
  // every 20s lets the button recover on its own once the analyzer is reachable.
  //
  // whenHidden: "continue" is deliberate, and it is the opposite of the call
  // made for LiveFeed.tsx's 1.2s poll on the same feature. LiveFeed is the
  // WATCHING side: nobody is looking at a hidden tab, so pausing costs nothing.
  // This is the BROADCASTING side, and a phone whose screen has slept is its
  // normal operating state. If this probe pauses, analyzerUp is frozen at
  // whatever it was when the screen went off, so the Listen button cannot
  // recover on its own, which is the exact defect the poll was added to fix.
  usePoll(() => fetch(`${API}/analyze/health`).then((r) => r.json())
    .then((h) => setAnalyzerUp(!!h.up))
    .catch(() => setAnalyzerUp(false)),
    20000, { whenHidden: "continue" });

  useEffect(() => {
    // Refresh this device's user-agent on load so it can be identified ("Chrome
    // on macOS") without having to start listening first. update_only: never
    // creates a row, so merely opening the app can't register a phantom device.
    const d = deviceRef.current;
    if (d.token && typeof navigator !== "undefined" && navigator.userAgent) {
      const fd = new FormData();
      fd.append("token", d.token);
      fd.append("user_agent", navigator.userAgent);
      fd.append("update_only", "1");
      fetch(`${API}/device`, { method: "POST", body: fd }).catch(() => {});
    }
    hydrateFromServer();
  }, [hydrateFromServer]);

  // Re-hydrate when the tab is brought back to the foreground, so a setting
  // changed on another screen takes effect here without a manual reload.
  useEffect(() => {
    const onVis = () => { if (document.visibilityState === "visible") hydrateFromServer(); };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [hydrateFromServer]);

  // Spectrogram draw loop lives HERE (provider is always mounted) so the image
  // keeps scrolling and its history persists while you move between pages.
  useEffect(() => {
    const cv = specCanvasRef.current;
    if (!cv) return;
    const cx = cv.getContext("2d");
    if (!cx) return;
    if (!recording) {
      cx.fillStyle = "#0b1020"; cx.fillRect(0, 0, cv.width, cv.height);
      return;
    }
    lastDrawRef.current = performance.now();
    carryRef.current = 0;
    const loop = () => {
      specRafRef.current = requestAnimationFrame(loop);
      const an = analyserRef.current;
      if (!an) return;
      const bins = an.frequencyBinCount;
      const data = new Uint8Array(bins);
      an.getByteFrequencyData(data);
      const nyq = (an.context.sampleRate || 48000) / 2;
      const topBin = Math.min(bins, Math.ceil((SPEC_MAX_HZ / nyq) * bins));

      // Scroll by elapsed TIME, not one pixel per frame — so px/s is constant
      // regardless of framerate. The carry preserves the sub-pixel remainder so
      // the rate stays exact over time; the clamp avoids smearing a huge block
      // after a stall (e.g. the tab was backgrounded).
      const now = performance.now();
      const dt = Math.min(0.25, (now - lastDrawRef.current) / 1000);
      lastDrawRef.current = now;
      carryRef.current += dt * SPEC_PX_PER_SEC;
      const dx = Math.floor(carryRef.current);
      carryRef.current -= dx;

      if (dx > 0) {
        cx.drawImage(cv, -dx, 0);
        for (let y = 0; y < cv.height; y++) {
          const bin = Math.floor((1 - y / cv.height) * topBin);
          const v = data[Math.max(0, Math.min(topBin - 1, bin))] || 0;
          cx.fillStyle = specColor(v);
          cx.fillRect(cv.width - dx, y, dx, 1);
        }
      }

      // Retain a reduced energy column (~33/s) so detections can be localized
      // against the actual heatmap; trim to the last few seconds.
      if (now - lastColRef.current >= SPEC_COL_MS) {
        lastColRef.current = now;
        const hist = specHistRef.current;
        hist.push({ t: now, bands: reduceColumn(data, topBin) });
        const cutoff = now - SPEC_HIST_MS;
        while (hist.length && hist[0].t < cutoff) hist.shift();
      }

      // Ease each pin toward its latest localized target (so it slides to the
      // refined spot as a burst completes), then drop ones scrolled off the
      // left. Position derives from time, never accumulated, so it can't drift.
      const ms = specMarkersRef.current;
      for (const m of ms) {
        const d = m.tTarget - m.tDetect;
        m.tDetect = Math.abs(d) < 2 ? m.tTarget : m.tDetect + d * TARGET_EASE;
      }
      if (ms.length && ms.some((m) => specMarkerX(m, now) < -8)) {
        specMarkersRef.current = ms.filter((m) => specMarkerX(m, now) >= -8);
        setMarkerEpoch((e) => e + 1);
      }
    };
    loop();
    return () => { if (specRafRef.current) cancelAnimationFrame(specRafRef.current); };
  }, [recording]);

  // Mirror the live session view to storage so a reload restores it.
  useEffect(() => { userStorage.local.setItem(SESS_LOG_KEY, JSON.stringify(liveLog.slice(0, 200))); }, [liveLog]);
  useEffect(() => { userStorage.local.setItem(SESS_SPECIES_KEY, JSON.stringify(sessionSpecies)); }, [sessionSpecies]);

  const setDeviceName = useCallback((n: string) => {
    const name = n.trim();
    userStorage.local.setItem(NAME_KEY, name);
    setDevice((d) => { const nd = { ...d, name }; postDevice(nd); return nd; });
  }, []);
  const adoptDevice = useCallback((token: string, name: string) => {
    userStorage.local.setItem(TOKEN_KEY, token);
    userStorage.local.setItem(NAME_KEY, name);
    setDevice((d) => { const nd = { ...d, token, name }; postDevice(nd); return nd; });
  }, []);
  const setMode = useCallback((mode: DeviceMode) => {
    userStorage.local.setItem(MODE_KEY, mode);
    setDevice((d) => { const nd = { ...d, mode }; postDevice(nd); return nd; });
  }, []);
  const setFixedLocation = useCallback((id: string) => {
    userStorage.local.setItem(FIXED_KEY, id);
    setDevice((d) => { const nd = { ...d, fixedLocationId: id }; postDevice(nd); return nd; });
  }, []);
  const setShared = useCallback((shared: boolean) => {
    userStorage.local.setItem(SHARED_KEY, shared ? "1" : "0");
    setDevice((d) => { const nd = { ...d, shared }; postDevice(nd); return nd; });
  }, []);

  // Group overlapping windows into per-species vocalization EVENTS, then jointly
  // localize all active events against the energy heatmap and (re)place each
  // event's pin. Called once per /analyze response with that window's post time.
  const ingestForMarkers = useCallback((dets: Det[], tPost: number) => {
    const now = performance.now();
    const series = seriesRef.current;
    // Plausible birds only: real taxa, not range-filtered, not muted. Deduped
    // windows still ride back and are kept — they fill in the confidence curve.
    const plausible = dets.filter(
      (d) => d.is_bird && d.in_range !== false && d.filtered !== "muted");
    for (const d of plausible) {
      let e = series.get(d.scientific_name);
      if (e && tPost - e.lastSeen > EVENT_GAP_MS) { series.delete(d.scientific_name); e = undefined; }
      if (!e) {
        const markerId = ++markerSeqRef.current;
        specMarkersRef.current.push({
          id: markerId, tDetect: tPost - 1500, tTarget: tPost - 1500, yFrac: 0.5,
          common: d.common_name, sci: d.scientific_name, conf: d.confidence, mode: "none",
        });
        e = { common: d.common_name, samples: [], lastSeen: tPost, markerId };
        series.set(d.scientific_name, e);
      }
      e.samples.push({ t: tPost, conf: d.confidence });
      if (e.samples.length > 8) e.samples.shift();
      e.lastSeen = tPost;
    }
    // Retire events gone silent (their pins stay put and scroll off naturally).
    for (const [sci, e] of [...series]) {
      if (now - e.lastSeen > EVENT_GAP_MS) series.delete(sci);
    }
    if (!series.size) return;
    const events: SpeciesEvent[] = [...series.entries()].map(
      ([sci, e]) => ({ sci, common: e.common, samples: e.samples }));
    const placements = localizeEvents(events, specHistRef.current);
    for (const p of placements) {
      const e = series.get(p.sci);
      if (!e) continue;
      const mk = specMarkersRef.current.find((m) => m.id === e.markerId);
      if (mk) {
        mk.tTarget = p.t; mk.yFrac = p.yFrac; mk.mode = p.mode;
        mk.conf = Math.max(...e.samples.map((s) => s.conf));
      }
    }
    setMarkerEpoch((x) => x + 1);
  }, []);

  const sendWindow = useCallback((samples: Float32Array, sampleRate: number) => {
    const dev = deviceRef.current;
    const wav = encodeWav(samples, sampleRate);
    const fd = new FormData();
    fd.append("audio", wav, "chunk.wav");
    fd.append("token", dev.token);
    // Only send a real, user-given name. An unnamed device stays unnamed: the
    // backend will NOT persist a placeholder "Untitled device" row from it, so
    // ephemeral/test sessions never pollute the device list. Detections are still
    // attributed by token regardless.
    if (dev.name) fd.append("device_name", dev.name);
    fd.append("mode", dev.mode);
    fd.append("min_conf", "0.5");  // backend enforces its own floor too (smart_settings)
    if (dev.mode === "fixed" && dev.fixedLocationId) fd.append("location_id", dev.fixedLocationId);
    if (dev.mode !== "fixed" && posRef.current) {
      fd.append("lat", String(posRef.current.lat));
      fd.append("lon", String(posRef.current.lon));
      if (posRef.current.address) fd.append("address", posRef.current.address);
    }
    inFlightRef.current += 1;
    setStatus("analyzing");
    // The window covers audio [tPost-3s, tPost]; tPost is the localization clock
    // for this window's confidence sample (same performance.now the spectrogram
    // and markers use).
    const tPost = performance.now();
    fetch(`${API}/analyze`, { method: "POST", body: fd })
      .then((r) => r.json())
      .then((res) => {
        if (res.location) setLocation(res.location);
        setNeedsLocation(!!res.needs_location);
        const dets: Det[] = res.detections || [];
        setNowHearing(dets);
        ingestForMarkers(dets, tPost);
        const stored = dets.filter((d) => d.stored !== false);
        if (stored.length) {
          const now = new Date().toLocaleTimeString();
          setLiveLog((prev) => [...stored.map((d) => ({ ...d, at: now })), ...prev].slice(0, 200));
          setSessionSpecies((prev) => {
            const map = new Map(prev.map((s) => [s.scientific_name, { ...s }]));
            for (const d of stored) {
              const ex = map.get(d.scientific_name);
              if (ex) { ex.hits += 1; ex.max_conf = Math.max(ex.max_conf, d.confidence); }
              else map.set(d.scientific_name, {
                scientific_name: d.scientific_name, common_name: d.common_name,
                hits: 1, max_conf: d.confidence, is_bird: d.is_bird,
              });
            }
            return [...map.values()].sort((a, b) => b.max_conf - a.max_conf);
          });
        }
      })
      .catch(() => { /* transient */ })
      .finally(() => {
        inFlightRef.current -= 1;
        setStatus(inFlightRef.current > 0 ? "analyzing" : "listening");
      });
  }, [ingestForMarkers]);

  const acquireWake = useCallback(async () => {
    try { wakeRef.current = (await navigator.wakeLock?.request("screen")) || null; } catch { /* unsupported */ }
  }, []);

  const stop = useCallback(() => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    if (watchRef.current != null) { navigator.geolocation.clearWatch(watchRef.current); watchRef.current = null; }
    nodeRef.current?.disconnect();
    srcRef.current?.disconnect();
    analyserRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    ctxRef.current?.close().catch(() => {});
    wakeRef.current?.release().catch(() => {});
    nodeRef.current = null; srcRef.current = null; streamRef.current = null;
    analyserRef.current = null; ctxRef.current = null; wakeRef.current = null;
    bufRef.current = []; bufLenRef.current = 0; sinceStepRef.current = 0; inFlightRef.current = 0;
    specMarkersRef.current = []; specHistRef.current = []; seriesRef.current.clear();
    setMarkerEpoch((e) => e + 1);
    setRecording(false); setStatus("idle"); setNowHearing([]); setNeedsLocation(false);
    setWasListening(false); userStorage.local.setItem(WAS_KEY, "0");
  }, []);

  const start = useCallback(async () => {
    setErr(null); setLiveLog([]); setSessionSpecies([]); setNowHearing([]);
    setElapsed(0); setLocation(null); setNeedsLocation(false);
    setWasListening(true); userStorage.local.setItem(WAS_KEY, "1");
    posRef.current = null;
    const dev = deviceRef.current;
    postDevice(dev);
    // Adaptive devices TRACK position for the whole session (watchPosition, not a
    // one-shot), so as you move, detections follow you - the backend merges into
    // a nearby location within ~40 m or creates a new one past that. Re-geocode
    // the name only after moving >40 m to avoid hammering the geocoder.
    if (dev.mode !== "fixed" && navigator.geolocation) {
      watchRef.current = navigator.geolocation.watchPosition(
        async (p) => {
          const lat = p.coords.latitude, lon = p.coords.longitude;
          const prev = posRef.current;
          posRef.current = { lat, lon, address: prev?.address || "" };
          if (!prev || distM(prev.lat, prev.lon, lat, lon) > 40) {
            const place = await reverseGeocode(lat, lon);
            if (place && posRef.current) posRef.current = { lat: posRef.current.lat, lon: posRef.current.lon, address: place.short };
          }
        },
        () => { /* permission denied / unavailable: backend will prompt for a location */ },
        { enableHighAccuracy: true, maximumAge: 10000, timeout: 15000 },
      );
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      });
      streamRef.current = stream;
      const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctor();
      await ctx.resume();
      ctxRef.current = ctx;
      const src = ctx.createMediaStreamSource(stream);
      srcRef.current = src;
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0.4;
      analyserRef.current = analyser;
      src.connect(analyser);
      const node = ctx.createScriptProcessor(4096, 1, 1);
      nodeRef.current = node;
      const windowSamples = Math.round(ctx.sampleRate * WINDOW_S);
      const stepSamples = Math.round(ctx.sampleRate * STEP_S);

      node.onaudioprocess = (e) => {
        const input = e.inputBuffer.getChannelData(0);
        bufRef.current.push(new Float32Array(input));
        bufLenRef.current += input.length;
        sinceStepRef.current += input.length;
        // Trim the rolling buffer to the last WINDOW_S seconds.
        while (bufLenRef.current - bufRef.current[0].length >= windowSamples && bufRef.current.length > 1) {
          bufLenRef.current -= bufRef.current[0].length;
          bufRef.current.shift();
        }
        // Every STEP_S seconds, post the current window.
        if (sinceStepRef.current >= stepSamples) {
          sinceStepRef.current = 0;
          const merged = new Float32Array(bufLenRef.current);
          let off = 0;
          for (const b of bufRef.current) { merged.set(b, off); off += b.length; }
          sendWindow(merged, ctx.sampleRate);
        }
      };
      src.connect(node);
      node.connect(ctx.destination); // some browsers require a sink for onaudioprocess
      setRecording(true); setStatus("listening");
      acquireWake();
      timerRef.current = window.setInterval(() => {
        setElapsed((s) => { if (s + 1 >= MAX_SECONDS) stop(); return s + 1; });
      }, 1000);
    } catch (e) {
      setErr(`Mic access failed: ${e instanceof Error ? e.message : String(e)}`);
      stop();
    }
  }, [sendWindow, acquireWake, stop]);

  // Re-acquire the wake lock if the tab is re-shown while recording.
  useEffect(() => {
    const onVis = () => { if (document.visibilityState === "visible" && recording && !wakeRef.current) acquireWake(); };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [recording, acquireWake]);

  const value = useMemo<Ctx>(() => ({
    device, setDeviceName, adoptDevice, setMode, setFixedLocation, setShared,
    recording, status, elapsed, location, needsLocation, wasListening,
    nowHearing, liveLog, sessionSpecies,
    analyzerUp, err, analyserRef, specCanvasRef, specMarkersRef, markerEpoch, start, stop,
  }), [device, setDeviceName, adoptDevice, setMode, setFixedLocation, setShared, recording, status, elapsed,
    location, needsLocation, wasListening, nowHearing, liveLog, sessionSpecies, analyzerUp, err,
    markerEpoch, start, stop]);

  return <RecorderContext.Provider value={value}>{children}</RecorderContext.Provider>;
}
