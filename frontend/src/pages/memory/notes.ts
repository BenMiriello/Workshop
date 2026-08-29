// The small pure rules a Memory note carries with it: who wrote it, what kind
// it is, which chat refs its body names, and what id a fresh one gets.
//
// Lifted out of Memory.tsx so they can be executed. Same reason as everything
// else in this folder: there is no render harness, so the target of a test is
// the rule, not the screen.
import type { components } from "../../api/schema.gen";

export type NoteRow = components["schemas"]["NoteRow"];
export type NoteDetail = components["schemas"]["NoteDetail"];

/**
 * Who wrote it. `author` is an origin string (services/origin on the backend):
 * "operator", "self", "agent:<slug>", "derived:<model>", "tool:<name>", or ""
 * on a note written before this metadata existed.
 */
export function authorLabel(author?: string | null): string {
  const a = (author || "").trim();
  if (!a) return "Unknown author";
  if (a === "operator") return "You";
  if (a === "self") return "The assistant";
  if (a.startsWith("agent:")) return `Agent: ${a.slice(6)}`;
  if (a.startsWith("derived:")) return `Distilled by ${a.slice(8)}`;
  if (a.startsWith("tool:")) return `Tool: ${a.slice(5)}`;
  return a;
}

// Any `workshop:chat/...` refs named in a note's body: the note's OWN
// provenance, not "referenced by". Agents that file a note from a chat write
// one in (memory-and-documentation-model sec 10), and this is what turns it
// from inert text into a navigable chip.
const CHAT_REF_RE = /workshop:chat\/[^\s)\]"'<>]+/g;

export function chatRefsIn(body: string): string[] {
  const found = body.match(CHAT_REF_RE) ?? [];
  return Array.from(new Set(found));
}

/**
 * A note id must match the store's slug rule ([a-z0-9] segments joined by "-").
 * Derived from the title, falling back to "note", always with a base36
 * timestamp appended so a fresh note never collides with an existing file.
 */
export function freshNoteId(title: string, now: number = Date.now()): string {
  const base =
    (title || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "note";
  return `${base}-${now.toString(36)}`;
}
