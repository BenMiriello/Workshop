import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import { reverseGeocode } from "./geocode";

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
const STEP_S = 1;
const MAX_SECONDS = 3600; // safety auto-stop at 1h

const TOKEN_KEY = "birdDeviceToken";
const NAME_KEY = "birdDeviceName";
const MODE_KEY = "birdDeviceMode";
const FIXED_KEY = "birdDeviceFixedLoc";

export type DeviceMode = "adaptive" | "fixed";
export type Device = { token: string; name: string; mode: DeviceMode; fixedLocationId: string };
export type Det = {
  scientific_name: string; common_name: string; confidence: number;
  is_bird: boolean; stored?: boolean;
};
export type LogEntry = Det & { at: string };
export type SpeciesRow = {
  scientific_name: string; common_name: string; hits: number; max_conf: number; is_bird: boolean;
};

type Ctx = {
  device: Device;
  setDeviceName: (n: string) => void;
  setMode: (m: DeviceMode) => void;
  setFixedLocation: (id: string) => void;
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
  start: () => Promise<void>;
  stop: () => void;
};

const RecorderContext = createContext<Ctx | null>(null);
export const useRecorder = () => {
  const c = useContext(RecorderContext);
  if (!c) throw new Error("useRecorder must be used inside <RecorderProvider>");
  return c;
};

function loadDevice(): Device {
  let token = localStorage.getItem(TOKEN_KEY) || localStorage.getItem("birdStationToken") || "";
  if (!token) token = crypto.randomUUID?.() || String(Math.random()).slice(2);
  localStorage.setItem(TOKEN_KEY, token);
  const name = localStorage.getItem(NAME_KEY) ?? localStorage.getItem("birdStationLabel") ?? "";
  const mode = (localStorage.getItem(MODE_KEY) as DeviceMode) || "adaptive";
  const fixedLocationId = localStorage.getItem(FIXED_KEY) || "";
  return { token, name, mode, fixedLocationId };
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
const SPEC_W = 600, SPEC_H = 160, SPEC_MAX_HZ = 12000;
function specColor(v: number): string {
  const t = v / 255;
  if (t < 0.25) { const k = t / 0.25; return `rgb(${10 + k * 10},${20 + k * 60},${40 + k * 60})`; }
  if (t < 0.55) { const k = (t - 0.25) / 0.3; return `rgb(20,${80 + k * 90},${100 + k * 60})`; }
  if (t < 0.8) { const k = (t - 0.55) / 0.25; return `rgb(${20 + k * 200},${170 + k * 60},${160 - k * 100})`; }
  const k = (t - 0.8) / 0.2; return `rgb(${220 + k * 35},${230 + k * 25},${60 + k * 195})`;
}

// Session view (live log + species + "was listening") is mirrored to localStorage
// so it survives a full page reload, not just navigation.
const SESS_SPECIES_KEY = "birdSessionSpecies";
const SESS_LOG_KEY = "birdSessionLog";
const WAS_KEY = "birdWasListening";
function loadJSON<T>(k: string, fallback: T): T {
  try { const r = localStorage.getItem(k); return r ? (JSON.parse(r) as T) : fallback; } catch { return fallback; }
}

function getPosition(): Promise<{ lat: number; lon: number } | null> {
  return new Promise((res) => {
    if (!navigator.geolocation) return res(null);
    navigator.geolocation.getCurrentPosition(
      (p) => res({ lat: p.coords.latitude, lon: p.coords.longitude }),
      () => res(null),
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 },
    );
  });
}

async function postDevice(d: Device) {
  const fd = new FormData();
  fd.append("token", d.token);
  if (d.name) fd.append("name", d.name);
  fd.append("mode", d.mode);
  if (d.mode === "fixed" && d.fixedLocationId) fd.append("fixed_location_id", d.fixedLocationId);
  try { await fetch(`${API}/device`, { method: "POST", body: fd }); } catch { /* offline ok */ }
}

export function RecorderProvider({ children }: { children: React.ReactNode }) {
  const [device, setDevice] = useState<Device>(loadDevice);
  const [recording, setRecording] = useState(false);
  const [status, setStatus] = useState<"idle" | "listening" | "analyzing">("idle");
  const [elapsed, setElapsed] = useState(0);
  const [location, setLocation] = useState<string | null>(null);
  const [needsLocation, setNeedsLocation] = useState(false);
  const [wasListening, setWasListening] = useState<boolean>(() => localStorage.getItem(WAS_KEY) === "1");
  const [nowHearing, setNowHearing] = useState<Det[]>([]);
  const [liveLog, setLiveLog] = useState<LogEntry[]>(() => loadJSON<LogEntry[]>(SESS_LOG_KEY, []));
  const [sessionSpecies, setSessionSpecies] = useState<SpeciesRow[]>(() => loadJSON<SpeciesRow[]>(SESS_SPECIES_KEY, []));
  const [analyzerUp, setAnalyzerUp] = useState<boolean | null>(null);
  const [err, setErr] = useState<string | null>(null);

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
  const bufRef = useRef<Float32Array[]>([]);
  const bufLenRef = useRef(0);
  const sinceStepRef = useRef(0);
  const posRef = useRef<{ lat: number; lon: number; address: string } | null>(null);
  const timerRef = useRef<number | null>(null);
  const wakeRef = useRef<WakeLockSentinel | null>(null);
  const inFlightRef = useRef(0);
  const deviceRef = useRef(device);
  deviceRef.current = device;

  useEffect(() => {
    fetch(`${API}/analyze/health`).then((r) => r.json())
      .then((h) => setAnalyzerUp(!!h.up)).catch(() => setAnalyzerUp(false));
  }, []);

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
    const loop = () => {
      specRafRef.current = requestAnimationFrame(loop);
      const an = analyserRef.current;
      if (!an) return;
      const bins = an.frequencyBinCount;
      const data = new Uint8Array(bins);
      an.getByteFrequencyData(data);
      cx.drawImage(cv, -1, 0);
      const nyq = (an.context.sampleRate || 48000) / 2;
      const topBin = Math.min(bins, Math.ceil((SPEC_MAX_HZ / nyq) * bins));
      for (let y = 0; y < cv.height; y++) {
        const bin = Math.floor((1 - y / cv.height) * topBin);
        cx.fillStyle = specColor(data[Math.max(0, Math.min(topBin - 1, bin))] || 0);
        cx.fillRect(cv.width - 1, y, 1, 1);
      }
    };
    loop();
    return () => { if (specRafRef.current) cancelAnimationFrame(specRafRef.current); };
  }, [recording]);

  // Mirror the live session view to localStorage so a reload restores it.
  useEffect(() => { try { localStorage.setItem(SESS_LOG_KEY, JSON.stringify(liveLog.slice(0, 200))); } catch { /* quota */ } }, [liveLog]);
  useEffect(() => { try { localStorage.setItem(SESS_SPECIES_KEY, JSON.stringify(sessionSpecies)); } catch { /* quota */ } }, [sessionSpecies]);

  const setDeviceName = useCallback((n: string) => {
    const name = n.trim();
    localStorage.setItem(NAME_KEY, name);
    setDevice((d) => { const nd = { ...d, name }; postDevice(nd); return nd; });
  }, []);
  const setMode = useCallback((mode: DeviceMode) => {
    localStorage.setItem(MODE_KEY, mode);
    setDevice((d) => { const nd = { ...d, mode }; postDevice(nd); return nd; });
  }, []);
  const setFixedLocation = useCallback((id: string) => {
    localStorage.setItem(FIXED_KEY, id);
    setDevice((d) => { const nd = { ...d, fixedLocationId: id }; postDevice(nd); return nd; });
  }, []);

  const sendWindow = useCallback((samples: Float32Array, sampleRate: number) => {
    const dev = deviceRef.current;
    const wav = encodeWav(samples, sampleRate);
    const fd = new FormData();
    fd.append("audio", wav, "chunk.wav");
    fd.append("token", dev.token);
    fd.append("device_name", dev.name || "Untitled device");
    fd.append("mode", dev.mode);
    fd.append("min_conf", "0.25");
    if (dev.mode === "fixed" && dev.fixedLocationId) fd.append("location_id", dev.fixedLocationId);
    if (dev.mode !== "fixed" && posRef.current) {
      fd.append("lat", String(posRef.current.lat));
      fd.append("lon", String(posRef.current.lon));
      if (posRef.current.address) fd.append("address", posRef.current.address);
    }
    inFlightRef.current += 1;
    setStatus("analyzing");
    fetch(`${API}/analyze`, { method: "POST", body: fd })
      .then((r) => r.json())
      .then((res) => {
        if (res.location) setLocation(res.location);
        setNeedsLocation(!!res.needs_location);
        const dets: Det[] = res.detections || [];
        setNowHearing(dets);
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
  }, []);

  const acquireWake = useCallback(async () => {
    try { wakeRef.current = (await navigator.wakeLock?.request("screen")) || null; } catch { /* unsupported */ }
  }, []);

  const stop = useCallback(() => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    nodeRef.current?.disconnect();
    srcRef.current?.disconnect();
    analyserRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    ctxRef.current?.close().catch(() => {});
    wakeRef.current?.release().catch(() => {});
    nodeRef.current = null; srcRef.current = null; streamRef.current = null;
    analyserRef.current = null; ctxRef.current = null; wakeRef.current = null;
    bufRef.current = []; bufLenRef.current = 0; sinceStepRef.current = 0; inFlightRef.current = 0;
    setRecording(false); setStatus("idle"); setNowHearing([]); setNeedsLocation(false);
    setWasListening(false); localStorage.setItem(WAS_KEY, "0");
  }, []);

  const start = useCallback(async () => {
    setErr(null); setLiveLog([]); setSessionSpecies([]); setNowHearing([]);
    setElapsed(0); setLocation(null); setNeedsLocation(false);
    setWasListening(true); localStorage.setItem(WAS_KEY, "1");
    posRef.current = null;
    const dev = deviceRef.current;
    postDevice(dev);
    if (dev.mode !== "fixed") getPosition().then(async (p) => {
      if (!p) return;
      posRef.current = { lat: p.lat, lon: p.lon, address: "" };
      const place = await reverseGeocode(p.lat, p.lon); // name the location by its real address
      if (place && posRef.current) posRef.current = { lat: p.lat, lon: p.lon, address: place.short };
    });
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
    device, setDeviceName, setMode, setFixedLocation,
    recording, status, elapsed, location, needsLocation, wasListening,
    nowHearing, liveLog, sessionSpecies,
    analyzerUp, err, analyserRef, specCanvasRef, start, stop,
  }), [device, setDeviceName, setMode, setFixedLocation, recording, status, elapsed,
    location, needsLocation, wasListening, nowHearing, liveLog, sessionSpecies, analyzerUp, err, start, stop]);

  return <RecorderContext.Provider value={value}>{children}</RecorderContext.Provider>;
}
