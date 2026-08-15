import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { ALL_WIRE_KINDS, CONSUMED_WIRE_KINDS, WIRE_KINDS } from "./streamWire";

// THE CONTRACT ACROSS THE SEAM, IN THE ONLY PLACE IT CAN BE CHECKED.
//
// The server names an SSE frame with the kind it pushed and the client
// subscribes by that string, so the two sides agree by convention and nothing
// enforces it. `cut` was pushed by the server for a day and a half with no
// listener anywhere, and every test on both sides stayed green.
//
// This reads the backend and fails when it emits a kind streamWire has never
// heard of. It is a source scan, not a running server: it costs nothing and it
// runs in the ordinary vitest pass.

const backend = fileURLToPath(
  new URL("../../../../backend/services/build_streaming.py", import.meta.url),
);
const route = fileURLToPath(
  new URL("../../../../backend/routes/chat.py", import.meta.url),
);

/** Every kind the backend can put on a turn's stream. */
function kindsTheServerSends(): string[] {
  const src = readFileSync(backend, "utf8") + readFileSync(route, "utf8");
  const found = new Set<string>();
  // The platform's own pushes: _push(turn, "done", ...) and the ping event.
  for (const m of src.matchAll(/_push\(turn,\s*"([a-z_]+)"/g)) found.add(m[1]);
  for (const m of src.matchAll(/TurnEvent\(kind="([a-z_]+)"/g)) found.add(m[1]);
  // The CLI kinds that are ingested into segments and pushed on.
  const ingest = src.match(/if kind in \(([^)]*)\):\s*\n\s*saw_tool = _segment_ingest/);
  if (ingest) {
    for (const m of ingest[1].matchAll(/"([a-z_]+)"/g)) found.add(m[1]);
  }
  // Synthesised by the stream route when the generator closes.
  if (/yield "event: end/.test(src)) found.add("end");
  return [...found].sort();
}

describe("the turn stream's wire vocabulary", () => {
  it("declares every kind the backend sends", () => {
    const sent = kindsTheServerSends();
    // Guard the scan itself: a regex that silently stops matching would make
    // this test pass by finding nothing at all.
    expect(sent.length).toBeGreaterThan(6);
    expect(sent).toContain("cut");
    const undeclared = sent.filter((k) => !(k in WIRE_KINDS));
    expect(undeclared, `backend sends kinds streamWire.ts does not declare: ${undeclared.join(", ")}`)
      .toEqual([]);
  });

  it("subscribes to cut, which is where a mid-turn message goes", () => {
    // Not merely declared: CONSUMED, so useTurnStream's exhaustive handler table
    // is required to have an entry for it. Pinned by name because this exact
    // frame going unsubscribed is the bug this file exists to prevent.
    expect(WIRE_KINDS.cut).toBe("delta");
    expect(CONSUMED_WIRE_KINDS).toContain("cut");
  });

  it("gives every declared kind a disposition", () => {
    for (const k of ALL_WIRE_KINDS) expect(WIRE_KINDS[k]).toBeTruthy();
  });
});
