import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Mic, Square, Radio, Loader2, Volume2, BarChart3 } from "lucide-react";
import { useShowNonBird } from "./settings";

// Browser mic-test: record from THIS device's mic and get live BirdNET
// detections, no always-on rig. Audio is captured as raw PCM, sliced into
// rolling ~3s windows (BirdNET's native window), each posted to the backend
// which runs the resident analyzer and returns detections we append live.
//
// Detections are stored server-side under a named TEST STATION (this device),
// kept fully separate from the yard data and never published.

const API = "/api/birds";
const CHUNK_SECONDS = 3;
const MAX_SECONDS = 600; // safety auto-stop at 10 min

type Det = { scientific_name: string; common_name: string; confidence: number; is_bird: boolean };
type LogEntry = Det & { at: string };
type SpeciesRow = { scientific_name: string; common_name: string; hits: number; max_conf: number; is_bird: boolean };

const pct = (c: number) => `${Math.round(c * 100)}%`;
const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

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

export default function MicTest() {
  const [station, setStation] = useState<string>(() => localStorage.getItem("birdStationLabel") || "");
  const [token] = useState<string>(() => {
    let t = localStorage.getItem("birdStationToken");
    if (!t) { t = (crypto.randomUUID?.() || String(Math.random()).slice(2)); localStorage.setItem("birdStationToken", t); }
    return t;
  });
  const [labelInput, setLabelInput] = useState("");
  const [recording, setRecording] = useState(false);
  const [status, setStatus] = useState<"idle" | "listening" | "analyzing">("idle");
  const [elapsed, setElapsed] = useState(0);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [species, setSpecies] = useState<SpeciesRow[]>([]);
  const [analyzerUp, setAnalyzerUp] = useState<boolean | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [inFlight, setInFlight] = useState(0);
  const [showNonBird] = useShowNonBird();

  // Everything is stored in state; the non-bird filter is applied at render so
  // toggling the setting updates the view instantly with no re-fetch.
  const visibleLog = useMemo(() => showNonBird ? log : log.filter((d) => d.is_bird), [log, showNonBird]);
  const visibleSpecies = useMemo(() => showNonBird ? species : species.filter((s) => s.is_bird), [species, showNonBird]);
  const hiddenCount = log.length - visibleLog.length;

  const ctxRef = useRef<AudioContext | null>(null);
  const nodeRef = useRef<ScriptProcessorNode | null>(null);
  const srcRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const bufRef = useRef<Float32Array[]>([]);
  const bufLenRef = useRef(0);
  const sessionStartRef = useRef<string>("");
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    fetch(`${API}/analyze/health`).then((r) => r.json())
      .then((h) => setAnalyzerUp(!!h.up)).catch(() => setAnalyzerUp(false));
  }, []);

  const saveStation = useCallback(() => {
    const label = labelInput.trim();
    if (!label) return;
    localStorage.setItem("birdStationLabel", label);
    setStation(label);
    const fd = new FormData(); fd.append("token", token); fd.append("label", label);
    fetch(`${API}/analyze/station`, { method: "POST", body: fd }).catch(() => {});
  }, [labelInput, token]);

  const sendChunk = useCallback((samples: Float32Array, sampleRate: number) => {
    const wav = encodeWav(samples, sampleRate);
    const fd = new FormData();
    fd.append("audio", wav, "chunk.wav");
    fd.append("station", station || "Mic test");
    fd.append("token", token);
    fd.append("min_conf", "0.25");
    setInFlight((n) => n + 1);
    setStatus("analyzing");
    fetch(`${API}/analyze`, { method: "POST", body: fd })
      .then((r) => r.json())
      .then((res) => {
        const dets: Det[] = res.detections || [];
        if (dets.length) {
          const now = new Date().toLocaleTimeString();
          setLog((prev) => [...dets.map((d) => ({ ...d, at: now })), ...prev].slice(0, 200));
          setSpecies((prev) => {
            const map = new Map(prev.map((s) => [s.scientific_name, s]));
            for (const d of dets) {
              const ex = map.get(d.scientific_name);
              if (ex) { ex.hits += 1; ex.max_conf = Math.max(ex.max_conf, d.confidence); }
              else map.set(d.scientific_name, { scientific_name: d.scientific_name, common_name: d.common_name, hits: 1, max_conf: d.confidence, is_bird: d.is_bird });
            }
            return [...map.values()].sort((a, b) => b.max_conf - a.max_conf);
          });
        }
      })
      .catch(() => {})
      .finally(() => setInFlight((n) => { const v = n - 1; setStatus(v > 0 ? "analyzing" : "listening"); return v; }));
  }, [station, token]);

  const stop = useCallback(() => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    nodeRef.current?.disconnect();
    srcRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    ctxRef.current?.close().catch(() => {});
    nodeRef.current = null; srcRef.current = null; streamRef.current = null; ctxRef.current = null;
    bufRef.current = []; bufLenRef.current = 0;
    setRecording(false); setStatus("idle");
  }, []);

  const start = useCallback(async () => {
    setErr(null); setLog([]); setSpecies([]); setElapsed(0);
    sessionStartRef.current = new Date().toISOString();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      });
      streamRef.current = stream;
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctx();
      await ctx.resume();
      ctxRef.current = ctx;
      const src = ctx.createMediaStreamSource(stream);
      srcRef.current = src;
      const node = ctx.createScriptProcessor(4096, 1, 1);
      nodeRef.current = node;
      const chunkSamples = Math.round(ctx.sampleRate * CHUNK_SECONDS);

      node.onaudioprocess = (e) => {
        const input = e.inputBuffer.getChannelData(0);
        bufRef.current.push(new Float32Array(input));
        bufLenRef.current += input.length;
        if (bufLenRef.current >= chunkSamples) {
          const merged = new Float32Array(bufLenRef.current);
          let off = 0;
          for (const b of bufRef.current) { merged.set(b, off); off += b.length; }
          bufRef.current = []; bufLenRef.current = 0;
          sendChunk(merged, ctx.sampleRate);
        }
      };
      src.connect(node);
      node.connect(ctx.destination); // required for onaudioprocess to fire in some browsers
      setRecording(true); setStatus("listening");
      timerRef.current = window.setInterval(() => {
        setElapsed((s) => { if (s + 1 >= MAX_SECONDS) stop(); return s + 1; });
      }, 1000);
    } catch (e) {
      setErr(`Mic access failed: ${e instanceof Error ? e.message : String(e)}`);
      stop();
    }
  }, [sendChunk, stop]);

  useEffect(() => () => stop(), [stop]);

  // Station not yet named on this device -> setup card.
  if (!station) {
    return (
      <section className="card" style={card}>
        <h3 style={{ marginTop: 0, display: "flex", alignItems: "center", gap: ".4rem" }}>
          <Mic size={18} /> Test with this device's mic
        </h3>
        <p className="muted" style={{ marginTop: 0 }}>
          Name this device or location. It is kept separate from the yard and stays on this device
          (a stranger opening this page won't see or pick your location).
        </p>
        <div style={{ display: "flex", gap: ".5rem", flexWrap: "wrap" }}>
          <input className="num-in" placeholder="e.g. Phone — back deck" value={labelInput}
            onChange={(e) => setLabelInput(e.target.value)} style={{ flex: 1, minWidth: 180 }} />
          <button className="pill" onClick={saveStation} style={{ cursor: "pointer" }}>Use this device</button>
        </div>
      </section>
    );
  }

  return (
    <section className="card" style={card}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: ".5rem", flexWrap: "wrap" }}>
        <h3 style={{ margin: 0, display: "flex", alignItems: "center", gap: ".4rem", flexWrap: "wrap" }}>
          <Mic size={18} /> Mic test
          <span className="muted" style={{ fontWeight: 400, fontSize: ".85rem" }}>· {station}</span>
          <Link to={`/apps/birds/station?s=${encodeURIComponent(station)}`}
            className="muted" style={{ display: "inline-flex", alignItems: "center", gap: ".2rem", fontSize: ".8rem", fontWeight: 400 }}>
            <BarChart3 size={13} /> view all
          </Link>
        </h3>
        {!recording ? (
          <button className="pill" onClick={start} disabled={analyzerUp === false}
            style={{ cursor: "pointer", background: "#1f6feb", color: "#fff", borderColor: "#1f6feb", display: "inline-flex", alignItems: "center", gap: ".35rem" }}>
            <Mic size={15} /> Start listening
          </button>
        ) : (
          <button className="pill" onClick={stop}
            style={{ cursor: "pointer", display: "inline-flex", alignItems: "center", gap: ".35rem" }}>
            <Square size={14} /> Stop
          </button>
        )}
      </div>

      {analyzerUp === false && (
        <p className="error" style={{ marginBottom: 0 }}>
          Analyzer service is not running. Start it on the box: <code>birdnet/analyzer/service.py</code>.
        </p>
      )}
      {err && <p className="error" style={{ marginBottom: 0 }}>{err}</p>}

      {recording && (
        <div style={{ display: "flex", alignItems: "center", gap: ".6rem", margin: ".6rem 0" }}>
          {status === "analyzing"
            ? <Loader2 size={16} className="spin" />
            : <Radio size={16} style={{ color: "#16a34a" }} />}
          <span style={{ fontWeight: 500 }}>
            {status === "analyzing" ? "Analyzing…" : "Listening…"}
          </span>
          <span className="mono muted">{clock(elapsed)}</span>
          {inFlight > 0 && <span className="muted" style={{ fontSize: ".8rem" }}>· {inFlight} in flight</span>}
        </div>
      )}

      {/* This-session species summary */}
      {visibleSpecies.length > 0 && (
        <div style={{ margin: ".4rem 0 .6rem" }}>
          <div className="muted" style={{ fontSize: ".78rem", marginBottom: ".3rem" }}>Detected this session</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: ".4rem" }}>
            {visibleSpecies.map((s) => (
              <span key={s.scientific_name} className="pill" title={s.scientific_name}
                style={s.is_bird ? undefined : { opacity: 0.6, borderStyle: "dashed" }}>
                {s.common_name} <span className="muted">{pct(s.max_conf)}{s.hits > 1 ? ` ·${s.hits}` : ""}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Live log */}
      {recording && visibleLog.length === 0 && (
        <p className="muted" style={{ margin: ".3rem 0 0", fontSize: ".85rem" }}>
          <Volume2 size={13} style={{ verticalAlign: "-2px" }} /> Make some bird sound or wait for one. Updates every {CHUNK_SECONDS}s.
          {hiddenCount > 0 && <> {hiddenCount} non-bird {hiddenCount === 1 ? "detection" : "detections"} hidden — <Link to="/apps/birds/settings" className="muted" style={{ textDecoration: "underline" }}>settings</Link>.</>}
        </p>
      )}
      {visibleLog.length > 0 && (
        <>
          {!showNonBird && hiddenCount > 0 && (
            <p className="muted" style={{ margin: ".3rem 0 0", fontSize: ".78rem" }}>
              {hiddenCount} non-bird {hiddenCount === 1 ? "detection" : "detections"} hidden.{" "}
              <Link to="/apps/birds/settings" className="muted" style={{ textDecoration: "underline" }}>Show</Link>
            </p>
          )}
          <ul style={{ listStyle: "none", margin: ".3rem 0 0", padding: 0, maxHeight: 240, overflowY: "auto" }}>
            {visibleLog.map((d, i) => (
              <li key={i} style={{ display: "flex", alignItems: "center", gap: ".6rem", padding: ".3rem 0", borderTop: "1px solid var(--border,#1e2636)", opacity: d.is_bird ? 1 : 0.65 }}>
                <span className="mono muted" style={{ fontSize: ".78rem", minWidth: 78 }}>{d.at}</span>
                <span style={{ flex: 1 }}>
                  {d.common_name}
                  {!d.is_bird && <span className="muted" style={{ fontSize: ".72rem", marginLeft: ".4rem" }}>non-bird</span>}
                </span>
                <span className="pill">{pct(d.confidence)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

const card: React.CSSProperties = { padding: "1rem", marginBottom: "1rem", border: "1px solid #1f6feb55" };
