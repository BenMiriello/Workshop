import { describe, expect, it } from "vitest";
import { HEADER_BYTES, KIND_MIC, KIND_TTS, decodeAudio, encodeAudio } from "./protocol";

// The wire format is the one place a client and a server can disagree silently:
// a header field written little-endian and read big-endian produces audio that
// plays as noise, or a cursor that puts a frame hours into the past. The Python
// side pins the same layout in backend/services/voice/protocol.py.

describe("the audio frame", () => {
  it("round-trips samples unchanged", () => {
    const pcm = new Int16Array([0, 1, -1, 32767, -32768, 1234]);
    const { pcm: out } = decodeAudio(encodeAudio(KIND_MIC, 0, pcm));
    expect(Array.from(out)).toEqual(Array.from(pcm));
  });

  it("round-trips the kind and the cursor", () => {
    const { kind, cursor } = decodeAudio(encodeAudio(KIND_TTS, 123456, new Int16Array(2)));
    expect(kind).toBe(KIND_TTS);
    expect(cursor).toBe(123456);
  });

  it("carries a cursor at the top of the 32-bit range", () => {
    // About 74 hours of audio at 16 kHz. The Python side unwraps this into an
    // absolute offset; getting the width wrong here would place a frame 2^32
    // samples in the past and silently corrupt the stream.
    const { cursor } = decodeAudio(encodeAudio(KIND_MIC, 4294967295, new Int16Array(2)));
    expect(cursor).toBe(4294967295);
  });

  it("uses an 8-byte header and nothing else", () => {
    const pcm = new Int16Array(320);
    expect(encodeAudio(KIND_MIC, 0, pcm).byteLength).toBe(HEADER_BYTES + 640);
  });

  it("copies the samples rather than viewing the received buffer", () => {
    // The decoded array is handed to an AudioWorklet, which transfers it. A view
    // onto the socket's buffer would be a use-after-free the moment it is.
    const buf = encodeAudio(KIND_TTS, 1, new Int16Array([7, 8]));
    const { pcm } = decodeAudio(buf);
    expect(pcm.buffer.byteLength).toBe(4);
    expect(pcm.buffer).not.toBe(buf);
  });

  it("handles an empty frame without throwing", () => {
    const { pcm } = decodeAudio(encodeAudio(KIND_MIC, 5, new Int16Array(0)));
    expect(pcm.length).toBe(0);
  });
});
