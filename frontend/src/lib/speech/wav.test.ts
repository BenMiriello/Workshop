import { describe, expect, it } from "vitest";
import { WAV_HEADER_BYTES, encodeWav } from "./wav";

const text = (v: DataView, at: number, n: number) =>
  String.fromCharCode(...Array.from({ length: n }, (_, i) => v.getUint8(at + i)));

describe("encodeWav", () => {
  it("writes a 16 kHz mono 16-bit header", () => {
    const v = new DataView(encodeWav([new Int16Array(4)], 16000));
    expect(text(v, 0, 4)).toBe("RIFF");
    expect(text(v, 8, 4)).toBe("WAVE");
    expect(text(v, 12, 4)).toBe("fmt ");
    expect(v.getUint16(20, true)).toBe(1);
    expect(v.getUint16(22, true)).toBe(1);
    expect(v.getUint32(24, true)).toBe(16000);
    expect(v.getUint32(28, true)).toBe(32000);
    expect(v.getUint16(32, true)).toBe(2);
    expect(v.getUint16(34, true)).toBe(16);
    expect(text(v, 36, 4)).toBe("data");
  });

  it("concatenates frames little-endian and sizes the chunks to match", () => {
    const buf = encodeWav([new Int16Array([1, -1]), new Int16Array([32767, -32768, 300])], 16000);
    const v = new DataView(buf);
    expect(buf.byteLength).toBe(WAV_HEADER_BYTES + 10);
    expect(v.getUint32(4, true)).toBe(36 + 10);
    expect(v.getUint32(40, true)).toBe(10);
    const samples = Array.from({ length: 5 }, (_, i) => v.getInt16(WAV_HEADER_BYTES + i * 2, true));
    expect(samples).toEqual([1, -1, 32767, -32768, 300]);
  });

  it("encodes silence as a header alone", () => {
    expect(encodeWav([], 16000).byteLength).toBe(WAV_HEADER_BYTES);
  });
});
