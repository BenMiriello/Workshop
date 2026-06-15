import { useEffect } from "react";
import { useParams } from "react-router-dom";
import Chat from "../components/Chat";

// Focused-chat route. Mounts Chat in "full" variant: a centred, viewport-tall
// conversation that owns its own chrome (a single compact toolbar with Back,
// conversation picker, model, tasks). No page header — the breadcrumb already
// names the chat, and a second title only repeated the same identifier.
export default function FocusedChat() {
  const { contextKey: raw = "" } = useParams();
  const contextKey = decodeURIComponent(raw);

  // Pin the full-screen shell to the *visual* viewport. CSS `100dvh` is close
  // but on real mobile browsers it disagrees with the visible area as the
  // address bar shows/hides and the keyboard opens, leaving a blank gap below
  // the composer. visualViewport.height is the exact visible height; the shell
  // reads it via --app-vh (with a 100dvh fallback for the first paint / no-VV).
  useEffect(() => {
    const vv = window.visualViewport;
    const set = () => {
      const h = vv ? vv.height : window.innerHeight;
      document.documentElement.style.setProperty("--app-vh", `${Math.round(h)}px`);
    };
    set();
    vv?.addEventListener("resize", set);
    vv?.addEventListener("scroll", set);
    window.addEventListener("resize", set);
    window.addEventListener("orientationchange", set);
    return () => {
      vv?.removeEventListener("resize", set);
      vv?.removeEventListener("scroll", set);
      window.removeEventListener("resize", set);
      window.removeEventListener("orientationchange", set);
      document.documentElement.style.removeProperty("--app-vh");
    };
  }, []);

  return <Chat contextKey={contextKey} variant="full" />;
}
