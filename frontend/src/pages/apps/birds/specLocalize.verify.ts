// Executable verification of the spectrogram localization core. Not a committed
// test suite (no runner in this app) — run with:
//   node_modules/.bin/esbuild specLocalize.verify.ts --bundle --platform=node \
//     --format=esm | node --input-type=module
import {
  coverageBound, confCentroid, localizeEvents,
  type SpecCol, type SpeciesEvent,
} from "./specLocalize";

let failures = 0;
function check(name: string, cond: boolean, detail = "") {
  if (cond) { console.log(`  ok   ${name}`); }
  else { failures++; console.log(`  FAIL ${name} ${detail}`); }
}

const NB = 40;
type BlobSpec = { t: number; band: number; amp: number; wT?: number; wB?: number };
function makeHistory(t0: number, t1: number, step: number, blobs: BlobSpec[]): SpecCol[] {
  const cols: SpecCol[] = [];
  for (let t = t0; t <= t1; t += step) {
    const bands = new Array(NB).fill(8);
    for (const bl of blobs) {
      const wT = bl.wT ?? 120, wB = bl.wB ?? 2;
      const dt = (t - bl.t) / wT, contrib = bl.amp * Math.exp(-dt * dt);
      if (contrib < 0.5) continue;
      for (let b = 0; b < NB; b++) {
        const dbn = (b - bl.band) / wB;
        bands[b] += contrib * Math.exp(-dbn * dbn);
      }
    }
    cols.push({ t, bands });
  }
  return cols;
}
const ev = (sci: string, samples: [number, number][]): SpeciesEvent =>
  ({ sci, common: sci, samples: samples.map(([t, conf]) => ({ t, conf })) });

console.log("coverageBound / confCentroid");
{
  const [s, e] = coverageBound([{ t: 5000, conf: 1 }, { t: 8000, conf: 1 }]);
  check("brief call across 5000..8000 pins to [5000,5000]", s === 5000 && e === 5000, `got [${s},${e}]`);
  const c = confCentroid([{ t: 6000, conf: 0.5 }, { t: 7000, conf: 0.5 }]);
  check("centroid pulled back ~half a window", Math.abs(c - (6500 - 1500)) < 1, `got ${c}`);
}

console.log("S1 single species, clean blob → precise on the blob");
{
  const hist = makeHistory(3000, 8000, 33, [{ t: 5000, band: 20, amp: 100 }]);
  const events = [ev("A", [[5000, 0.4], [6000, 0.7], [7000, 0.8], [8000, 0.5]])];
  const [p] = localizeEvents(events, hist);
  check("mode precise", p.mode === "precise", `mode=${p.mode} score=${p.score.toFixed(2)}`);
  check("time on the chirp (~5000)", Math.abs(p.t - 5000) < 200, `t=${p.t}`);
  check("vertical ~ band 20 (yFrac~0.49)", Math.abs(p.yFrac - (1 - 20.5 / NB)) < 0.05, `yFrac=${p.yFrac.toFixed(3)}`);
}

console.log("S2 two species, same time, DIFFERENT bands → both precise, separated vertically");
{
  const hist = makeHistory(3000, 8000, 33, [
    { t: 5000, band: 28, amp: 100 }, { t: 5000, band: 8, amp: 100 },
  ]);
  const events = [
    ev("A", [[5000, 0.5], [6000, 0.8], [7000, 0.7]]),
    ev("B", [[5000, 0.5], [6000, 0.75], [7000, 0.6]]),
  ];
  const res = localizeEvents(events, hist);
  const a = res.find((r) => r.sci === "A")!, b = res.find((r) => r.sci === "B")!;
  check("A precise", a.mode === "precise", `A=${a.mode}`);
  check("B precise", b.mode === "precise", `B=${b.mode}`);
  check("different vertical positions", Math.abs(a.yFrac - b.yFrac) > 0.3, `dy=${Math.abs(a.yFrac - b.yFrac).toFixed(3)}`);
}

console.log("S3 two species, same time + ONE shared blob → louder precise, other forgoes position");
{
  const hist = makeHistory(3000, 8000, 33, [{ t: 5000, band: 20, amp: 100 }]);
  const events = [
    ev("A", [[5000, 0.6], [6000, 0.85], [7000, 0.7]]), // more confident → wins the blob
    ev("B", [[5000, 0.5], [6000, 0.7], [7000, 0.6]]),
  ];
  const res = localizeEvents(events, hist);
  const a = res.find((r) => r.sci === "A")!, b = res.find((r) => r.sci === "B")!;
  check("A (louder) precise", a.mode === "precise", `A=${a.mode}`);
  check("B forgoes position (approx|none, not precise)", b.mode !== "precise", `B=${b.mode}`);
}

console.log("S4 contested top blob → rival falls back to its OWN next-best blob (both placed)");
{
  const hist = makeHistory(3000, 8000, 33, [
    { t: 5000, band: 30, amp: 120 }, // strongest — both want it
    { t: 5000, band: 10, amp: 95 },  // rival's fallback
  ]);
  const events = [
    ev("A", [[5000, 0.6], [6000, 0.9], [7000, 0.7]]),
    ev("B", [[5000, 0.6], [6000, 0.85], [7000, 0.7]]),
  ];
  const res = localizeEvents(events, hist);
  const a = res.find((r) => r.sci === "A")!, b = res.find((r) => r.sci === "B")!;
  check("A precise on strong blob (band30, high)", a.mode === "precise" && a.yFrac < 0.35, `A=${a.mode} y=${a.yFrac.toFixed(3)}`);
  check("B precise on fallback blob (band10, low)", b.mode === "precise" && b.yFrac > 0.65, `B=${b.mode} y=${b.yFrac.toFixed(3)}`);
}

console.log("S5 single window + murky energy → no position (card only)");
{
  const hist = makeHistory(2000, 5000, 33, []); // flat, no blob
  const [p] = localizeEvents([ev("A", [[5000, 0.7]])], hist);
  check("mode none", p.mode === "none", `mode=${p.mode}`);
}

console.log("S6 repeated chirps → pin tracks the NEWEST chirp (not lagging behind)");
{
  // four chirps over the burst; the pin should land on the last (~7000), and a
  // lone chirp must still resolve to itself (no spurious recency drift).
  const hist = makeHistory(3000, 8000, 33, [
    { t: 4000, band: 20, amp: 100 }, { t: 5000, band: 20, amp: 100 },
    { t: 6000, band: 20, amp: 100 }, { t: 7000, band: 20, amp: 100 },
  ]);
  const events = [ev("A", [[4000, 0.6], [5000, 0.7], [6000, 0.8], [7000, 0.8]])];
  const [p] = localizeEvents(events, hist);
  check("precise", p.mode === "precise", `mode=${p.mode}`);
  check("on the latest chirp (~7000, not an earlier one)", p.t >= 6500, `t=${p.t}`);

  const lone = makeHistory(3000, 8000, 33, [{ t: 5000, band: 20, amp: 100 }]);
  const [q] = localizeEvents([ev("A", [[5000, 0.5], [6000, 0.8], [7000, 0.7]])], lone);
  check("lone chirp still resolves to itself (~5000)", q.mode === "precise" && Math.abs(q.t - 5000) < 250, `mode=${q.mode} t=${q.t}`);
}

console.log(failures ? `\n${failures} FAILURE(S)` : "\nALL PASS");
if (failures) throw new Error(`${failures} verification failure(s)`);
