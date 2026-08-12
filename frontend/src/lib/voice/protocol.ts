// The wire format, mirrored from backend/services/voice/protocol.py.
//
// One socket carries everything: binary frames for audio, JSON for control and
// state. Audio is raw little-endian int16 mono PCM behind an 8-byte header, so
// there is no container, no codec negotiation and no Safari special case. The
// reason the old client needed `pickRecorderMime`, `extForMime` and a
// fragmented-mp4 path is that it shipped MediaRecorder containers, which is the
// same choice that forced audio to be cut by stopping the device.

export const HEADER_BYTES = 8;
export const KIND_MIC = 1;
export const KIND_TTS = 2;

export function encodeAudio(kind: number, cursor: number, pcm: Int16Array): ArrayBuffer {
  const buf = new ArrayBuffer(HEADER_BYTES + pcm.byteLength);
  const view = new DataView(buf);
  view.setUint8(0, kind);
  view.setUint8(1, 0);
  view.setUint16(2, 0, true);
  view.setUint32(4, cursor >>> 0, true);
  new Int16Array(buf, HEADER_BYTES).set(pcm);
  return buf;
}

export function decodeAudio(buf: ArrayBuffer): { kind: number; cursor: number; pcm: Int16Array } {
  const view = new DataView(buf);
  const kind = view.getUint8(0);
  const cursor = view.getUint32(4, true);
  // Copy rather than view: the caller hands this to an AudioWorklet, and a view
  // onto a transferred buffer is a use-after-free waiting to happen.
  const pcm = new Int16Array(buf.slice(HEADER_BYTES));
  return { kind, cursor, pcm };
}

// ---- what the server says --------------------------------------------------
export type ClauseStatus = "pending" | "speaking" | "heard";
export type VoicePhase = "idle" | "connecting" | "listening" | "thinking" | "speaking";

export type ServerState = {
  t: "state";
  phase: Exclude<VoicePhase, "connecting">;
  partial: string;
  pendingUser: string;
  lastUser: string;
  sentences: { text: string; status: ClauseStatus }[];
  spokenCount: number;
  error: string | null;
  micOn: boolean;
  speakOn: boolean;
  deviceOk: boolean;
  userSpeaking: boolean;
  interruptRate: number;
  // Null until the session has been spoken to: the chat is created by the first
  // utterance, not by the connection. See VoiceRuntime._thread.
  threadId: string | null;
  tiers: { vad: string; turnEnd: string };
};

export type ServerReady = {
  t: "ready";
  threadId: string | null;
  micSampleRate: number;
  ttsSampleRate: number;
};

export type ServerClause = { t: "clause"; token: number; turn: string; index: number; text: string };
export type ServerClauseEnd = { t: "clause_end"; token: number };
export type ServerFlush = { t: "flush"; turn: string };
export type ServerMicTest = { t: "mic_test"; text: string };

export type ServerMessage =
  | ServerState | ServerReady | ServerClause | ServerClauseEnd | ServerFlush
  | ServerMicTest;
