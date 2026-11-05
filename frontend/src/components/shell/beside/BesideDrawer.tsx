// The chat beside the page: a column on the right on a desktop, the lower part
// of the screen on a phone, with the page still in front of it. The rules are in
// docs/chat-beside-a-page.md.
//
// The chat inside is the ordinary chat (`Chat`, the dock variant, which never
// writes the address bar). This component only decides which conversation it
// is on, links that conversation to the page beside it (usePageLink), and hands
// the chat what was attached from outside.

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { isOpenChat } from "../places";
import Chat from "../../Chat";
import { userStorage } from "../../../lib/storage";
import { ChatEmbedContext, type ChatEmbed } from "../../chat/chatEmbed";
import { OverlayScope } from "@workshop/ui";
import { useMediaQuery } from "../../../lib/useMediaQuery";
import { useAppFrames } from "../frames/AppFrames";
import { receiveAttach } from "./attach";
import { beside, useBeside } from "./besideStore";
import { rememberBeside } from "./lastBeside";
import { ContextChips } from "./ContextChips";
import { DrawerChats } from "./DrawerChats";
import { attachedBlocks } from "./contextItems";
import { startFramePages } from "./framePages";
import { bindThread, noteSteppedIn, usePageLink, type BesidePage } from "./usePageLink";
import { usePageScope } from "./usePageScope";
import { useBesideShortcut, useBesideToggle } from "./useBesideToggle";
import "./beside.css";

const WIDTH_KEY = "ws-beside-width";
const HEIGHT_KEY = "ws-beside-height";
const MIN_W = 320;
const MAX_W = 760;
const POLL_MS = 1500;

function stored(key: string, fallback: number): number {
  const n = Number(userStorage.local.getItem(key));
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function frameUrl(w: Window | null, slug: string): string {
  if (typeof window === "undefined") return "";
  if (!w) return window.location.pathname + window.location.search;
  try {
    const loc = w.location;
    const path = loc.pathname.replace(new RegExp(`^/api/base-apps/run/${slug}/proxy/[^/]+`), "");
    return (path || "/") + loc.search + loc.hash;
  } catch {
    return "";
  }
}

/** Which page is beside the chat: the app frame in front, else the Workshop
 *  page itself. Re-read while open, since an app moves inside its own document
 *  without the shell seeing it. */
function usePage(placeName: string, open: boolean): BesidePage {
  const frames = useAppFrames();
  const active = frames.frames.find((f) => f.key === frames.active) ?? null;
  const el = active ? frames.elements.get(active.key) ?? null : null;
  const win = el?.contentWindow ?? null;
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!open) return;
    const t = window.setInterval(() => setTick((n) => n + 1), POLL_MS);
    return () => window.clearInterval(t);
  }, [open]);
  return useMemo(() => {
    let title = placeName;
    try {
      title = (win?.document.title || (typeof document === "undefined" ? "" : document.title) || placeName).trim();
    } catch { /* gone */ }
    return {
      win,
      app: active?.slug ?? null,
      // The place's own name ("Photos"), not the frame's title ("photos web").
      label: placeName || active?.title || "",
      title,
      url: frameUrl(win, active?.slug ?? ""),
      root: () => {
        try {
          return win ? win.document.body : document.querySelector(".ws-shell .ws-app__body main");
        } catch {
          return null;
        }
      },
    };
  }, [win, active?.slug, active?.title, placeName, tick]);  // eslint-disable-line react-hooks/exhaustive-deps
}

/** Pages that are themselves a chat, or the list of chats: the drawer steps
 *  aside there (its state is kept), so one conversation is never on screen
 *  twice and the list is never beside a copy of one of its rows. */
export function isChatPage(pathname: string): boolean {
  return isOpenChat(pathname) || pathname === "/chats" || pathname.startsWith("/chats/");
}

export function BesideDrawer({ placeId, placeName }: { placeId: string; placeName: string }) {
  const b = useBeside();
  const location = useLocation();
  const { pathname } = location;
  const shown = b.open && !isChatPage(pathname);
  const navigate = useNavigate();
  // Each place keeps its own drawer (besideStore). The place effect runs first,
  // so a conversation carried by a link lands in the place the link went to;
  // a navigation that stayed in the place spends nothing.
  useEffect(() => { beside.setPlace(placeId); }, [placeId]);
  // Every navigation spends what was carried, a hash-only one included (a
  // tab or web link that stays in this place), so it cannot ride along to
  // the next place the operator goes by the rail.
  useEffect(() => { beside.dropCarry(); }, [location.key]);
  useEffect(() => {
    if (!isChatPage(pathname)) beside.setPoppedFrom(null);
  }, [pathname]);
  useBesideShortcut(useBesideToggle(), !isChatPage(pathname));
  const page = usePage(placeName, b.open);
  const pageScope = usePageScope(page.app);
  const narrow = useMediaQuery("(max-width: 899px)");
  const [width, setWidth] = useState(() => stored(WIDTH_KEY, 420));
  const [height, setHeight] = useState(() => stored(HEIGHT_KEY, 0.5));

  usePageLink(shown, b.thread, page, b.level, b.driving, b.view);
  useEffect(() => { beside.setPage(page.label, pageScope); }, [page.label, pageScope]);
  const scopeNow = b.scope ?? pageScope;
  useEffect(() => {
    // A conversation that only passed through is not the one had beside this place.
    if (shown && b.thread && !b.roamed) rememberBeside(placeId, b.thread, scopeNow);
  }, [shown, b.thread, b.roamed, placeId, scopeNow]);

  const pageRef = useRef({ page, pageScope });
  pageRef.current = { page, pageScope };
  useEffect(() => startFramePages({
    attach: (req) => { void receiveAttach(req, pageRef.current.page.label, pageRef.current.pageScope); },
    declared: () => {},
    // A page the shell cannot see into says the operator took over.
    takeover: (from) => {
      const { thread, driving } = beside.get();
      if (thread && driving && from === pageRef.current.page.win) noteSteppedIn();
    },
    // The mark on something a chat made or works in opens that chat here.
    showChat: (thread) => beside.open({ threadId: thread }),
  }), []);

  const scope = b.scope ?? pageScope;
  const embed = useMemo<ChatEmbed>(() => ({
    threadPath: () => "",
    // Every chat, not this scope's: the list is a page of its own, and the
    // drawer steps aside on it.
    listPath: "/chats",
    // Every recent chat, whatever it is filed under: the drawer is how you
    // get back to a conversation you had beside any page.
    pickerScope: null,
    host: {
      openThread: (threadId, contextKey) => beside.open({ scope: contextKey, threadId }),
      // A link from the chat opens in this window and takes the chat along.
      leave: (path) => { beside.carry(beside.get().thread, scope); navigate(path); },
      openFull: (path) => {
        beside.setPoppedFrom({ path: location.pathname + location.search + location.hash, label: placeName });
        navigate(path);
      },
      onThread: (id) => beside.setThread(id),
      bindThread,
      bindComposer: (api) => beside.bindComposer(api),
      pending: b.items.length,
      takeContext: () => attachedBlocks(beside.takeItems()),
      chips: <ContextChips />,
      showChats: () => beside.showChats(true),
      onClose: () => beside.close(),
    },
  }), [scope, b.items.length, navigate, location, placeName]);

  const closeChats = useCallback(() => beside.showChats(false), []);
  const asideRef = useRef<HTMLElement>(null);
  const listing = shown && b.chats;
  useEffect(() => {
    const panel = asideRef.current?.querySelector(".chat-panel");
    if (!panel) return;
    panel.toggleAttribute("inert", listing);
    return () => panel.removeAttribute("inert");
  }, [listing]);

  const startResize = useCallback((e: PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId);
    const move = (ev: globalThis.PointerEvent) => {
      if (narrow) {
        const frac = 1 - ev.clientY / window.innerHeight;
        setHeight(Math.min(0.85, Math.max(0.25, frac)));
      } else {
        setWidth(Math.min(MAX_W, Math.max(MIN_W, window.innerWidth - ev.clientX)));
      }
    };
    const up = () => {
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      setWidth((w) => { userStorage.local.setItem(WIDTH_KEY, String(w)); return w; });
      setHeight((h) => { userStorage.local.setItem(HEIGHT_KEY, String(h)); return h; });
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
  }, [narrow]);

  if (!shown) return null;
  const size = (narrow
    ? { "--beside-h": `${Math.round(height * 100)}%` }
    : { width, "--beside-w": `${width}px` }) as unknown as CSSProperties;
  return (
    <aside ref={asideRef} className="ws-beside" style={size} aria-label="Chat beside this page" data-beside-skip="">
      <div
        className="ws-beside__grip"
        role="separator"
        aria-orientation={narrow ? "horizontal" : "vertical"}
        aria-label="Resize the chat"
        onPointerDown={startResize}
      />
      {/* The drawer's own overlay host: a dialog opened from the chat is
          scoped to this column, not centred in the whole window. A phone gives
          the column half the window, too short for one, so it keeps the whole. */}
      <OverlayScope active={!narrow}>
        <ChatEmbedContext.Provider value={embed}>
          <Chat
            // A request for a NEW conversation remounts the chat: its start-blank
            // intent is read once per mount.
            key={b.intent.startNew ? `${scope}:new:${b.intent.nonce}` : scope}
            contextKey={scope}
            variant="dock"
            // Exactly the scope the page asks for: never the scope last picked
            // on the full chat page for the same key.
            scoped
            initialThreadId={b.intent.threadId ?? undefined}
            navKey={String(b.intent.nonce)}
            startNew={b.intent.startNew}
          />
        </ChatEmbedContext.Provider>
        {b.chats && <DrawerChats scope={scope} onClose={closeChats} />}
      </OverlayScope>
    </aside>
  );
}

