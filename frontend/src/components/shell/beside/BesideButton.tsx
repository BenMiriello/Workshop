// The Chat button: opens the chat beside the page, or closes it, for the place
// you are in (each place keeps its own drawer, besideStore). On the rail on a
// desktop and in the top bar on a phone.

import { MessageSquare } from "lucide-react";
import { useLocation } from "react-router-dom";
import { isChatPage } from "./BesideDrawer";
import { useBeside } from "./besideStore";
import { useBesideToggle } from "./useBesideToggle";

export function BesideButton({ variant }: { variant: "rail" | "bar" }) {
  const b = useBeside();
  const { pathname } = useLocation();
  const toggle = useBesideToggle();
  // A chat page is already a chat: nothing to open beside it.
  if (isChatPage(pathname)) return null;
  const label = b.open ? "Close the chat beside this page" : "Chat beside this page";
  return (
    <button
      type="button"
      className={variant === "rail"
        ? `ws-rail__tile ws-beside-btn${b.open ? " is-on" : ""}`
        : `ws-beside-btn ws-beside-btn--bar${b.open ? " is-on" : ""}`}
      aria-label={label}
      aria-pressed={b.open}
      title={label}
      onClick={toggle}
    >
      <MessageSquare size={18} aria-hidden />
    </button>
  );
}
