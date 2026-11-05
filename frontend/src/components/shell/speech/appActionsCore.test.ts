import { describe, expect, it } from "vitest";
import type { SpokenAction } from "@workshop/ui";
import { afterPhrase, argFor, asksFirst, pickChoice, shownPhrase, wireOf } from "./appActionsCore";

const run = () => {};
const render: SpokenAction = { id: "render", title: "Render", description: "Render the recipe", run };
const group: SpokenAction = {
  id: "group", title: "Add to group", description: "Put the item in a group", run,
  phrases: ["add to group {group}", "put this in {group} now"],
  input: { name: "group", choices: ["Sunsets", "Old portraits"] },
};
const note: SpokenAction = {
  id: "note", title: "Add a note", description: "Write a note", run,
  phrases: ["add a note", "note {text} here", "{a} and {b}"], input: { name: "text" },
};

describe("wireOf", () => {
  it("says an action without phrases by its title", () => {
    expect(wireOf("app:1", render)).toMatchObject({ id: "app:1", phrases: ["render"], fixed: [], takesArg: false });
  });

  it("fills a slot with each choice, anywhere in the phrase, and keeps a last slot open", () => {
    const w = wireOf("app:2", group);
    expect(w.phrases).toEqual(["add to group"]);
    expect(w.fixed).toContainEqual(["put this in Old portraits now", "Old portraits"]);
    expect(w.fixed).toContainEqual(["add to group Sunsets", "Sunsets"]);
    expect(w.examples).toContain("put this in Sunsets now");
    expect(w.takesArg).toBe(true);
  });

  it("drops a free text slot before the end and a phrase with two slots", () => {
    const w = wireOf("app:3", note);
    expect(w.phrases).toEqual(["add a note"]);
    expect(w.fixed).toEqual([]);
  });

  it("fills slots in examples rather than teaching the braces", () => {
    const w = wireOf("app:4", { ...group, examples: ["file it under {group}", "group this"] });
    expect(w.examples).toContain("file it under Sunsets");
    expect(w.examples.some((e) => e.includes("{"))).toBe(false);
  });
});

describe("arguments", () => {
  it("finds the choice named in a sentence or inside a choice", () => {
    expect(pickChoice("add this to the sunset group", group.input!.choices!)).toBe("Sunsets");
    expect(pickChoice("portraits", group.input!.choices!)).toBe("Old portraits");
    expect(pickChoice("landscapes", group.input!.choices!)).toBeNull();
  });

  it("takes free text as said and a choice as named", () => {
    expect(argFor(note, "buy milk")).toEqual({ ok: true, arg: "buy milk" });
    expect(argFor(group, "old portraits")).toEqual({ ok: true, arg: "Old portraits" });
    expect(argFor(group, "cats")).toEqual({ ok: false, missing: 'No group "cats"' });
    expect(argFor(render, "anything")).toEqual({ ok: true, arg: undefined });
  });

  it("reads free text after a phrase in a loose sentence", () => {
    expect(afterPhrase("Add a note call the vet.", note)).toBe("call the vet");
    expect(afterPhrase("please add a note", note)).toBeNull();
  });

  it("shows the slot by its name and asks only when consequential", () => {
    expect(shownPhrase(group)).toBe("add to group ‹group›");
    expect(shownPhrase(note)).toBe("add a note ‹text›");
    expect(asksFirst({ ...render, annotations: { consequentialHint: true } })).toBe(true);
    expect(asksFirst({ ...render, annotations: { readOnlyHint: true } })).toBe(false);
  });
});
