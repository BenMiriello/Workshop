import { describe, expect, it } from "vitest";
import { matchModel, spokenNumbers } from "./chatTargetsModel";

const MODELS = [
  { id: "claude-fable-5-1", label: "Fable 5.1" },
  { id: "claude-opus-5-5", label: "Opus 5.5" },
  { id: "claude-opus-5", label: "Opus 5" },
  { id: "claude-opus-4-8", label: "Opus 4.8" },
  { id: "claude-sonnet-5-5", label: "Sonnet 5.5" },
  { id: "claude-sonnet-4-6", label: "Sonnet 4.6" },
  { id: "claude-haiku-4-5-20251001", label: "Haiku 4.5" },
  { id: "qwen3-coder:30b", label: "qwen3-coder:30b" },
];

const pick = (q: string) => matchModel(q, MODELS)?.id ?? null;

describe("spokenNumbers", () => {
  it("reads number words as digits, with point as a decimal", () => {
    expect(spokenNumbers("opus five point five")).toBe("opus 5.5");
    expect(spokenNumbers("Four point eight.")).toBe("4.8");
    expect(spokenNumbers("twenty one")).toBe("21");
    expect(spokenNumbers("five five")).toBe("5 5");
    expect(spokenNumbers("Opus 5.5")).toBe("opus 5.5");
  });
});

describe("matchModel", () => {
  it("matches a spoken name with its version", () => {
    expect(pick("opus five point five")).toBe("claude-opus-5-5");
    expect(pick("Opus 4.8")).toBe("claude-opus-4-8");
    expect(pick("sonnet four point six")).toBe("claude-sonnet-4-6");
    expect(pick("haiku")).toBe("claude-haiku-4-5-20251001");
  });

  it("prefers the exact version over a longer one", () => {
    expect(pick("opus five")).toBe("claude-opus-5");
  });

  it("reads two numbers in a row as one version", () => {
    expect(pick("opus five five")).toBe("claude-opus-5-5");
  });

  it("takes the first listed on a tie, which is the newest", () => {
    expect(pick("opus")).toBe("claude-opus-5-5");
    expect(pick("the sonnet model")).toBe("claude-sonnet-5-5");
  });

  it("forgives one slip in a long word", () => {
    expect(pick("sonet 4.6")).toBe("claude-sonnet-4-6");
  });

  it("matches by id as well as label", () => {
    expect(pick("qwen coder")).toBe("qwen3-coder:30b");
  });

  it("returns null when nothing matches", () => {
    expect(pick("gemini")).toBeNull();
    expect(pick("opus 9")).toBeNull();
    expect(pick("")).toBeNull();
    expect(pick("the model")).toBeNull();
  });
});
