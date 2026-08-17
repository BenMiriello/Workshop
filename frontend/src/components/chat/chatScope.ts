// What a chat's contextKey means: its mode, the entities it is bound to, and the
// task scope it files into.
//
// These are the pure derivations of "where am I". They were inline in Chat.tsx,
// which meant every question about a chat's identity was answered by reading the
// component.
import type { ChatMsg } from "../../lib/chat";
import { DEV_MODE, DEVELOPMENT_SCOPE } from "../../lib/devMode";
import { scopeForContext, scopeHasQueue } from "../../lib/scope";

export type ChatMode = "data" | "build";
export type ChatVariant = "dock" | "panel" | "full";

export function deriveMode(contextKey: string): ChatMode {
  return contextKey.startsWith("build:") ? "build" : "data";
}

export function buildSlug(contextKey: string): string | null {
  return contextKey.startsWith("build:") ? contextKey.slice("build:".length) : null;
}

// ---- What a scope key MEANS ------------------------------------------------
// A chat's contextKey is its scope: the place the conversation lives, which
// decides the thread list you are in and where its work files. These two
// functions are the only place that decides how such a key reads, because the
// chat list and the toolbar's scope picker both name the same scopes and must
// never name them differently.
//
// Related but deliberately separate: `scopeLabel` in lib/scope.ts names SCOPE
// keys, a near-identical key space that differs in two ways (the root is
// `workshop` there and `general` here, and it has no `build:`). The two spaces
// are now bridged by ONE function, `scopeForContext` in lib/scope.ts; these
// stay because they LABEL a context key, which is a different job from
// translating one. Do not add a third naming function.

export type ScopeKind = "development" | "workshop" | "project" | "app" | "agent" | "other";

// App chats bind a BARE app id (finance, budget, …), not `app:<id>`. build: and
// agent: are two runtimes of one scope kind (entity model 2026-07-18 §6), so
// they collapse here; the runtime distinction shows on AgentDetail, not in a
// list of places.
//
// `development` is its own kind, not a project (ADR 0014). It is work ON the
// Workshop — its source and this installation's host — and it is the only name
// that means the platform repo. Present only in a developer build.
export function scopeKind(key: string): ScopeKind {
  if (key === DEVELOPMENT_SCOPE) return "development";
  if (!key || key === "general" || key === "workshop") return "workshop";
  if (!key.includes(":")) return "app";
  const kind = key.split(":", 1)[0];
  if (kind === "project" || kind === "app") return kind;
  if (kind === "build" || kind === "agent") return "agent";
  return "other";
}

export function scopeLabel(key: string): string {
  if (key === DEVELOPMENT_SCOPE) return "Development";
  if (!key || key === "general" || key === "workshop") return "Workshop";
  if (!key.includes(":")) return `App · ${key}`;
  const [kind, rest] = key.split(":", 2);
  if (kind === "project") return `Project · ${rest}`;
  if (kind === "build" || kind === "agent") return `Agent · ${rest}`;
  if (kind === "app") return `App · ${rest}`;
  return key;
}

// Inside a GROUPED picker the heading already names the kind, so an item shows
// only its own name. The full "Project · foo" form is for surfaces with no
// heading above the row: the chat list, and the picker's own trigger.
export function scopeName(key: string): string {
  if (key === DEVELOPMENT_SCOPE) return "Development";
  if (!key || key === "general" || key === "workshop") return "Workshop";
  if (!key.includes(":")) return key; // a bare app id, shown as the chat list shows it
  const [, rest] = key.split(":", 2);
  return rest || key;
}

// What the composer says when it is empty. It names WHERE you are typing,
// because that is the thing a chat surface most needs to make obvious and the
// one thing the operator cannot otherwise see at a glance.
//
// It used to say "Message the assistant..." everywhere, chosen deliberately as
// provider-neutral so it could not name the wrong scope. But neutral is not the
// same as correct: in a project or the workshop there is no "the assistant" to
// message, so the one always-visible label in the chat described a thing that
// was not there. Naming the place is both accurate and useful.
//
// Articles are per kind because they have to read as English: a project and an
// app take "the", an agent is a proper name and takes none.
export function composerPlaceholder(key: string): string {
  switch (scopeKind(key)) {
    case "project": return `Message the ${scopeName(key)} project...`;
    case "app": return `Message the ${scopeName(key)} app...`;
    case "agent": return `Message ${scopeName(key)}...`;
    case "development": return "Message the Workshop's own source...";
    case "workshop": return "Message the workshop...";
    default: return `Message ${scopeName(key)}...`;
  }
}

// Group headings for the scope picker, in the order they read best: Development
// and Workshop unlabelled at the top, then the places work actually happens,
// then individual agents. Those two take no heading — each is one row, and
// "Workshop" above "Workshop" says it twice; sitting unlabelled at the top is
// what makes them read as the roots. `other` takes none either: an unrecognised
// key is shown rather than hidden, but it does not deserve a section.
//
// Development sorts ABOVE Workshop: it is where a developer already is, and
// putting the root organising layer first would make the common case second.
// `orderScopes` in lib/devMode applies the same rank to flat scope lists.
//
// The group is present only in a developer build (ADR 0014) — absent, not
// disabled, so a normal bundle does not advertise a capability it lacks.
export function scopeGroups(devMode: boolean = DEV_MODE): { kind: ScopeKind; label: string }[] {
  return [
    ...(devMode ? [{ kind: "development" as ScopeKind, label: "" }] : []),
    { kind: "workshop" as ScopeKind, label: "" },
    { kind: "project" as ScopeKind, label: "Projects" },
    { kind: "app" as ScopeKind, label: "Apps" },
    { kind: "agent" as ScopeKind, label: "Agents" },
    { kind: "other" as ScopeKind, label: "" },
  ];
}

export const SCOPE_GROUPS = scopeGroups();

// The task-store scope a chat maps to, or null when the chat has no task queue
// (general chat, app docks). build/project/agent chats all get the shared
// TaskPanel.
//
// It used to answer with the contextKey itself, off its own four-branch prefix
// test. That was the client's copy of a mapping the backend had five copies of,
// and the copies disagreed: `development` came out of `chat_store` as
// `app:development` and got stamped onto 27 live threads. Both sides now go
// through one function -- `scopeForContext` in lib/scope.ts here,
// `scope_tree.scope_for_context` there.
//
// `development` still resolves to a queue, and that is deliberate: it owns one
// like any other work scope (the platform repo's own tickets). It was once
// omitted, so the ONE scope with the most work was the only work scope whose
// chat could not see its own tickets.
export function taskScopeFor(contextKey: string): string | null {
  const scope = scopeForContext(contextKey);
  return scopeHasQueue(scope) ? scope : null;
}

// "12s" under a minute, "1m 05s" beyond — for the live working indicator.
export function formatElapsed(s: number): string {
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}m ${r.toString().padStart(2, "0")}s`;
}

// The context meter is an estimate used only BEFORE a thread's first turn
// reports real usage; after that the server's measured window wins. ~4 chars per
// token over the visible turns plus the current draft.
export function estimateContextTokens(messages: ChatMsg[], draft: string): number {
  let chars = draft.length;
  for (const m of messages) {
    chars += (m.content || "").length;
    for (const s of m.meta?.segments || []) {
      chars +=
        s.t === "text" ? s.text.length
          // An approval is a control, not content the model read. Its label is
          // the only text of it that exists, and counting the tool input twice
          // (once here, once when the call goes through) would overstate the
          // window every time the operator allows something.
          : s.t === "approval" ? s.label.length
            : s.name.length + (s.input?.length || 0);
    }
  }
  return Math.ceil(chars / 4);
}

// Blocked external network, recognised from the shape of the failure. It gets a
// distinct banner because it is the one error class with a specific remedy the
// operator can act on.
const EGRESS_MARKERS = [
  "egress", "blocked", "could not resolve host", "network is unreachable",
  "npm err! network", "fatal: unable to access",
  "temporary failure in name resolution", "connection refused",
];

export function isEgressFailure(text: string | null | undefined): boolean {
  if (!text) return false;
  const t = text.toLowerCase();
  return EGRESS_MARKERS.some((m) => t.includes(m));
}
