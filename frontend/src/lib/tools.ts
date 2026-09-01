// The pure rules behind Settings -> Tools.
//
// Everything here is a function of its arguments: no fetch, no React, no DOM.
// That is what makes it executable in the vitest run, which is the only harness
// this repo has (frontend/CLAUDE.md: "the target is the PURE RULE behind a
// behaviour, extracted into its own module").
//
// Model of record: docs/platform-redesign/tools-are-declared-2026-08-23.md.
// A tool record is an ENUMERATION with an owner, not a permission. Nothing in
// this module or on the page it serves grants a tool to anything;
// `services/llm_router.tool_flags` is still the only thing that does.

import type { components } from "../api/schema.gen";

/** Generated from the backend's own OpenAPI schema. Never hand-write these. */
export type ToolRecord = components["schemas"]["ToolRecord"];
export type ToolCreateBody = components["schemas"]["ToolCreate"];
export type ToolWriteBody = components["schemas"]["ToolWrite"];
export type ToolSpec = components["schemas"]["ToolSpec"];

/** The three kinds the registry accepts, `command` first because it is the one
 *  a person declaring their own tool actually wants (doc section 2.1). */
export const TOOL_KINDS = ["command", "builtin", "mcp"] as const;

/** What the form edits. A flat, all-string shape so `useEditMode`'s shallow
 *  compare answers "dirty" correctly; the argv lives here as the one line the
 *  operator typed, and becomes a list only on the way to the API. */
export type ToolDraft = {
  id: string;
  title: string;
  description: string;
  kind: string;
  command: string;
};

export const BLANK_TOOL: ToolDraft = {
  id: "",
  title: "",
  description: "",
  kind: "command",
  command: "",
};

// ---------------------------------------------------------------------------
// The command line
//
// The API takes an argv LIST (`spec.command`), and a person types one LINE. A
// naive `line.split(/\s+/)` loses every argument that contains a space, which
// is most real paths and every quoted message, and it does it silently: the
// tool is written, looks saved, and is wrong. So the split honours quotes.
// ---------------------------------------------------------------------------

/** Split a typed command line into argv.
 *
 *  Whitespace separates. `'` and `"` group, and the quotes themselves are not
 *  part of the token. A backslash escapes the next character outside quotes and
 *  inside double quotes; inside single quotes it is literal, as in sh.
 *  An unterminated quote yields what has been typed so far rather than an error,
 *  because this runs on every keystroke of a field someone is still typing.
 */
export function parseArgv(line: string): string[] {
  const out: string[] = [];
  let token = "";
  let started = false;
  let quote: '"' | "'" | null = null;

  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === "\\" && quote !== "'" && i + 1 < line.length) {
      token += line[++i];
      started = true;
      continue;
    }
    if (quote) {
      if (ch === quote) quote = null;
      else token += ch;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      started = true;
      continue;
    }
    if (/\s/.test(ch)) {
      if (started) out.push(token);
      token = "";
      started = false;
      continue;
    }
    token += ch;
    started = true;
  }
  if (started) out.push(token);
  return out;
}

/** argv back to one editable line, quoted so `parseArgv` returns the same argv.
 *  The round trip is the property that matters: a stored command must survive
 *  being opened in the form and saved again untouched. */
export function formatArgv(argv: string[] | null | undefined): string {
  if (!argv) return "";
  return argv
    .map((raw) => {
      const arg = String(raw);
      if (arg === "") return '""';
      if (!/[\s"'\\]/.test(arg)) return arg;
      return `"${arg.replace(/(["\\])/g, "\\$1")}"`;
    })
    .join(" ");
}

// ---------------------------------------------------------------------------
// Record <-> draft
// ---------------------------------------------------------------------------

export function draftFromTool(tool: ToolRecord): ToolDraft {
  return {
    id: tool.id,
    title: tool.title ?? "",
    description: tool.description ?? "",
    kind: tool.kind,
    command: formatArgv(tool.spec?.command),
  };
}

/** The spec to send, carrying forward every key the form does not own.
 *
 *  The form edits ONE spec key (`command`). A body built from the form alone
 *  therefore deletes `params`, `server` and `tool` from any record that had
 *  them, so renaming a tool would quietly erase how it launches. PUT is a
 *  whole-record replace on this API, so the carry-forward has to happen here.
 */
function specFor(draft: ToolDraft, base?: ToolRecord | null): ToolSpec {
  const spec: ToolSpec = { ...(base?.spec ?? {}) };
  if (draft.kind === "command") {
    const argv = parseArgv(draft.command);
    if (argv.length) spec.command = argv;
    else delete spec.command;
  }
  return spec;
}

/** POST body for a new user tool. The id is trimmed and nothing else about it
 *  is judged here: whether it is well formed, and whether it collides with a
 *  platform or app tool, is the registry's answer to give, and its refusal
 *  names the conflicting id. A client-side guess would replace that sentence
 *  with a worse one. */
export function createBody(draft: ToolDraft): ToolCreateBody {
  return {
    id: draft.id.trim(),
    title: draft.title.trim(),
    description: draft.description.trim(),
    kind: draft.kind,
    spec: specFor(draft),
  };
}

/** PUT body for an existing user tool. `scopes` and `ceiling` are carried from
 *  the stored record: they are not on this form, and the API's write model
 *  defaults them to empty, so omitting them would silently narrow a tool's
 *  declared ceiling to nothing on a title edit. */
export function updateBody(draft: ToolDraft, base: ToolRecord): ToolWriteBody {
  return {
    title: draft.title.trim(),
    description: draft.description.trim(),
    kind: draft.kind,
    spec: specFor(draft, base),
    scopes: [...(base.scopes ?? [])],
    ceiling: [...(base.ceiling ?? [])],
  };
}

// ---------------------------------------------------------------------------
// Reading the catalog
// ---------------------------------------------------------------------------

/** Provenance in the operator's words. The wire says `user`; on a single
 *  operator's screen that is "Yours", which is the question the page exists to
 *  answer at a glance. `platform` and `app` keep their own names because those
 *  are the words the design doc and the API's refusals use. */
export function provenanceLabel(provenance: string): string {
  if (provenance === "user") return "Yours";
  if (provenance === "platform") return "Platform";
  if (provenance === "app") return "App";
  return provenance;
}

export function countByProvenance(tools: ToolRecord[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const tool of tools) counts[tool.provenance] = (counts[tool.provenance] ?? 0) + 1;
  return counts;
}

/** One line for the status band: "9 platform, 2 yours". Ordered platform, app,
 *  yours (the catalog's own precedence), and a provenance with nothing in it is
 *  left out rather than printed as a zero. */
export function catalogSummary(tools: ToolRecord[]): string {
  const counts = countByProvenance(tools);
  const parts: string[] = [];
  for (const key of ["platform", "app", "user"]) {
    if (counts[key]) parts.push(`${counts[key]} ${provenanceLabel(key).toLowerCase()}`);
  }
  for (const key of Object.keys(counts).sort()) {
    if (!["platform", "app", "user"].includes(key)) parts.push(`${counts[key]} ${key}`);
  }
  return parts.join(", ");
}

/** Overflow: show a capped window, and say how many are not shown.
 *  A catalog is nine rows today and grows by one every time anyone declares a
 *  tool, so the page must not assume the whole list fits on a phone. */
export function capRows<T>(rows: T[], expanded: boolean, cap = 24): { rows: T[]; hidden: number } {
  if (expanded || rows.length <= cap) return { rows, hidden: 0 };
  return { rows: rows.slice(0, cap), hidden: rows.length - cap };
}

/** Whether Save may be pressed. Create needs an id (the one field the API
 *  cannot default); edit needs an actual change. */
export function canSave(draft: ToolDraft, isCreate: boolean, dirty: boolean): boolean {
  if (isCreate) return draft.id.trim().length > 0;
  return dirty;
}

// ---------------------------------------------------------------------------
// The catalog filter
//
// A catalog that only grows needs the same two facets and the same search box
// /stores already has: the axes the rows differ on, plus free text over the
// words a person remembers. Pure, so the rule is executable; the page holds
// only the controls.
// ---------------------------------------------------------------------------

export type ToolFilter = {
  /** "all", or one `kind` value. */
  kind: string;
  /** "all", or one `provenance` value. */
  provenance: string;
  /** Free text over id, title and description. */
  q: string;
};

export const NO_TOOL_FILTER: ToolFilter = { kind: "all", provenance: "all", q: "" };

/** The distinct values of one field, in first-seen order.
 *  Built from the rows rather than from a constant, because provenance is the
 *  API's vocabulary and a new one must appear in the menu without an edit
 *  here. */
export function toolFacet(tools: ToolRecord[], field: "kind" | "provenance"): string[] {
  const out: string[] = [];
  for (const t of tools) {
    const v = t[field];
    if (v && !out.includes(v)) out.push(v);
  }
  return out;
}

/** The rows that match. Case-insensitive; an empty or whitespace query matches
 *  everything, so the field is inert until it is typed in. */
export function filterTools(tools: ToolRecord[], f: ToolFilter): ToolRecord[] {
  const needle = f.q.trim().toLowerCase();
  return tools.filter((t) => {
    if (f.kind !== "all" && t.kind !== f.kind) return false;
    if (f.provenance !== "all" && t.provenance !== f.provenance) return false;
    if (!needle) return true;
    return [t.id, t.title ?? "", t.description ?? ""]
      .join(" ")
      .toLowerCase()
      .includes(needle);
  });
}
