// The link between the drawer's conversation and the page beside it
// (services/page_link on the server has the whole design).
//
// While the drawer is open on a conversation, this holds the call stream open,
// keeps the server told what the page is, runs each call (the page's own tool
// when it declared one, else the platform's reading of its DOM), and tells the
// page whether a chat is beside it. In control (`drive`), the operator's own
// input on the page takes control back.

import { useEffect, useRef } from "react";
import { beside } from "./besideStore";
import { declOf, callFrame, useDeclOf } from "./framePages";
import { act } from "./domAct";
import { changesSince, findOnPage, readPage } from "./domRead";
import { newOutlineState, type OutlineState } from "./outline";
import { isLevel, isView, linkMessage, type ChatView, type Level } from "./protocol";

export type BesidePage = {
  /** The app frame's window, or null for a Workshop page. */
  win: Window | null;
  app: string | null;
  label: string;
  title: string;
  url: string;
  root: () => Element | null;
};

type Call = { id: string; tool: string; args: Record<string, unknown>; access?: string[] | null };

/** This tab's drawer: the id its stream and its page are held under on the
 *  server, so a conversation can be bound to it before its first turn. */
export const LINK_ID = typeof crypto !== "undefined" && "randomUUID" in crypto
  ? crypto.randomUUID()
  : `link-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

const ACT_SETTLE_MS = 350;

async function put(path: string, body: object): Promise<{ level?: string } | null> {
  try {
    const r = await fetch(path, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    return r.ok ? r.json() : null;
  } catch {
    return null;
  }
}

const threadPath = (thread: string) => `/api/page-link/${encodeURIComponent(thread)}`;
const linkPath = `/api/page-link/links/${encodeURIComponent(LINK_ID)}`;

// A level picked on a blank chat, before it has a conversation to store it on.
let pendingLevel: Level | null = null;

/** Set the level, on `thread`, or for the blank chat's first send when null.
 *  False, with the level put back, when the server did not take it. */
export async function setLevel(thread: string | null, level: Level): Promise<boolean> {
  const before = beside.get().level;
  beside.setLevel(level);
  if (!thread) {
    pendingLevel = level;
    return true;
  }
  const saved = await put(threadPath(thread), { level });
  if (!saved) beside.setLevel(before);
  return !!saved;
}

/** Say that `thread` is the conversation open in this tab's drawer. Awaited by
 *  a blank chat's first send, so its first turn already has the page, and at
 *  the level picked before it existed. */
export async function bindThread(thread: string): Promise<void> {
  const level = pendingLevel;
  pendingLevel = null;
  await put(threadPath(thread), level ? { link: LINK_ID, level } : { link: LINK_ID });
}

function describePage(page: BesidePage) {
  const decl = declOf(page.win);
  return {
    app: page.app, label: page.label, title: page.title, url: page.url,
    context: decl?.context ?? "", tools: decl?.tools ?? [],
  };
}

// The operator worked on the page themselves since the chat's last call: the
// next call's answer says so, so the chat reads again rather than undoing it.
let steppedIn = false;

/** The operator's own hand on the page while the chat works: the level stays,
 *  the chat is told on its next call. */
export function noteSteppedIn(): void {
  steppedIn = true;
  beside.setDriving(false);
}

const STEPPED_IN = "The operator just worked on this page themselves. Read it again before you act, "
  + "and carry on only if your task still stands.";

async function run(page: BesidePage, outline: OutlineState, call: Call) {
  const answer = await runCall(page, outline, call);
  if (!steppedIn || !answer.ok) return answer;
  steppedIn = false;
  const result = answer.result;
  return { ...answer, result: typeof result === "string" ? `${STEPPED_IN}\n${result}`
    : result && typeof result === "object" && "text" in result
      ? { ...(result as object), text: `${STEPPED_IN}\n${String((result as { text: unknown }).text)}` }
      : result };
}

async function runCall(page: BesidePage, outline: OutlineState, call: Call) {
  const decl = declOf(page.win);
  const own = decl?.tools.find((t) => t.name === call.tool);
  const level = beside.get().level;
  if ((level === "drive" || level === "use") && (call.tool === "act" || own?.acts)) beside.setDriving(true);
  if (page.win && own) {
    return callFrame(page.win, call.tool, call.args, call.access ?? null);
  }
  const root = page.root();
  if (!root) return { ok: false, error: "The page is not showing anything that can be read." };
  try {
    if (call.tool === "read_page") {
      return { ok: true, result: readPage(root, outline, call.args as { ref?: string; all?: boolean }) };
    }
    if (call.tool === "find") {
      return { ok: true, result: findOnPage(root, outline, String(call.args.query ?? "")) };
    }
    if (call.tool === "act") {
      const done = act(root, outline, call.args);
      await new Promise((r) => window.setTimeout(r, ACT_SETTLE_MS));
      const changed = changesSince(root, outline);
      return { ok: true, result: changed ? `${done}\n${changed}` : done };
    }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
  return { ok: false, error: `This page has no tool named ${call.tool}.` };
}

export function usePageLink(open: boolean, thread: string | null, page: BesidePage, level: Level, driving: boolean,
  view: ChatView = "follow") {
  const pageRef = useRef(page);
  pageRef.current = page;
  const outline = useRef<OutlineState>(newOutlineState());
  const decl = useDeclOf(page.win);
  const pageKey = `${page.app}|${page.title}|${page.url}|${decl?.context ?? ""}|${(decl?.tools ?? []).map((t) => t.name).join(",")}`;

  // A different page is a different reading: old refs mean nothing there.
  useEffect(() => { outline.current = newOutlineState(); }, [page.win, page.app, page.url]);

  // The level belongs to the conversation, so read it when that changes. A
  // blank chat starts at the default.
  useEffect(() => {
    if (!thread) {
      if (!pendingLevel) beside.setLevel("send");
      return;
    }
    let live = true;
    fetch(`/api/page-link/${encodeURIComponent(thread)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => {
        if (!live || !s) return;
        if (isLevel(s.level)) beside.setLevel(s.level);
        beside.setView(isView(s.view) ? s.view : s.follow === false ? "show" : "follow");
      })
      .catch(() => {});
    return () => { live = false; };
  }, [thread]);

  // The stream, held while the drawer is open, and the page state sent each
  // time it (re)connects.
  useEffect(() => {
    if (!open) return;
    const es = new EventSource(`${linkPath}/stream`);
    es.addEventListener("ready", () => {
      void put(linkPath, { page: describePage(pageRef.current) });
      const t = beside.get().thread;
      if (t) void bindThread(t);
    });
    es.addEventListener("call", (e) => {
      let call: Call;
      try { call = JSON.parse((e as MessageEvent).data); } catch { return; }
      void run(pageRef.current, outline.current, call).then((answer) => fetch(
        `${linkPath}/results/${encodeURIComponent(call.id)}`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(answer) },
      ).catch(() => {}));
    });
    return () => es.close();
  }, [open]);

  // Which conversation the drawer shows.
  useEffect(() => {
    if (open && thread) void bindThread(thread);
  }, [open, thread]);

  // What the page is, whenever it changes while open.
  useEffect(() => {
    if (!open) return;
    void put(linkPath, { page: describePage(pageRef.current) });
  }, [open, pageKey]);

  // Tell the page whether a chat is beside it, and how much it may do.
  useEffect(() => {
    const w = page.win;
    if (!w) return;
    try { w.postMessage(linkMessage(open && !!thread, level, driving, open ? thread : null, view), window.location.origin); } catch { /* gone */ }
  }, [page.win, open, thread, level, driving, view, decl]);

  // At full autonomy the operator can step in anytime: their own hand on the
  // page pauses the drive outline and the chat hears of it on its next call.
  useEffect(() => {
    if (!driving || !thread) return;
    const doc = page.win?.document ?? document;
    const takeBack = (e: Event) => {
      if (!e.isTrusted) return;
      const target = e.target as Element | null;
      if (target?.closest?.(".ws-beside")) return;
      noteSteppedIn();
    };
    const opts = { capture: true } as const;
    doc.addEventListener("pointerdown", takeBack, opts);
    doc.addEventListener("keydown", takeBack, opts);
    return () => {
      doc.removeEventListener("pointerdown", takeBack, opts);
      doc.removeEventListener("keydown", takeBack, opts);
    };
  }, [driving, thread, page.win]);
}
