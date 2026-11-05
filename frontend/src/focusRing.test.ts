import { describe, it, expect } from "vitest";
// Read as TEXT: the vitest configs stub `.css` imports to an empty string,
// which would make every assertion below pass vacuously.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

const sheets = {
  "frontend/src/styles.css": read("./styles.css"),
  "packages/ui/src/styles/components.css": read("../../packages/ui/src/styles/components.css"),
};

/** Every `selector { body }` pair in a sheet, comments removed. Rules nested
 *  in an at-rule come out on their own, since a selector never holds a brace. */
const rules = (css: string): { selector: string; body: string }[] =>
  [...css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .map(([, selector, body]) => ({ selector: selector.trim(), body }));

/** The element a selector styles: its last compound, after any combinator. */
const subject = (selector: string): string =>
  selector.trim().split(/\s*[\s>+~]\s*/).pop() ?? "";

const TEXT_FIELD =
  /^(input|textarea|select)\b|\.ws-(input|textarea|select|searchfield|stepper)\b|\.chat-input\b|contenteditable|\.cm-content\b/;

// Browsers match :focus-visible on a text field however it was focused, so a
// ring on a field shows on every click and tap. Reported repeatedly, fixed one
// field at a time, and back with the next new field, until 2026-10-28 when the
// global rule stopped matching fields at all.
describe("a text field never wears the focus ring", () => {
  it("the global ring rule excludes every text-entry element", () => {
    const kit = sheets["packages/ui/src/styles/components.css"];
    const global = rules(kit).find(
      (r) => r.selector.startsWith(".ws-root :focus-visible:not([tabindex=\"-1\"])")
        && r.body.includes("var(--focus-ring)"),
    );
    expect(global, "no global focus ring rule in the kit").toBeDefined();
    for (const el of ["textarea", "select", "[contenteditable]", "input:not("]) {
      expect(global!.selector, `global ring still matches ${el}`).toContain(el);
    }
  });

  it("no rule puts the ring on a field itself", () => {
    const offenders: string[] = [];
    for (const [file, css] of Object.entries(sheets)) {
      for (const { selector, body } of rules(css)) {
        if (!body.includes("var(--focus-ring)")) continue;
        if (selector.startsWith(".ws-root :focus-visible")) continue;
        for (const one of selector.split(",")) {
          if (TEXT_FIELD.test(subject(one))) offenders.push(`${file}: ${one.trim()}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
