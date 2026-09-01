// ARMING AND ROLES, as the Roles surface reads them.
//
// The model (docs/platform-redesign/threads-branches-and-pages-2026-08-29.md
// §1, §2): a THREAD is the one entity and every thread is chattable. There is
// no second object called an agent. A thread carries two independent settings:
//
//   armed — what advances it without a person. The ONLY field that decides
//           whether a thread works unasked.
//   role  — optional, nullable, naming a definition. Null is a real value: an
//           armed thread with no role continues with its own context.
//
// NEITHER IMPLIES THE OTHER, and nothing in this file derives one from the
// other. That is the whole reason they are two fields.
//
// Pure on purpose (frontend/CLAUDE.md: "the target of a test is always the pure
// rule behind a behaviour"). This repo has no DOM harness, so the rules that
// decide a row's status kind, its words, and which threads the page lists live
// here and are executed by roles.test.ts.
import type { StatusKind } from "@workshop/ui";

// ---- armed ------------------------------------------------------------------

/** The closed set, mirroring `chat_store.THREAD_ARMED`. Order is the order the
 *  picker offers, weakest first: `off`, then the three ways a thread can move
 *  on its own. */
export const ARMED_VALUES = ["off", "on-message", "schedule", "on-ready-ticket"] as const;

export type Armed = (typeof ARMED_VALUES)[number];

/** Anything the wire can hand us. A thread stored before the field existed
 *  carries nothing, and the server clamps an unreadable value to `off` — but a
 *  client that trusts the server to have done so is a client that renders
 *  `undefined` the day it has not. */
export function asArmed(value: unknown): Armed {
  return (ARMED_VALUES as readonly string[]).includes(value as string)
    ? (value as Armed)
    : "off";
}

export function isArmed(value: unknown): boolean {
  return asArmed(value) !== "off";
}

const ARMED_COPY: Record<Armed, { name: string; gloss: string }> = {
  "off": {
    name: "Off",
    gloss: "Nothing advances this thread. It moves when you type in it.",
  },
  "on-message": {
    name: "On message",
    gloss: "A message from anywhere advances it: another thread, or a bridge.",
  },
  "schedule": {
    name: "On a schedule",
    gloss: "A clock advances it, with nobody watching.",
  },
  "on-ready-ticket": {
    name: "On a ready ticket",
    gloss: "A ticket reaching ready advances it. This is what works a queue.",
  },
};

export function armedName(value: unknown): string {
  return ARMED_COPY[asArmed(value)].name;
}

export function armedGloss(value: unknown): string {
  return ARMED_COPY[asArmed(value)].gloss;
}

/** The status kind for a thread's arming.
 *
 *  `off` is `stopped`, not `warn`: a thread that only moves when you type in it
 *  is the ordinary case and the default, and marking the default is how a list
 *  ends up coloured everywhere and legible nowhere. Armed is `ok` — it says
 *  the thread will move on its own, which is what the operator armed it for.
 *  Nothing here returns `running`; whether a turn is in flight is `running` on
 *  the summary row and is a different question. */
export function armedStatus(value: unknown): StatusKind {
  return isArmed(value) ? "ok" : "stopped";
}

// ---- write authority --------------------------------------------------------

/** The closed enum, mirroring `agents_registry.ROLES` / the keys of
 *  `agent_authority._ROLE_WRITES`. It is a WRITE-AUTHORITY set — a permission,
 *  not a job title — and it stays closed. A role's `name` is the free text the
 *  operator picks; this is what that name is allowed to write. */
export const WRITE_AUTHORITIES = ["maker", "reviewer", "triager", "keeper", "broker"] as const;

export type WriteAuthority = (typeof WRITE_AUTHORITIES)[number];

const WRITES_GLOSS: Record<WriteAuthority, string> = {
  maker: "Writes code and moves a ticket through its own work.",
  reviewer: "Judges finished work. Writes a verdict, never the change.",
  triager: "Sorts what comes in: files, links and labels, and escalates.",
  keeper: "Consolidates memory. Writes notes, never code.",
  broker: "Adjudicates on demand. Never wakes up on a timer.",
};

export function asWriteAuthority(value: unknown): WriteAuthority | null {
  return (WRITE_AUTHORITIES as readonly string[]).includes(value as string)
    ? (value as WriteAuthority)
    : null;
}

/** What this authority may write, in one line. Null for anything outside the
 *  closed set, including a role that declares no authority at all — an absence
 *  is rendered as nothing, never as a sentence about a permission nobody
 *  granted. */
export function writesGloss(value: unknown): string | null {
  const w = asWriteAuthority(value);
  return w ? WRITES_GLOSS[w] : null;
}

// ---- role definitions -------------------------------------------------------

/** A role, as §2 defines it: `{ name, prompt, tools, writes }`.
 *
 *  `key` is the addressable identifier a thread points AT. Assignment replaces
 *  forking: a thread stores this key, it does not copy the definition, so
 *  editing the definition reaches every thread that names it. */
export type RoleDefinition = {
  key: string;
  name: string;
  /** The closed write-authority enum, or null when the definition declares none. */
  writes: WriteAuthority | null;
  /** One line, for the list. */
  purpose: string;
  /** The definition body. Empty string when the catalog did not carry one. */
  prompt: string;
};

/** Normalize the shipped catalog into role definitions.
 *
 *  DELIBERATELY TOLERANT of the wire. This reads the definitions the platform
 *  already ships; a member missing a usable key is DROPPED rather than rendered
 *  as a row that cannot be pointed at, because a role the operator can see and
 *  cannot assign is worse than one that is not listed.
 */
export function rolesFromCatalog(raw: unknown): RoleDefinition[] {
  const items = Array.isArray(raw)
    ? raw
    : Array.isArray((raw as { templates?: unknown })?.templates)
      ? ((raw as { templates: unknown[] }).templates)
      : [];
  const out: RoleDefinition[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    if (!item || typeof item !== "object") continue;
    const r = item as Record<string, unknown>;
    const key = String(r.template ?? r.key ?? r.slug ?? "").trim();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    const name = String(r.name ?? "").trim() || key;
    out.push({
      key,
      name,
      writes: asWriteAuthority(r.role ?? r.writes),
      purpose: String(r.purpose ?? r.description ?? "").trim(),
      prompt: String(r.definition ?? r.prompt ?? ""),
    });
  }
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

// ---- threads ----------------------------------------------------------------

/** The two fields off the thread summary, plus what a row needs to name the
 *  thread. Declared here rather than taken from `components["schemas"]` because
 *  the generated client is regenerated from the backend's OpenAPI and does not
 *  carry `armed`/`role` until that regeneration runs; every field below IS on
 *  the wire (`routes/chat_models.ThreadSummary`). */
export type ArmableThread = {
  id: string;
  title?: string;
  scope?: string;
  context_key?: string;
  updated_at?: string | null;
  last_user_at?: string | null;
  armed?: string | null;
  role?: string | null;
};

/** Which threads the Roles page lists: everything that is armed OR carries a
 *  role.
 *
 *  BOTH, not just armed. A thread that carries a role and is not armed is the
 *  case §1 calls out explicitly ("a thread can carry a role and never be
 *  armed"), and a page that listed only the armed ones would make that thread
 *  invisible on the one surface that owns the setting — which is exactly how
 *  the roster and the reality came apart in the first place.
 *
 *  Sorted armed-first, then by recency, then by id so the order is total and a
 *  re-render cannot reshuffle two threads that share a timestamp.
 */
export function assignedThreads<T extends ArmableThread>(threads: readonly T[]): T[] {
  return threads
    .filter((t) => isArmed(t.armed) || !!(t.role || "").trim())
    .sort((a, b) => {
      const armDelta = Number(isArmed(b.armed)) - Number(isArmed(a.armed));
      if (armDelta !== 0) return armDelta;
      const at = a.updated_at || a.last_user_at || "";
      const bt = b.updated_at || b.last_user_at || "";
      if (at !== bt) return at < bt ? 1 : -1;
      return a.id.localeCompare(b.id);
    });
}

export type RoleUsage = { threads: number; armed: number };

/** How many threads point at `roleKey`, and how many of those are armed.
 *
 *  This is the number the retired roster was reaching for and never had: it
 *  measured declared agents, and the answer to "what runs unasked" is a count
 *  of armed threads. A role nothing points at reads `0 / 0`, which is a fact
 *  about the role and not an error. */
export function roleUsage(threads: readonly ArmableThread[], roleKey: string): RoleUsage {
  const key = roleKey.trim();
  let total = 0;
  let armed = 0;
  for (const t of threads) {
    if ((t.role || "").trim() !== key) continue;
    total += 1;
    if (isArmed(t.armed)) armed += 1;
  }
  return { threads: total, armed };
}

/** The page's one-line summary: how much of this workshop runs unasked. */
export function armingSummary(threads: readonly ArmableThread[]): {
  total: number;
  armed: number;
  withRole: number;
} {
  let armed = 0;
  let withRole = 0;
  for (const t of threads) {
    if (isArmed(t.armed)) armed += 1;
    if ((t.role || "").trim()) withRole += 1;
  }
  return { total: threads.length, armed, withRole };
}

/** The body for `POST /api/chat/threads/{id}/arming`.
 *
 *  OMITTED IS NOT NULL. The route tells "leave this alone" from "clear this"
 *  by which keys are present, so a caller changing only the arming must not
 *  send `role: null` and wipe a role it never asked about. This builds the
 *  body from what was actually asked for, which is the only place that
 *  distinction can be got right once.
 */
export function armingBody(change: { armed?: Armed; role?: string | null }): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (change.armed !== undefined) body.armed = change.armed;
  if (change.role !== undefined) body.role = (change.role || "").trim() || null;
  return body;
}
