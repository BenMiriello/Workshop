import { describe, expect, it } from "vitest";
import { isCommand, splitFirstTurn } from "./turnBatching";

// A command has to run BETWEEN turns to run at all, so it cannot be joined into
// a batch with prose. The bug this pins: ["/compact", "Continue"] went out as
// "/compact\n\nContinue", which the backend reads as plain text — so nothing
// compacted and the model answered "Continue" under a line that looked like a
// command. The operator's words: "instead of compacting it just processed my
// next message".

const q = (...texts: string[]) => texts.map((text) => ({ text, sentAt: "" }));
const texts = ([now, rest]: [{ text: string }[], { text: string }[]]) =>
  [now.map((m) => m.text), rest.map((m) => m.text)];

describe("isCommand", () => {
  it("accepts a bare slash command", () => expect(isCommand("/compact")).toBe(true));
  it("accepts a command with arguments", () => expect(isCommand("/model opus")).toBe(true));
  it("tolerates surrounding whitespace", () => expect(isCommand("  /cost  ")).toBe(true));

  it("rejects a path, which is prose", () => {
    // "/usr/bin/foo is missing" is a sentence, not a command.
    expect(isCommand("/usr/bin/foo is missing")).toBe(false);
  });
  it("rejects a slash followed by a digit", () => expect(isCommand("/2 things")).toBe(false));
  it("rejects a bare slash", () => expect(isCommand("/")).toBe(false));
  it("rejects anything multi-line", () => {
    // The backend only treats a single line as a command; a blob becomes one
    // enormous command NAME and fails.
    expect(isCommand("/compact\nand continue")).toBe(false);
  });
  it("rejects ordinary prose", () => expect(isCommand("compact the chat")).toBe(false));
});

describe("splitFirstTurn", () => {
  it("sends nothing from an empty queue", () => {
    expect(texts(splitFirstTurn([]))).toEqual([[], []]);
  });

  it("batches plain messages into one turn", () => {
    expect(texts(splitFirstTurn(q("one", "two", "three"))))
      .toEqual([["one", "two", "three"], []]);
  });

  it("sends a leading command alone", () => {
    expect(texts(splitFirstTurn(q("/compact", "Continue"))))
      .toEqual([["/compact"], ["Continue"]]);
  });

  it("stops a batch at the first command", () => {
    expect(texts(splitFirstTurn(q("hello", "more", "/compact", "after"))))
      .toEqual([["hello", "more"], ["/compact", "after"]]);
  });

  it("keeps two commands in separate turns", () => {
    expect(texts(splitFirstTurn(q("/compact", "/cost"))))
      .toEqual([["/compact"], ["/cost"]]);
  });

  it("preserves the order messages were typed in", () => {
    const [now, rest] = splitFirstTurn(q("a", "b", "/x", "c"));
    expect([...now, ...rest].map((m) => m.text)).toEqual(["a", "b", "/x", "c"]);
  });

  it("never drops a message", () => {
    for (const queue of [q("a"), q("/a"), q("a", "/b", "c"), q("/a", "/b"), q("a", "b")]) {
      const [now, rest] = splitFirstTurn(queue);
      expect(now.length + rest.length).toBe(queue.length);
    }
  });

  it("always makes progress, so the queue cannot stall", () => {
    // A split that sent nothing would leave the drain looping on a queue it
    // never empties.
    for (const queue of [q("a"), q("/a"), q("a", "/b"), q("/a", "b")]) {
      expect(splitFirstTurn(queue)[0].length).toBeGreaterThan(0);
    }
  });
});
