import { describe, expect, it } from "vitest";
import { phraseFor, phrasesFor, type SpeechCommands } from "./commandsApi";

const cmds = {
  mode: "mixed",
  trigger: "ok workshop",
  builtins: [
    { id: "send", label: "Send message", kind: "action", phrases: ["send message", "submit message"], when: "trigger" },
    { id: "scroll_up", label: "Scroll up", kind: "action", phrases: ["scroll up"], when: "alone" },
    { id: "close", label: "Close", kind: "action", phrases: ["close that"], when: "off" },
    { id: "stop", label: "Stop dictation", kind: "action", phrases: ["stop dictation"], when: "alone" },
  ],
  custom: [],
} as unknown as SpeechCommands;

describe("phrasesFor", () => {
  it("puts the trigger word before every phrase of a command that needs it", () => {
    expect(phrasesFor(cmds, "send")).toEqual(["ok workshop send message", "ok workshop submit message"]);
    expect(phraseFor(cmds, "send")).toBe("ok workshop send message");
  });

  it("drops the trigger word in Commands only", () => {
    expect(phrasesFor(cmds, "send", "commands")).toEqual(["send message", "submit message"]);
  });

  it("gives nothing for a command that is off or not heard in the mode", () => {
    expect(phrasesFor(cmds, "close")).toEqual([]);
    expect(phrasesFor(cmds, "scroll_up", "dictation")).toEqual([]);
    expect(phrasesFor(cmds, "stop", "dictation")).toEqual(["stop dictation"]);
  });
});
