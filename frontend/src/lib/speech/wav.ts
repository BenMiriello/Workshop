// PCM frames into a WAV file: 16-bit little-endian, mono, one 44-byte header.
// The transcribe endpoint prefers exactly this at 16 kHz, which is what the
// capture worklet emits, so nothing is resampled or compressed on the way.

export const WAV_HEADER_BYTES = 44;

export function encodeWav(chunks: Int16Array[], sampleRate: number): ArrayBuffer {
  const samples = chunks.reduce((n, c) => n + c.length, 0);
  const dataBytes = samples * 2;
  const buf = new ArrayBuffer(WAV_HEADER_BYTES + dataBytes);
  const v = new DataView(buf);
  const ascii = (at: number, s: string) => {
    for (let i = 0; i < s.length; i++) v.setUint8(at + i, s.charCodeAt(i));
  };
  ascii(0, "RIFF");
  v.setUint32(4, 36 + dataBytes, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true);
  v.setUint32(28, sampleRate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  ascii(36, "data");
  v.setUint32(40, dataBytes, true);
  let at = WAV_HEADER_BYTES;
  for (const c of chunks) {
    for (let i = 0; i < c.length; i++, at += 2) v.setInt16(at, c[i], true);
  }
  return buf;
}
