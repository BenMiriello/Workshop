// The messages between a page and the chat beside it (docs/chat-beside-a-page.md
// is the contract an app reads). Same namespaced, versioned shape as every other
// shell message (`{v: 1, type: "workshop/v1/..."}`), exchanged with same-origin
// frames only.
//
//   app -> shell   workshop/v1/page        what the page is, and its own tools
//   app -> shell   workshop/v1/attach      send items to the chat beside it
//   app -> shell   workshop/v1/pageResult  the answer to one pageCall
//   shell -> app   workshop/v1/pageCall    run one of the app's declared tools
//   shell -> app   workshop/v1/pageLink    whether a chat is beside it, at what level
//   app -> shell   workshop/v1/pageTakeover  the operator's own input on a page
//                                          the shell cannot see (a native view)
//   app -> shell   workshop/v1/showChat    open the drawer on that conversation

import {
  CHAT_VIEWS, PAGE_CONTEXT_MAX, PAGE_LEVELS, PAGE_LINK_MESSAGES, type ChatView, type PageLevel, type PageTool as AppPageTool,
} from "@workshop/ui";

import { CONTEXT_KINDS, type ContextKind } from "./contextItems";

export const PAGE_MSG = PAGE_LINK_MESSAGES.page;
export const ATTACH_MSG = PAGE_LINK_MESSAGES.attach;
export const RESULT_MSG = PAGE_LINK_MESSAGES.result;
export const CALL_MSG = PAGE_LINK_MESSAGES.call;
export const LINK_MSG = PAGE_LINK_MESSAGES.link;
export const TAKEOVER_MSG = PAGE_LINK_MESSAGES.takeover;
export const SHOW_CHAT_MSG = PAGE_LINK_MESSAGES.showChat;

export const LEVELS = PAGE_LEVELS;
export const VIEWS = CHAT_VIEWS;
export type { ChatView };

export function isView(v: unknown): v is ChatView {
  return typeof v === "string" && (VIEWS as readonly string[]).includes(v);
}
export type Level = PageLevel;

export function isLevel(v: unknown): v is Level {
  return typeof v === "string" && (LEVELS as readonly string[]).includes(v);
}

/** A declared tool as the shell keeps it, with `acts` settled. */
export type PageTool = AppPageTool & { acts: boolean };

export type PageDecl = { context: string; tools: PageTool[] };

export type AttachItem = {
  kind: ContextKind; title?: string; text?: string; url?: string; src?: string;
  app?: string; id?: string;
};

export type AttachRequest = {
  items: AttachItem[]; prompt: string; send: boolean; newChat: boolean;
  scope: string | null; apps: string[];
  /** Send the items as a message of their own, leaving the composer alone. */
  sendOnly: boolean;
};

export type CallResult = { id: string; ok: boolean; result?: unknown; error?: string };

function str(v: unknown, cap = 2000): string | undefined {
  return typeof v === "string" && v.trim() ? v.slice(0, cap) : undefined;
}

function isMsg(data: unknown, type: string): data is Record<string, unknown> {
  return !!data && typeof data === "object"
    && (data as { v?: unknown }).v === 1 && (data as { type?: unknown }).type === type;
}

function tool(raw: unknown): PageTool | null {
  if (!raw || typeof raw !== "object") return null;
  const t = raw as Record<string, unknown>;
  const name = str(t.name, 48);
  if (!name || !/^[A-Za-z0-9_]+$/.test(name)) return null;
  return {
    name,
    description: str(t.description, 600) ?? "",
    input_schema: t.input_schema && typeof t.input_schema === "object"
      ? t.input_schema as Record<string, unknown> : { type: "object" },
    acts: t.acts !== false,
  };
}

export function parsePage(data: unknown): PageDecl | null {
  if (!isMsg(data, PAGE_MSG)) return null;
  const ctx = data.context;
  const context = typeof ctx === "string" ? ctx : ctx ? JSON.stringify(ctx) : "";
  const tools = Array.isArray(data.tools)
    ? data.tools.slice(0, 24).map(tool).filter((t): t is PageTool => !!t)
    : [];
  return { context: context.slice(0, PAGE_CONTEXT_MAX), tools };
}

export function parseAttach(data: unknown): AttachRequest | null {
  if (!isMsg(data, ATTACH_MSG)) return null;
  const items = (Array.isArray(data.items) ? data.items : [])
    .slice(0, 20)
    .map((raw): AttachItem | null => {
      if (!raw || typeof raw !== "object") return null;
      const r = raw as Record<string, unknown>;
      const kind = (CONTEXT_KINDS as readonly string[]).includes(r.kind as string) ? r.kind as ContextKind : null;
      if (!kind) return null;
      return {
        kind, title: str(r.title, 200), text: str(r.text, 20000), url: str(r.url, 2000),
        src: str(r.src, 4_000_000), app: str(r.app, 64), id: str(r.id, 200),
      };
    })
    .filter((i): i is AttachItem => !!i);
  const prompt = str(data.prompt, 8000) ?? "";
  if (!items.length && !prompt) return null;
  return {
    items, prompt,
    send: data.send === true && !!prompt,
    newChat: data.newChat === true,
    sendOnly: data.sendOnly === true && items.length > 0,
    scope: str(data.scope, 128) ?? null,
    apps: Array.isArray(data.apps) ? data.apps.filter((a): a is string => typeof a === "string").slice(0, 8) : [],
  };
}

export function parseResult(data: unknown): CallResult | null {
  if (!isMsg(data, RESULT_MSG) || typeof data.id !== "string") return null;
  return { id: data.id, ok: data.ok === true, result: data.result, error: str(data.error) };
}

export function callMessage(id: string, tool: string, args: unknown, access: string[] | null = null) {
  return { v: 1, type: CALL_MSG, id, tool, args, access };
}

export function isTakeover(data: unknown): boolean {
  return isMsg(data, TAKEOVER_MSG);
}

/** The conversation a page asks the drawer to show, or null. */
export function parseShowChat(data: unknown): string | null {
  if (!isMsg(data, SHOW_CHAT_MSG) || typeof data.thread !== "string") return null;
  return /^[A-Za-z0-9_-]{1,64}$/.test(data.thread) ? data.thread : null;
}

/** `thread`: the conversation beside the page, so an app can tell its own
 *  work for that chat (a tab it opened) from the rest. `view`: how the
 *  operator sees that chat's work (follow, show or hide); `follow` repeats
 *  whether it is follow, for apps that read only that. */
export function linkMessage(open: boolean, level: Level, driving: boolean, thread: string | null = null,
  view: ChatView = "follow") {
  return { v: 1, type: LINK_MSG, open, level, driving, thread, follow: view === "follow", view };
}
