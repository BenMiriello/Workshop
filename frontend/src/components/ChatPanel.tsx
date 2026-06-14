// DEPRECATED — use ./Chat instead. This file is a shim so existing imports
// (e.g. `import ChatPanel from "../components/ChatPanel"`) keep working while
// callers migrate. Removes after the migration sweep.
import Chat, { type ChatProps } from "./Chat";

export default function ChatPanel(props: Omit<ChatProps, "variant" | "mode">) {
  return <Chat {...props} variant="panel" mode="data" />;
}
