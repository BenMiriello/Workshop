import { describe, expect, it } from "vitest";
import { decide } from "./guessRule";

const g = (name: string, p: number) => ({ name, p });
const all = () => true;

describe("what a sentence worded freely does", () => {
  it("runs a clear winner", () => {
    expect(decide([g("scroll_down", 0.8), g("scroll_up", 0.1)], 0.05, all)).toEqual({ kind: "act", guess: g("scroll_down", 0.8) });
  });

  it("asks when no command is likely enough, best first, at most three", () => {
    const d = decide([g("open", 0.4), g("go_back", 0.3), g("click", 0.15), g("send", 0.12)], 0.02, all);
    expect(d).toEqual({ kind: "ask", guesses: [g("open", 0.4), g("go_back", 0.3), g("click", 0.15)] });
  });

  it("asks rather than acts when no command at all is likelier", () => {
    expect(decide([g("pause", 0.5)], 0.52, all).kind).toBe("ask");
  });

  it("counts only commands that make sense here", () => {
    const noChat = (x: { name: string }) => x.name !== "send";
    expect(decide([g("send", 0.9), g("new_chat", 0.6)], 0.01, noChat)).toEqual({ kind: "act", guess: g("new_chat", 0.6) });
  });

  it("does nothing when nothing is near", () => {
    expect(decide([g("open", 0.05), g("send", 0.04)], 0.9, all)).toEqual({ kind: "none" });
    expect(decide([], 1, all)).toEqual({ kind: "none" });
  });
});
