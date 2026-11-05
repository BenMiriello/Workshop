// The app side of a chat beside a page (the Workshop's docs/chat-beside-a-page.md).
// An app framed by the shell tells it what is on screen and which of its own
// tools a chat may call, answers those calls, and hands items to the chat. The
// shell parses these same names and shapes, so an app that uses this module
// cannot drift from it. Framework-free; outside a Workshop frame it does nothing.

export const PAGE_LINK_MESSAGES = {
  page: "workshop/v1/page",
  attach: "workshop/v1/attach",
  result: "workshop/v1/pageResult",
  call: "workshop/v1/pageCall",
  link: "workshop/v1/pageLink",
  takeover: "workshop/v1/pageTakeover",
  showChat: "workshop/v1/showChat",
} as const;

/** How much of the page a chat beside it reaches, least first. */
export const PAGE_LEVELS = ["send", "see", "use", "drive"] as const;
export type PageLevel = (typeof PAGE_LEVELS)[number];

/** One of the app's own tools. `acts` (default true): it changes something, so
 *  it is only offered while the chat works with the operator or is in control;
 *  false for a tool that only reads. */
export type PageTool = {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  acts?: boolean;
};

/** Text, or text with a screenshot as a `data:image/...` URL. */
export type PageResult = string | { text: string; image?: string };

/** `access`: the ids of the app's declared chat options this chat has on, or
 *  null when the chat has no grant for the app (its defaults apply). */
export type PageCall = { id: string; tool: string; args: Record<string, unknown>; access: string[] | null };

/** How the operator sees a chat's work in an app that shows it: `follow`
 *  (what it opens or acts on comes to the front), `show` (its work waits in
 *  the app, nothing comes forward) or `hide` (it works unseen, kept out of the
 *  app's lists). */
export const CHAT_VIEWS = ["follow", "show", "hide"] as const;
export type ChatView = (typeof CHAT_VIEWS)[number];

/** `thread`: the conversation beside the page, null when none. An app marks
 *  what that chat made (a tab it opened) and can follow it. `view`: how the
 *  operator sees that chat's work; `follow` is `view === "follow"`, kept for
 *  apps that read only it. Both absent from a shell older than them. */
export type PageLinkState = {
  open: boolean; level: PageLevel; driving: boolean; thread: string | null; follow?: boolean; view?: ChatView;
};

export type ChatAttachItem = {
  kind: "text" | "link" | "image" | "page" | "item";
  title?: string; text?: string; url?: string; src?: string; app?: string; id?: string;
};

/** `scope`: open the chat in that scope (`app:<slug>`); `apps`: grant those apps
 *  to the conversation before its first turn; `send` needs a `prompt`.
 *  `sendOnly` sends the items as a message of their own at once, and leaves
 *  whatever the composer already holds (a draft, other attachments) as it was. */
export type ChatAttach = {
  items: ChatAttachItem[];
  prompt?: string; send?: boolean; newChat?: boolean; scope?: string; apps?: string[];
  sendOnly?: boolean;
};

export type ShellMessage = { type: "pageLink"; link: PageLinkState } | { type: "pageCall"; call: PageCall };

/** What the shell keeps of a page's context, in characters of JSON. */
export const PAGE_CONTEXT_MAX = 6000;

const framed = (): boolean => {
  try { return typeof window !== "undefined" && window.parent !== window; } catch { return true; }
};

function post(type: string, body: object): boolean {
  if (!framed()) return false;
  window.parent.postMessage({ v: 1, type, ...body }, location.origin);
  return true;
}

const isLevel = (v: unknown): v is PageLevel =>
  typeof v === "string" && (PAGE_LEVELS as readonly string[]).includes(v);

/** What a message posted to this frame says, if the shell sent it. */
export function readShellMessage(e: MessageEvent): ShellMessage | null {
  if (e.origin !== location.origin || e.source !== window.parent || e.source === window) return null;
  const d = e.data as Record<string, unknown> | null;
  if (!d || typeof d !== "object" || d.v !== 1) return null;
  if (d.type === PAGE_LINK_MESSAGES.link) {
    const level = isLevel(d.level) ? d.level : "send";
    const thread = typeof d.thread === "string" && d.thread ? d.thread : null;
    const view: ChatView = (CHAT_VIEWS as readonly unknown[]).includes(d.view) ? d.view as ChatView
      : d.follow === false ? "show" : "follow";
    return { type: "pageLink", link: { open: d.open === true, level, driving: d.driving === true, thread, follow: view === "follow", view } };
  }
  if (d.type === PAGE_LINK_MESSAGES.call && typeof d.id === "string" && typeof d.tool === "string") {
    const args = d.args && typeof d.args === "object" ? d.args as Record<string, unknown> : {};
    const access = Array.isArray(d.access) ? d.access.filter((a): a is string => typeof a === "string") : null;
    return { type: "pageCall", call: { id: d.id, tool: d.tool, args, access } };
  }
  return null;
}

/** Hands items (and optionally a prompt) to the chat beside the page; the shell
 *  opens its drawer. False outside a Workshop frame. */
export function attachToChat(a: ChatAttach): boolean {
  return post(PAGE_LINK_MESSAGES.attach, a);
}

/** Opens the chat beside the page on that conversation: the one that made or
 *  works in something the operator tapped (a tab a chat opened). False outside
 *  a Workshop frame. */
export function showChat(thread: string): boolean {
  return post(PAGE_LINK_MESSAGES.showChat, { thread });
}

/** The operator's own input landed on a surface the shell cannot see (a native
 *  view): a chat in control hands the page back. */
export function reportTakeover(): void {
  post(PAGE_LINK_MESSAGES.takeover, {});
}

export type PageLinkOptions = {
  /** What is on screen, read at each post. Ids the app's tools take belong here. */
  context: () => object | string;
  tools?: PageTool[] | (() => PageTool[]);
  /** Runs one of `tools` for the chat. A throw answers the chat with its message. */
  run?: (call: PageCall) => PageResult | Promise<PageResult>;
  onLink?: (link: PageLinkState) => void;
  /** Debounce for `changed()`, so a burst of updates posts once. */
  delayMs?: number;
};

export type PageLinkHandle = {
  /** Something on screen changed; posts the page after the debounce. */
  changed: () => void;
  link: () => PageLinkState;
  stop: () => void;
};

/** Keeps the shell told what this page is and answers the chat's calls to its tools. */
export function startPageLink(opts: PageLinkOptions): PageLinkHandle {
  let state: PageLinkState = { open: false, level: "send", driving: false, thread: null };
  if (!framed()) return { changed: () => {}, link: () => state, stop: () => {} };
  let timer: ReturnType<typeof setTimeout> | undefined;
  const tools = () => (typeof opts.tools === "function" ? opts.tools() : opts.tools ?? []);

  const send = () => {
    clearTimeout(timer);
    const raw = opts.context();
    const json = typeof raw === "string" ? raw : JSON.stringify(raw);
    const context = json.length > PAGE_CONTEXT_MAX ? `${json.slice(0, PAGE_CONTEXT_MAX - 1)}…` : raw;
    post(PAGE_LINK_MESSAGES.page, { context, tools: tools() });
  };
  const answer = async (call: PageCall) => {
    const reply = (body: object) => post(PAGE_LINK_MESSAGES.result, { id: call.id, ...body });
    if (!opts.run || !tools().some((t) => t.name === call.tool)) {
      reply({ ok: false, error: `This page has no tool named ${call.tool}.` });
      return;
    }
    try {
      reply({ ok: true, result: await opts.run(call) });
    } catch (e) {
      reply({ ok: false, error: e instanceof Error ? e.message : String(e) });
    }
  };
  const onMessage = (e: MessageEvent) => {
    const m = readShellMessage(e);
    if (m?.type === "pageCall") void answer(m.call);
    if (m?.type !== "pageLink") return;
    const opened = m.link.open && !state.open;
    state = m.link;
    opts.onLink?.(state);
    // A drawer opened after the last post has not seen it.
    if (opened) send();
  };

  window.addEventListener("message", onMessage);
  const changed = () => {
    clearTimeout(timer);
    timer = setTimeout(send, opts.delayMs ?? 300);
  };
  changed();
  return {
    changed,
    link: () => state,
    stop: () => { clearTimeout(timer); window.removeEventListener("message", onMessage); },
  };
}
