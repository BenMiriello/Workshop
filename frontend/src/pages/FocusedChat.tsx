import { useParams } from "react-router-dom";
import Chat from "../components/Chat";

// Focused-chat route. Mounts Chat in "full" variant: a centred, viewport-tall
// conversation that owns its own chrome (a single compact toolbar with Back,
// conversation picker, model, tasks). No page header — the breadcrumb already
// names the chat, and a second title only repeated the same identifier.
export default function FocusedChat() {
  const { contextKey: raw = "" } = useParams();
  const contextKey = decodeURIComponent(raw);
  return <Chat contextKey={contextKey} variant="full" />;
}
