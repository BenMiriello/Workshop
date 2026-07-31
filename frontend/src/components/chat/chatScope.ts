// What a chat's contextKey means: its mode, the entities it is bound to, and the
// task scope it files into.
//
// These are the pure derivations of "where am I". They were inline in Chat.tsx,
// which meant every question about a chat's identity was answered by reading the
// component.
import type { ChatMsg } from "../../lib/chat";

export type ChatMode = "data" | "build";
export type ChatVariant = "dock" | "panel" | "full";

export function deriveMode(contextKey: string): ChatMode {
  return contextKey.startsWith("build:") ? "build" : "data";
}

export function buildSlug(contextKey: string): string | null {
  return contextKey.startsWith("build:") ? contextKey.slice("build:".length) : null;
}

// The project slug this chat is bound to (if any). Used by the /deploy slash
// command to jump straight to that project's Deploys panel; general chat and app
// docks fall through to the /deploys index.
export function projectSlugFor(contextKey: string): string | null {
  if (contextKey.startsWith("project:")) return contextKey.slice("project:".length);
  if (contextKey.startsWith("build:")) return contextKey.slice("build:".length);
  return null;
}

// The task-store scope a chat maps to, or null when the chat has no task queue
// (general chat, app docks). build/project/agent chats all get the shared
// TaskPanel; the contextKey IS the scope key for those.
export function taskScopeFor(contextKey: string): string | null {
  if (
    contextKey.startsWith("build:") ||
    contextKey.startsWith("project:") ||
    contextKey.startsWith("agent:")
  ) {
    return contextKey;
  }
  return null;
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
      chars += s.t === "text" ? s.text.length : s.name.length + (s.input?.length || 0);
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
