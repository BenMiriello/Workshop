import { describe, expect, it } from "vitest";
import { meOutcome, probeSession, type MeResult } from "./sessionProbe";

type U = { id: number };

/** A probe that answers from a script, one entry per call. */
function scripted(answers: MeResult<U>[]) {
  let calls = 0;
  const probe = async () => answers[Math.min(calls++, answers.length - 1)];
  return { probe, calls: () => calls };
}

describe("what an /api/auth/me status means", () => {
  it("is signed in on a 2xx", () => {
    expect(meOutcome(200)).toBe("authed");
  });

  it("is signed out only when the backend says so", () => {
    expect(meOutcome(401)).toBe("anon");
  });

  it("is no answer at all on a gateway error, a server error, a throttle or an edge's 403", () => {
    for (const s of [500, 502, 503, 504, 429, 404, 403]) expect(meOutcome(s)).toBe("unanswered");
  });
});

describe("asking until the server answers", () => {
  it("rides out a restart instead of signing the operator out", async () => {
    const waits: number[] = [];
    const s = scripted([
      { kind: "unanswered" }, { kind: "unanswered" }, { kind: "unanswered" },
      { kind: "authed", user: { id: 7 } },
    ]);
    const user = await probeSession(s.probe, async (ms) => { waits.push(ms); }, () => true);
    expect(user).toEqual({ id: 7 });
    expect(waits).toEqual([500, 1000, 2000]);
  });

  it("shows Login on a real 'not signed in', without waiting", async () => {
    const s = scripted([{ kind: "anon" }]);
    const waits: number[] = [];
    expect(await probeSession(s.probe, async (ms) => { waits.push(ms); }, () => true)).toBeNull();
    expect(waits).toEqual([]);
  });

  it("never gives up on its own while the server stays silent", async () => {
    const s = scripted([{ kind: "unanswered" }]);
    let n = 0;
    const out = await probeSession(s.probe, async () => { n += 1; }, () => n < 50);
    expect(out).toBeUndefined();
    expect(s.calls()).toBe(50);
  });

  it("stops when its caller goes away, and decides nothing", async () => {
    let alive = true;
    const probe = async (): Promise<MeResult<U>> => {
      alive = false;
      return { kind: "anon" };
    };
    expect(await probeSession(probe, async () => {}, () => alive)).toBeUndefined();
  });
});
