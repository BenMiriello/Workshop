import { useEffect } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import Chat from "../components/Chat";

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

  // P5: legacy agent:<slug> URLs that point at a thread the backend has
  // since promoted (context_key rewritten to general or project:<slug>)
  // redirect quietly to the current URL so the composer picks the right
  // provider. Bookmarks keep resolving; the URL just updates to match the
  // thread's actual home.
  useEffect(() => {
    if (!initialThreadId) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`/api/chat/threads/${encodeURIComponent(initialThreadId)}`);
        if (!r.ok) return;
        const t = await r.json();
        const actual = String(t.context_key || "general");
        if (!cancelled && actual !== contextKey) {
          nav(`/chat/${encodeURIComponent(actual)}?thread=${encodeURIComponent(initialThreadId)}`, { replace: true });
        }
      } catch {
        // Silently keep the URL as-is; Chat will show its own load-error UI.
      }
    })();
    return () => { cancelled = true; };
  }, [contextKey, initialThreadId, nav]);

  // Pin the full-screen shell to the *visual* viewport so the composer sits
  // right above the keyboard and the messages above it stay put.
  //
  // The shell is position:fixed (see .ws-app--chatfull). On iOS the keyboard
  // does NOT resize the layout viewport — it overlays, and Safari scrolls the
  // page up to reveal the focused input, which would carry a static layout up
  // and out of view. visualViewport gives the true visible window: its height
  // shrinks for the keyboard and its offsetTop is how far the page scrolled
  // under it. We feed both into --app-vh / --app-top so the fixed shell exactly
  // overlays the visible area on every browser (100dvh fallback pre-paint).
  useEffect(() => {
    const vv = window.visualViewport;
    const set = () => {
      const h = vv ? vv.height : window.innerHeight;
      const top = vv ? vv.offsetTop : 0;
      const root = document.documentElement.style;
      root.setProperty("--app-vh", `${Math.round(h)}px`);
      root.setProperty("--app-top", `${Math.round(top)}px`);
    };
    set();
    // Keep the page itself from scrolling behind the fixed shell.
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    vv?.addEventListener("resize", set);
    vv?.addEventListener("scroll", set);
    window.addEventListener("resize", set);
    window.addEventListener("orientationchange", set);
    return () => {
      vv?.removeEventListener("resize", set);
      vv?.removeEventListener("scroll", set);
      window.removeEventListener("resize", set);
      window.removeEventListener("orientationchange", set);
      document.body.style.overflow = prevOverflow;
      document.documentElement.style.removeProperty("--app-vh");
      document.documentElement.style.removeProperty("--app-top");
    };
  }, []);

  return <Chat contextKey={contextKey} variant="full" initialThreadId={initialThreadId} />;
}
