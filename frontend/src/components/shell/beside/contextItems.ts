// What the operator attached to the next message in the chat beside a page,
// and how it travels: as `=== ATTACHED (...) ===` blocks after the typed text,
// in the message itself. Blocks are never closed and their bodies are escaped
// against the two structures a body could forge (a leading `=== ` and a speaker
// label), the same rule the server applies to everything it injects
// (docs/decisions/0015 section 8). Being part of the message is what lets an
// attachment survive every path a message takes: queued, joined to a running
// turn, drained, resumed.
//
// Images are not here: they become ordinary image attachments, so the model
// sees the picture.

export const CONTEXT_KINDS = ["text", "link", "page", "item", "image"] as const;
export type ContextKind = (typeof CONTEXT_KINDS)[number];

export type ContextItem = {
  id: string;
  kind: ContextKind;
  /** Where it came from, as the operator would name it: the app or page. */
  source: string;
  title?: string;
  text?: string;
  url?: string;
  app?: string;
  /** A record in `app`, for that app's tools. */
  ref?: string;
};

export const ATTACHED_HEADING = "=== ATTACHED";
const TEXT_CAP = 20000;
const SPEAKER = /^(Operator|Assistant|You):/;

function escapeBody(text: string): string {
  return text.split("\n").map((line) =>
    line.startsWith("=== ") || SPEAKER.test(line) ? ` ${line}` : line).join("\n");
}

const KIND_WORD: Record<ContextKind, string> = {
  text: "selected text", link: "link", page: "page", item: "item", image: "image",
};

function oneLine(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

export function headingFor(item: ContextItem): string {
  const parts = [KIND_WORD[item.kind], `from ${oneLine(item.source) || "a page"}`];
  if (item.title) parts.push(`"${oneLine(item.title).slice(0, 120)}"`);
  return `${ATTACHED_HEADING} (${parts.join(", ")}) ===`;
}

function bodyFor(item: ContextItem): string {
  const lines: string[] = [];
  if (item.url) lines.push(`url: ${item.url}`);
  if (item.app && item.ref) lines.push(`${item.app} id: ${item.ref}`);
  if (item.text) lines.push(item.text.slice(0, TEXT_CAP));
  return escapeBody(lines.join("\n"));
}

/** The blocks for `items`, in order, or "" for none. */
export function attachedBlocks(items: readonly ContextItem[]): string {
  return items
    .filter((i) => i.kind !== "image")
    .map((i) => `${headingFor(i)}\n${bodyFor(i)}`.trimEnd())
    .join("\n\n");
}

/** `typed` with the blocks after it. */
export function withAttached(typed: string, items: readonly ContextItem[]): string {
  const blocks = attachedBlocks(items);
  if (!blocks) return typed;
  return typed ? `${typed}\n\n${blocks}` : blocks;
}

export type AttachedPart = { heading: string; body: string };

/** A sent message split back into what was typed and what was attached, for
 *  showing the attachments as chips. Only blocks at the END of the message
 *  count, and only from a line that starts exactly with the heading. */
export function splitAttached(content: string): { text: string; parts: AttachedPart[] } {
  const lines = content.split("\n");
  const first = lines.findIndex((l) => l.startsWith(`${ATTACHED_HEADING} (`) && l.endsWith(" ==="));
  if (first < 0) return { text: content, parts: [] };
  const parts: AttachedPart[] = [];
  for (let i = first; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith(`${ATTACHED_HEADING} (`) && line.endsWith(" ===")) {
      parts.push({ heading: line.slice(ATTACHED_HEADING.length + 2, -5), body: "" });
    } else if (parts.length) {
      const p = parts[parts.length - 1];
      p.body = p.body ? `${p.body}\n${line}` : line;
    }
  }
  return {
    text: lines.slice(0, first).join("\n").trimEnd(),
    parts: parts.map((p) => ({ heading: p.heading, body: p.body.trim() })),
  };
}

/** A short label for a chip. */
export function chipLabel(item: ContextItem): string {
  const base = item.title || item.text || item.url || KIND_WORD[item.kind];
  const flat = oneLine(base);
  return flat.length > 48 ? `${flat.slice(0, 47)}…` : flat;
}
