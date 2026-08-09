import { useEffect } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import Chat from "../components/Chat";
import { chatApi } from "../lib/chat";
import { useVisualViewport } from "../lib/useVisualViewport";

// Focused-chat route. Mounts Chat in "full" variant: a centred, viewport-tall
// conversation that owns its own chrome (a single compact toolbar with Back,
// conversation picker, model, tasks). No page header — the breadcrumb already
// names the chat, and a second title only repeated the same identifier.
export default function FocusedChat() {
  const nav = useNavigate();
  const { contextKey: raw = "" } = useParams();
  const contextKey = decodeURIComponent(raw);
  // `?thread=<id>` resumes a specific conversation (e.g. a draft project chat
  // reopened from the Projects page) rather than the most recent one.
  const [search] = useSearchParams();
  const initialThreadId = search.get("thread") || undefined;
  // `?new=1` is an explicit "start blank here", set by newChatHref. It outranks
  // every restore rule inside Chat; see lib/chatSelection.
  const startNew = search.get("new") === "1";

  // P5: legacy `agent:<slug>` URLs that point at a thread the backend has since
  // promoted (context_key rewritten to general or project:<slug>) redirect
  // quietly to the thread's real home, so bookmarks keep resolving and the URL
  // updates to match. THE REDIRECT ONLY; Chat resolves the scope itself and
  // opens the right conversation either way.
  //
  // ONE FIELD, and that is the whole of this read. It used to be a raw fetch of
  // the thread DOCUMENT — which arrived carrying the entire transcript, a second
  // copy of a conversation the component below was about to load for itself.
  // Two components holding two copies of a thread is the shape of every reorder
  // bug this chat has had. `getThread` is now the thread's SETTINGS endpoint and
  // nothing reads its `messages`; the transcript has exactly one reader, the
  // event log.
  useEffect(() => {
    if (!initialThreadId) return;
    let cancelled = false;
    chatApi.getThread(initialThreadId)
      .then((t) => {
        const actual = String(t.context_key || "general");
        if (!cancelled && actual !== contextKey) {
          nav(`/chat/${encodeURIComponent(actual)}?thread=${encodeURIComponent(initialThreadId)}`, { replace: true });
        }
      })
      // Silently keep the URL as-is; Chat shows its own load-error UI.
      .catch(() => {});
    return () => { cancelled = true; };
  }, [contextKey, initialThreadId, nav]);

  // Pin the full-screen shell to the visual viewport (mobile keyboard handling).
  // One shared implementation — see lib/useVisualViewport.
  useVisualViewport();

  return (
    <Chat
      contextKey={contextKey}
      variant="full"
      initialThreadId={initialThreadId}
      startNew={startNew}
    />
  );
}
