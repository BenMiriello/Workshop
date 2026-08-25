import { describe, it, expect } from "vitest";
import { computeSupersededKeys, splitUiCards, cardKey } from "./UiCard";

// A ui-card block as the model emits it, and the key the card renders it under.
// Building the body the same way the transcript does keeps the raw JSON (which
// `cardKey` hashes) identical to what the running card sees.
function fence(card: object): string {
  return "```ui-card\n" + JSON.stringify(card) + "\n```";
}
function body(...cards: object[]): string {
  return cards.map(fence).join("\n\nsome prose\n\n");
}
function keyOf(card: object): string {
  const part = splitUiCards(fence(card)).find((p) => p.t === "card");
  return cardKey((part as { raw: string }).raw);
}

const prop = (id: string, extra: Record<string, unknown> = {}) => ({
  kind: "task-proposal", id, title: `T ${id}`, ...extra,
});

describe("computeSupersededKeys", () => {
  it("supersedes every earlier instance of a repeated id, keeping the latest active", () => {
    const a1 = prop("fix-send");
    const a2 = prop("fix-send", { title: "T fix-send v2" });
    const out = computeSupersededKeys([body(a1), body(a2)], {});
    expect(out.has(keyOf(a1))).toBe(true);
    expect(out.has(keyOf(a2))).toBe(false);
  });

  it("closes the card a later card names in `supersedes`", () => {
    const old = prop("old-plan");
    const next = prop("new-plan", { supersedes: "old-plan" });
    const out = computeSupersededKeys([body(old), body(next)], {});
    expect(out.has(keyOf(old))).toBe(true);
    expect(out.has(keyOf(next))).toBe(false);
  });

  it("never supersedes a card already filed or dismissed", () => {
    const a1 = prop("fix-send");
    const a2 = prop("fix-send", { title: "T fix-send v2" });
    const states = { [keyOf(a1)]: { phase: "success" } };
    const out = computeSupersededKeys([body(a1), body(a2)], states);
    expect(out.has(keyOf(a1))).toBe(false); // resolved, so it keeps its render
  });

  it("does not supersede a dismissed target named by `supersedes` either", () => {
    const old = prop("old-plan");
    const next = prop("new-plan", { supersedes: "old-plan" });
    const states = { [keyOf(old)]: { phase: "dismissed" } };
    const out = computeSupersededKeys([body(old), body(next)], states);
    expect(out.has(keyOf(old))).toBe(false);
  });

  it("ignores cards with no id (they cannot be matched to a later proposal)", () => {
    const a1 = { kind: "task-proposal", title: "no id" };
    const a2 = { kind: "task-proposal", title: "also no id" };
    const out = computeSupersededKeys([body(a1), body(a2)], {});
    expect(out.size).toBe(0);
  });

  it("leaves a lone proposal active", () => {
    const only = prop("solo");
    const out = computeSupersededKeys([body(only)], {});
    expect(out.size).toBe(0);
  });

  it("supersedes the older of three same-id instances but not the newest two positions correctly", () => {
    const a1 = prop("t", { title: "one" });
    const a2 = prop("t", { title: "two" });
    const a3 = prop("t", { title: "three" });
    const out = computeSupersededKeys([body(a1, a2), body(a3)], {});
    expect(out.has(keyOf(a1))).toBe(true);
    expect(out.has(keyOf(a2))).toBe(true);
    expect(out.has(keyOf(a3))).toBe(false);
  });
});
