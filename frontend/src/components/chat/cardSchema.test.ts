import { describe, expect, it } from "vitest";
import { CARD_SCHEMAS, cardSchemaFor, normalizeCardSpec } from "./cardSchema";
import { composeTaskText, type UiCardSpec } from "./uiCardActions";

const card = (fields: { name: string; value?: string; type?: string }[]): UiCardSpec => ({
  id: "c1",
  kind: "task-proposal",
  fields: fields as UiCardSpec["fields"],
});

describe("card schema binding", () => {
  it("task-proposal is store-bound with exactly three fields", () => {
    const s = cardSchemaFor("task-proposal");
    expect(s?.binding).toBe("store");
    expect(s?.fields.map((f) => f.name)).toEqual(["scope", "title", "description"]);
  });

  it("declares no conversation-bound kind yet", () => {
    const conversational = Object.values(CARD_SCHEMAS).filter((s) => s.binding === "conversation");
    expect(conversational).toEqual([]);
  });

  it("leaves a kind with no schema untouched", () => {
    const spec = { id: "p", kind: "project-proposal", fields: [{ name: "anything" }] } as UiCardSpec;
    const out = normalizeCardSpec(spec);
    expect(out?.spec.fields?.map((f) => f.name)).toEqual(["anything"]);
    expect(out?.carried).toEqual({});
  });
});

describe("a store-bound kind cannot render a field the client does not know", () => {
  it("drops invented fields from the rendered list", () => {
    const out = normalizeCardSpec(
      card([
        { name: "scope", value: "development" },
        { name: "title", value: "Do the thing" },
        { name: "description", value: "Objective: ship it" },
        { name: "banana", value: "peel" },
        { name: "execution", value: "code" },
      ]),
    );
    expect(out?.spec.fields?.map((f) => f.name)).toEqual(["scope", "title", "description"]);
  });

  it("keeps an unknown value reachable by the handler without rendering it", () => {
    const out = normalizeCardSpec(
      card([
        { name: "title", value: "T" },
        { name: "priority", value: "3" },
        { name: "effort", value: "deep" },
      ]),
    );
    expect(out?.spec.fields?.some((f) => f.name === "priority")).toBe(false);
    expect(out?.carried.priority).toBe("3");
    expect(out?.carried.effort).toBe("deep");
  });

  it("always renders every canonical field, even when the model omitted it", () => {
    const out = normalizeCardSpec(card([{ name: "title", value: "T" }]));
    expect(out?.spec.fields?.map((f) => f.name)).toEqual(["scope", "title", "description"]);
  });

  it("takes the type from the schema, never from the model", () => {
    const out = normalizeCardSpec(card([{ name: "description", value: "x", type: "text" }]));
    const desc = out?.spec.fields?.find((f) => f.name === "description");
    expect(desc?.type).toBe("textarea");
  });
});

describe("a legacy eight-field card still reads and files the same", () => {
  const legacy = card([
    { name: "scope", value: "development" },
    { name: "title", value: "Old card" },
    { name: "objective", value: "O" },
    { name: "output", value: "P" },
    { name: "entry_points", value: "E" },
    { name: "boundaries", value: "B" },
    { name: "priority", value: "0" },
  ]);

  it("folds the section fields into the description so the spec stays visible", () => {
    const out = normalizeCardSpec(legacy);
    const desc = out?.spec.fields?.find((f) => f.name === "description")?.value ?? "";
    expect(desc).toContain("Objective: O");
    expect(desc).toContain("Output: P");
    expect(desc).toContain("Start from: E");
    expect(desc).toContain("Boundaries: B");
  });

  it("does not carry the section keys, so an edit to the box is what files", () => {
    const out = normalizeCardSpec(legacy);
    // composeTaskText prefers sections over description when both are present,
    // so leaving them carried would silently discard the operator's edit.
    expect(out?.carried.objective).toBeUndefined();
    expect(out?.carried.boundaries).toBeUndefined();
    const values: Record<string, string> = { ...out!.carried };
    for (const f of out!.spec.fields ?? []) values[f.name] = f.value ?? "";
    values.description = "Objective: edited";
    expect(composeTaskText(values)).toContain("Objective: edited");
  });

  it("files the same text it would have before normalization", () => {
    const before = composeTaskText({
      title: "Old card",
      objective: "O",
      output: "P",
      entry_points: "E",
      boundaries: "B",
    });
    const out = normalizeCardSpec(legacy);
    const values: Record<string, string> = { ...out!.carried };
    for (const f of out!.spec.fields ?? []) values[f.name] = f.value ?? "";
    const after = composeTaskText(values);
    for (const line of ["Objective: O", "Output: P", "Start from: E", "Boundaries: B"]) {
      expect(before).toContain(line);
      expect(after).toContain(line);
    }
    expect(after.split("\n")[0]).toBe("Old card");
  });
});
